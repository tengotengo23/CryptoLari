#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""CryptoLari block explorer.

A small, dependency-free block explorer. It follows a cryptolarid node over
JSON-RPC, indexes blocks, transactions and address balances into SQLite, and
serves HTML pages and a JSON API.

    contrib/cryptolari/explorer/explorer.py --rpcuser=<user> --rpcpassword=<pass>

Then open http://localhost:8080/. Run with --help for all options.

Pages:  /  /block/<hash|height>  /tx/<txid>  /address/<address>  /search?q=...
API:    /api/stats  /api/block/<hash|height>  /api/tx/<txid>  /api/address/<address>
"""
import argparse
import base64
import html
import http.server
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from decimal import Decimal

COIN = 100000000
UNIT = "LARI"
DEFAULT_RPC_PORTS = {"main": 9554, "test": 19554, "regtest": 18443}

log = logging.getLogger("explorer")


class RPCError(Exception):
    def __init__(self, error):
        super().__init__(error.get("message", str(error)))
        self.code = error.get("code")


class RPC:
    """Minimal JSON-RPC client for cryptolarid."""

    def __init__(self, url, user, password):
        self.url = url
        self.auth = "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        self.lock = threading.Lock()
        self.id = 0

    def call(self, method, *params):
        with self.lock:
            self.id += 1
            req_id = self.id
        body = json.dumps({"version": "1.1", "id": req_id, "method": method, "params": list(params)}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                reply = json.loads(resp.read().decode(), parse_float=Decimal)
        except urllib.error.HTTPError as e:
            # cryptolarid returns 404/500 with a JSON body for RPC errors
            reply = json.loads(e.read().decode() or "{}", parse_float=Decimal)
            if not reply.get("error"):
                raise
        if reply.get("error"):
            raise RPCError(reply["error"])
        return reply["result"]

    def __getattr__(self, name):
        return lambda *params: self.call(name, *params)


def to_sat(value):
    return int(Decimal(value) * COIN)


def fmt(sat):
    """Format satoshis as LARI without trailing zeros."""
    sign = "-" if sat < 0 else ""
    whole, frac = divmod(abs(int(sat)), COIN)
    s = "%s%d.%08d" % (sign, whole, frac)
    return s.rstrip("0").rstrip(".")


def out_address(scriptpubkey):
    addrs = scriptpubkey.get("addresses")
    if addrs and len(addrs) == 1:
        return addrs[0]
    return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    height INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    time INTEGER NOT NULL,
    ntx INTEGER NOT NULL,
    size INTEGER NOT NULL,
    reward INTEGER NOT NULL,   -- total value of the coinbase outputs
    devfee INTEGER NOT NULL    -- part of the coinbase paid to the dev fee script
);
CREATE TABLE IF NOT EXISTS txs (
    txid TEXT PRIMARY KEY,
    height INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outputs (
    txid TEXT NOT NULL,
    n INTEGER NOT NULL,
    height INTEGER NOT NULL,
    address TEXT,
    script TEXT NOT NULL,
    value INTEGER NOT NULL,
    spent_txid TEXT,
    spent_height INTEGER,
    PRIMARY KEY (txid, n)
);
CREATE INDEX IF NOT EXISTS outputs_address ON outputs(address);
CREATE INDEX IF NOT EXISTS outputs_height ON outputs(height);
CREATE INDEX IF NOT EXISTS outputs_spent_height ON outputs(spent_height);
CREATE INDEX IF NOT EXISTS txs_height ON txs(height);
"""


class Index:
    """SQLite index of the chain. Only the indexer thread writes to it."""

    def __init__(self, path):
        self.path = path
        conn = self.connect()
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn


class Indexer(threading.Thread):
    """Follows the node and keeps the index in sync, handling reorgs."""

    def __init__(self, rpc, index, devfee_script, poll=2.0):
        super().__init__(daemon=True)
        self.rpc = rpc
        self.index = index
        self.devfee_script = devfee_script
        self.poll = poll
        self.synced = threading.Event()
        self.stop_event = threading.Event()

    def run(self):
        conn = self.index.connect()
        while not self.stop_event.is_set():
            try:
                self.sync(conn)
                self.synced.set()
            except Exception:
                log.exception("indexer error")
            self.stop_event.wait(self.poll)
        conn.close()

    def stop(self):
        self.stop_event.set()

    def tip(self, conn):
        row = conn.execute("SELECT height, hash FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
        return (row["height"], row["hash"]) if row else (-1, None)

    def sync(self, conn):
        node_height = self.rpc.getblockcount()
        # Roll back blocks that are no longer in the node's active chain.
        height, blockhash = self.tip(conn)
        while height >= 0 and (height > node_height or self.rpc.getblockhash(height) != blockhash):
            log.info("reorg: disconnecting block %d %s", height, blockhash)
            self.disconnect(conn, height)
            height, blockhash = self.tip(conn)
        for h in range(height + 1, node_height + 1):
            if self.stop_event.is_set():
                return
            self.connect_block(conn, self.rpc.getblock(self.rpc.getblockhash(h), 2))
            if h % 1000 == 0:
                log.info("indexed block %d / %d", h, node_height)

    def disconnect(self, conn, height):
        with conn:
            conn.execute("UPDATE outputs SET spent_txid = NULL, spent_height = NULL WHERE spent_height = ?", (height,))
            conn.execute("DELETE FROM outputs WHERE height = ?", (height,))
            conn.execute("DELETE FROM txs WHERE height = ?", (height,))
            conn.execute("DELETE FROM blocks WHERE height = ?", (height,))

    def connect_block(self, conn, block):
        height = block["height"]
        reward = devfee = 0
        with conn:
            for i, tx in enumerate(block["tx"]):
                conn.execute("INSERT OR REPLACE INTO txs (txid, height) VALUES (?, ?)", (tx["txid"], height))
                for vin in tx["vin"]:
                    if "coinbase" not in vin:
                        conn.execute("UPDATE outputs SET spent_txid = ?, spent_height = ? WHERE txid = ? AND n = ?",
                                     (tx["txid"], height, vin["txid"], vin["vout"]))
                for vout in tx["vout"]:
                    value = to_sat(vout["value"])
                    script = vout["scriptPubKey"]["hex"]
                    conn.execute("INSERT OR REPLACE INTO outputs (txid, n, height, address, script, value) VALUES (?, ?, ?, ?, ?, ?)",
                                 (tx["txid"], vout["n"], height, out_address(vout["scriptPubKey"]), script, value))
                    if i == 0:
                        reward += value
                        if script == self.devfee_script:
                            devfee += value
            conn.execute("INSERT INTO blocks (height, hash, time, ntx, size, reward, devfee) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (height, block["hash"], block["time"], len(block["tx"]), block["size"], reward, devfee))


PAGE = """<!doctype html>
<html lang="ka"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · CryptoLari Explorer</title>
<style>
:root {{ --bg:#f7f7f5; --fg:#1d1d1f; --muted:#6b6b70; --card:#fff; --line:#e3e3e0; --accent:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#121214; --fg:#ececef; --muted:#9a9aa2; --card:#1c1c20; --line:#2c2c32; --accent:#ff6b5e; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
header {{ background:var(--card); border-bottom:1px solid var(--line); }}
.wrap {{ max-width:1100px; margin:0 auto; padding:16px; }}
header .wrap {{ display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; }}
header a.logo {{ font-weight:700; font-size:20px; color:var(--fg); text-decoration:none; }}
header a.logo span {{ color:var(--accent); }}
form {{ display:flex; gap:8px; flex:1; max-width:560px; }}
input {{ flex:1; min-width:0; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--fg); }}
button {{ padding:8px 14px; border:0; border-radius:8px; background:var(--accent); color:#fff; cursor:pointer; }}
h1 {{ font-size:22px; margin:8px 0 16px; word-break:break-all; }}
h2 {{ font-size:17px; margin:24px 0 8px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; }}
.card b {{ display:block; font-size:20px; }}
.card small {{ color:var(--muted); }}
.table {{ background:var(--card); border:1px solid var(--line); border-radius:12px; overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; }}
th, td {{ text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th {{ color:var(--muted); font-weight:500; font-size:13px; }}
tr:last-child td {{ border-bottom:0; }}
td.wrap-anywhere {{ white-space:normal; word-break:break-all; }}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; }}
.tag {{ display:inline-block; padding:1px 8px; border-radius:99px; background:var(--bg); border:1px solid var(--line); font-size:12px; color:var(--muted); }}
.dev {{ color:var(--accent); }}
.muted {{ color:var(--muted); }}
footer {{ color:var(--muted); font-size:13px; text-align:center; padding:24px 16px; }}
</style></head><body>
<header><div class="wrap">
<a class="logo" href="/">Crypto<span>Lari</span> Explorer</a>
<form action="/search"><input name="q" placeholder="ბლოკი, ტრანზაქცია ან მისამართი / block, tx or address" aria-label="Search"><button>ძებნა</button></form>
</div></header>
<main class="wrap">{body}</main>
<footer>CryptoLari · {chain} · {devnote}</footer>
</body></html>"""


def link_block(h, text=None):
    return '<a href="/block/%s">%s</a>' % (html.escape(str(h)), html.escape(str(text if text is not None else h)))


def link_tx(txid, short=False):
    text = txid[:16] + "…" if short else txid
    return '<a class="mono" href="/tx/%s">%s</a>' % (html.escape(txid), html.escape(text))


def link_addr(addr):
    if not addr:
        return '<span class="muted">—</span>'
    return '<a class="mono" href="/address/%s">%s</a>' % (html.escape(addr), html.escape(addr))


def ago(ts):
    d = max(0, int(time.time()) - int(ts))
    if d < 60:
        return "%d წმ" % d
    if d < 3600:
        return "%d წთ" % (d // 60)
    if d < 86400:
        return "%d სთ" % (d // 3600)
    return "%d დღე" % (d // 86400)


class NotFound(Exception):
    pass


class Explorer:
    """Queries shared by the HTML pages and the JSON API."""

    def __init__(self, rpc, index, chain_info, devfee):
        self.rpc = rpc
        self.index = index
        self.chain = chain_info["chain"]
        self.devfee = devfee

    def stats(self):
        conn = self.index.connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(reward), 0) AS supply, COALESCE(SUM(devfee), 0) AS devfee FROM blocks WHERE height > 0").fetchone()
            ntx = conn.execute("SELECT COUNT(*) FROM txs").fetchone()[0]
            indexed = conn.execute("SELECT MAX(height) FROM blocks").fetchone()[0]
        finally:
            conn.close()
        mining = self.rpc.getmininginfo()
        return {
            "chain": self.chain,
            "height": mining["blocks"],
            "indexed_height": indexed if indexed is not None else -1,
            "difficulty": float(mining["difficulty"]),
            "networkhashps": float(mining["networkhashps"]),
            "mempool_txs": mining["pooledtx"],
            "supply": row["supply"],
            "transactions": ntx,
            "devfee": {
                "script": self.devfee["script"],
                "percent": self.devfee["percent"],
                "paid": row["devfee"],
                "next_block": mining["devfee"]["nextamount"],
            },
        }

    def recent_blocks(self, count=20):
        conn = self.index.connect()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM blocks ORDER BY height DESC LIMIT ?", (count,))]
        finally:
            conn.close()

    def block(self, ident):
        try:
            if ident.isdigit():
                blockhash = self.rpc.getblockhash(int(ident))
            else:
                blockhash = ident
            block = self.rpc.getblock(blockhash, 2)
        except RPCError:
            raise NotFound(ident)
        coinbase = block["tx"][0]
        return {
            "hash": block["hash"],
            "height": block["height"],
            "confirmations": block["confirmations"],
            "time": block["time"],
            "size": block["size"],
            "weight": block["weight"],
            "version": block["version"],
            "merkleroot": block["merkleroot"],
            "bits": block["bits"],
            "nonce": block["nonce"],
            "difficulty": float(block["difficulty"]),
            "previousblockhash": block.get("previousblockhash"),
            "nextblockhash": block.get("nextblockhash"),
            "reward": sum(to_sat(o["value"]) for o in coinbase["vout"]),
            "devfee": sum(to_sat(o["value"]) for o in coinbase["vout"] if o["scriptPubKey"]["hex"] == self.devfee["script"]),
            "tx": [t["txid"] for t in block["tx"]],
        }

    def tx(self, txid):
        conn = self.index.connect()
        try:
            row = conn.execute("SELECT height FROM txs WHERE txid = ?", (txid,)).fetchone()
            try:
                if row is not None:
                    block = self.rpc.getblock(self.rpc.getblockhash(row["height"]), 2)
                    raw = next(t for t in block["tx"] if t["txid"] == txid)
                    raw.update(blockhash=block["hash"], confirmations=block["confirmations"], time=block["time"])
                else:
                    raw = self.rpc.getrawtransaction(txid, 1)  # mempool, or -txindex
            except (RPCError, StopIteration):
                raise NotFound(txid)
            inputs = []
            for vin in raw["vin"]:
                if "coinbase" in vin:
                    inputs.append({"coinbase": True})
                    continue
                prev = conn.execute("SELECT address, value FROM outputs WHERE txid = ? AND n = ?", (vin["txid"], vin["vout"])).fetchone()
                inputs.append({"txid": vin["txid"], "vout": vin["vout"],
                               "address": prev["address"] if prev else None,
                               "value": prev["value"] if prev else None})
            outputs = []
            for vout in raw["vout"]:
                spent = conn.execute("SELECT spent_txid FROM outputs WHERE txid = ? AND n = ?", (txid, vout["n"])).fetchone()
                outputs.append({"n": vout["n"], "value": to_sat(vout["value"]),
                                "address": out_address(vout["scriptPubKey"]),
                                "script": vout["scriptPubKey"]["hex"],
                                "type": vout["scriptPubKey"]["type"],
                                "devfee": "coinbase" in raw["vin"][0] and vout["scriptPubKey"]["hex"] == self.devfee["script"],
                                "spent_by": spent["spent_txid"] if spent else None})
        finally:
            conn.close()
        known_inputs = [i["value"] for i in inputs if i.get("value") is not None]
        fee = None
        if inputs and "coinbase" not in inputs[0] and len(known_inputs) == len(inputs):
            fee = sum(known_inputs) - sum(o["value"] for o in outputs)
        return {
            "txid": raw["txid"],
            "hash": raw["hash"],
            "size": raw["size"],
            "vsize": raw["vsize"],
            "blockhash": raw.get("blockhash"),
            "height": row["height"] if row else None,
            "confirmations": raw.get("confirmations", 0),
            "time": raw.get("time"),
            "coinbase": bool(inputs) and "coinbase" in inputs[0],
            "fee": fee,
            "inputs": inputs,
            "outputs": outputs,
        }

    def address(self, addr, limit=100):
        valid = self.rpc.validateaddress(addr)
        if not valid.get("isvalid"):
            raise NotFound(addr)
        conn = self.index.connect()
        try:
            totals = conn.execute(
                "SELECT COALESCE(SUM(value), 0) AS received, "
                "COALESCE(SUM(CASE WHEN spent_txid IS NULL THEN value ELSE 0 END), 0) AS balance, "
                "COUNT(*) AS outputs FROM outputs WHERE address = ?", (addr,)).fetchone()
            rows = conn.execute(
                "SELECT txid, n, height, value, spent_txid FROM outputs WHERE address = ? "
                "ORDER BY height DESC, txid, n LIMIT ?", (addr, limit)).fetchall()
        finally:
            conn.close()
        return {
            "address": addr,
            "script": valid["scriptPubKey"],
            "is_devfee": valid["scriptPubKey"] == self.devfee["script"],
            "balance": totals["balance"],
            "received": totals["received"],
            "outputs": totals["outputs"],
            "recent_outputs": [dict(r) for r in rows],
        }

    def search(self, q):
        q = q.strip()
        if q.isdigit():
            return "/block/" + q
        if len(q) == 64 and all(c in "0123456789abcdefABCDEF" for c in q):
            q = q.lower()
            conn = self.index.connect()
            try:
                if conn.execute("SELECT 1 FROM txs WHERE txid = ?", (q,)).fetchone():
                    return "/tx/" + q
                if conn.execute("SELECT 1 FROM blocks WHERE hash = ?", (q,)).fetchone():
                    return "/block/" + q
            finally:
                conn.close()
            try:
                self.rpc.getblockheader(q)
                return "/block/" + q
            except RPCError:
                return "/tx/" + q
        return "/address/" + urllib.parse.quote(q)

    # ---- HTML ----

    def page(self, title, body):
        note = 'dev fee: %d%% → %s' % (self.devfee["percent"], html.escape(self.devfee["script"][:20]) + "…")
        return PAGE.format(title=html.escape(title), body=body, chain=html.escape(self.chain), devnote=note)

    def html_home(self):
        s = self.stats()
        cards = [
            ("ბლოკები / Blocks", "{:,}".format(s["height"])),
            ("მარაგი / Supply", fmt(s["supply"]) + " " + UNIT),
            ("სირთულე / Difficulty", "%.6g" % s["difficulty"]),
            ("ჰეშრეიტი / Hashrate", "%.4g H/s" % s["networkhashps"]),
            ("ტრანზაქციები / Txs", "{:,}".format(s["transactions"])),
            ("Dev fee გადახდილი", fmt(s["devfee"]["paid"]) + " " + UNIT),
        ]
        body = "<h1>CryptoLari</h1>"
        if s["indexed_height"] < s["height"]:
            body += '<p class="tag">ინდექსირება / indexing: %d / %d</p>' % (s["indexed_height"], s["height"])
        body += '<div class="cards">' + "".join('<div class="card"><small>%s</small><b>%s</b></div>' % (k, html.escape(v)) for k, v in cards) + "</div>"
        body += "<h2>ბოლო ბლოკები / Latest blocks</h2><div class=\"table\"><table><tr><th>სიმაღლე</th><th>ჰეში / Hash</th><th>დრო</th><th>Tx</th><th>ზომა</th><th>ჯილდო</th><th>Dev fee</th></tr>"
        for b in self.recent_blocks():
            body += "<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d B</td><td>%s</td><td class=\"dev\">%s</td></tr>" % (
                link_block(b["height"]), link_block(b["hash"], b["hash"][:20] + "…"), ago(b["time"]),
                b["ntx"], b["size"], fmt(b["reward"]), fmt(b["devfee"]))
        body += "</table></div>"
        return self.page("მთავარი", body)

    def html_block(self, ident):
        b = self.block(ident)
        rows = [
            ("სიმაღლე / Height", str(b["height"])),
            ("დადასტურებები / Confirmations", str(b["confirmations"])),
            ("დრო / Time", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(b["time"]))),
            ("ჯილდო / Reward", fmt(b["reward"]) + " " + UNIT),
            ("Dev fee", fmt(b["devfee"]) + " " + UNIT),
            ("ზომა / Size", "%d B (weight %d)" % (b["size"], b["weight"])),
            ("სირთულე / Difficulty", "%.6g" % b["difficulty"]),
            ("Bits / Nonce / Version", "%s / %d / 0x%08x" % (b["bits"], b["nonce"], b["version"])),
            ("Merkle root", b["merkleroot"]),
        ]
        body = "<h1>ბლოკი #%d</h1><p class=\"mono muted\">%s</p>" % (b["height"], html.escape(b["hash"]))
        nav = []
        if b["previousblockhash"]:
            nav.append(link_block(b["previousblockhash"], "← #%d" % (b["height"] - 1)))
        if b["nextblockhash"]:
            nav.append(link_block(b["nextblockhash"], "#%d →" % (b["height"] + 1)))
        body += "<p>" + " · ".join(nav) + "</p>"
        body += '<div class="table"><table>' + "".join("<tr><th>%s</th><td class=\"wrap-anywhere\">%s</td></tr>" % (k, html.escape(v)) for k, v in rows) + "</table></div>"
        body += "<h2>ტრანზაქციები / Transactions (%d)</h2><div class=\"table\"><table>" % len(b["tx"])
        body += "".join("<tr><td>%d</td><td>%s</td></tr>" % (i, link_tx(t)) for i, t in enumerate(b["tx"]))
        body += "</table></div>"
        return self.page("ბლოკი %d" % b["height"], body)

    def html_tx(self, txid):
        t = self.tx(txid)
        status = "mempool" if t["height"] is None and not t["blockhash"] else "ბლოკი %s, %d დადასტურება" % (
            link_block(t["height"]) if t["height"] is not None else link_block(t["blockhash"], t["blockhash"][:16] + "…"), t["confirmations"])
        body = "<h1>ტრანზაქცია</h1><p class=\"mono muted\">%s</p><p>%s%s</p>" % (
            html.escape(t["txid"]), status, ' · <span class="tag">coinbase</span>' if t["coinbase"] else "")
        if t["fee"] is not None:
            body += "<p>საკომისიო / Fee: %s %s · %d vB</p>" % (fmt(t["fee"]), UNIT, t["vsize"])
        body += "<h2>შემავალი / Inputs</h2><div class=\"table\"><table><tr><th>წყარო</th><th>მისამართი</th><th>თანხა</th></tr>"
        for i in t["inputs"]:
            if i.get("coinbase"):
                body += '<tr><td colspan="3">ახალი მონეტები (coinbase)</td></tr>'
            else:
                body += "<tr><td>%s:%d</td><td>%s</td><td>%s</td></tr>" % (
                    link_tx(i["txid"], short=True), i["vout"], link_addr(i["address"]),
                    fmt(i["value"]) if i["value"] is not None else "?")
        body += "</table></div><h2>გამავალი / Outputs</h2><div class=\"table\"><table><tr><th>#</th><th>მისამართი</th><th>თანხა</th><th>სტატუსი</th></tr>"
        for o in t["outputs"]:
            addr = link_addr(o["address"]) if o["address"] else '<span class="muted">%s</span>' % html.escape(o["type"])
            if o["devfee"]:
                addr += ' <span class="tag dev">dev fee</span>'
            spent = ("დახარჯული → " + link_tx(o["spent_by"], short=True)) if o["spent_by"] else '<span class="muted">დაუხარჯავი</span>'
            body += "<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (o["n"], addr, fmt(o["value"]), spent)
        body += "</table></div>"
        return self.page("ტრანზაქცია", body)

    def html_address(self, addr):
        a = self.address(addr)
        body = "<h1>მისამართი</h1><p class=\"mono\">%s%s</p>" % (html.escape(a["address"]), ' <span class="tag dev">dev fee</span>' if a["is_devfee"] else "")
        body += '<div class="cards"><div class="card"><small>ბალანსი / Balance</small><b>%s %s</b></div><div class="card"><small>სულ მიღებული / Received</small><b>%s %s</b></div><div class="card"><small>გამოსავლები / Outputs</small><b>%d</b></div></div>' % (
            fmt(a["balance"]), UNIT, fmt(a["received"]), UNIT, a["outputs"])
        body += "<h2>ბოლო მიღებები / Recent outputs</h2><div class=\"table\"><table><tr><th>ბლოკი</th><th>ტრანზაქცია</th><th>თანხა</th><th>სტატუსი</th></tr>"
        for o in a["recent_outputs"]:
            spent = ("დახარჯული → " + link_tx(o["spent_txid"], short=True)) if o["spent_txid"] else '<span class="muted">დაუხარჯავი</span>'
            body += "<tr><td>%s</td><td>%s:%d</td><td>%s</td><td>%s</td></tr>" % (link_block(o["height"]), link_tx(o["txid"], short=True), o["n"], fmt(o["value"]), spent)
        body += "</table></div>"
        return self.page("მისამართი", body)

    def html_not_found(self, what):
        return self.page("ვერ მოიძებნა", "<h1>ვერ მოიძებნა / Not found</h1><p class=\"mono\">%s</p>" % html.escape(what))


def make_handler(explorer):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "CryptoLariExplorer/1.0"

        def log_message(self, fmt_, *args):
            log.debug("%s - %s", self.address_string(), fmt_ % args)

        def send(self, code, body, content_type="text/html; charset=utf-8", headers=None):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            parts = [urllib.parse.unquote(p) for p in url.path.strip("/").split("/") if p]
            api = bool(parts) and parts[0] == "api"
            if api:
                parts = parts[1:]
            try:
                if api:
                    self.send(200, json.dumps(self.api(parts), indent=1), "application/json")
                    return
                if not parts:
                    self.send(200, explorer.html_home())
                elif parts[0] == "block" and len(parts) == 2:
                    self.send(200, explorer.html_block(parts[1]))
                elif parts[0] == "tx" and len(parts) == 2:
                    self.send(200, explorer.html_tx(parts[1]))
                elif parts[0] == "address" and len(parts) == 2:
                    self.send(200, explorer.html_address(parts[1]))
                elif parts[0] == "search":
                    q = urllib.parse.parse_qs(url.query).get("q", [""])[0]
                    self.send(302, "", headers={"Location": explorer.search(q) if q.strip() else "/"})
                else:
                    raise NotFound(url.path)
            except NotFound as e:
                if api:
                    self.send(404, json.dumps({"error": "not found", "query": str(e)}), "application/json")
                else:
                    self.send(404, explorer.html_not_found(str(e)))
            except Exception as e:
                log.exception("error serving %s", self.path)
                if api:
                    self.send(500, json.dumps({"error": str(e)}), "application/json")
                else:
                    self.send(500, explorer.page("შეცდომა", "<h1>შეცდომა / Error</h1><p>%s</p>" % html.escape(str(e))))

        def api(self, parts):
            if parts == ["stats"]:
                return explorer.stats()
            if len(parts) == 2 and parts[0] == "block":
                return explorer.block(parts[1])
            if len(parts) == 2 and parts[0] == "tx":
                return explorer.tx(parts[1])
            if len(parts) == 2 and parts[0] == "address":
                return explorer.address(parts[1])
            if parts == ["blocks"]:
                return explorer.recent_blocks()
            raise NotFound("/api/" + "/".join(parts))

    return Handler


def read_cookie(datadir, chain):
    sub = {"main": "", "test": "testnet", "regtest": "regtest"}[chain]
    path = os.path.join(os.path.expanduser(datadir), sub, ".cookie")
    with open(path) as f:
        return f.read().strip().split(":", 1)


def create(args):
    """Build the explorer objects from parsed arguments. Returns (explorer, indexer, server)."""
    rpcuser, rpcpassword = args.rpcuser, args.rpcpassword
    if not rpcuser:
        rpcuser, rpcpassword = read_cookie(args.datadir, args.chain)
    url = args.rpcurl or "http://127.0.0.1:%d/" % DEFAULT_RPC_PORTS[args.chain]
    rpc = RPC(url, rpcuser, rpcpassword)
    chain_info = rpc.getblockchaininfo()
    devfee = rpc.getmininginfo()["devfee"]
    index = Index(args.db or "explorer-%s.sqlite" % chain_info["chain"])
    indexer = Indexer(rpc, index, devfee["script"], poll=args.poll)
    explorer = Explorer(rpc, index, chain_info, devfee)
    server = http.server.ThreadingHTTPServer((args.bind, args.port), make_handler(explorer))
    return explorer, indexer, server


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="CryptoLari block explorer")
    p.add_argument("--chain", choices=["main", "test", "regtest"], default="main")
    p.add_argument("--rpcurl", help="node RPC URL (default: http://127.0.0.1:<chain rpc port>/)")
    p.add_argument("--rpcuser", help="RPC user (default: read the .cookie file from --datadir)")
    p.add_argument("--rpcpassword")
    p.add_argument("--datadir", default="~/.cryptolari", help="node data dir, for cookie authentication")
    p.add_argument("--db", help="SQLite index file (default: explorer-<chain>.sqlite)")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--poll", type=float, default=2.0, help="seconds between checks for new blocks")
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def main():
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    explorer, indexer, server = create(args)
    indexer.start()
    log.info("CryptoLari explorer (%s) listening on http://%s:%d/", explorer.chain, args.bind, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        indexer.stop()
        server.server_close()


if __name__ == "__main__":
    main()
