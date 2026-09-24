"""
Paper trading per il bot DCA / Ribilanciatore.

Mantiene un portafoglio virtuale (USDC, WETH, CBBTC, ETH),
simula gli swap su Uniswap V3 applicando costi gas e slippage realistici,
e calcola l'equity curve e il PnL nel tempo.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


class PaperBook:
    def __init__(self, path: str = None, start_usdc: float = None, start_eth: float = None):
        self.path = path or config.PAPER_STATE_PATH
        self.start_usdc = config.PAPER_START_USDC if start_usdc is None else start_usdc
        self.start_eth = config.PAPER_START_ETH if start_eth is None else start_eth
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.state = json.load(fh) or {}
        except (OSError, ValueError):
            self.state = {}
        if not self.state:
            self.state = {
                "initial_usdc": float(self.start_usdc),
                "initial_eth": float(self.start_eth),
                "balances": {
                    "USDC": float(self.start_usdc),
                    "WETH": 0.0,
                    "CBBTC": 0.0,
                    "ETH": float(self.start_eth),
                },
                "trades": [],
                "gas_spent_usd": 0.0,
                "operations": 0,
                "created_at": time.time(),
            }
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, indent=2)
        except OSError as exc:
            logger.warning("Impossibile salvare %s: %s", self.path, exc)

    @property
    def balances(self) -> Dict[str, float]:
        return self.state.setdefault("balances", {})

    def get_balances_with_eth(self) -> Dict[str, float]:
        """Restituisce i saldi unificando WETH ed ETH nativo."""
        b = dict(self.balances)
        # Inclusione di ETH nativo (spendibile oltre riserva)
        weth_total = float(b.get("WETH", 0.0))
        return {
            "USDC": float(b.get("USDC", 0.0)),
            "WETH": weth_total,
            "CBBTC": float(b.get("CBBTC", 0.0)),
            "ETH": float(b.get("ETH", 0.0)),
        }

    def execute_swap(self, token_in: str, token_out: str, amount_in: float,
                     prices: Dict[str, float], slippage_bps: int = None,
                     reason: str = "") -> Dict[str, Any]:
        """
        Simula uno swap su Uniswap V3.
        """
        slippage_bps = config.DEFAULT_SLIPPAGE_BPS if slippage_bps is None else slippage_bps
        p_in = float(prices.get(token_in, 1.0 if token_in == "USDC" else 0.0))
        p_out = float(prices.get(token_out, 1.0 if token_out == "USDC" else 0.0))

        if p_in <= 0 or p_out <= 0:
            raise ValueError(f"Prezzi non validi per swap {token_in} -> {token_out}")

        curr_bal = float(self.balances.get(token_in, 0.0))
        amount_in = min(amount_in, curr_bal)
        if amount_in <= 0:
            return {"status": "rejected", "reason": f"Saldo {token_in} insufficiente ({curr_bal:.6f})"}

        # Valore in USD dello swap
        val_usd = amount_in * p_in

        # Costo gas e slippage
        gas_cost = config.PAPER_GAS_USD
        slip_pct = slippage_bps / 10_000.0
        amount_out_gross = (val_usd / p_out)
        amount_out_net = amount_out_gross * (1.0 - slip_pct)

        # Aggiorna saldi
        self.balances[token_in] = float(self.balances.get(token_in, 0.0)) - amount_in
        self.balances[token_out] = float(self.balances.get(token_out, 0.0)) + amount_out_net
        self.state["gas_spent_usd"] = float(self.state.get("gas_spent_usd", 0.0)) + gas_cost
        self.state["operations"] = int(self.state.get("operations", 0)) + 1

        trade = {
            "time": time.time(),
            "from": token_in,
            "to": token_out,
            "amount_in": round(amount_in, 6),
            "amount_out": round(amount_out_net, 6),
            "value_usd": round(val_usd, 4),
            "gas_usd": gas_cost,
            "reason": reason,
        }
        self.state.setdefault("trades", []).append(trade)
        self.save()

        return {
            "status": "success",
            "from": token_in,
            "to": token_out,
            "amount_in": round(amount_in, 6),
            "amount_out": round(amount_out_net, 6),
            "value_usd": round(val_usd, 4),
            "gas_cost_usd": gas_cost,
        }

    def total_value_usd(self, prices: Dict[str, float]) -> float:
        total = 0.0
        for sym, qty in self.balances.items():
            price = prices.get(sym, 1.0 if sym == "USDC" else 0.0)
            total += qty * price
        return total

    def summary(self, prices: Dict[str, float]) -> Dict[str, Any]:
        curr_val = self.total_value_usd(prices)
        init_val = float(self.state.get("initial_usdc", self.start_usdc))
        pnl = curr_val - init_val
        pnl_pct = (pnl / init_val * 100.0) if init_val > 0 else 0.0
        return {
            "initial_usdc": init_val,
            "current_value_usd": round(curr_val, 4),
            "pnl_usd": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
            "gas_spent_usd": round(float(self.state.get("gas_spent_usd", 0.0)), 4),
            "operations": int(self.state.get("operations", 0)),
            "created_at": self.state.get("created_at"),
            "trades": list(self.state.get("trades", []))[-20:],
        }
