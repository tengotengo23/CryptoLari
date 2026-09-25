#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test the CryptoLari stratum pool (contrib/cryptolari/pool).

Two stratum miners submit shares; the pool finds blocks that pay the dev
fee, rejects duplicate/stale shares, and pays miners proportionally once
the blocks mature. Orphaned blocks are not paid.
"""
import asyncio
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import urllib.parse
import urllib.request
from decimal import Decimal

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal, rpc_url, wait_until

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "contrib", "cryptolari", "pool"))
import pool as lari_pool  # noqa: E402

COIN = 100000000
DEV_FEE = 50 * COIN * 5 // 100


def dsha256(b):
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


class StratumClient:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.buf = b""
        self.next_id = 0
        self.jobs = []
        self.difficulty = None

    def readline(self):
        while b"\n" not in self.buf:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("pool closed the connection")
            self.buf += data
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode()

    def request(self, method, params):
        self.next_id += 1
        self.sock.sendall((json.dumps({"id": self.next_id, "method": method, "params": params}) + "\n").encode())
        while True:
            msg = json.loads(self.readline())
            if msg.get("id") == self.next_id:
                return msg
            self.handle(msg)

    def handle(self, msg):
        if msg.get("method") == "mining.notify":
            self.jobs.append(msg["params"])
        elif msg.get("method") == "mining.set_difficulty":
            self.difficulty = msg["params"][0]

    def poll(self):
        """Read pending notifications."""
        self.sock.settimeout(0.3)
        try:
            while True:
                self.handle(json.loads(self.readline()))
        except socket.timeout:
            pass
        finally:
            self.sock.settimeout(30)

    def subscribe(self):
        result = self.request("mining.subscribe", ["test-miner/1.0"])["result"]
        self.extranonce1 = bytes.fromhex(result[1])
        self.extranonce2_size = result[2]

    def mine(self, want_block, extranonce2=b"\0\0\0\1"):
        """Find a nonce for the latest job. want_block: whether the hash must meet the network target."""
        job_id, prevhash, coinb1, coinb2, branch, version, bits, ntime, _ = self.jobs[-1]
        coinbase = bytes.fromhex(coinb1) + self.extranonce1 + extranonce2 + bytes.fromhex(coinb2)
        root = dsha256(coinbase)
        for h in branch:
            root = dsha256(root + bytes.fromhex(h))
        prev = b"".join(bytes.fromhex(prevhash)[i:i + 4][::-1] for i in range(0, 32, 4))
        target = lari_pool.target_from_bits(bits)
        base = struct.pack("<I", int(version, 16)) + prev + root + struct.pack("<I", int(ntime, 16)) + struct.pack("<I", int(bits, 16))
        for nonce in range(1 << 20):
            h = int.from_bytes(dsha256(base + struct.pack("<I", nonce)), "little")
            if (h <= target) == want_block:
                return [job_id, extranonce2.hex(), ntime, "%08x" % nonce]
        raise AssertionError("no nonce found")

    def submit(self, worker, share):
        return self.request("mining.submit", [worker] + share)


class PoolTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 2  # getblocktemplate needs a peer
        self.setup_clean_chain = True
        self.extra_args = [["-devfeeheight=1"]] * 2

    def run_test(self):
        node = self.nodes[0]
        node.generate(101)
        pool_address = node.getnewaddress()
        self.addr_a = node.getnewaddress()
        self.addr_b = node.getnewaddress()

        url = urllib.parse.urlparse(rpc_url(node.datadir, 0, None))
        args = lari_pool.parse_args([
            "--chain=regtest", "--address=" + pool_address,
            "--rpcurl=http://%s:%d/" % (url.hostname, url.port),
            "--rpcuser=" + urllib.parse.unquote(url.username), "--rpcpassword=" + urllib.parse.unquote(url.password),
            "--db=" + os.path.join(self.options.tmpdir, "pool.sqlite"), "--bind=127.0.0.1", "--port=0",
            "--stats-bind=127.0.0.1", "--stats-port=0", "--fee=1", "--difficulty=1e-12",
            "--poll=0.2", "--refresh=0.5", "--payout-interval=3600"])
        self.pool = lari_pool.create(args)
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.run_async(self.pool.start())
        try:
            self.check(node, pool_address)
        finally:
            self.run_async(self.pool.stop())
            self.loop.call_soon_threadsafe(self.loop.stop)

    def run_async(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(60)

    def stats(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.pool.stats_port, path)) as resp:
            body = resp.read().decode()
            return json.loads(body) if path.startswith("/api") else body

    def check(self, node, pool_address):
        a = StratumClient(self.pool.port)
        b = StratumClient(self.pool.port)
        for c in (a, b):
            c.subscribe()
        assert a.extranonce1 != b.extranonce1

        self.log.info("Authorization requires a CryptoLari address")
        assert_equal(a.request("mining.authorize", ["notanaddress", "x"])["error"][0], 24)
        assert a.request("mining.authorize", [self.addr_a + ".rig1", "x"])["result"]
        assert b.request("mining.authorize", [self.addr_b, "x"])["result"]
        a.poll()
        b.poll()
        assert a.jobs and b.jobs

        self.log.info("Shares below the network target are credited but are not blocks")
        height = node.getblockcount()
        for i in range(3):
            assert a.submit(self.addr_a + ".rig1", a.mine(False, struct.pack(">I", i)))["result"]
        assert b.submit(self.addr_b, b.mine(False))["result"]
        assert_equal(node.getblockcount(), height)

        self.log.info("Duplicate, stale and unauthorized shares are rejected")
        share = a.mine(False, b"\xff\0\0\0")
        assert a.submit(self.addr_a + ".rig1", share)["result"]
        assert_equal(a.submit(self.addr_a + ".rig1", share)["error"][0], 22)
        assert_equal(a.submit(self.addr_a + ".rig1", ["ffff"] + share[1:])["error"][0], 21)
        assert_equal(a.submit(self.addr_b, share)["error"][0], 24)

        self.log.info("A block found through the pool pays the pool and the dev fee and includes mempool txs")
        txids = [node.sendtoaddress(node.getnewaddress(), 1) for _ in range(3)]
        wait_until(lambda: (a.poll() or True) and len(a.jobs[-1][4]) == 2, timeout=10)  # 3 txs -> merkle branch of 2
        assert a.submit(self.addr_a + ".rig1", a.mine(True, b"\0\0\0\x09"))["result"]
        assert_equal(node.getblockcount(), height + 1)
        block = node.getblock(node.getbestblockhash(), 2)
        outputs = block["tx"][0]["vout"]
        pool_out = [o for o in outputs if o["scriptPubKey"].get("addresses") == [pool_address]]
        dev_out = [o for o in outputs if o["scriptPubKey"]["hex"] == "52"]
        assert_equal(len(pool_out), 1)
        fees = -sum(node.gettransaction(t)["fee"] for t in txids)
        assert_equal(pool_out[0]["value"], Decimal("47.5") + fees)
        assert_equal(dev_out[0]["value"], Decimal("2.5"))
        assert_equal(sorted(t["txid"] for t in block["tx"][1:]), sorted(txids))
        first_block = block["hash"]

        self.log.info("Miners get new work for the next block")
        wait_until(lambda: (a.poll() or True) and a.jobs[-1][8] and a.jobs[-1][0] != share[0], timeout=10)

        self.log.info("A second pool block that gets orphaned is not paid")
        b.poll()
        assert b.submit(self.addr_b, b.mine(True))["result"]
        orphan = node.getbestblockhash()
        assert orphan != first_block
        node.invalidateblock(orphan)
        node.generatetoaddress(1, node.getnewaddress())

        self.log.info("Payouts are proportional to shares once blocks mature")
        node.generatetoaddress(100, node.getnewaddress())
        txid = self.run_async(self.pool.process_payouts())
        assert txid
        node.generate(1)
        reward = pool_out[0]["value"]
        # a: 5 shares (3 + 1 + the block), b: 1 share; 1% pool fee
        expected_a = (reward * 99 / 100 * 5 / 6).quantize(Decimal("0.00000001"), rounding="ROUND_DOWN")
        expected_b = (reward * 99 / 100 * 1 / 6).quantize(Decimal("0.00000001"), rounding="ROUND_DOWN")
        assert_equal(node.getreceivedbyaddress(self.addr_a), expected_a)
        assert_equal(node.getreceivedbyaddress(self.addr_b), expected_b)
        statuses = {blk["hash"]: blk["status"] for blk in self.stats("/api/stats")["blocks"]}
        assert_equal(statuses, {first_block: "paid", orphan: "orphan"})
        assert_equal(self.run_async(self.pool.process_payouts()), None)  # nothing left to pay

        self.log.info("Stats page")
        miner = self.stats("/api/miner/" + self.addr_a)
        assert_equal(miner["paid"], int(expected_a * COIN))
        assert "CryptoLari Pool" in self.stats("/")


if __name__ == '__main__':
    PoolTest().main()
