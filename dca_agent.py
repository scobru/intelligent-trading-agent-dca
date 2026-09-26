"""
Modulo di intelligenza decisionale LLM (OpenRouter) per il bot DCA e Ribilanciatore.
"""

import json
import logging
import os
import re
from typing import Any, Dict

from openai import OpenAI

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


def _clean_json(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1).strip()
    return text


def decide_action(system_prompt: str, fallback_action: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Invia lo stato del portafoglio e il sentiment all'LLM e riceve la decisione JSON.
    Se OpenRouter fallisce o la chiave e' assente, usa il piano deterministico.
    """
    fallback = fallback_action or {"operation": "hold", "reason": "Nessuna azione pianificata"}

    if not OPENROUTER_API_KEY:
        logger.info("OPENROUTER_API_KEY non configurata: uso decisione algoritmica di fallback")
        return fallback

    try:
        client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            timeout=float(os.getenv("HTTP_TIMEOUT", 30)),
        )

        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Analizza il portafoglio, il sentiment e decidi la prossima mossa in formato JSON valido."},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or ""
        cleaned = _clean_json(raw)
        data = json.loads(cleaned)

        op = str(data.get("operation", "hold")).lower()
        if op not in ("dca", "rebalance", "hold"):
            op = "hold"
        data["operation"] = op
        return data

    except Exception as exc:
        logger.warning("Chiamata OpenRouter fallita: %s (uso decisione di fallback)", exc)
        return fallback


def get_quantitative_fallback_weights(fg_value: int) -> Dict[str, Any]:
    """Matrice quantitativa di fallback basata sull'indice Crypto Fear & Greed."""
    if fg_value <= 25:
        weights = {"CBBTC": 0.35, "WETH": 0.25, "USDC": 0.20, "LINK": 0.10, "UNI": 0.05, "AERO": 0.05}
        rationale = f"Extreme Fear ({fg_value}/100): sovrappeso difensivo su CBBTC e USDC; esposizione prudente su altcoin."
    elif fg_value <= 45:
        weights = {"CBBTC": 0.30, "WETH": 0.25, "USDC": 0.15, "LINK": 0.15, "UNI": 0.08, "AERO": 0.07}
        rationale = f"Fear ({fg_value}/100): accumulo moderato di CBBTC e WETH, con cassa USDC pronta per eventuali dip."
    elif fg_value <= 55:
        weights = {"CBBTC": 0.25, "WETH": 0.25, "LINK": 0.15, "UNI": 0.10, "AERO": 0.10, "USDC": 0.15}
        rationale = f"Neutral ({fg_value}/100): allocazione bilanciata standard sul basket Base."
    elif fg_value <= 75:
        weights = {"CBBTC": 0.20, "WETH": 0.25, "LINK": 0.18, "UNI": 0.15, "AERO": 0.15, "USDC": 0.07}
        rationale = f"Greed ({fg_value}/100): espansione dell'esposizione all'ecosistema DeFi (LINK, UNI, AERO)."
    else:
        weights = {"USDC": 0.25, "CBBTC": 0.25, "WETH": 0.20, "LINK": 0.12, "UNI": 0.09, "AERO": 0.09}
        rationale = f"Extreme Greed ({fg_value}/100): presa di profitto e incremento della cassa USDC per preservare i guadagni."

    return {"target_weights": weights, "rationale": rationale, "source": "quantitative_fallback"}


def recalibrate_weights_ai(
    portfolio_summary: Dict[str, Any],
    sentiment_data: Dict[str, Any],
    current_weights: Dict[str, float]
) -> Dict[str, Any]:
    """
    Ricalibra i pesi target del basket tramite AI (OpenRouter) o motore quantitativo di fallback.
    Garantisce normalizzazione a 1.0 e rispetto dei vincoli minimi/massimi per asset.
    """
    import config

    fg_val = int(sentiment_data.get("value", 50))
    fg_label = sentiment_data.get("classification", "Neutral")
    fallback = get_quantitative_fallback_weights(fg_val)

    if not OPENROUTER_API_KEY:
        logger.info("OPENROUTER_API_KEY non presente: uso fallback quantitativo per allocazione tattica")
        return fallback

    prompt_data = {
        "fear_and_greed": f"{fg_val}/100 ({fg_label})",
        "current_weights": current_weights,
        "portfolio_total_usd": portfolio_summary.get("total_value_usd", 0.0),
        "available_assets": list(config.KNOWN_ASSETS.keys()),
        "constraints": {
            "min_weight_per_asset": config.MIN_ASSET_WEIGHT,
            "max_weight_per_asset": config.MAX_ASSET_WEIGHT,
            "sum_weights": 1.0,
        }
    }

    system_prompt = (
        "Sei il Portfolio Manager e Asset Allocator del fondo indicizzato su Base L2.\n"
        "Il basket di investimento è composto da:\n"
        "- CBBTC: Bitcoin Store of Value\n"
        "- WETH: Ethereum Settlement & Smart Contracts\n"
        "- LINK: Chainlink Oracoli e RWA\n"
        "- UNI: Uniswap Decentralized Exchange\n"
        "- AERO: Aerodrome Base Native DEX\n"
        "- USDC: Riserva di Liquidità / Cash Buffer\n\n"
        "Regole di allocazione:\n"
        "1. In Extreme Fear / Fear (<= 45): sovrappesa CBBTC e USDC per proteggere il capitale dai ribassi.\n"
        "2. In Greed / Extreme Greed (>= 56): espandi su DeFi/altcoin (LINK, UNI, AERO) o aumenta la cassa USDC per prendere profitto.\n"
        f"3. Ciascun asset deve avere un peso compreso tra {config.MIN_ASSET_WEIGHT:.2f} e {config.MAX_ASSET_WEIGHT:.2f}.\n"
        "4. La somma dei pesi deve essere esattamente 1.00 (100%).\n"
        "Rispondi ESCLUSIVAMENTE con un oggetto JSON valido:\n"
        "{\n"
        '  "target_weights": {"CBBTC": float, "WETH": float, "LINK": float, "UNI": float, "AERO": float, "USDC": float},\n'
        '  "rationale": "Breve sintesi della decisione in italiano (max 200 caratteri)"\n'
        "}"
    )

    try:
        client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            timeout=float(os.getenv("HTTP_TIMEOUT", 30)),
        )

        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Dati attuali di mercato:\n{json.dumps(prompt_data, indent=2)}\n\nRicalibra i pesi target."},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or ""
        cleaned = _clean_json(raw)
        data = json.loads(cleaned)

        raw_weights = data.get("target_weights", {})
        if not raw_weights or not isinstance(raw_weights, dict):
            logger.warning("Risposta AI priva di target_weights validi: uso fallback")
            return fallback

        # Validazione e clamping
        clamped = {}
        for sym in config.KNOWN_ASSETS:
            val = float(raw_weights.get(sym, config.MIN_ASSET_WEIGHT))
            val = max(config.MIN_ASSET_WEIGHT, min(config.MAX_ASSET_WEIGHT, val))
            clamped[sym] = val

        tot = sum(clamped.values())
        normalized = {k: round(v / tot, 4) for k, v in clamped.items()}

        rationale = str(data.get("rationale", "")).strip() or fallback["rationale"]
        return {
            "target_weights": normalized,
            "rationale": rationale,
            "source": f"openrouter_ai ({OPENROUTER_MODEL})"
        }

    except Exception as exc:
        logger.warning("Errore OpenRouter nella ricalibrazione pesi: %s (uso fallback)", exc)
        return fallback
