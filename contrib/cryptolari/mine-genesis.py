#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""
Mine a new CryptoLari genesis block.

Run this on launch day with that day's date and a recent news headline in the
message, so everyone can see the chain did not exist (and was not premined)
before then. Then update src/chainparams.cpp: pszTimestamp in
CreateCryptoLariGenesisBlock, and in CMainParams and CTestNetParams the genesis
line, both asserts, the height-0 checkpoint and the chainTxData timestamp.

Usage:
    contrib/cryptolari/mine-genesis.py --time 2026-10-01 --message "01/Oct/2026 <headline>"
"""
import argparse
import calendar
import hashlib
import struct
import time

# Must match consensus.powLimit (0x1e0fffff) in src/chainparams.cpp.
GENESIS_BITS = 0x1e0fffff
GENESIS_REWARD = 10 * 100000000
OP_RETURN = b'\x6a'


def dsha256(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def push_data(data):
    assert len(data) < 256
    return (bytes([len(data)]) if len(data) < 76 else b'\x4c' + bytes([len(data)])) + data


def coinbase_tx(message):
    """Serialize the genesis coinbase exactly as CreateGenesisBlock does."""
    script_sig = push_data(bytes.fromhex('ffff001d')) + push_data(b'\x04') + push_data(message.encode())
    tx = struct.pack('<i', 1)
    tx += b'\x01' + b'\x00' * 32 + struct.pack('<I', 0xffffffff)
    tx += bytes([len(script_sig)]) + script_sig + struct.pack('<I', 0xffffffff)
    tx += b'\x01' + struct.pack('<q', GENESIS_REWARD) + bytes([len(OP_RETURN)]) + OP_RETURN
    tx += struct.pack('<I', 0)
    return tx


def target_from_bits(bits):
    return (bits & 0xffffff) << (8 * ((bits >> 24) - 3))


def mine(message, ntime):
    merkle_root = dsha256(coinbase_tx(message))
    header = struct.pack('<i', 1) + b'\x00' * 32 + merkle_root + struct.pack('<II', ntime, GENESIS_BITS)
    target = target_from_bits(GENESIS_BITS)
    nonce = 0
    while True:
        block_hash = dsha256(header + struct.pack('<I', nonce))
        if int.from_bytes(block_hash, 'little') <= target:
            return nonce, block_hash[::-1].hex(), merkle_root[::-1].hex()
        nonce += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--time', required=True, help='launch date, YYYY-MM-DD (UTC midnight)')
    parser.add_argument('--message', required=True, help='genesis coinbase text, under 76 bytes')
    args = parser.parse_args()
    if len(args.message.encode()) >= 76:
        parser.error('message must be shorter than 76 bytes')

    ntime = calendar.timegm(time.strptime(args.time, '%Y-%m-%d'))
    for network, offset in (('main', 0), ('testnet', 60)):
        started = time.time()
        nonce, block_hash, merkle_root = mine(args.message, ntime + offset)
        print('%s (%.0fs):' % (network, time.time() - started))
        print('    genesis = CreateCryptoLariGenesisBlock(%d, %d, 0x%08x, 1, 10 * COIN);' % (ntime + offset, nonce, GENESIS_BITS))
        print('    hashGenesisBlock == uint256S("0x%s")' % block_hash)
        print('    hashMerkleRoot   == uint256S("0x%s")' % merkle_root)
    print('pszTimestamp = "%s"' % args.message)


if __name__ == '__main__':
    main()
