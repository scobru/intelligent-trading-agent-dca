"""
Integrazione Telegram per il bot DCA & Rebalancer.

Consente notifiche delle operazioni (DCA e ribilanciamento) e risponde ai comandi:
  /status    - Valore portafoglio, Fear & Greed e pesi attuali
  /weights   - Dettaglio pesi attuali vs target con drift
  /run       - Esegue un ciclo immediato
  /help      - Guida comandi
"""

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

import config
from base_client import BaseClient
from dca_manager import DcaManager

logger = logging.getLogger("telegram_bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
API_URL = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""


def send_message(text: str, chat_id: str = None) -> bool:
    if not TOKEN:
        return False
    target_chat = chat_id or CHAT_ID
    if not target_chat:
        return False
    try:
        res = requests.post(
            f"{API_URL}/sendMessage",
            json={"chat_id": target_chat, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return res.status_code == 200
    except Exception as exc:
        logger.warning("Invio Telegram fallito: %s", exc)
        return False


def format_status(status: Dict[str, Any]) -> str:
    total = status.get("total_value_usd", 0.0)
    mode = status.get("mode", "dry_run").upper()
    fng = status.get("sentiment", {})
    portfolio = status.get("portfolio", {})
    assets = portfolio.get("assets", {})

    lines = [
        f"📊 <b>DCA &amp; Rebalancer Status ({mode})</b>",
        f"💼 <b>Valore totale:</b> ${total:.2f}",
        f"🎭 <b>Fear &amp; Greed:</b> {fng.get('value', '--')}/100 ({fng.get('classification', 'Neutral')})",
        f"📈 <b>Moltiplicatore DCA:</b> {fng.get('dca_multiplier', 1.0):.2f}x",
        "",
        "<b>Pesi di Portafoglio:</b>",
    ]

    for sym, a in assets.items():
        curr_pct = a["current_weight"] * 100.0
        targ_pct = a["target_weight"] * 100.0
        drift = a["drift_pct"]
        lines.append(f"• <b>{sym}:</b> ${a['value_usd']:.2f} ({curr_pct:.1f}% vs {targ_pct:.1f}% | drift: {drift:+.1f}%)")

    reb_txt = "⚠️ <i>Ribilanciamento dovuto!</i>" if portfolio.get("needs_rebalance") else "✅ <i>In equilibrio</i>"
    lines.append(f"\n{reb_txt}")
    return "\n".join(lines)


def handle_command(cmd: str, chat_id: str):
    cmd = cmd.strip().lower()
    client = BaseClient()
    manager = DcaManager(client)

    if cmd in ("/status", "/start"):
        try:
            status = manager.get_status()
            send_message(format_status(status), chat_id=chat_id)
        except Exception as e:
            send_message(f"Errore lettura stato: {e}", chat_id=chat_id)

    elif cmd == "/weights":
        try:
            status = manager.get_status()
            portfolio = status.get("portfolio", {})
            assets = portfolio.get("assets", {})
            lines = ["📊 <b>Dettaglio Pesi &amp; Scostamenti:</b>"]
            for sym, a in assets.items():
                lines.append(
                    f"<b>{sym}:</b>\n"
                    f"  Saldo: {a['amount']:.4f}\n"
                    f"  Valore: ${a['value_usd']:.2f} (prezzo: ${a['price_usd']:.2f})\n"
                    f"  Peso: {a['current_weight']*100:.1f}% / Target: {a['target_weight']*100:.1f}%\n"
                    f"  Scostamento: {a['drift_pct']:+.1f}% (${a['diff_usd']:+.2f})\n"
                )
            send_message("\n".join(lines), chat_id=chat_id)
        except Exception as e:
            send_message(f"Errore: {e}", chat_id=chat_id)

    elif cmd == "/run":
        send_message("⚡ Avvio ciclo DCA &amp; Ribilanciamento in corso...", chat_id=chat_id)
        def _exec():
            try:
                subprocess.run([sys.executable, "main.py"], check=False)
            except Exception as exc:
                send_message(f"Errore ciclo: {exc}", chat_id=chat_id)
        threading.Thread(target=_exec, daemon=True).start()

    elif cmd == "/help":
        msg = (
            "🤖 <b>Comandi DCA Agent:</b>\n"
            "/status - Riepilogo valore, Fear & Greed e quote\n"
            "/weights - Dettaglio quote e scostamenti drift\n"
            "/run - Esegui ciclo di valutazione ed esecuzione\n"
            "/help - Mostra questa guida"
        )
        send_message(msg, chat_id=chat_id)


def start_listener():
    if not TOKEN:
        return
    offset = 0
    print("📱 Telegram listener avviato...")
    while True:
        try:
            res = requests.get(f"{API_URL}/getUpdates", params={"offset": offset, "timeout": 20}, timeout=25)
            if res.status_code == 200:
                data = res.json()
                for update in data.get("result", []):
                    offset = max(offset, update["update_id"] + 1)
                    msg = update.get("message", {})
                    chat = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")
                    if CHAT_ID and chat != str(CHAT_ID):
                        continue
                    if text.startswith("/"):
                        handle_command(text, chat)
        except Exception as exc:
            logger.debug("Polling error: %s", exc)
        time.sleep(2)


if __name__ == "__main__":
    start_listener()
