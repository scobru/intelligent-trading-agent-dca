"""
Gestione e analisi della composizione del portafoglio per il bot DCA / Ribilanciatore.

Calcola:
  - Valore USD per ciascun asset e totale portafoglio;
  - Pesi attuali vs pesi target;
  - Scostamento (drift) percentuale e controvalore in USD;
  - Individuazione degli asset sovrappesati (da vendere) e sottopesati (da comprare).
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import config
from base_client import BaseClient
from uniswap import UniswapV3

logger = logging.getLogger(__name__)


class PortfolioTracker:
    def __init__(self, client: BaseClient, uniswap: Optional[UniswapV3] = None, state_path: str = None):
        self.client = client
        self.uniswap = uniswap or UniswapV3(client)
        self.state_path = state_path or config.PORTFOLIO_PATH
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self):
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                self.state = json.load(fh) or {}
        except (OSError, ValueError):
            self.state = {}
        if not self.state:
            self.state = {
                "created_at": time.time(),
                "last_dca_time": 0.0,
                "last_rebalance_time": 0.0,
                "dca_count": 0,
                "rebalance_count": 0,
                "total_dca_spent_usd": 0.0,
            }
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, indent=2)
        except OSError as exc:
            logger.warning("Impossibile salvare %s: %s", self.state_path, exc)

    def record_dca(self, spent_usd: float):
        self.state["last_dca_time"] = time.time()
        self.state["dca_count"] = int(self.state.get("dca_count", 0)) + 1
        self.state["total_dca_spent_usd"] = float(self.state.get("total_dca_spent_usd", 0.0)) + float(spent_usd)
        self.save()

    def record_rebalance(self):
        self.state["last_rebalance_time"] = time.time()
        self.state["rebalance_count"] = int(self.state.get("rebalance_count", 0)) + 1
        self.save()

    def get_prices(self) -> Dict[str, float]:
        """Prezzi USD dei token target (USDC = 1.0)."""
        prices = {"USDC": 1.0}
        # Prezza prima WETH
        try:
            weth_price = self.uniswap.price_in_quote(config.WETH, config.USDC, probe_units=0.01)
            if weth_price:
                prices["WETH"] = weth_price
                prices["ETH"] = weth_price
        except Exception as exc:
            logger.warning("Prezzo WETH non disponibile: %s", exc)

        for sym, meta in config.KNOWN_ASSETS.items():
            if sym in prices:
                continue
            try:
                probe = 0.0001 if sym in ("CBBTC", "BTC") else (5.0 if sym == "AERO" else 0.01)
                price = self.uniswap.price_in_quote(meta["address"], config.USDC, probe_units=probe)
                if price:
                    prices[sym] = price
            except Exception as exc:
                logger.warning("Prezzo %s non disponibile: %s", sym, exc)

        return prices

    def evaluate(self, balances: Dict[str, float], prices: Dict[str, float]) -> Dict[str, Any]:
        """
        Analizza i saldi attuali, confronta con i pesi target e calcola il drift.
        `balances`: dizionario simbolo -> quantita' (float)
        `prices`: dizionario simbolo -> prezzo USD (float)
        """
        assets_data = {}
        total_value_usd = 0.0

        try:
            gas_eth_qty = float(balances.get("ETH", 0.0))
        except (TypeError, ValueError):
            gas_eth_qty = 0.0
        try:
            eth_price = float(prices.get("ETH", prices.get("WETH", 0.0)))
        except (TypeError, ValueError):
            eth_price = 0.0
        gas_eth_val = gas_eth_qty * eth_price

        target_weights = dict(config.TARGET_WEIGHTS)

        # Includi tutti gli asset target e gli asset detenuti con saldo significativo (> $0.10)
        # Esclude ETH nativo se non ha un peso target specifico (riservato al gas)
        all_symbols = set(target_weights.keys())
        for sym, qty in balances.items():
            if sym == "ETH" and target_weights.get("ETH", 0.0) <= 0:
                continue
            try:
                qty_f = float(qty)
                p_f = float(prices.get(sym, 1.0 if sym == "USDC" else 0.0))
                if (qty_f * p_f) >= 0.10:
                    all_symbols.add(sym)
            except (TypeError, ValueError):
                continue

        for sym in all_symbols:
            try:
                qty = float(balances.get(sym, 0.0))
            except (TypeError, ValueError):
                qty = 0.0
            try:
                price = float(prices.get(sym, 1.0 if sym == "USDC" else 0.0))
            except (TypeError, ValueError):
                price = 0.0
            val_usd = qty * price
            total_value_usd += val_usd
            assets_data[sym] = {
                "symbol": sym,
                "amount": round(qty, 6),
                "price_usd": round(price, 4),
                "value_usd": round(val_usd, 4),
                "target_weight": target_weights.get(sym, 0.0),
                "category": config.KNOWN_ASSETS.get(sym, {}).get("category", "Asset"),
            }

        # Calcolo quote e drift
        needs_rebalance = False
        overweight = []
        underweight = []

        for sym, data in assets_data.items():
            curr_weight = (data["value_usd"] / total_value_usd) if total_value_usd > 0 else 0.0
            drift_pct = (curr_weight - data["target_weight"]) * 100.0  # punti percentuali
            diff_usd = (data["target_weight"] - curr_weight) * total_value_usd  # >0: comprare, <0: vendere

            data["current_weight"] = round(curr_weight, 4)
            data["drift_pct"] = round(drift_pct, 2)
            data["diff_usd"] = round(diff_usd, 2)

            if abs(drift_pct) >= config.REBALANCE_THRESHOLD_PCT and abs(diff_usd) >= config.MIN_REBALANCE_USD:
                needs_rebalance = True
                if drift_pct > 0:
                    overweight.append(sym)
                else:
                    underweight.append(sym)

        # Ordina per scostamento decrescente
        sorted_assets = sorted(assets_data.values(), key=lambda a: a["target_weight"], reverse=True)

        return {
            "total_value_usd": round(total_value_usd, 4),
            "assets": {a["symbol"]: a for a in sorted_assets},
            "gas_eth": {
                "amount": round(gas_eth_qty, 6),
                "price_usd": round(eth_price, 2),
                "value_usd": round(gas_eth_val, 2),
            },
            "needs_rebalance": needs_rebalance,
            "overweight_symbols": overweight,
            "underweight_symbols": underweight,
            "target_weights": target_weights,
            "last_dca_time": self.state.get("last_dca_time", 0.0),
            "last_rebalance_time": self.state.get("last_rebalance_time", 0.0),
            "dca_count": self.state.get("dca_count", 0),
            "rebalance_count": self.state.get("rebalance_count", 0),
            "total_dca_spent_usd": round(self.state.get("total_dca_spent_usd", 0.0), 2),
        }
