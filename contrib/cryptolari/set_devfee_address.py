#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Set the address that receives the CryptoLari dev fee.

Usage:
    contrib/cryptolari/set_devfee_address.py <address> [--network main|test|both]

<address> may be a mainnet (G..., T..., lari1...) or testnet (t..., u..., tlari1...)
address, or a raw hex scriptPubKey. The same key controls the dev fee on both
networks, so by default both mainnet and testnet are updated.

The script rewrites DEV_FEE_SCRIPT_MAIN / DEV_FEE_SCRIPT_TEST in
src/chainparams.cpp. Rebuild (make) afterwards. Never change the mainnet value
after the network has launched: that is a hard fork.
"""
import argparse
import hashlib
import os
import re
import sys

B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
BECH32 = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'

# version byte -> script type
P2PKH_VERSIONS = {38: 'main', 127: 'test'}
P2SH_VERSIONS = {65: 'main', 130: 'test'}
HRPS = {'lari': 'main', 'tlari': 'test'}


def b58decode_check(s):
    n = 0
    for c in s:
        if c not in B58:
            return None
        n = n * 58 + B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    raw = b'\0' * (len(s) - len(s.lstrip('1'))) + raw
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        return None
    return payload


def bech32_polymod(values):
    gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((top >> i) & 1) else 0
    return chk


def bech32_decode(s):
    if s.lower() != s and s.upper() != s:
        return None, None
    s = s.lower()
    pos = s.rfind('1')
    if pos < 1 or pos + 7 > len(s):
        return None, None
    hrp = s[:pos]
    if any(c not in BECH32 for c in s[pos + 1:]):
        return None, None
    data = [BECH32.index(c) for c in s[pos + 1:]]
    expanded = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]
    if bech32_polymod(expanded + data) != 1:
        return None, None
    return hrp, data[:-6]


def convertbits(data, frombits, tobits):
    acc, bits, ret = 0, 0, []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def address_to_script(addr):
    """Return (scriptPubKey hex, network) for an address or raw script hex."""
    if re.fullmatch(r'(?:[0-9a-fA-F]{2})+', addr) and len(addr) >= 44:
        return addr.lower(), None

    hrp, data = bech32_decode(addr)
    if hrp in HRPS and data:
        version = data[0]
        program = convertbits(data[1:], 5, 8)
        if version == 0 and program and len(program) in (20, 32):
            return '00' + '%02x' % len(program) + bytes(program).hex(), HRPS[hrp]
        raise ValueError('unsupported segwit address version or length')

    payload = b58decode_check(addr)
    if payload and len(payload) == 21:
        version, h160 = payload[0], payload[1:].hex()
        if version in P2PKH_VERSIONS:
            return '76a914' + h160 + '88ac', P2PKH_VERSIONS[version]
        if version in P2SH_VERSIONS:
            return 'a914' + h160 + '87', P2SH_VERSIONS[version]
    raise ValueError('%s is not a valid CryptoLari address' % addr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('address', help='CryptoLari address (mainnet or testnet) or hex scriptPubKey')
    parser.add_argument('--network', choices=['main', 'test', 'both'], default='both')
    parser.add_argument('--chainparams', default=os.path.join(os.path.dirname(__file__), '..', '..', 'src', 'chainparams.cpp'))
    args = parser.parse_args()

    try:
        script, network = address_to_script(args.address)
    except ValueError as e:
        sys.exit('error: %s' % e)

    with open(args.chainparams, encoding='utf8') as f:
        src = f.read()

    names = {'main': ['DEV_FEE_SCRIPT_MAIN'], 'test': ['DEV_FEE_SCRIPT_TEST'],
             'both': ['DEV_FEE_SCRIPT_MAIN', 'DEV_FEE_SCRIPT_TEST']}[args.network]
    for name in names:
        pattern = r'(static const char\* const %s = )[^;]+;' % name
        src, count = re.subn(pattern, r'\g<1>"%s";' % script, src)
        if count != 1:
            sys.exit('error: could not find %s in %s' % (name, args.chainparams))

    with open(args.chainparams, 'w', encoding='utf8') as f:
        f.write(src)

    print('Dev fee scriptPubKey: %s' % script)
    if network:
        print('(decoded from a %s address)' % network)
    print('Updated %s in %s' % (', '.join(names), os.path.normpath(args.chainparams)))
    print('Now rebuild with: make')


if __name__ == '__main__':
    main()
