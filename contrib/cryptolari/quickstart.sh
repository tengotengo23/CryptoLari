#!/usr/bin/env bash
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
#
# One-command CryptoLari setup on Ubuntu/Debian: installs the build
# dependencies, builds the node, writes a config, starts the node and prints
# the next steps. Safe to run again: finished steps are skipped.
#
# Usage: contrib/cryptolari/quickstart.sh [--test|--main] [--skip-deps] [--addnode <ip>]
#   --test       CryptoLari testnet, coins have no value (default)
#   --main       CryptoLari mainnet
#   --skip-deps  do not install packages with apt
#   --addnode    connect to another CryptoLari node (for example a workshop host)
set -euo pipefail

NETWORK=test
INSTALL_DEPS=1
ADDNODE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --test) NETWORK=test ;;
        --main) NETWORK=main ;;
        --skip-deps) INSTALL_DEPS=0 ;;
        --addnode) ADDNODE="${2:?--addnode needs an IP address}"; shift ;;
        -h|--help) sed -n '6,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATADIR="$HOME/.cryptolari"
if [ "$NETWORK" = test ]; then NETFLAG=-testnet; PORT=19955; else NETFLAG=""; PORT=9955; fi
CLI="$REPO/src/cryptolari-cli $NETFLAG"

step() { printf '\n\033[1;31m==>\033[0m \033[1m%s\033[0m\n' "$*"; }

if [ "$INSTALL_DEPS" = 1 ]; then
    step "1/4 Installing build dependencies (asks for your password)"
    sudo apt-get update -qq
    sudo apt-get install -y -qq build-essential libtool autotools-dev automake pkg-config bsdmainutils \
        python3 curl libssl-dev libevent-dev libdb5.3++-dev \
        libboost-system-dev libboost-filesystem-dev libboost-chrono-dev \
        libboost-program-options-dev libboost-test-dev libboost-thread-dev
fi

if [ ! -x "$REPO/src/cryptolarid" ]; then
    step "2/4 Building CryptoLari (takes 10-30 minutes the first time)"
    cd "$REPO"
    ./autogen.sh
    ./configure --with-incompatible-bdb --without-gui --disable-tests --disable-bench
    make -j"$(nproc)"
else
    step "2/4 Already built"
fi

step "3/4 Writing $DATADIR/cryptolari.conf"
mkdir -p "$DATADIR"
if [ ! -f "$DATADIR/cryptolari.conf" ]; then
    cat > "$DATADIR/cryptolari.conf" <<EOF
# Keep an index of all transactions so the explorer can show any of them.
txindex=1
# mine.sh uses one RPC thread per mining thread; leave some for everything else.
rpcthreads=16
EOF
    echo "created"
else
    echo "kept the existing file"
fi

step "4/4 Starting the node ($NETWORK)"
if $CLI getblockcount >/dev/null 2>&1; then
    echo "already running"
else
    "$REPO/src/cryptolarid" $NETFLAG -daemon ${ADDNODE:+-addnode=$ADDNODE:$PORT}
    for _ in $(seq 1 60); do
        $CLI getblockcount >/dev/null 2>&1 && break
        sleep 1
    done
fi
if [ -n "$ADDNODE" ]; then
    $CLI addnode "$ADDNODE:$PORT" add 2>/dev/null || true
fi

ADDRESS=$($CLI getaccountaddress "")
BLOCKS=$($CLI getblockcount)
PEERS=$($CLI getconnectioncount)

cat <<EOF

CryptoLari is running.
  Network:       $NETWORK
  Blocks:        $BLOCKS
  Connections:   $PEERS
  Your address:  $ADDRESS

Next steps:
  Mine with your CPU:     contrib/cryptolari/mine.sh --$NETWORK
  Open the explorer:      contrib/cryptolari/explorer/explorer.py --network $NETWORK
                          then visit http://127.0.0.1:8080/
  Balance:                src/cryptolari-cli $NETFLAG getbalance
  Send coins:             src/cryptolari-cli $NETFLAG sendtoaddress <address> <amount>
  Stop the node:          src/cryptolari-cli $NETFLAG stop
EOF
