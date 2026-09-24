"""
Modulo di analisi del sentiment di mercato per il bot DCA.

Interroga l'indice Crypto Fear & Greed (alternative.me) e calcola il
moltiplicatore dinamico dell'importo DCA:
  - Extreme Fear (<=25): aumenta l'accumulo (+50%)
  - Fear (26-45): aumenta leggermente (+25%)
  - Neutral (46-55): acquisto standard (1.0x)
  - Greed (56-75): riduce l'acquisto (-25%)
  - Extreme Greed (>=76): riduce fortemente (-50%)
"""

import logging
import time
from typing import Any, Dict

import config
import http_client

logger = logging.getLogger(__name__)

_cache: Dict[str, Any] = {"time": 0.0, "data": None}


def calculate_dca_multiplier(fng_value: int) -> float:
    """Calcola il moltiplicatore dell'importo DCA dato il valore di Fear & Greed (0-100)."""
    if not config.FEAR_GREED_ENABLED:
        return 1.0

    val = max(0, min(100, int(fng_value)))
    if val <= 25:
        return config.MULTIPLIER_EXTREME_FEAR
    if val <= 45:
        return config.MULTIPLIER_FEAR
    if val <= 55:
        return config.MULTIPLIER_NEUTRAL
    if val <= 75:
        return config.MULTIPLIER_GREED
    return config.MULTIPLIER_EXTREME_GREED


def get_fear_and_greed(force: bool = False) -> Dict[str, Any]:
    """
    Recupera l'indice Crypto Fear & Greed aggiornato.
    Usa cache interna di 30 minuti per evitare chiamate ripetute.
    """
    now = time.time()
    if not force and _cache["data"] and now - _cache["time"] < 1800:
        return _cache["data"]

    fallback = {
        "value": 50,
        "classification": "Neutral",
        "dca_multiplier": 1.0,
        "timestamp": int(now),
        "source": "fallback",
    }

    if not config.FEAR_GREED_ENABLED:
        return fallback

    try:
        data = http_client.get_json(config.FEAR_GREED_API_URL, timeout=config.HTTP_TIMEOUT, cache_ttl=900)
        if data and isinstance(data, dict) and "data" in data and len(data["data"]) > 0:
            entry = data["data"][0]
            val = int(entry.get("value", 50))
            classification = str(entry.get("value_classification", "Neutral"))
            multiplier = calculate_dca_multiplier(val)
            ts = int(entry.get("timestamp", int(now)))
            result = {
                "value": val,
                "classification": classification,
                "dca_multiplier": round(multiplier, 2),
                "timestamp": ts,
                "source": "alternative.me",
            }
            _cache.update({"time": now, "data": result})
            return result
    except Exception as exc:
        logger.warning("Impossibile recuperare Fear & Greed: %s (uso fallback)", exc)

    return fallback


def format_sentiment_for_prompt(sentiment: Dict[str, Any]) -> str:
    """Formatta i dati di sentiment per il prompt decisionale del modello."""
    val = sentiment.get("value", 50)
    cls = sentiment.get("classification", "Neutral")
    mult = sentiment.get("dca_multiplier", 1.0)
    return (
        f"Crypto Fear & Greed Index: {val}/100 ({cls})\n"
        f"DCA Multiplier calcolato: {mult:.2f}x "
        f"({'accumulo aggressivo' if mult > 1.2 else 'accumulo ridotto' if mult < 0.9 else 'accumulo standard'})"
    )
