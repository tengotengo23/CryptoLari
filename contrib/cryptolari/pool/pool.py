#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""CryptoLari stratum mining pool.

A dependency-free stratum v1 pool server for cryptolarid. It builds block
templates that pay the consensus dev fee, accepts shares from miners, submits
blocks to the node and pays miners proportionally to their shares once a
block has matured (PROP payout scheme).

Miners connect with any stratum v1 SHA-256 miner, using their CryptoLari
address as the username (an optional ".worker" suffix is allowed) and any
password:

    cpuminer -a sha256d -o stratum+tcp://<pool host>:3333 -u G...address -p x

Run the pool next to a cryptolarid with a wallet:

    contrib/cryptolari/pool/pool.py --rpcuser=<user> --rpcpassword=<pass> \\
        --address <pool address in the node wallet> --fee 1

Use --no-payouts for solo mining (all rewards stay at --address).
"""
import argparse
import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_DOWN

COIN = 100000000
COINBASE_MATURITY = 100
DIFF1_TARGET = 0x00000000ffff0000000000000000000000000000000000000000000000000000
DEFAULT_RPC_PORTS = {"main": 9554, "test": 19554, "regtest": 18443}
EXTRANONCE2_SIZE = 4
POOL_TAG = b"/CryptoLari pool/"

log = logging.getLogger("pool")


# ---------------------------------------------------------------- helpers

def dsha256(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def varint(n):
    if n < 0xfd:
        return struct.pack("<B", n)
    if n <= 0xffff:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xffffffff:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def script_push(data):
    if len(data) < 0x4c:
        return bytes([len(data)]) + data
    if len(data) <= 0xff:
        return b"\x4c" + bytes([len(data)]) + data
    return b"\x4d" + struct.pack("<H", len(data)) + data


def script_number(n):
    """Serialize n the way CScript() << n does (used for the BIP34 height)."""
    if n == 0:
        return b"\x00"
    if 1 <= n <= 16:
        return bytes([0x50 + n])
    out = bytearray()
    while n:
        out.append(n & 0xff)
        n >>= 8
    if out[-1] & 0x80:
        out.append(0)
    return script_push(bytes(out))


def merkle_branch(hashes):
    """Merkle branch for the coinbase (index 0), given the other tx hashes (internal byte order)."""
    branch = []
    level = [None] + list(hashes)
    while len(level) > 1:
        branch.append(level[1])
        if len(level) % 2:
            level.append(level[-1])
        level = [None] + [dsha256(level[i] + level[i + 1]) for i in range(2, len(level), 2)]
    return branch


def stratum_prevhash(display_hex):
    """Previous block hash as sent in mining.notify (internal order, each 32-bit word byte-swapped)."""
    internal = bytes.fromhex(display_hex)[::-1]
    return b"".join(internal[i:i + 4][::-1] for i in range(0, 32, 4)).hex()


def target_from_bits(bits_hex):
    bits = int(bits_hex, 16)
    return (bits & 0x7fffff) << (8 * ((bits >> 24) - 3))


def difficulty_to_target(difficulty):
    return int(Decimal(DIFF1_TARGET) / Decimal(repr(difficulty)))


def target_to_difficulty(target):
    return float(Decimal(DIFF1_TARGET) / Decimal(target))


class RPCError(Exception):
    def __init__(self, error):
        super().__init__(error.get("message", str(error)))
        self.code = error.get("code")


class RPC:
    def __init__(self, url, user, password):
        self.url = url
        self.auth = "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        self.id = 0

    def call(self, method, *params):
        self.id += 1
        body = json.dumps({"version": "1.1", "id": self.id, "method": method, "params": list(params)}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                reply = json.loads(resp.read().decode(), parse_float=Decimal)
        except urllib.error.HTTPError as e:
            reply = json.loads(e.read().decode() or "{}", parse_float=Decimal)
            if not reply.get("error"):
                raise
        if reply.get("error"):
            raise RPCError(reply["error"])
        return reply["result"]

    def __getattr__(self, name):
        return lambda *params: self.call(name, *params)


# ---------------------------------------------------------------- jobs

class Job:
    """One block template, split into the pieces a stratum miner needs."""

    def __init__(self, job_id, tmpl, payout_script, extranonce1_size):
        self.id = job_id
        self.height = tmpl["height"]
        self.prevhash = tmpl["previousblockhash"]
        self.version = tmpl["version"]
        self.bits = tmpl["bits"]
        self.curtime = tmpl["curtime"]
        self.mintime = tmpl["mintime"]
        self.target = int(tmpl["target"], 16)
        self.reward = tmpl["coinbasevalue"]
        self.txs = [bytes.fromhex(t["data"]) for t in tmpl["transactions"]]
        self.witness = "default_witness_commitment" in tmpl

        outputs = [(tmpl["coinbasevalue"], payout_script)]
        if "devfee" in tmpl:
            outputs.append((tmpl["devfee"]["amount"], bytes.fromhex(tmpl["devfee"]["script"])))
        if self.witness:
            outputs.append((0, bytes.fromhex(tmpl["default_witness_commitment"])))
        outs = varint(len(outputs)) + b"".join(struct.pack("<q", v) + varint(len(s)) + s for v, s in outputs)

        height = script_number(self.height)
        script_len = len(height) + extranonce1_size + EXTRANONCE2_SIZE + len(POOL_TAG)
        assert script_len <= 100, "coinbase scriptSig too long"
        # coinbase = coinb1 | extranonce1 | extranonce2 | coinb2
        self.coinb1 = (struct.pack("<i", 1) + b"\x01" + b"\x00" * 32 + b"\xff\xff\xff\xff" +
                       varint(script_len) + height)
        self.coinb2 = POOL_TAG + b"\xff\xff\xff\xff" + outs + b"\x00\x00\x00\x00"
        self.branch = merkle_branch([bytes.fromhex(t["txid"])[::-1] for t in tmpl["transactions"]])

    def notify_params(self, clean):
        return [self.id, stratum_prevhash(self.prevhash), self.coinb1.hex(), self.coinb2.hex(),
                [h.hex() for h in self.branch], "%08x" % self.version, self.bits, "%08x" % self.curtime, clean]

    def coinbase(self, extranonce1, extranonce2):
        return self.coinb1 + extranonce1 + extranonce2 + self.coinb2

    def header(self, coinbase, ntime, nonce):
        root = dsha256(coinbase)
        for h in self.branch:
            root = dsha256(root + h)
        return (struct.pack("<I", self.version) + bytes.fromhex(self.prevhash)[::-1] + root +
                struct.pack("<I", ntime) + struct.pack("<I", int(self.bits, 16)) + struct.pack("<I", nonce))

    def block(self, header, coinbase):
        if self.witness:
            # BIP141: coinbase witness is the 32-byte reserved value (all zeros)
            cb = (coinbase[:4] + b"\x00\x01" + coinbase[4:-4] +
                  b"\x01\x20" + b"\x00" * 32 + coinbase[-4:])
        else:
            cb = coinbase
        return header + varint(1 + len(self.txs)) + cb + b"".join(self.txs)


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY,
    address TEXT NOT NULL,
    worker TEXT NOT NULL,
    difficulty REAL NOT NULL,
    time INTEGER NOT NULL,
    block_hash TEXT              -- NULL while the round is open
);
CREATE INDEX IF NOT EXISTS shares_round ON shares(block_hash);
CREATE TABLE IF NOT EXISTS blocks (
    hash TEXT PRIMARY KEY,
    height INTEGER NOT NULL,
    reward INTEGER NOT NULL,      -- value of the pool output (satoshis)
    time INTEGER NOT NULL,
    finder TEXT NOT NULL,
    status TEXT NOT NULL,         -- pending, orphan, paid, solo
    payout_txid TEXT
);
CREATE TABLE IF NOT EXISTS credits (
    block_hash TEXT NOT NULL,
    address TEXT NOT NULL,
    amount INTEGER NOT NULL,
    PRIMARY KEY (block_hash, address)
);
"""


class Ledger:
    def __init__(self, path, fee_percent):
        # Created by the main thread, then only used from the event loop thread.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.fee = Decimal(repr(fee_percent))

    def add_share(self, address, worker, difficulty):
        with self.db:
            self.db.execute("INSERT INTO shares (address, worker, difficulty, time) VALUES (?, ?, ?, ?)",
                            (address, worker, difficulty, int(time.time())))

    def close_round(self, block_hash, height, reward, finder, payouts):
        """Record a found block and split its reward over the open round's shares."""
        with self.db:
            self.db.execute("INSERT INTO blocks (hash, height, reward, time, finder, status) VALUES (?, ?, ?, ?, ?, ?)",
                            (block_hash, height, reward, int(time.time()), finder, "pending" if payouts else "solo"))
            self.db.execute("UPDATE shares SET block_hash = ? WHERE block_hash IS NULL", (block_hash,))
            if not payouts:
                return {}
            rows = self.db.execute("SELECT address, SUM(difficulty) AS d FROM shares WHERE block_hash = ? GROUP BY address",
                                   (block_hash,)).fetchall()
            total = sum(Decimal(repr(r["d"])) for r in rows)
            distributable = Decimal(reward) * (100 - self.fee) / 100
            credits = {}
            for r in rows:
                amount = int((distributable * Decimal(repr(r["d"])) / total).to_integral_value(ROUND_DOWN))
                if amount > 0:
                    credits[r["address"]] = amount
                    self.db.execute("INSERT INTO credits (block_hash, address, amount) VALUES (?, ?, ?)", (block_hash, r["address"], amount))
            return credits

    def pending_blocks(self):
        return self.db.execute("SELECT * FROM blocks WHERE status = 'pending' ORDER BY height").fetchall()

    def credits(self, block_hash):
        return {r["address"]: r["amount"] for r in self.db.execute("SELECT address, amount FROM credits WHERE block_hash = ?", (block_hash,))}

    def stats(self, window=600):
        since = int(time.time()) - window
        work = self.db.execute("SELECT COALESCE(SUM(difficulty), 0) FROM shares WHERE time >= ?", (since,)).fetchone()[0]
        miners = self.db.execute("SELECT COUNT(DISTINCT address) FROM shares WHERE time >= ?", (since,)).fetchone()[0]
        blocks = [dict(r) for r in self.db.execute("SELECT hash, height, reward, time, finder, status, payout_txid FROM blocks ORDER BY height DESC LIMIT 20")]
        return {"hashrate": work * 2**32 / window, "active_miners": miners, "blocks": blocks}

    def miner_stats(self, address, window=600):
        since = int(time.time()) - window
        work = self.db.execute("SELECT COALESCE(SUM(difficulty), 0) FROM shares WHERE address = ? AND time >= ?", (address, since)).fetchone()[0]
        round_work = self.db.execute("SELECT COALESCE(SUM(difficulty), 0) FROM shares WHERE address = ? AND block_hash IS NULL", (address,)).fetchone()[0]
        rows = self.db.execute("SELECT b.status, SUM(c.amount) AS amount FROM credits c JOIN blocks b ON b.hash = c.block_hash "
                               "WHERE c.address = ? GROUP BY b.status", (address,)).fetchall()
        by_status = {r["status"]: r["amount"] for r in rows}
        return {"address": address, "hashrate": work * 2**32 / window, "round_shares": round_work,
                "immature": by_status.get("pending", 0), "paid": by_status.get("paid", 0)}

    def set_status(self, block_hashes, status, txid=None):
        with self.db:
            for h in block_hashes:
                self.db.execute("UPDATE blocks SET status = ?, payout_txid = ? WHERE hash = ?", (status, txid, h))


# ---------------------------------------------------------------- stratum

class Miner:
    def __init__(self, pool, reader, writer, extranonce1):
        self.pool = pool
        self.reader = reader
        self.writer = writer
        self.extranonce1 = extranonce1
        self.subscribed = False
        self.workers = {}          # worker name -> payout address
        self.difficulty = None
        self.peer = writer.get_extra_info("peername")

    async def send(self, obj):
        self.writer.write((json.dumps(obj) + "\n").encode())
        await self.writer.drain()

    async def notify(self, job, clean):
        await self.send({"id": None, "method": "mining.notify", "params": job.notify_params(clean)})

    async def set_difficulty(self, difficulty):
        if difficulty != self.difficulty:
            self.difficulty = difficulty
            await self.send({"id": None, "method": "mining.set_difficulty", "params": [difficulty]})

    async def handle(self):
        log.info("miner connected: %s", self.peer)
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                if len(line) > 16384:
                    break
                try:
                    msg = json.loads(line)
                    result = await self.dispatch(msg.get("method"), msg.get("params") or [])
                    await self.send({"id": msg.get("id"), "result": result, "error": None})
                except StratumError as e:
                    await self.send({"id": msg.get("id"), "result": None, "error": [e.code, e.message, None]})
                except (ValueError, TypeError, AttributeError):
                    await self.send({"id": None, "result": None, "error": [20, "Malformed request", None]})
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.pool.miners.discard(self)
            self.writer.close()
            log.info("miner disconnected: %s", self.peer)

    async def dispatch(self, method, params):
        if method == "mining.subscribe":
            self.subscribed = True
            asyncio.get_event_loop().call_soon(lambda: asyncio.ensure_future(self.after_subscribe()))
            return [[["mining.set_difficulty", "1"], ["mining.notify", "1"]], self.extranonce1.hex(), EXTRANONCE2_SIZE]
        if method == "mining.authorize":
            return await self.authorize(str(params[0]))
        if method == "mining.submit":
            return await self.submit(*params[:5])
        if method == "mining.extranonce.subscribe":
            return False
        raise StratumError(20, "Unknown method")

    async def after_subscribe(self):
        if self.pool.job:
            await self.set_difficulty(self.pool.share_difficulty(self.pool.job))
            await self.notify(self.pool.job, True)

    async def authorize(self, username):
        address = username.split(".", 1)[0]
        valid = await self.pool.rpc_call("validateaddress", address)
        if not valid.get("isvalid"):
            raise StratumError(24, "Username must be a CryptoLari address")
        self.workers[username] = address
        log.info("authorized %s from %s", username, self.peer)
        return True

    async def submit(self, username, job_id, extranonce2_hex, ntime_hex, nonce_hex):
        pool = self.pool
        if username not in self.workers:
            raise StratumError(24, "Unauthorized worker")
        job = pool.jobs.get(job_id)
        if job is None:
            raise StratumError(21, "Job not found (stale)")
        extranonce2 = bytes.fromhex(extranonce2_hex)
        if len(extranonce2) != EXTRANONCE2_SIZE or len(ntime_hex) != 8 or len(nonce_hex) != 8:
            raise StratumError(20, "Malformed share")
        ntime, nonce = int(ntime_hex, 16), int(nonce_hex, 16)
        if ntime < job.mintime or ntime > time.time() + 7200:
            raise StratumError(20, "ntime out of range")
        key = (job_id, self.extranonce1, extranonce2, ntime, nonce)
        if key in pool.seen_shares:
            raise StratumError(22, "Duplicate share")
        pool.seen_shares.add(key)

        coinbase = job.coinbase(self.extranonce1, extranonce2)
        header = job.header(coinbase, ntime, nonce)
        hash_int = int.from_bytes(dsha256(header), "little")
        difficulty = self.difficulty or pool.share_difficulty(job)
        if hash_int > difficulty_to_target(difficulty) and hash_int > job.target:
            raise StratumError(23, "Low difficulty share")

        address = self.workers[username]
        pool.ledger.add_share(address, username, difficulty)
        if hash_int <= job.target:
            await pool.found_block(job, header, coinbase, username)
        return True


class StratumError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Pool:
    def __init__(self, rpc, payout_script, ledger, args):
        self.rpc = rpc
        self.payout_script = payout_script
        self.ledger = ledger
        self.args = args
        self.miners = set()
        self.jobs = {}
        self.job = None
        self.job_counter = 0
        self.extranonce_counter = int.from_bytes(os.urandom(2), "big") << 16
        self.seen_shares = set()
        self.last_refresh = 0
        self.server = None
        self.blocks_found = 0

    async def rpc_call(self, method, *params):
        return await asyncio.get_event_loop().run_in_executor(None, lambda: self.rpc.call(method, *params))

    def share_difficulty(self, job):
        network = target_to_difficulty(job.target)
        return min(self.args.difficulty, network) if self.args.difficulty else network

    async def update_template(self, force=False):
        tmpl = await self.rpc_call("getblocktemplate", {"rules": ["segwit"]})
        new_block = self.job is None or tmpl["previousblockhash"] != self.job.prevhash
        if not (new_block or force or time.time() - self.last_refresh >= self.args.refresh):
            return
        self.job_counter += 1
        job = Job("%x" % self.job_counter, tmpl, self.payout_script, 4)
        if new_block:
            self.jobs.clear()
            self.seen_shares.clear()
        self.jobs[job.id] = job
        self.job = job
        self.last_refresh = time.time()
        if new_block:
            log.info("new block template: height %d, reward %d, %d txs", job.height, job.reward, len(job.txs))
        for miner in list(self.miners):
            if miner.subscribed:
                try:
                    await miner.set_difficulty(self.share_difficulty(job))
                    await miner.notify(job, new_block)
                except ConnectionError:
                    pass

    async def found_block(self, job, header, coinbase, finder):
        block_hex = job.block(header, coinbase).hex()
        block_hash = dsha256(header)[::-1].hex()
        result = await self.rpc_call("submitblock", block_hex)
        if result is not None:
            log.warning("block %s at height %d rejected: %s", block_hash, job.height, result)
            return
        self.blocks_found += 1
        credits = self.ledger.close_round(block_hash, job.height, job.reward, finder, not self.args.no_payouts)
        log.info("BLOCK FOUND at height %d by %s: %s (credits: %s)", job.height, finder, block_hash, credits)
        await self.update_template(force=True)

    async def process_payouts(self):
        """Pay out matured blocks, and mark orphaned ones."""
        pending = self.ledger.pending_blocks()
        mature, totals = [], {}
        for b in pending:
            try:
                info = await self.rpc_call("getblock", b["hash"])
            except RPCError:
                info = {"confirmations": -1}
            if info["confirmations"] < 0:
                log.warning("block %s at height %d orphaned", b["hash"], b["height"])
                self.ledger.set_status([b["hash"]], "orphan")
            elif info["confirmations"] > COINBASE_MATURITY:
                mature.append(b["hash"])
                for addr, amount in self.ledger.credits(b["hash"]).items():
                    totals[addr] = totals.get(addr, 0) + amount
        if not mature:
            return None
        if not totals:
            self.ledger.set_status(mature, "paid")
            return None
        amounts = {addr: str(Decimal(sat) / COIN) for addr, sat in totals.items()}
        txid = await self.rpc_call("sendmany", "", amounts, 1, "CryptoLari pool payout")
        self.ledger.set_status(mature, "paid", txid)
        log.info("paid %d blocks to %d miners: %s", len(mature), len(totals), txid)
        return txid

    async def template_loop(self):
        while True:
            try:
                await self.update_template()
            except Exception:
                log.exception("getblocktemplate failed")
            await asyncio.sleep(self.args.poll)

    async def payout_loop(self):
        while True:
            try:
                await self.process_payouts()
            except Exception:
                log.exception("payout failed")
            await asyncio.sleep(self.args.payout_interval)

    async def on_http(self, reader, writer):
        """Tiny read-only HTTP server: / (HTML), /api/stats, /api/miner/<address>."""
        try:
            request = (await asyncio.wait_for(reader.readline(), 10)).decode(errors="replace").split()
            while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                pass
            path = request[1] if len(request) > 1 else "/"
            code, ctype = 200, "application/json"
            if path == "/api/stats":
                body = json.dumps(self.stats(), indent=1)
            elif path.startswith("/api/miner/"):
                body = json.dumps(self.ledger.miner_stats(path[len("/api/miner/"):]), indent=1)
            elif path == "/":
                ctype, body = "text/html; charset=utf-8", self.html()
            else:
                code, body = 404, json.dumps({"error": "not found"})
            data = body.encode()
            writer.write(("HTTP/1.0 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n\r\n" %
                          (code, "OK" if code == 200 else "Not Found", ctype, len(data))).encode() + data)
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, IndexError):
            pass
        finally:
            writer.close()

    def stats(self):
        s = self.ledger.stats()
        s.update(height=self.job.height if self.job else None,
                 network_difficulty=target_to_difficulty(self.job.target) if self.job else None,
                 connected_miners=len(self.miners), fee_percent=self.args.fee,
                 payouts=not self.args.no_payouts, stratum_port=self.port)
        return s

    def html(self):
        import html as h
        s = self.stats()
        rows = "".join("<tr><td>%d</td><td>%s…</td><td>%s</td><td>%s</td></tr>" % (
            b["height"], h.escape(b["hash"][:16]), Decimal(b["reward"]) / COIN, h.escape(b["status"])) for b in s["blocks"])
        return ("<!doctype html><html lang=ka><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>CryptoLari Pool</title><style>body{font:15px system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px;"
                "background:#f7f7f5;color:#1d1d1f}@media(prefers-color-scheme:dark){body{background:#121214;color:#ececef}}"
                "table{border-collapse:collapse;width:100%%}td,th{text-align:left;padding:6px;border-bottom:1px solid #8884}"
                "code{background:#8882;padding:2px 6px;border-radius:4px}</style></head><body>"
                "<h1>CryptoLari Pool</h1><p>სიმაღლე / height: %s · ჰეშრეიტი: %.4g H/s · მაინერები: %d · საკომისიო: %s%%</p>"
                "<p>მიერთება / connect: <code>stratum+tcp://&lt;host&gt;:%d</code>, username = შენი LARI მისამართი, password = x</p>"
                "<p>შენი ბალანსი / your stats: <code>/api/miner/&lt;address&gt;</code></p>"
                "<h2>ნაპოვნი ბლოკები / Blocks found</h2><table><tr><th>სიმაღლე</th><th>ჰეში</th><th>ჯილდო</th><th>სტატუსი</th></tr>%s</table>"
                "</body></html>") % (s["height"], s["hashrate"], s["active_miners"], s["fee_percent"], s["stratum_port"], rows)

    async def on_connect(self, reader, writer):
        self.extranonce_counter = (self.extranonce_counter + 1) & 0xffffffff
        miner = Miner(self, reader, writer, struct.pack(">I", self.extranonce_counter))
        self.miners.add(miner)
        await miner.handle()

    async def start(self):
        await self.update_template(force=True)
        self.server = await asyncio.start_server(self.on_connect, self.args.bind, self.args.port)
        self.port = self.server.sockets[0].getsockname()[1]
        log.info("stratum pool listening on %s:%d (fee %s%%, payouts %s)", self.args.bind, self.port,
                 self.args.fee, "off" if self.args.no_payouts else "on")
        if self.args.stats_port is not None:
            self.http_server = await asyncio.start_server(self.on_http, self.args.stats_bind, self.args.stats_port)
            self.stats_port = self.http_server.sockets[0].getsockname()[1]
            log.info("pool stats on http://%s:%d/", self.args.stats_bind, self.stats_port)
        self.tasks = [asyncio.ensure_future(self.template_loop())]
        if not self.args.no_payouts:
            self.tasks.append(asyncio.ensure_future(self.payout_loop()))
        return self.tasks

    async def stop(self):
        for server in (self.server, getattr(self, "http_server", None)):
            if server:
                server.close()
        for miner in list(self.miners):
            miner.writer.close()
        for task in self.tasks:
            task.cancel()
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------- setup

def read_cookie(datadir, chain):
    sub = {"main": "", "test": "testnet", "regtest": "regtest"}[chain]
    with open(os.path.join(os.path.expanduser(datadir), sub, ".cookie")) as f:
        return f.read().strip().split(":", 1)


def create(args):
    rpcuser, rpcpassword = args.rpcuser, args.rpcpassword
    if not rpcuser:
        rpcuser, rpcpassword = read_cookie(args.datadir, args.chain)
    rpc = RPC(args.rpcurl or "http://127.0.0.1:%d/" % DEFAULT_RPC_PORTS[args.chain], rpcuser, rpcpassword)
    info = rpc.validateaddress(args.address)
    if not info.get("isvalid"):
        raise SystemExit("error: --address %s is not a valid address" % args.address)
    if not args.no_payouts and not info.get("ismine"):
        raise SystemExit("error: --address must belong to the node's wallet to pay miners (or use --no-payouts)")
    ledger = Ledger(args.db or "pool-%s.sqlite" % args.chain, args.fee)
    return Pool(rpc, bytes.fromhex(info["scriptPubKey"]), ledger, args)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="CryptoLari stratum mining pool")
    p.add_argument("--address", required=True, help="pool address that receives block rewards")
    p.add_argument("--chain", choices=["main", "test", "regtest"], default="main")
    p.add_argument("--rpcurl", help="node RPC URL (default: http://127.0.0.1:<chain rpc port>/)")
    p.add_argument("--rpcuser", help="RPC user (default: read the .cookie file from --datadir)")
    p.add_argument("--rpcpassword")
    p.add_argument("--datadir", default="~/.cryptolari")
    p.add_argument("--db", help="SQLite database (default: pool-<chain>.sqlite)")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=3333)
    p.add_argument("--stats-bind", default="0.0.0.0")
    p.add_argument("--stats-port", type=int, default=8081, help="HTTP stats port (default: 8081)")
    p.add_argument("--fee", type=float, default=1.0, help="pool fee in percent (default: 1)")
    p.add_argument("--difficulty", type=float, help="share difficulty (default: network difficulty)")
    p.add_argument("--no-payouts", action="store_true", help="solo mode: keep all rewards at --address")
    p.add_argument("--poll", type=float, default=1.0, help="seconds between new block checks")
    p.add_argument("--refresh", type=float, default=30.0, help="seconds between template refreshes with new transactions")
    p.add_argument("--payout-interval", type=float, default=300.0, help="seconds between payout runs")
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def main():
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pool = create(args)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(pool.start())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
