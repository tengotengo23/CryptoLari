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

Proof of work is yespower (as on the main and test networks), so this script
compiles src/crypto/yespower with the system C compiler and calls it directly.

Usage:
    contrib/cryptolari/mine-genesis.py --time 2026-10-01 --message "01/Oct/2026 <headline>"
"""
import argparse
import calendar
import ctypes
import hashlib
import os
import struct
import subprocess
import tempfile
import time

# Must match consensus.powLimit (0x1f0fffff) in src/chainparams.cpp.
GENESIS_BITS = 0x1f0fffff
GENESIS_REWARD = 10 * 100000000
OP_RETURN = b'\x6a'
# Must match GetBlockPoWHash in src/pow.cpp.
YESPOWER_1_0 = 10
YESPOWER_N = 2048
YESPOWER_R = 8
YESPOWER_PERS = b'CryptoLari'

YESPOWER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src', 'crypto', 'yespower')


class YespowerParams(ctypes.Structure):
    _fields_ = [('version', ctypes.c_int), ('N', ctypes.c_uint32), ('r', ctypes.c_uint32),
                ('pers', ctypes.c_char_p), ('perslen', ctypes.c_size_t)]


class Yespower:
    def __init__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        library = os.path.join(self._tmpdir.name, 'libyespower.so')
        subprocess.check_call([os.environ.get('CC', 'cc'), '-O2', '-shared', '-fPIC', '-Wno-cpp', '-o', library,
                               os.path.join(YESPOWER_DIR, 'yespower-opt.c'), os.path.join(YESPOWER_DIR, 'sha256.c')])
        self._lib = ctypes.CDLL(library)
        self._lib.yespower_tls.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                           ctypes.POINTER(YespowerParams), ctypes.c_void_p]
        self._lib.yespower_tls.restype = ctypes.c_int

    def hash(self, data, N=YESPOWER_N, r=YESPOWER_R, pers=YESPOWER_PERS):
        params = YespowerParams(YESPOWER_1_0, N, r, pers, len(pers) if pers else 0)
        out = ctypes.create_string_buffer(32)
        if self._lib.yespower_tls(data, len(data), ctypes.byref(params), out) != 0:
            raise MemoryError('yespower failed')
        return out.raw


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


def mine(yespower, message, ntime):
    merkle_root = dsha256(coinbase_tx(message))
    prefix = struct.pack('<i', 1) + b'\x00' * 32 + merkle_root + struct.pack('<II', ntime, GENESIS_BITS)
    target = target_from_bits(GENESIS_BITS)
    nonce = 0
    while True:
        header = prefix + struct.pack('<I', nonce)
        pow_hash = yespower.hash(header)
        if int.from_bytes(pow_hash, 'little') <= target:
            return nonce, dsha256(header)[::-1].hex(), merkle_root[::-1].hex(), pow_hash[::-1].hex()
        nonce += 1


def self_test(yespower):
    """Official yespower 1.0 test vector: yespower(10, 2048, 8, NULL) over bytes i*3."""
    src = bytes((i * 3) & 0xff for i in range(80))
    expected = '69e0e895b3df7aeeb837d71fe199e9d34f7ec46ecbca7a2c4308e51857ae9b46'
    if yespower.hash(src, 2048, 8, None).hex() != expected:
        raise SystemExit('yespower self-test failed; refusing to mine')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--time', required=True, help='launch date, YYYY-MM-DD (UTC midnight)')
    parser.add_argument('--message', required=True, help='genesis coinbase text, under 76 bytes')
    args = parser.parse_args()
    if len(args.message.encode()) >= 76:
        parser.error('message must be shorter than 76 bytes')

    yespower = Yespower()
    self_test(yespower)

    ntime = calendar.timegm(time.strptime(args.time, '%Y-%m-%d'))
    for network, offset in (('main', 0), ('testnet', 60)):
        started = time.time()
        nonce, block_hash, merkle_root, pow_hash = mine(yespower, args.message, ntime + offset)
        print('%s (%.0fs):' % (network, time.time() - started))
        print('    genesis = CreateCryptoLariGenesisBlock(%d, %d, 0x%08x, 1, 10 * COIN);' % (ntime + offset, nonce, GENESIS_BITS))
        print('    hashGenesisBlock == uint256S("0x%s")' % block_hash)
        print('    hashMerkleRoot   == uint256S("0x%s")' % merkle_root)
        print('    yespower hash       %s' % pow_hash)
    print('pszTimestamp = "%s"' % args.message)


if __name__ == '__main__':
    main()
