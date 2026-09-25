#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test the CryptoLari dev fee.

On regtest the dev fee is disabled by default and enabled with -devfeeheight.
The dev fee script on regtest is OP_2.

- blocks mined by the node pay the dev fee once it is active
- getblocktemplate reports the dev fee
- blocks that do not pay the dev fee are rejected
- blocks built by an external miner that pay the dev fee are accepted
"""
from decimal import Decimal

from test_framework.blocktools import create_block, create_coinbase
from test_framework.mininode import CTxOut, COIN
from test_framework.script import CScript, OP_2
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal, bytes_to_hex_str

DEV_FEE_HEIGHT = 5
DEV_FEE_SCRIPT = "52"  # OP_2
DEV_FEE = 50 * COIN * 5 // 100


class DevFeeTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 2
        self.setup_clean_chain = True
        self.extra_args = [["-devfeeheight=%d" % DEV_FEE_HEIGHT]] * self.num_nodes

    def dev_fee_outputs(self, blockhash):
        coinbase = self.nodes[0].getblock(blockhash, 2)["tx"][0]
        return [o for o in coinbase["vout"] if o["scriptPubKey"]["hex"] == DEV_FEE_SCRIPT]

    def run_test(self):
        node = self.nodes[0]

        self.log.info("No dev fee before the start height")
        node.generate(DEV_FEE_HEIGHT - 2)
        tmpl = node.getblocktemplate({"rules": ["segwit"]})
        assert "devfee" not in tmpl
        assert_equal(tmpl["coinbasevalue"], 50 * COIN)
        for blockhash in node.generate(1) + [node.getblockhash(h) for h in range(1, DEV_FEE_HEIGHT - 1)]:
            assert_equal(self.dev_fee_outputs(blockhash), [])

        self.log.info("getblocktemplate reports the dev fee")
        tmpl = node.getblocktemplate({"rules": ["segwit"]})
        assert_equal(tmpl["devfee"], {"script": DEV_FEE_SCRIPT, "amount": DEV_FEE})
        assert_equal(tmpl["coinbasevalue"], 50 * COIN - DEV_FEE)

        info = node.getmininginfo()["devfee"]
        assert_equal(info, {"script": DEV_FEE_SCRIPT, "percent": 5, "startheight": DEV_FEE_HEIGHT, "nextamount": DEV_FEE})

        self.log.info("Mined blocks pay the dev fee")
        for blockhash in node.generate(3):
            outputs = self.dev_fee_outputs(blockhash)
            assert_equal(len(outputs), 1)
            assert_equal(outputs[0]["value"], Decimal("2.5"))
        self.sync_all()

        self.log.info("A block that does not pay the dev fee is rejected")
        tip = node.getblock(node.getbestblockhash())
        height = tip["height"] + 1
        block = create_block(int(tip["hash"], 16), create_coinbase(height), tip["time"] + 1)
        block.solve()
        assert_equal(node.submitblock(bytes_to_hex_str(block.serialize())), "bad-cb-devfee")

        self.log.info("A block that pays too little dev fee is rejected")
        coinbase = create_coinbase(height)
        coinbase.vout[0].nValue -= DEV_FEE
        coinbase.vout.append(CTxOut(DEV_FEE - 1, CScript([OP_2])))
        coinbase.rehash()
        block = create_block(int(tip["hash"], 16), coinbase, tip["time"] + 1)
        block.solve()
        assert_equal(node.submitblock(bytes_to_hex_str(block.serialize())), "bad-cb-devfee")

        self.log.info("A block built by an external miner that pays the dev fee is accepted")
        coinbase = create_coinbase(height)
        coinbase.vout[0].nValue -= DEV_FEE
        coinbase.vout.append(CTxOut(DEV_FEE, CScript([OP_2])))
        coinbase.rehash()
        block = create_block(int(tip["hash"], 16), coinbase, tip["time"] + 1)
        block.solve()
        assert_equal(node.submitblock(bytes_to_hex_str(block.serialize())), None)
        assert_equal(node.getbestblockhash(), block.hash)
        self.sync_all()
        assert_equal(self.nodes[1].getbestblockhash(), block.hash)


if __name__ == '__main__':
    DevFeeTest().main()
