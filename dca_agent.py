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
