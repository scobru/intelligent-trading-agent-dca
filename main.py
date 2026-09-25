"""
Un ciclo dell'agente DCA & Ribilanciatore su Base:
  1. Verifica contratti e connessione Base
  2. Auto-refuel USDC se necessario
  3. Lettura saldi, prezzi e calcolo pesi vs target (drift)
  4. Analisi sentiment (Fear & Greed)
  5. Decisione (LLM OpenRouter con fallback algoritmico)
  6. Esecuzione su Uniswap V3 (Paper, Dry-Run o Live)
  7. Registrazione snapshot e operazioni su SQLite
"""

import json
import logging
import os
import sys
from typing import Any, Dict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import config
import db_utils
import sentiment
from base_client import BaseClient
from dca_agent import OPENROUTER_MODEL, decide_action
from dca_manager import DcaManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("dca_agent")


def _format_portfolio_for_prompt(status: Dict[str, Any]) -> str:
    lines = ["Asset | Saldo | Prezzo USD | Valore USD | Peso Attuale | Peso Target | Drift"]
    portfolio = status.get("portfolio", {})
    for sym, a in portfolio.get("assets", {}).items():
        lines.append(
            f"{sym} | {a['amount']:.4f} | ${a['price_usd']:.2f} | ${a['value_usd']:.2f} | "
            f"{a['current_weight']*100:.1f}% | {a['target_weight']*100:.1f}% | "
            f"{a['drift_pct']:+.1f}%"
        )
    return "\n".join(lines)


def run_cycle():
    if db_utils.is_bot_paused():
        pinfo = db_utils.get_pause_info()
        logger.info("⏸️ Bot DCA in stato di PAUSA (%s). Ciclo ignorato.", pinfo.get("reason", "Pausa attiva"))
        return None

    print(f"🚀 Avvio DCA & Rebalancer Agent su Base (wallet: {config.WALLET_ADDRESS or 'paper'})")
    if config.PAPER_TRADING:
        print(f"📝 PAPER TRADING attivo: portafoglio virtuale da ${config.PAPER_START_USDC:.2f} USDC.")
    elif config.DRY_RUN:
        print("🧪 DRY-RUN attivo: nessuna transazione verrà firmata sulla chain.")

    client = BaseClient()
    if not config.PAPER_TRADING:
        if not client.is_connected():
            logger.warning("RPC Base non raggiungibile: %s", config.BASE_RPC_URL)
        else:
            try:
                problems = client.verify_contracts()
                if problems:
                    logger.warning("Verifica contratti parziale (possibile rate limit RPC):\n  - " + "\n  - ".join(problems))
                else:
                    print(f"⛓️  Connesso a Base (chain {client.chain_id()}), gas {client.gas_price_gwei():.4f} gwei")
            except Exception as exc:
                logger.warning("Verifica contratti non completata causa RPC: %s", exc)

    manager = DcaManager(client)

    # 0. Auto-refuel USDC se applicabile
    try:
        refuel = manager.ensure_usdc_balance()
        if refuel:
            print(f"⛽ Auto-refuel completato: {refuel.get('description', '')}")
    except Exception as exc:
        print(f"⚠️  Auto-refuel non riuscito: {exc}")

    # 1. Lettura dello stato del portafoglio
    print("👛 Calcolo quote di portafoglio e verifica drift...")
    status = manager.get_status()
    total_usd = status["total_value_usd"]
    fng = status["sentiment"]

    print(f"   Valore totale: ${total_usd:.2f} | Fear & Greed: {fng.get('value')}/100 ({fng.get('classification')})")
    for sym, a in status["portfolio"]["assets"].items():
        print(f"   - {sym:5s}: ${a['value_usd']:8.2f} ({a['current_weight']*100:5.1f}% vs target {a['target_weight']*100:5.1f}%, drift {a['drift_pct']:+5.1f}%)")

    # 2. Log snapshot a DB
    try:
        db_utils.log_snapshot(status)
    except Exception as exc:
        print(f"[db_utils] snapshot non salvato: {exc}")

    # 3. Valutazione algoritmica di base (priorita': rebalance > dca > hold)
    algo_plan = None
    if status.get("rebalance_due"):
        algo_plan = manager.plan_rebalance(status)
    if not algo_plan and status.get("dca_due"):
        algo_plan = manager.plan_dca(status)
    if not algo_plan:
        algo_plan = {"operation": "hold", "reason": "Portafoglio bilanciato e nessun DCA in scadenza"}

    print(f"💡 Piano algoritmico: {algo_plan.get('operation').upper()} - {algo_plan.get('reason')}")

    # 4. Prompt per il modello decisionale
    context = (
        f"<portafoglio_dettaglio>\n{_format_portfolio_for_prompt(status)}\n</portafoglio_dettaglio>\n\n"
        f"<sentiment>\n{sentiment.format_sentiment_for_prompt(fng)}\n</sentiment>\n\n"
        f"<regole_esecutore>\n"
        f"Pesi obiettivo: {json.dumps(config.TARGET_WEIGHTS)}\n"
        f"Soglia ribilanciamento: {config.REBALANCE_THRESHOLD_PCT:.1f}% drift, min ${config.MIN_REBALANCE_USD:.0f}, max ${config.MAX_REBALANCE_USD:.0f}\n"
        f"DCA base: ${config.DCA_BASE_AMOUNT_USD:.2f} ogni {config.DCA_INTERVAL_HOURS:.0f}h\n"
        f"Ore dall'ultimo DCA: {status.get('hours_since_last_dca') or 'n/d'}\n"
        f"Ore dall'ultimo rebalance: {status.get('hours_since_last_rebalance') or 'n/d'}\n"
        f"</regole_esecutore>\n"
    )

    portfolio_data = json.dumps({
        "total_value_usd": total_usd,
        "balances": status["balances"],
        "dca_due": status["dca_due"],
        "rebalance_due": status["rebalance_due"],
        "recommended_operation": algo_plan.get("operation"),
    }, default=str)

    prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt")
    with open(prompt_path, encoding="utf-8") as f:
        raw_prompt = f.read()

    if "{{PORTFOLIO_DATA}}" in raw_prompt:
        system_prompt = raw_prompt.replace("{{PORTFOLIO_DATA}}", portfolio_data).replace("{{CONTEXT_INFO}}", context)
    else:
        # Fallback sicuro se il file contiene {} evitando KeyError sulla sintassi JSON
        parts = raw_prompt.split("{}", 2)
        if len(parts) == 3:
            system_prompt = parts[0] + portfolio_data + parts[1] + context + parts[2]
        else:
            system_prompt = raw_prompt + f"\n\nPortfolio Data:\n{portfolio_data}\n\nContext:\n{context}"

    print(f"🤖 Interrogazione AI ({OPENROUTER_MODEL})...")
    decision = decide_action(system_prompt, fallback_action=algo_plan)
    print(f"   Decisione finale: {decision.get('operation', 'hold').upper()} - {decision.get('reason')}")

    # 5. Esecuzione
    result = manager.execute_action(decision, status)
    print(f"⚡ Esito esecuzione: {result.get('status', 'noop').upper()}")

    # 6. Registrazione DB
    try:
        db_utils.log_operation(decision, result)
    except Exception as exc:
        print(f"[db_utils] operazione non salvata: {exc}")

    print("🏁 Ciclo completato con successo.\n")
    return {"status": status, "decision": decision, "result": result}


if __name__ == "__main__":
    run_cycle()
