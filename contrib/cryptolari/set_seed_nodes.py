#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Set the seed nodes that new CryptoLari nodes connect to first.

Usage:
    contrib/cryptolari/set_seed_nodes.py [--network main|test] <node> [<node> ...]

Each <node> is an IPv4/IPv6 address, optionally with a port ("1.2.3.4",
"1.2.3.4:9555", "[2001:db8::1]:9555"), or a hostname ("seed1.example.ge").
The list replaces SEED_NODES_MAIN (or SEED_NODES_TEST) in src/chainparams.cpp.
Rebuild (make) afterwards. Pass no nodes to clear the list.
"""
import argparse
import ipaddress
import os
import re
import sys


def check_node(node):
    host, port = node, None
    m = re.fullmatch(r"\[([0-9a-fA-F:]+)\](?::(\d+))?", node)
    if m:
        host, port = m.group(1), m.group(2)
        ipaddress.IPv6Address(host)
    elif node.count(":") == 1:
        host, port = node.split(":")
    elif ":" in node:
        ipaddress.IPv6Address(node)
    if port is not None and not 0 < int(port) < 65536:
        raise ValueError("bad port")
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError("%s is not a public IP address" % host)
    except ValueError as e:
        if "public" in str(e):
            raise
        if not re.fullmatch(r"(?=.{1,253}$)([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}", host):
            raise ValueError("not an IP address or hostname")
    return node


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("nodes", nargs="*")
    parser.add_argument("--network", choices=["main", "test"], default="main")
    parser.add_argument("--chainparams", default=os.path.join(os.path.dirname(__file__), "..", "..", "src", "chainparams.cpp"))
    args = parser.parse_args()

    for node in args.nodes:
        try:
            check_node(node)
        except ValueError as e:
            sys.exit("error: invalid node %r: %s" % (node, e))

    name = "SEED_NODES_MAIN" if args.network == "main" else "SEED_NODES_TEST"
    with open(args.chainparams, encoding="utf8") as f:
        src = f.read()
    body = "".join('    "%s",\n' % n for n in args.nodes)
    src, count = re.subn(r"(static const std::vector<std::string> %s = \{\n)(.*?)(\};)" % name,
                         lambda m: m.group(1) + body + m.group(3), src, flags=re.S)
    if count != 1:
        sys.exit("error: could not find %s in %s" % (name, args.chainparams))
    with open(args.chainparams, "w", encoding="utf8") as f:
        f.write(src)
    print("%s = %s" % (name, args.nodes))
    print("Now rebuild with: make")


if __name__ == "__main__":
    main()
