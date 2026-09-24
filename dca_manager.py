"""
Esecutore e gestore delle operazioni DCA e di Ribilanciamento su Base.

Verifica le barriere di sicurezza, calcola le allocazioni ottimali,
pianifica ed esegue gli swap su Uniswap V3 sia in modalita' Paper, Dry-run che Live.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import config
import sentiment
from base_client import BaseChainError, BaseClient
from paper import PaperBook
from portfolio import PortfolioTracker
from uniswap import UniswapV3

logger = logging.getLogger(__name__)


class DcaManager:
    def __init__(self, client: BaseClient):
        self.client = client
        self.uniswap = UniswapV3(client)
        self.paper = PaperBook() if config.PAPER_TRADING else None
        self.tracker = PortfolioTracker(client, self.uniswap)
        self._prices: Optional[Dict[str, float]] = None

    # ------------------------------------------------------------ prezzi
    def prices(self) -> Dict[str, float]:
        if self._prices is None:
            self._prices = self.tracker.get_prices()
        return self._prices

    # ------------------------------------------------------------ auto-refuel
    def ensure_usdc_balance(self) -> Optional[Dict[str, Any]]:
        if self.paper or not self.client:
            return None
        return self.uniswap.auto_refuel_usdc()

    # ------------------------------------------------------------ saldi & stato
    def get_status(self) -> Dict[str, Any]:
        prices = self.prices()
        if self.paper:
            balances = self.paper.get_balances_with_eth()
            mode = "paper"
        else:
            balances = {
                "USDC": self.client.balance_of_float(config.USDC),
                "WETH": self.client.balance_of_float(config.WETH),
                "CBBTC": self.client.balance_of_float(config.CBBTC),
                "ETH": self.client.eth_balance(),
            }
            # Se ci sono altri asset noti
            for sym, meta in config.KNOWN_ASSETS.items():
                if sym not in balances:
                    balances[sym] = self.client.balance_of_float(meta["address"])
            mode = "dry_run" if config.DRY_RUN else "live"

        eval_data = self.tracker.evaluate(balances, prices)
        fng = sentiment.get_fear_and_greed()

        # DCA eligibility check
        now = time.time()
        time_since_dca = now - float(eval_data.get("last_dca_time", 0.0))
        dca_due = config.DCA_ENABLED and (time_since_dca >= config.DCA_INTERVAL_HOURS * 3600.0)

        # Rebalance eligibility check
        time_since_reb = now - float(eval_data.get("last_rebalance_time", 0.0))
        reb_due = config.REBALANCE_ENABLED and eval_data["needs_rebalance"] and (
            time_since_reb >= config.MIN_HOLD_HOURS_BETWEEN_REBALANCE * 3600.0
        )

        status = {
            "wallet": self.client.address if self.client else "",
            "mode": mode,
            "total_value_usd": eval_data["total_value_usd"],
            "balances": balances,
            "prices": prices,
            "portfolio": eval_data,
            "sentiment": fng,
            "dca_due": dca_due,
            "rebalance_due": reb_due,
            "hours_since_last_dca": round(time_since_dca / 3600.0, 1) if eval_data.get("last_dca_time") else None,
            "hours_since_last_rebalance": round(time_since_reb / 3600.0, 1) if eval_data.get("last_rebalance_time") else None,
        }

        if self.paper:
            status["paper"] = self.paper.summary(prices)

        return status

    # ------------------------------------------------------------ pianificazione DCA
    def plan_dca(self, status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Calcola la proposta di acquisto DCA modulata da Fear & Greed.
        """
        if not config.DCA_ENABLED:
            return None

        fng = status.get("sentiment") or {}
        multiplier = float(fng.get("dca_multiplier", 1.0))
        base_amt = config.DCA_BASE_AMOUNT_USD
        total_dca_usd = round(base_amt * multiplier, 2)

        usdc_avail = float(status["balances"].get("USDC", 0.0))
        if usdc_avail < total_dca_usd:
            # Riduci a quanto disponibile oltre 1 dollaro di sicurezza
            total_dca_usd = max(0.0, round(usdc_avail - 1.0, 2))

        if total_dca_usd < config.MIN_REBALANCE_USD:
            return None

        # Ripartisci tra gli asset non-USDC target in base ai pesi o al drift
        target_assets = {k: v for k, v in config.TARGET_WEIGHTS.items() if k != "USDC"}
        sum_targets = sum(target_assets.values())
        if sum_targets <= 0:
            return None

        purchases = []
        for sym, weight in target_assets.items():
            alloc_pct = weight / sum_targets
            amt_usd = round(total_dca_usd * alloc_pct, 2)
            if amt_usd >= config.MIN_REBALANCE_USD:
                purchases.append({"asset": sym, "amount_usd": amt_usd})

        if not purchases:
            return None

        return {
            "operation": "dca",
            "total_usd": total_dca_usd,
            "multiplier": multiplier,
            "fear_greed": fng.get("value"),
            "classification": fng.get("classification"),
            "purchases": purchases,
            "reason": (
                f"DCA schedulato: ${total_dca_usd:.2f} USDC ({multiplier:.2f}x F&G {fng.get('value')}) "
                f"ripartito tra {', '.join(p['asset'] for p in purchases)}"
            ),
        }

    # ------------------------------------------------------------ pianificazione ribilanciamento
    def plan_rebalance(self, status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Identifica l'asset piu' sovrappesato e lo riequilibra verso il piu' sottopesato.
        """
        if not config.REBALANCE_ENABLED:
            return None

        portfolio = status.get("portfolio") or {}
        if not portfolio.get("needs_rebalance"):
            return None

        assets = portfolio.get("assets", {})
        # Trova asset con max drift positivo e max drift negativo
        overweight = max(assets.values(), key=lambda a: a.get("drift_pct", 0.0))
        underweight = min(assets.values(), key=lambda a: a.get("drift_pct", 0.0))

        if overweight["drift_pct"] < config.REBALANCE_THRESHOLD_PCT or underweight["drift_pct"] > -config.REBALANCE_THRESHOLD_PCT:
            return None

        # Importo da riequilibrare (metà della discrepanza o cap massimo)
        amount_usd = min(
            abs(overweight["diff_usd"]),
            abs(underweight["diff_usd"]),
            config.MAX_REBALANCE_USD,
        )

        if amount_usd < config.MIN_REBALANCE_USD:
            return None

        return {
            "operation": "rebalance",
            "from_asset": overweight["symbol"],
            "to_asset": underweight["symbol"],
            "amount_usd": round(amount_usd, 2),
            "from_drift_pct": overweight["drift_pct"],
            "to_drift_pct": underweight["drift_pct"],
            "reason": (
                f"Ribilanciamento drift: vendo ${amount_usd:.2f} di {overweight['symbol']} "
                f"({overweight['drift_pct']:+.1f}%) e compro {underweight['symbol']} ({underweight['drift_pct']:+.1f}%)"
            ),
        }

    # ------------------------------------------------------------ esecuzione
    def execute_action(self, action: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
        op = str(action.get("operation", "hold")).lower()
        if op == "hold":
            return {"status": "hold", "operation": "hold", "reason": action.get("reason", "Nessuna azione richiesta")}

        if op == "dca":
            return self._execute_dca(action, status)
        elif op == "rebalance":
            return self._execute_rebalance(action, status)

        return {"status": "rejected", "operation": op, "reason": f"Operazione '{op}' non supportata"}

    def _execute_dca(self, action: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
        purchases = action.get("purchases", [])
        prices = status.get("prices") or self.prices()
        results = []
        total_spent = 0.0

        for item in purchases:
            target_sym = item["asset"]
            amt_usd = float(item["amount_usd"])
            if self.paper:
                res = self.paper.execute_swap("USDC", target_sym, amt_usd, prices, reason=action.get("reason", ""))
                results.append(res)
                if res.get("status") == "success":
                    total_spent += amt_usd
            elif config.DRY_RUN:
                plan = f"swap ${amt_usd:.2f} USDC -> {target_sym} su Uniswap V3"
                results.append({"status": "dry_run", "plan": plan, "target": target_sym, "amount_usd": amt_usd})
                total_spent += amt_usd
            else:
                # Live swap
                token_out_addr = config.KNOWN_ASSETS[target_sym]["address"]
                amt_raw = int(amt_usd * 1e6)
                route = self.uniswap.best_route(config.USDC, token_out_addr, amt_raw)
                if not route:
                    results.append({"status": "error", "reason": f"Nessuna rotta Uniswap USDC -> {target_sym}"})
                    continue
                tx = self.uniswap.swap(route, config.DEFAULT_SLIPPAGE_BPS)
                results.append({"status": "success", "tx": tx, "target": target_sym, "amount_usd": amt_usd})
                total_spent += amt_usd

        self.tracker.record_dca(total_spent)
        return {
            "status": "success" if total_spent > 0 else "noop",
            "operation": "dca",
            "total_spent_usd": round(total_spent, 2),
            "purchases": results,
            "reason": action.get("reason", ""),
        }

    def _execute_rebalance(self, action: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
        from_sym = action["from_asset"]
        to_sym = action["to_asset"]
        amt_usd = float(action["amount_usd"])
        prices = status.get("prices") or self.prices()

        p_from = prices.get(from_sym, 1.0 if from_sym == "USDC" else 0.0)
        if p_from <= 0:
            return {"status": "error", "reason": f"Prezzo di {from_sym} non disponibile"}

        qty_from = amt_usd / p_from

        if self.paper:
            res = self.paper.execute_swap(from_sym, to_sym, qty_from, prices, reason=action.get("reason", ""))
            self.tracker.record_rebalance()
            return dict(res, operation="rebalance", reason=action.get("reason", ""))

        if config.DRY_RUN:
            plan = f"swap {qty_from:.6f} {from_sym} (${amt_usd:.2f}) -> {to_sym} su Uniswap V3"
            self.tracker.record_rebalance()
            return {"status": "dry_run", "operation": "rebalance", "plan": plan, "reason": action.get("reason", "")}

        # Live swap
        token_in_addr = config.KNOWN_ASSETS[from_sym]["address"]
        token_out_addr = config.KNOWN_ASSETS[to_sym]["address"]
        decimals_in = config.KNOWN_ASSETS[from_sym]["decimals"]
        amount_raw = int(qty_from * (10 ** decimals_in))

        route = self.uniswap.best_route(token_in_addr, token_out_addr, amount_raw)
        if not route:
            return {"status": "error", "reason": f"Nessuna rotta Uniswap {from_sym} -> {to_sym}"}

        tx = self.uniswap.swap(route, config.DEFAULT_SLIPPAGE_BPS)
        self.tracker.record_rebalance()
        return {
            "status": "success",
            "operation": "rebalance",
            "from_asset": from_sym,
            "to_asset": to_sym,
            "amount_usd": amt_usd,
            "transactions": [tx],
            "reason": action.get("reason", ""),
        }
