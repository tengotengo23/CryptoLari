#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test the CryptoLari block explorer (contrib/cryptolari/explorer)."""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal, rpc_url, wait_until

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "contrib", "cryptolari", "explorer"))
import explorer as lari_explorer  # noqa: E402

COIN = 100000000
DEV_FEE = 50 * COIN * 5 // 100


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ExplorerTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 1
        self.setup_clean_chain = True
        self.extra_args = [["-devfeeheight=1"]]

    def get(self, path, expect=200):
        try:
            with urllib.request.urlopen(self.base + path) as resp:
                assert_equal(resp.status, expect)
                return resp.read().decode()
        except urllib.error.HTTPError as e:
            assert_equal(e.code, expect)
            return e.read().decode()

    def api(self, path, expect=200):
        return json.loads(self.get("/api" + path, expect))

    def redirect(self, query):
        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(self.base + "/search?q=" + urllib.parse.quote(query))
        except urllib.error.HTTPError as e:
            assert_equal(e.code, 302)
            return e.headers["Location"]
        raise AssertionError("no redirect")

    def wait_indexed(self):
        node = self.nodes[0]
        wait_until(lambda: self.api("/stats")["indexed_height"] == node.getblockcount() and
                   self.api("/blocks")[0]["hash"] == node.getbestblockhash(), timeout=30)

    def run_test(self):
        node = self.nodes[0]
        miner = node.getnewaddress()
        node.generatetoaddress(110, miner)

        url = urllib.parse.urlparse(rpc_url(node.datadir, 0, None))
        args = lari_explorer.parse_args([
            "--chain=regtest", "--rpcurl=http://%s:%d/" % (url.hostname, url.port),
            "--rpcuser=" + urllib.parse.unquote(url.username), "--rpcpassword=" + urllib.parse.unquote(url.password),
            "--db=" + os.path.join(self.options.tmpdir, "explorer.sqlite"), "--port=0", "--poll=0.2"])
        lari_explorer.log = self.log.getChild("explorer")  # keep explorer logs out of stderr
        _, indexer, server = lari_explorer.create(args)
        indexer.start()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            self.check(node, miner)
        finally:
            indexer.stop()
            server.shutdown()
            server.server_close()

    def check(self, node, miner):
        self.log.info("Indexes the chain and reports dev fee statistics")
        self.wait_indexed()
        stats = self.api("/stats")
        assert_equal(stats["height"], 110)
        assert_equal(stats["supply"], 110 * 50 * COIN)
        assert_equal(stats["devfee"]["script"], "52")
        assert_equal(stats["devfee"]["paid"], 110 * DEV_FEE)
        assert_equal(stats["devfee"]["next_block"], DEV_FEE)
        assert_equal(stats["transactions"], 111)  # genesis + 110 coinbases

        self.log.info("Block pages")
        block = self.api("/block/5")
        assert_equal(block["hash"], node.getblockhash(5))
        assert_equal(block["devfee"], DEV_FEE)
        assert_equal(block["reward"], 50 * COIN)
        assert_equal(self.api("/block/" + block["hash"])["height"], 5)
        assert "ბლოკი #5" in self.get("/block/5")
        self.api("/block/99999", expect=404)

        self.log.info("Coinbase transaction marks the dev fee output")
        cb = self.api("/tx/" + block["tx"][0])
        assert cb["coinbase"]
        devouts = [o for o in cb["outputs"] if o["devfee"]]
        assert_equal(len(devouts), 1)
        assert_equal(devouts[0]["value"], DEV_FEE)
        assert "dev fee" in self.get("/tx/" + block["tx"][0])

        self.log.info("Spending transaction: inputs, fee, spent outputs and address balances")
        dest = node.getnewaddress()
        txid = node.sendtoaddress(dest, 10)
        mempool_tx = self.api("/tx/" + txid)
        assert_equal(mempool_tx["height"], None)
        node.generatetoaddress(1, miner)
        self.wait_indexed()
        tx = self.api("/tx/" + txid)
        assert_equal(tx["height"], 111)
        rawfee = node.gettransaction(txid)["fee"]
        assert_equal(tx["fee"], -int(Decimal(rawfee) * COIN))
        spent_input = tx["inputs"][0]
        prev = self.api("/tx/" + spent_input["txid"])
        assert_equal(prev["outputs"][spent_input["vout"]]["spent_by"], txid)
        addr = self.api("/address/" + dest)
        assert_equal(addr["balance"], 10 * COIN)
        assert_equal(addr["recent_outputs"][0]["txid"], txid)
        assert "10 LARI" in self.get("/address/" + dest)
        self.api("/address/notanaddress", expect=404)

        self.log.info("Search")
        assert_equal(self.redirect("5"), "/block/5")
        assert_equal(self.redirect(txid), "/tx/" + txid)
        assert_equal(self.redirect(node.getblockhash(7)), "/block/" + node.getblockhash(7))
        assert_equal(self.redirect(dest), "/address/" + dest)
        assert "CryptoLari" in self.get("/")

        self.log.info("Follows reorgs")
        old_tip = node.getbestblockhash()
        node.invalidateblock(node.getblockhash(110))
        new_blocks = node.generatetoaddress(3, node.getnewaddress())  # new address: blocks differ from the old ones
        self.wait_indexed()
        assert_equal(self.api("/stats")["height"], 112)
        assert_equal(self.api("/block/110")["hash"], new_blocks[0])
        assert_equal(self.api("/stats")["devfee"]["paid"], 112 * DEV_FEE)
        assert old_tip not in [b["hash"] for b in self.api("/blocks")]


if __name__ == '__main__':
    ExplorerTest().main()
