#!/usr/bin/env python3
# Copyright (c) 2026 The CryptoLari developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""
CryptoLari block explorer and faucet.

A small web app (Python 3 standard library only) that talks to a local
cryptolarid over RPC and shows the chain: network stats, recent blocks,
blocks, transactions and search. Optionally it runs a faucet that sends a small
fixed amount from the node's wallet to newcomers, rate limited per address and
per IP.

Run the node with -txindex=1 so any transaction can be looked up; without it
only transactions reached from a block page (or still in the mempool) work.

Usage:
    contrib/cryptolari/explorer/explorer.py --network test
    contrib/cryptolari/explorer/explorer.py --network main --faucet-amount 0.5 --port 8080

It listens on 127.0.0.1 by default. To publish it, put it behind a reverse proxy
(nginx, Caddy) with HTTPS and pass --trust-proxy so the faucet sees real client IPs.
"""
import argparse
import base64
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COIN = 100000000
NETWORKS = {
    # name: (RPC port, datadir subdirectory, label, initial subsidy, halving interval)
    'main': (9954, '', 'Mainnet', 10 * COIN, 1050000),
    'test': (19954, 'testnet', 'Testnet', 10 * COIN, 1050000),
    'regtest': (18443, 'regtest', 'Regtest', 50 * COIN, 150),
}
HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')


class RPCError(Exception):
    pass


class RPC:
    """Minimal JSON-RPC client using cookie or user/password authentication."""

    def __init__(self, port, datadir, network_subdir, user=None, password=None):
        self.url = 'http://127.0.0.1:%d/' % port
        self.cookie_path = os.path.join(datadir, network_subdir, '.cookie')
        self.user, self.password = user, password

    def _auth(self):
        if self.user is not None:
            credentials = '%s:%s' % (self.user, self.password)
        else:
            # The node rewrites the cookie on every start, so read it each time.
            with open(self.cookie_path, encoding='utf8') as f:
                credentials = f.read().strip()
        return 'Basic ' + base64.b64encode(credentials.encode()).decode()

    def call(self, method, *params):
        body = json.dumps({'jsonrpc': '1.0', 'id': 'explorer', 'method': method, 'params': list(params)}).encode()
        request = urllib.request.Request(self.url, data=body, headers={
            'Authorization': self._auth(), 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                reply = json.loads(response.read().decode(), parse_float=Decimal)
        except urllib.error.HTTPError as e:
            # The node answers RPC errors with HTTP 404/500 and a JSON body.
            try:
                reply = json.loads(e.read().decode(), parse_float=Decimal)
            except ValueError:
                raise RPCError('HTTP %d from node' % e.code)
        except (urllib.error.URLError, OSError) as e:
            raise RPCError('cannot reach the node: %s' % e)
        if reply.get('error'):
            raise RPCError(reply['error'].get('message', 'unknown error'))
        return reply['result']


class Faucet:
    """Rate-limited payouts from the node wallet, persisted to a JSON file."""

    def __init__(self, rpc, amount, interval_hours, daily_limit, state_path):
        self.rpc = rpc
        self.amount = Decimal(amount)
        self.interval = interval_hours * 3600
        self.daily_limit = Decimal(daily_limit)
        self.state_path = state_path
        self.lock = threading.Lock()
        self.state = {'by_address': {}, 'by_ip': {}, 'payouts': []}
        if os.path.exists(state_path):
            with open(state_path, encoding='utf8') as f:
                self.state = json.load(f)

    def _save(self):
        tmp = self.state_path + '.new'
        with open(tmp, 'w', encoding='utf8') as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_path)

    def request(self, address, ip):
        """Pay out to address. Returns the txid, or raises ValueError with a user-facing message."""
        now = time.time()
        with self.lock:
            for table in ('by_address', 'by_ip'):
                self.state[table] = {k: t for k, t in self.state[table].items() if now - t < self.interval}
            if not self.rpc.call('validateaddress', address).get('isvalid'):
                raise ValueError('ეს არ არის სწორი CryptoLari მისამართი ამ ქსელისთვის.')
            for key, table in ((address, 'by_address'), (ip, 'by_ip')):
                last = self.state[table].get(key, 0)
                if now - last < self.interval:
                    hours = (self.interval - (now - last)) / 3600
                    raise ValueError('უკვე მიიღე. ისევ სცადე %.1f საათში.' % hours)
            self.state['payouts'] = [p for p in self.state['payouts'] if now - p[0] < 86400]
            paid_today = sum(Decimal(p[1]) for p in self.state['payouts'])
            if paid_today + self.amount > self.daily_limit:
                raise ValueError('დღევანდელი ლიმიტი ამოიწურა. ხვალ სცადე.')
            if Decimal(self.rpc.call('getbalance')) < self.amount:
                raise ValueError('ონკანი ცარიელია. მოგვიანებით სცადე.')
            txid = self.rpc.call('sendtoaddress', address, str(self.amount))
            self.state['by_address'][address] = now
            self.state['by_ip'][ip] = now
            self.state['payouts'].append([now, str(self.amount)])
            self._save()
            return txid


def coins(value):
    """Format an amount of CLARI without trailing zeros."""
    text = format(Decimal(value).quantize(Decimal('0.00000001')), 'f').rstrip('0').rstrip('.')
    return text or '0'


def supply_at(height, initial_subsidy, halving_interval):
    """Coins issued by blocks 1..height (the genesis output is unspendable)."""
    total = 0
    for halving in range(64):
        first = max(1, halving * halving_interval)
        last = min(height, (halving + 1) * halving_interval - 1)
        if last < first:
            break
        total += (last - first + 1) * (initial_subsidy >> halving)
    return Decimal(total) / COIN


def ago(timestamp):
    seconds = max(0, int(time.time() - timestamp))
    for size, unit in ((86400, 'დღის'), (3600, 'საათის'), (60, 'წუთის')):
        if seconds >= size:
            return '%d %s წინ' % (seconds // size, unit)
    return '%d წამის წინ' % seconds


def when(timestamp):
    return time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(timestamp))


def hashrate(hps):
    hps = float(hps)
    for unit in ('H/s', 'kH/s', 'MH/s', 'GH/s'):
        if hps < 1000:
            return '%.1f %s' % (hps, unit)
        hps /= 1000
    return '%.1f TH/s' % hps


e = html.escape

STYLE = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1c2330; --muted:#5b6475; --line:#e3e6ec; --accent:#c8102e; --ok:#1a7f4b; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1319; --card:#171d26; --text:#e6e9ef; --muted:#9aa3b2; --line:#263041; --accent:#ff4d5e; --ok:#3ecf8e; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
header { background:var(--card); border-bottom:1px solid var(--line); }
.wrap { max-width:1040px; margin:0 auto; padding:0 16px; }
header .wrap { display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; padding:12px 16px; }
.brand { font-weight:700; font-size:18px; color:var(--text); text-decoration:none; }
.brand span { color:var(--accent); }
.net { font-size:12px; border:1px solid var(--line); border-radius:99px; padding:2px 8px; color:var(--muted); margin-left:8px; }
nav a { color:var(--muted); text-decoration:none; margin-left:14px; }
form.search { display:flex; gap:6px; flex:1 1 280px; max-width:460px; }
input[type=text] { flex:1; min-width:0; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--text); font:inherit; }
button { padding:8px 14px; border:0; border-radius:8px; background:var(--accent); color:#fff; font:inherit; cursor:pointer; }
main { padding:20px 0 40px; }
h1 { font-size:22px; margin:0 0 14px; }
h2 { font-size:17px; margin:24px 0 10px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }
.stat b { display:block; font-size:19px; overflow-wrap:anywhere; }
.stat small { color:var(--muted); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; overflow-x:auto; }
table { width:100%; border-collapse:collapse; }
th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:13px; white-space:nowrap; }
tr:last-child td { border-bottom:0; }
a { color:var(--accent); }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; overflow-wrap:anywhere; }
.muted { color:var(--muted); }
.ok { color:var(--ok); }
.note { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:8px; padding:10px 14px; margin:14px 0; }
.pager { display:flex; justify-content:space-between; margin:12px 0; }
td.nowrap { white-space:nowrap; }
@media (max-width:600px) { .hide-sm { display:none; } th, td { padding:8px; } }
footer { color:var(--muted); font-size:13px; padding:24px 0; border-top:1px solid var(--line); }
"""


class App:
    def __init__(self, rpc, network, faucet):
        self.rpc = rpc
        self.network = network
        self.faucet = faucet

    # ---- page frame ----
    def page(self, title, body):
        faucet_link = '<a href="/faucet">ონკანი</a>' if self.faucet else ''
        return """<!doctype html>
<html lang="ka"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s · CryptoLari Explorer</title><style>%s</style></head>
<body><header><div class="wrap">
<div><a class="brand" href="/">Crypto<span>Lari</span> Explorer</a><span class="net">%s</span></div>
<form class="search" action="/search"><input type="text" name="q" placeholder="ბლოკის სიმაღლე, ბლოკის ან ტრანზაქციის hash" aria-label="ძებნა"><button>ძებნა</button></form>
<nav><a href="/">მთავარი</a>%s</nav>
</div></header>
<main><div class="wrap">%s</div></main>
<footer><div class="wrap">CryptoLari ექსპერიმენტული პროექტია. ის არ არის საქართველოს ეროვნული ვალუტა და კავშირი არ აქვს საქართველოს ეროვნულ ბანკთან. ეს არ არის საინვესტიციო რჩევა.</div></footer>
</body></html>""" % (e(title), STYLE, e(NETWORKS[self.network][2]), faucet_link, body)

    # ---- data ----
    def stats(self):
        info = self.rpc.call('getblockchaininfo')
        mempool = self.rpc.call('getmempoolinfo')
        network = self.rpc.call('getnetworkinfo')
        return {
            'network': self.network,
            'height': info['blocks'],
            'bestblockhash': info['bestblockhash'],
            'difficulty': float(info['difficulty']),
            'hashrate': float(self.rpc.call('getnetworkhashps')),
            'supply': str(supply_at(info['blocks'], *NETWORKS[self.network][3:])),
            'mempool_txs': mempool['size'],
            'connections': network['connections'],
        }

    def block_by_id(self, block_id):
        if block_id.isdigit():
            block_id = self.rpc.call('getblockhash', int(block_id))
        return self.rpc.call('getblock', block_id, 2)

    # ---- pages ----
    def home(self):
        s = self.stats()
        cards = [
            ('ბლოკების რაოდენობა', '{:,}'.format(s['height'])),
            ('სირთულე', '%.4g' % s['difficulty']),
            ('ქსელის სიმძლავრე', hashrate(s['hashrate'])),
            ('მოპოვებული CLARI', '{:,.0f}'.format(float(s['supply']))),
            ('ტრანზაქციები რიგში', str(s['mempool_txs'])),
            ('კავშირები', str(s['connections'])),
        ]
        body = '<h1>ქსელის მდგომარეობა</h1><div class="stats">'
        body += ''.join('<div class="stat"><small>%s</small><b>%s</b></div>' % (e(k), e(v)) for k, v in cards)
        body += '</div><h2>ბოლო ბლოკები</h2><div class="card"><table>'
        body += '<tr><th>სიმაღლე</th><th>დრო</th><th>ტრანზ.</th><th class="hide-sm">ზომა</th><th class="hide-sm">Hash</th></tr>'
        block_hash = s['bestblockhash']
        for _ in range(min(15, s['height'] + 1)):
            b = self.rpc.call('getblock', block_hash)
            body += '<tr><td><a href="/block/%s">%d</a></td><td class="nowrap">%s</td><td>%d</td><td class="hide-sm">%s B</td><td class="mono hide-sm">%s…</td></tr>' % (
                e(b['hash']), b['height'], e(ago(b['time'])), len(b['tx']), '{:,}'.format(b['size']), e(b['hash'][:20]))
            if 'previousblockhash' not in b:
                break
            block_hash = b['previousblockhash']
        body += '</table></div>'
        return self.page('მთავარი', body)

    def block(self, block_id):
        b = self.block_by_id(block_id)
        coinbase_value = sum(Decimal(o['value']) for o in b['tx'][0]['vout'])
        rows = [
            ('სიმაღლე', str(b['height'])),
            ('Hash', b['hash']),
            ('დრო', '%s (%s)' % (when(b['time']), ago(b['time']))),
            ('დადასტურებები', str(b['confirmations'])),
            ('ტრანზაქციები', str(len(b['tx']))),
            ('ზომა', '{:,} B'.format(b['size'])),
            ('მაინერის ჯილდო + საკომისიო', coins(coinbase_value) + ' CLARI'),
            ('სირთულე', '%.6g' % float(b['difficulty'])),
            ('Nonce', str(b['nonce'])),
            ('Merkle root', b['merkleroot']),
        ]
        body = '<h1>ბლოკი #%d</h1><div class="card"><table>' % b['height']
        body += ''.join('<tr><th>%s</th><td class="mono">%s</td></tr>' % (e(k), e(v)) for k, v in rows)
        body += '</table></div><div class="pager">'
        body += '<a href="/block/%s">← წინა ბლოკი</a>' % e(b['previousblockhash']) if 'previousblockhash' in b else '<span></span>'
        body += '<a href="/block/%s">შემდეგი ბლოკი →</a>' % e(b['nextblockhash']) if 'nextblockhash' in b else '<span></span>'
        body += '</div><h2>ტრანზაქციები</h2><div class="card"><table><tr><th>TXID</th><th>გამოსავლები</th><th>ჯამი</th></tr>'
        for tx in b['tx']:
            total = sum(Decimal(o['value']) for o in tx['vout'])
            label = ' <span class="muted">(coinbase)</span>' if 'coinbase' in tx['vin'][0] else ''
            body += '<tr><td class="mono"><a href="/tx/%s?block=%s">%s</a>%s</td><td>%d</td><td>%s CLARI</td></tr>' % (
                e(tx['txid']), e(b['hash']), e(tx['txid']), label, len(tx['vout']), coins(total))
        body += '</table></div>'
        return self.page('ბლოკი %d' % b['height'], body)

    def lookup_tx(self, txid, block_hash=None):
        if block_hash:
            for tx in self.rpc.call('getblock', block_hash, 2)['tx']:
                if tx['txid'] == txid:
                    tx['blockhash'] = block_hash
                    return tx
        return self.rpc.call('getrawtransaction', txid, 1)

    def tx(self, txid, block_hash=None):
        tx = self.lookup_tx(txid, block_hash)
        total_out = sum(Decimal(o['value']) for o in tx['vout'])
        body = '<h1>ტრანზაქცია</h1><div class="card"><table>'
        body += '<tr><th>TXID</th><td class="mono">%s</td></tr>' % e(tx['txid'])
        if tx.get('blockhash'):
            body += '<tr><th>ბლოკი</th><td class="mono"><a href="/block/%s">%s</a></td></tr>' % (e(tx['blockhash']), e(tx['blockhash']))
        else:
            body += '<tr><th>სტატუსი</th><td>ელოდება დადასტურებას (mempool)</td></tr>'
        body += '<tr><th>ზომა</th><td>%s B</td></tr><tr><th>გამოსავლების ჯამი</th><td>%s CLARI</td></tr></table></div>' % (
            '{:,}'.format(tx['size']), coins(total_out))

        body += '<h2>შესავლები</h2><div class="card"><table><tr><th>წყარო</th><th>მისამართი</th><th>თანხა</th></tr>'
        total_in = Decimal(0)
        for i, vin in enumerate(tx['vin']):
            if 'coinbase' in vin:
                body += '<tr><td colspan="3">ახალი მონეტები (ბლოკის ჯილდო)</td></tr>'
                total_in = None
                continue
            address, value = '—', None
            if i < 20:
                try:
                    prev = self.rpc.call('getrawtransaction', vin['txid'], 1)['vout'][vin['vout']]
                    address = ', '.join(prev['scriptPubKey'].get('addresses', [])) or prev['scriptPubKey']['type']
                    value = Decimal(prev['value'])
                except RPCError:
                    pass
            if total_in is not None:
                total_in = total_in + value if value is not None else None
            body += '<tr><td class="mono"><a href="/tx/%s">%s…:%d</a></td><td class="mono">%s</td><td>%s</td></tr>' % (
                e(vin['txid']), e(vin['txid'][:16]), vin['vout'], e(address), coins(value) + ' CLARI' if value is not None else '—')
        body += '</table></div>'
        if total_in is not None:
            body += '<p class="muted">საკომისიო: %s CLARI</p>' % coins(total_in - total_out)

        body += '<h2>გამოსავლები</h2><div class="card"><table><tr><th>#</th><th>მისამართი</th><th>თანხა</th></tr>'
        for vout in tx['vout']:
            script = vout['scriptPubKey']
            address = ', '.join(script.get('addresses', [])) or script['type']
            body += '<tr><td>%d</td><td class="mono">%s</td><td>%s CLARI</td></tr>' % (vout['n'], e(address), coins(vout['value']))
        body += '</table></div>'
        return self.page('ტრანზაქცია', body)

    def search(self, query):
        query = query.strip()
        if query.isdigit():
            return ('redirect', '/block/%d' % int(query))
        if HEX64.match(query):
            try:
                self.rpc.call('getblockheader', query)
                return ('redirect', '/block/%s' % query.lower())
            except RPCError:
                return ('redirect', '/tx/%s' % query.lower())
        return ('page', self.page('ძებნა', '<h1>ვერ მოიძებნა</h1><p>შეიყვანე ბლოკის სიმაღლე ან 64-სიმბოლოიანი hash.</p>'))

    def faucet_page(self, message=None, ok=False):
        f = self.faucet
        balance = Decimal(self.rpc.call('getbalance'))
        body = '<h1>CLARI ონკანი</h1>'
        body += '<p>მიიღე %s CLARI უფასოდ, რომ სცადო ტრანზაქციები. ერთ მისამართზე და ერთ IP-ზე ყოველ %d საათში ერთხელ.</p>' % (
            coins(f.amount), f.interval // 3600)
        if message:
            body += '<div class="note%s">%s</div>' % (' ok' if ok else '', message)
        body += '<form method="post" action="/faucet" class="search" style="max-width:none">'
        body += '<input type="text" name="address" placeholder="შენი CryptoLari მისამართი" aria-label="მისამართი" required>'
        body += '<button>მიიღე CLARI</button></form>'
        body += '<p class="muted">ონკანში დარჩენილია %s CLARI.</p>' % coins(balance)
        return self.page('ონკანი', body)


def make_handler(app, trust_proxy):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'CryptoLariExplorer/1.0'

        def send(self, status, content, content_type='text/html; charset=utf-8'):
            data = content.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, location):
            self.send_response(303)
            self.send_header('Location', location)
            self.end_headers()

        def client_ip(self):
            forwarded = self.headers.get('X-Forwarded-For')
            if trust_proxy and forwarded:
                # The last entry is the one our own proxy added; earlier ones come from the client.
                return forwarded.split(',')[-1].strip()
            return self.client_address[0]

        def error_page(self, status, text):
            self.send(status, app.page('შეცდომა', '<h1>შეცდომა</h1><p>%s</p>' % e(text)))

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            parts = [p for p in url.path.split('/') if p]
            try:
                if not parts:
                    self.send(200, app.home())
                elif parts == ['api', 'stats']:
                    self.send(200, json.dumps(app.stats()), 'application/json')
                elif len(parts) == 2 and parts[0] == 'block' and (parts[1].isdigit() or HEX64.match(parts[1])):
                    self.send(200, app.block(parts[1]))
                elif len(parts) == 2 and parts[0] == 'tx' and HEX64.match(parts[1]):
                    block_hash = query.get('block', [None])[0]
                    if block_hash and not HEX64.match(block_hash):
                        block_hash = None
                    self.send(200, app.tx(parts[1], block_hash))
                elif parts == ['search']:
                    kind, result = app.search(query.get('q', [''])[0])
                    self.redirect(result) if kind == 'redirect' else self.send(200, result)
                elif parts == ['faucet'] and app.faucet:
                    self.send(200, app.faucet_page())
                else:
                    self.error_page(404, 'გვერდი ვერ მოიძებნა.')
            except RPCError as error:
                hint = ' ტრანზაქციების მოსაძებნად კვანძი -txindex=1-ით გაუშვი.' if parts and parts[0] == 'tx' else ''
                self.error_page(404, 'ვერ მოიძებნა: %s.%s' % (error, hint))

        def do_POST(self):
            if urllib.parse.urlsplit(self.path).path != '/faucet' or not app.faucet:
                return self.error_page(404, 'გვერდი ვერ მოიძებნა.')
            length = min(int(self.headers.get('Content-Length') or 0), 4096)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode('utf8', 'replace'))
            address = form.get('address', [''])[0].strip()
            try:
                if not address or len(address) > 100:
                    raise ValueError('შეიყვანე მისამართი.')
                txid = app.faucet.request(address, self.client_ip())
                message = 'გამოგზავნილია! ტრანზაქცია: <a class="mono" href="/tx/%s">%s</a>' % (e(txid), e(txid))
                self.send(200, app.faucet_page(message, ok=True))
            except ValueError as error:
                self.send(200, app.faucet_page(e(str(error))))
            except RPCError as error:
                self.send(200, app.faucet_page(e('ვერ გაიგზავნა: %s' % error)))

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--network', choices=NETWORKS, default='main')
    parser.add_argument('--datadir', default=os.path.expanduser('~/.cryptolari'), help='node data directory (for the RPC cookie)')
    parser.add_argument('--rpcport', type=int, help='node RPC port (default depends on --network)')
    parser.add_argument('--rpcuser', help='RPC user, if the node uses rpcuser/rpcpassword instead of the cookie')
    parser.add_argument('--rpcpassword')
    parser.add_argument('--host', default='127.0.0.1', help='address to listen on (default: %(default)s)')
    parser.add_argument('--port', type=int, default=8080, help='port to listen on (default: %(default)s)')
    parser.add_argument('--no-faucet', action='store_true', help='disable the faucet')
    parser.add_argument('--faucet-amount', default='1', help='CLARI per payout (default: %(default)s)')
    parser.add_argument('--faucet-interval', type=int, default=24, help='hours between payouts per address/IP (default: %(default)s)')
    parser.add_argument('--faucet-daily-limit', default='100', help='CLARI paid out per 24 hours at most (default: %(default)s)')
    parser.add_argument('--trust-proxy', action='store_true', help='take the client IP from X-Forwarded-For (only behind your own proxy)')
    args = parser.parse_args()

    port, subdir = NETWORKS[args.network][:2]
    rpc = RPC(args.rpcport or port, args.datadir, subdir, args.rpcuser, args.rpcpassword)
    rpc.call('getblockcount')  # fail early if the node is unreachable

    faucet = None
    if not args.no_faucet:
        state = os.path.join(args.datadir, subdir, 'faucet-state.json')
        faucet = Faucet(rpc, args.faucet_amount, args.faucet_interval, args.faucet_daily_limit, state)

    server = ThreadingHTTPServer((args.host, args.port), make_handler(App(rpc, args.network, faucet), args.trust_proxy))
    print('CryptoLari explorer: http://%s:%d/ (%s%s)' % (args.host, args.port, args.network, ', faucet on' if faucet else ''))
    server.serve_forever()


if __name__ == '__main__':
    main()
