#!/usr/bin/env bash
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
#
# Mine CryptoLari with this computer's CPU through the local node.
# Each thread pays to its own address so the threads never repeat each
# other's work. Rewards can be spent after 100 confirmations. Ctrl+C stops.
#
# Usage: contrib/cryptolari/mine.sh [--test|--main] [threads]
set -euo pipefail

NETFLAG=-testnet
THREADS=1
for arg in "$@"; do
    case "$arg" in
        --test) NETFLAG=-testnet ;;
        --main) NETFLAG="" ;;
        ''|*[!0-9]*) echo "Usage: $0 [--test|--main] [threads]" >&2; exit 1 ;;
        *) THREADS=$arg ;;
    esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLI="$REPO/src/cryptolari-cli $NETFLAG"
if ! $CLI getblockcount >/dev/null 2>&1; then
    echo "The node is not running. Start it with contrib/cryptolari/quickstart.sh first." >&2
    exit 1
fi

trap 'kill $(jobs -p) 2>/dev/null; echo; echo "Mining stopped."; exit 0' INT TERM

mine() {
    local thread=$1 address hash
    address=$($CLI getnewaddress "mining")
    while true; do
        # Short calls (~20 s of hashing), so the node stops soon after Ctrl+C.
        hash=$($CLI generatetoaddress 1 "$address" 20000 | tr -d '[]" \n')
        if [ -n "$hash" ]; then
            printf '%s  thread %d found block %s (height %s)\n' "$(date +%H:%M:%S)" "$thread" \
                "${hash:0:16}…" "$($CLI getblockcount)"
        fi
    done
}

echo "Mining with $THREADS thread(s). Press Ctrl+C to stop."
for thread in $(seq 1 "$THREADS"); do
    mine "$thread" &
done
wait
