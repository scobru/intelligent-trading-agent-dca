"""
Dashboard web per DCA & Portfolio Rebalancer su Base.

Standard library HTTP server (senza Flask/FastAPI), identica struttura ai bot fratelli:
  - Valore totale portafoglio ed equity curve
  - Crypto Fear & Greed Index e moltiplicatore DCA dinamico
  - Pesi attuali vs Pesi target con indicatore visuale del drift
  - Storico operazioni DCA e ribilanciamento
  - Pulsante 'Esegui ciclo ora'
"""

import hmac
import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

import config
import db_utils
from base_client import BaseClient
from dca_manager import DcaManager

logger = logging.getLogger("dashboard")

PORT = int(os.getenv("DASHBOARD_PORT", os.getenv("PORT", "3000")))
RUN_TOKEN = os.getenv("DASHBOARD_RUN_TOKEN", "")
_run_lock = threading.Lock()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_ROUTES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/static/icon.svg": ("icon.svg", "image/svg+xml"),
    "/static/icon-small.svg": ("icon-small.svg", "image/svg+xml"),
    "/static/icon-192.png": ("icon-192.png", "image/png"),
    "/static/icon-512.png": ("icon-512.png", "image/png"),
    "/static/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/static/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
    "/static/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/static/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8"),
}

HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DCA &amp; Rebalancer Agent</title>
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/static/icon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/dashboard.css?v=4">
<style>
:root { --primary: #3b82f6; --accent: #60a5fa; }
.fng-card { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.fng-gauge { font-size: 2.2rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; }
.bar-wrap { background: rgba(255,255,255,0.06); border-radius: 6px; height: 10px; width: 100%; overflow: hidden; position: relative; margin-top: 4px; }
.bar-fill { height: 100%; border-radius: 6px; transition: width 0.3s; }
.drift-tag { font-size: 0.8rem; font-weight: 600; padding: 2px 6px; border-radius: 4px; }
.tag-over { background: rgba(239,68,68,0.2); color: #ef4444; }
.tag-under { background: rgba(16,185,129,0.2); color: #10b981; }
.tag-ok { background: rgba(59,130,246,0.2); color: #3b82f6; }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="/static/dashboard.js?v=4"></script>
</head>
<body>
<header class="header">
  <div class="brand">
    <img src="/static/icon.svg" alt="">
    <div>
      <h1>DCA &amp; Rebalancer <span class="badge b-no" id="mode">…</span> <span class="badge" id="gas-pill" style="font-size:0.8rem; vertical-align:middle; background:rgba(255,255,255,0.08);">⛽ Gas: --</span></h1>
      <p class="tagline">Accumulo dinamico &amp; Ribilanciamento pesi su Base • Fear &amp; Greed • Uniswap V3</p>
    </div>
  </div>
  <div class="header-actions">
    <span class="updated" id="updated"></span>
    <button class="btn" id="recal-btn" style="background:#4f46e5; border-color:#6366f1; margin-right:6px;">🧠 Ricalibra Pesi AI</button>
    <button class="btn" id="run">⚡ Esegui ciclo ora</button>
  </div>
</header>

<section class="card paper-panel" id="paper-panel" hidden>
  <div class="card-head"><h2>📝 Paper trading <small>portafoglio virtuale</small></h2></div>
  <div class="paper-grid" id="paper-grid"></div>
</section>

<section class="stats">
  <div class="card"><h3>Valore totale portafoglio</h3><div class="value" id="total">--</div><div class="sub" id="total-sub"></div></div>
  <div class="card">
    <h3>Crypto Fear &amp; Greed</h3>
    <div class="fng-card">
      <div class="fng-gauge" id="fng-val">--</div>
      <div style="text-align: right;">
        <span class="badge" id="fng-class">--</span>
        <div class="sub" id="fng-mult">Moltiplicatore DCA: --</div>
      </div>
    </div>
  </div>
  <div class="card"><h3>Stato Ribilanciamento</h3><div class="value" id="reb-status">--</div><div class="sub" id="reb-sub">Soglia drift: &plusmn;5%</div></div>
  <div class="card"><h3>Ultima operazione</h3><div class="value" id="last-op">--</div><div class="sub" id="last-op-time">nessuna operazione registrata</div></div>
</section>

<section class="card section">
  <div class="card-head"><h2>💼 Andamento capitale</h2></div>
  <div class="chart-box tall"><canvas id="equity"></canvas></div>
</section>

<div class="grid-2">
  <section class="card">
    <div class="card-head"><h2>📊 Composizione Portafoglio vs Target</h2><small id="weights-sub" style="display:block; color:var(--text-muted); font-size:0.75rem; margin-top:2px;">Asset detenuti su Base</small></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Asset</th><th>Saldo</th><th>Prezzo</th><th>Valore</th><th>Attuale</th><th>Target</th><th>Drift</th><th>Stato</th></tr></thead>
      <tbody id="assets"></tbody>
    </table></div>
  </section>
  <section class="card">
    <div class="card-head"><h2>🧠 Ultima decisione AI</h2></div>
    <div class="decision-box">
      <div class="title" id="ai-action">In attesa del primo ciclo...</div>
      <div class="desc" id="ai-reason">L'agente valuterà il portafoglio al prossimo intervallo o con "Esegui ciclo ora".</div>
    </div>
    <div class="kv">
      <div><b>Modello:</b> OpenRouter</div>
      <div><b>Strategia:</b> DCA modulato dal sentiment + ribilanciamento automatico</div>
    </div>
  </section>
</div>

<section class="card section">
  <div class="card-head"><h2>📜 Storico operazioni DCA &amp; Ribilanciamento</h2></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Data (UTC)</th><th>Operazione</th><th>Dettaglio</th><th>Importo</th><th>Esito</th><th>Motivazione</th></tr></thead>
    <tbody id="ops"></tbody>
  </table></div>
</section>

<footer class="footer">DCA &amp; Rebalancer Agent • Base Network &amp; Uniswap V3 • OpenRouter AI</footer>

<script>
const { $, esc, usd, signedUsd, pct, big, cls, time, empty } = ITA;
let chart = null, stateData = null;

function renderStatus(s) {
  if (!s) return;
  $('total').textContent = usd(s.total_value_usd);
  $('mode').textContent = s.mode.toUpperCase();
  $('mode').className = 'badge ' + (s.mode === 'live' ? 'b-ok' : 'b-no');

  const paper = s.paper;
  if (paper && s.mode === 'paper') {
    $('paper-panel').hidden = false;
    $('total-sub').innerHTML = `<span class="${cls(paper.pnl_usd)}">${signedUsd(paper.pnl_usd)} (${paper.pnl_pct}%)</span> da inizio`;
    $('paper-grid').innerHTML = `
      <div><span class="lbl">Iniziale:</span> ${usd(paper.initial_usdc)}</div>
      <div><span class="lbl">Operazioni:</span> ${paper.operations}</div>
      <div><span class="lbl">Gas simulato:</span> ${usd(paper.gas_spent_usd)}</div>
    `;
  } else {
    $('total-sub').textContent = 'Live wallet Base';
  }

  // Sentiment F&G
  const fng = s.sentiment || {};
  const fngVal = fng.value != null ? fng.value : '--';
  $('fng-val').textContent = fngVal;
  $('fng-class').textContent = fng.classification || 'Neutral';
  $('fng-class').className = 'badge ' + (fngVal <= 30 ? 'tag-under' : fngVal >= 70 ? 'tag-over' : 'tag-ok');
  $('fng-mult').textContent = 'Moltiplicatore DCA: ' + (fng.dca_multiplier || 1.0).toFixed(2) + 'x';

  // Rebalance status
  const p = s.portfolio || {};
  $('reb-status').textContent = p.needs_rebalance ? '⚠️ Ribilanciamento dovuto' : '✅ In equilibrio';
  $('reb-status').style.color = p.needs_rebalance ? '#ef4444' : '#10b981';

  // Gas reserve pill
  if (s.gas_eth && s.gas_eth.amount != null) {
    $('gas-pill').textContent = `⛽ Gas: ${Number(s.gas_eth.amount).toFixed(4)} ETH (${usd(s.gas_eth.value_usd)})`;
  }
  if ($('weights-sub')) {
    $('weights-sub').textContent = 'AI Allocator: ' + (s.weights_rationale || 'Pesi bilanciati di default');
  }

  // Assets table
  const assets = Object.values(p.assets || {});
  $('assets').innerHTML = assets.map(a => {
    const isOver = a.drift_pct >= 5.0;
    const isUnder = a.drift_pct <= -5.0;
    const tagCls = isOver ? 'tag-over' : isUnder ? 'tag-under' : 'tag-ok';
    const tagTxt = isOver ? 'Sovrappeso' : isUnder ? 'Sottopeso' : 'In target';
    return `<tr>
      <td><b>${esc(a.symbol)}</b>${a.category ? `<small style="color:var(--text-muted); display:block; font-size:0.75rem;">${esc(a.category)}</small>` : ''}</td>
      <td class="num">${Number(a.amount).toFixed(4)}</td>
      <td class="num">${usd(a.price_usd)}</td>
      <td class="num"><b>${usd(a.value_usd)}</b></td>
      <td class="num">${(a.current_weight * 100).toFixed(1)}%</td>
      <td class="num">${(a.target_weight * 100).toFixed(1)}%</td>
      <td class="num"><span class="${cls(-a.drift_pct)}">${a.drift_pct > 0 ? '+' : ''}${a.drift_pct.toFixed(1)}%</span></td>
      <td><span class="drift-tag ${tagCls}">${tagTxt}</span></td>
    </tr>`;
  }).join('') || empty(8, 'Nessun asset trovato.');
}

function renderOps(ops) {
  $('ops').innerHTML = ops.map(o => {
    let detail = '--';
    try {
      const d = JSON.parse(o.details_json || '{}');
      if (d.operation === 'dca') {
        detail = (d.purchases || []).map(p => `${p.asset}: $${p.amount_usd}`).join(', ');
      } else if (d.operation === 'rebalance') {
        detail = `${d.from_asset} ➔ ${d.to_asset}`;
      }
    } catch(e) {}

    return `<tr>
      <td>${time(o.created_at)}</td>
      <td><span class="badge ${o.operation === 'dca' ? 'b-ok' : 'b-no'}">${(o.operation || '').toUpperCase()}</span></td>
      <td>${esc(detail)}</td>
      <td class="num">${usd(o.amount_usd)}</td>
      <td><span class="badge ${o.status === 'success' ? 'b-ok' : 'b-no'}">${esc(o.status)}</span></td>
      <td class="reason">${esc(o.reason)}</td>
    </tr>`;
  }).join('') || empty(6, 'Nessuna operazione registrata.');

  if (ops.length > 0) {
    const last = ops[0];
    $('last-op').textContent = (last.operation || '--').toUpperCase();
    $('last-op-time').textContent = time(last.created_at);
  }
}

function renderChart(points) {
  chart = ITA.lineChart(chart, $('equity'), points.map(p => time(p.created_at)), [
    { label: 'Valore totale ($)', data: points.map(p => p.total_value_usd) }
  ]);
}

async function refresh() {
  try {
    const [sRes, snapRes, opRes] = await Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch('/api/snapshots').then(r => r.json()),
      fetch('/api/operations').then(r => r.json())
    ]);
    renderStatus(sRes);
    renderOps(opRes);
    renderChart(snapRes);
    $('updated').textContent = 'Aggiornato: ' + new Date().toLocaleTimeString();
  } catch(e) {
    console.error(e);
  }
}

$('run').addEventListener('click', async () => {
  const token = prompt('Inserisci il DASHBOARD_RUN_TOKEN (se configurato):') || '';
  try {
    const res = await fetch('/api/run', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token } });
    if (res.ok) {
      alert('Ciclo avviato in background.');
      setTimeout(refresh, 2500);
    } else {
      alert('Avvio rifiutato (token non valido).');
    }
  } catch(e) { alert('Errore: ' + e); }
});

$('recal-btn').addEventListener('click', async () => {
  const token = prompt('Inserisci il DASHBOARD_RUN_TOKEN (se configurato):') || '';
  try {
    const res = await fetch('/api/ai_recalibrate', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token } });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      alert('🎯 Pesi aggiornati con successo dall\'AI!\n\nRationale: ' + (data.rationale || ''));
      refresh();
    } else {
      alert('Ricalibrazione: ' + (data.message || data.error || 'Errore o autorizzazione negata'));
    }
  } catch(e) { alert('Errore: ' + e); }
});

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    client: Optional[BaseClient] = None
    manager: Optional[DcaManager] = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in STATIC_ROUTES:
            rel, ctype = STATIC_ROUTES[path]
            fpath = os.path.join(STATIC_DIR, rel)
            if os.path.isfile(fpath):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                with open(fpath, "rb") as f:
                    self.wfile.write(f.read())
                return
            self.send_error(404)
            return

        if path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                status = self.manager.get_status()
                status["is_paused"] = db_utils.is_bot_paused()
                status["pause_info"] = db_utils.get_pause_info()
                self.wfile.write(json.dumps(status, default=str).encode("utf-8"))
            except Exception as exc:
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if path == "/api/snapshots":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            snaps = db_utils.get_recent_snapshots(100)
            self.wfile.write(json.dumps(snaps, default=str).encode("utf-8"))
            return

        if path == "/api/operations":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            ops = db_utils.get_recent_operations(50)
            self.wfile.write(json.dumps(ops, default=str).encode("utf-8"))
            return

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))
            return

        self.send_error(404)

    def _is_auth_valid(self) -> bool:
        if not RUN_TOKEN:
            return False
        token = self.headers.get("X-Run-Token", "") or self.headers.get("X-Admin-Token", "")
        if not token and "Authorization" in self.headers:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:].strip()
            else:
                token = auth.strip()
        return bool(token and hmac.compare_digest(token, RUN_TOKEN))

    def _send_json(self, status: int, data: Any):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path not in ("/api/run", "/api/pause", "/api/resume", "/api/release_funds", "/api/recalibrate_weights", "/api/ai_recalibrate"):
            self.send_error(404)
            return

        if not self._is_auth_valid():
            self._send_json(403, {"error": "unauthorized", "message": "Token non valido o mancante"})
            return

        if path in ("/api/recalibrate_weights", "/api/ai_recalibrate"):
            try:
                from base_client import BaseClient
                from dca_manager import DcaManager
                client = DashboardHandler.client or BaseClient()
                manager = DashboardHandler.manager or DcaManager(client)
                res = manager.recalibrate_weights(force=True)
                self._send_json(200, res)
            except Exception as exc:
                logger.error("Errore ricalibrazione pesi: %s", exc)
                self._send_json(500, {"status": "error", "message": str(exc)})
            return

        if path == "/api/pause":
            reason = "Pausa richiesta da API"
            try:
                clen = int(self.headers.get("Content-Length", 0))
                if clen > 0:
                    body = json.loads(self.rfile.read(clen).decode("utf-8"))
                    reason = body.get("reason", reason)
            except Exception:
                pass
            db_utils.set_bot_paused(True, reason=reason)
            self._send_json(200, {"status": "success", "is_paused": True, "message": f"Bot DCA in pausa: {reason}"})
            return

        if path == "/api/resume":
            db_utils.set_bot_paused(False)
            self._send_json(200, {"status": "success", "is_paused": False, "message": "Bot DCA riattivato con successo."})
            return

        if path == "/api/run":
            if db_utils.is_bot_paused():
                pinfo = db_utils.get_pause_info()
                self._send_json(200, {
                    "status": "paused",
                    "is_paused": True,
                    "message": f"Bot DCA attualmente in PAUSA ({pinfo.get('reason', 'Pausa attiva')}). Ciclo ignorato."
                })
                return

            def _target():
                with _run_lock:
                    try:
                        subprocess.run([sys.executable, "main.py"], check=False)
                    except Exception as e:
                        logger.error("Errore esecuzione main.py: %s", e)

            threading.Thread(target=_target, daemon=True).start()
            self._send_json(200, {"status": "started", "message": "Ciclo DCA avviato in background."})
            return

        if path == "/api/release_funds":
            target_amount = 0.0
            try:
                clen = int(self.headers.get("Content-Length", 0))
                if clen > 0:
                    body = json.loads(self.rfile.read(clen).decode("utf-8"))
                    target_amount = float(body.get("amount_usd", 0.0) or body.get("amount", 0.0))
            except Exception:
                pass
            try:
                from base_client import BaseClient
                from dca_manager import DcaManager
                client = BaseClient()
                manager = DcaManager(client)
                res = manager.release_funds(target_usdc=target_amount)
                self._send_json(200, res)
            except Exception as exc:
                self._send_json(500, {"status": "error", "message": str(exc)})
            return


def run_dashboard():
    client = BaseClient()
    DashboardHandler.client = client
    DashboardHandler.manager = DcaManager(client)

    server = ThreadingHTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"🚀 Dashboard DCA attiva su http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_dashboard()
