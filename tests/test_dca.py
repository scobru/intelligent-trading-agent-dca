"""
Unit tests per Intelligent Trading Agent - DCA & Portfolio Rebalancer.
Testa:
  - Parsing e normalizzazione pesi target
  - Logica moltiplicatore Fear & Greed
  - PortfolioTracker (calcolo pesi, drift, trigger di ribilanciamento)
  - PaperBook (simulazione swap, saldi, equity curve)
  - DcaManager (pianificazione ed esecuzione DCA & Rebalance in paper mode)
  - db_utils (salvataggio ed estrazione snapshot ed operazioni)
"""

import os
import sys
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import db_utils
import sentiment
from dca_manager import DcaManager
from paper import PaperBook
from portfolio import PortfolioTracker


class TestConfigAndWeights(unittest.TestCase):
    def test_parse_target_weights(self):
        # Test default normalization
        res = config._parse_target_weights("ETH:0.50,BTC:0.30,USDC:0.20")
        self.assertIn("WETH", res)
        self.assertIn("CBBTC", res)
        self.assertIn("USDC", res)
        self.assertAlmostEqual(sum(res.values()), 1.0, places=3)
        self.assertEqual(res["WETH"], 0.50)
        self.assertEqual(res["CBBTC"], 0.30)
        self.assertEqual(res["USDC"], 0.20)

    def test_parse_target_weights_unnormalized(self):
        res = config._parse_target_weights("WETH:50,USDC:50")
        self.assertEqual(res["WETH"], 0.50)
        self.assertEqual(res["USDC"], 0.50)

    def test_parse_target_weights_invalid(self):
        res = config._parse_target_weights("INVALID_FORMAT")
        self.assertIn("WETH", res)
        self.assertEqual(res["WETH"], 0.25)


class TestSentiment(unittest.TestCase):
    def test_calculate_dca_multiplier(self):
        self.assertEqual(sentiment.calculate_dca_multiplier(10), config.MULTIPLIER_EXTREME_FEAR)
        self.assertEqual(sentiment.calculate_dca_multiplier(25), config.MULTIPLIER_EXTREME_FEAR)
        self.assertEqual(sentiment.calculate_dca_multiplier(35), config.MULTIPLIER_FEAR)
        self.assertEqual(sentiment.calculate_dca_multiplier(45), config.MULTIPLIER_FEAR)
        self.assertEqual(sentiment.calculate_dca_multiplier(50), config.MULTIPLIER_NEUTRAL)
        self.assertEqual(sentiment.calculate_dca_multiplier(60), config.MULTIPLIER_GREED)
        self.assertEqual(sentiment.calculate_dca_multiplier(75), config.MULTIPLIER_GREED)
        self.assertEqual(sentiment.calculate_dca_multiplier(85), config.MULTIPLIER_EXTREME_GREED)

    def test_get_fear_and_greed_structure(self):
        fg = sentiment.get_fear_and_greed(force=True)
        self.assertIn("value", fg)
        self.assertIn("classification", fg)
        self.assertIn("dca_multiplier", fg)
        self.assertGreaterEqual(fg["value"], 0)
        self.assertLessEqual(fg["value"], 100)


class TestPortfolioTracker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test_portfolio.json")
        self.client_mock = MagicMock()
        self.uniswap_mock = MagicMock()
        self.tracker = PortfolioTracker(self.client_mock, self.uniswap_mock, state_path=self.state_file)
        self.p_weights = patch.dict("config.TARGET_WEIGHTS", {"WETH": 0.50, "CBBTC": 0.30, "USDC": 0.20}, clear=True)
        self.p_weights.start()

    def tearDown(self):
        self.p_weights.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_evaluate_balanced(self):
        balances = {"WETH": 2.0, "CBBTC": 0.05, "USDC": 2000.0}
        prices = {"WETH": 2500.0, "CBBTC": 60000.0, "USDC": 1.0}

        eval_res = self.tracker.evaluate(balances, prices)
        self.assertEqual(eval_res["total_value_usd"], 10000.0)
        self.assertFalse(eval_res["needs_rebalance"])
        self.assertEqual(len(eval_res["overweight_symbols"]), 0)
        self.assertEqual(len(eval_res["underweight_symbols"]), 0)

    def test_evaluate_unbalanced(self):
        balances = {"WETH": 2.0, "CBBTC": 0.05, "USDC": 2000.0}
        prices = {"WETH": 5000.0, "CBBTC": 60000.0, "USDC": 1.0}

        eval_res = self.tracker.evaluate(balances, prices)
        self.assertTrue(eval_res["needs_rebalance"])
        self.assertIn("WETH", eval_res["overweight_symbols"])
        self.assertIn("CBBTC", eval_res["underweight_symbols"])
        self.assertIn("USDC", eval_res["underweight_symbols"])


class TestPaperBook(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test_paper.json")
        self.paper = PaperBook(path=self.state_file, start_usdc=1000.0, start_eth=0.1)
        self.prices = {
            "USDC": 1.0, "WETH": 2000.0, "CBBTC": 60000.0, "ETH": 2000.0,
            "LINK": 15.0, "UNI": 10.0, "AERO": 1.0,
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initial_state(self):
        self.assertEqual(self.paper.balances.get("USDC"), 1000.0)
        self.assertEqual(self.paper.balances.get("ETH"), 0.1)
        self.assertEqual(self.paper.balances.get("WETH"), 0.0)

    def test_execute_swap(self):
        swap_res = self.paper.execute_swap(
            token_in="USDC",
            token_out="WETH",
            amount_in=100.0,
            prices=self.prices,
            slippage_bps=50,
        )
        self.assertEqual(swap_res["status"], "success")
        self.assertAlmostEqual(self.paper.balances.get("USDC"), 900.0, places=2)
        self.assertGreater(self.paper.balances.get("WETH"), 0.049)
        self.assertEqual(len(self.paper.state["trades"]), 1)

    def test_insufficient_balance(self):
        self.paper.balances["USDC"] = 0.0
        swap_res = self.paper.execute_swap(
            token_in="USDC",
            token_out="WETH",
            amount_in=5000.0,
            prices=self.prices,
        )
        self.assertEqual(swap_res["status"], "rejected")


class TestDcaManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "test_portfolio.json")
        self.paper_file = os.path.join(self.temp_dir, "test_paper.json")

        self.client_mock = MagicMock()
        self.client_mock.address = "0x1111111111111111111111111111111111111111"
        self.client_mock.balance_of_float.return_value = 0.0
        self.client_mock.eth_balance.return_value = 0.0

        self.p_port = patch("config.PORTFOLIO_PATH", self.state_file)
        self.p_paper = patch("config.PAPER_STATE_PATH", self.paper_file)
        self.p_pt = patch("config.PAPER_TRADING", True)
        self.p_port.start()
        self.p_paper.start()
        self.p_pt.start()

        self.manager = DcaManager(self.client_mock)
        self.manager._prices = {
            "USDC": 1.0, "WETH": 2500.0, "CBBTC": 60000.0, "ETH": 2500.0,
            "LINK": 15.0, "UNI": 10.0, "AERO": 1.0,
        }

    def tearDown(self):
        self.p_port.stop()
        self.p_paper.stop()
        self.p_pt.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_plan_dca(self):
        status = self.manager.get_status()
        status["sentiment"] = {"value": 20, "dca_multiplier": 1.50, "classification": "Extreme Fear"}
        plan = self.manager.plan_dca(status)

        self.assertIsNotNone(plan)
        self.assertAlmostEqual(plan["total_usd"], 50.0 * 1.50, places=2)  # 75 USD
        self.assertGreater(len(plan["purchases"]), 0)

    def test_execute_dca_in_paper(self):
        status = self.manager.get_status()
        status["sentiment"] = {"value": 25, "dca_multiplier": 1.50, "classification": "Extreme Fear"}
        plan = self.manager.plan_dca(status)

        res = self.manager.execute_action(plan, status)
        self.assertEqual(res["status"], "success")
        self.assertGreater(len(res["purchases"]), 0)

        # Verifica saldi paper
        self.assertGreater(self.manager.paper.balances.get("WETH", 0.0), 0.0)
        self.assertLess(self.manager.paper.balances.get("USDC", 0.0), 1000.0)

    def test_plan_and_execute_rebalance(self):
        self.manager.paper.balances["WETH"] = 2.0
        self.manager.paper.balances["USDC"] = 100.0
        self.manager.paper.balances["CBBTC"] = 0.0

        status = self.manager.get_status()
        plan = self.manager.plan_rebalance(status)

        self.assertIsNotNone(plan)
        self.assertEqual(plan["operation"], "rebalance")
        self.assertEqual(plan["from_asset"], "WETH")

        res = self.manager.execute_action(plan, status)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["operation"], "rebalance")


class TestDatabaseUtils(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.temp_dir, "test_dca.db")
        self.p_db = patch("config.SQLITE_DB_PATH", self.db_file)
        self.p_db.start()
        db_utils.init_db()

    def tearDown(self):
        self.p_db.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_snapshot_and_get_recent(self):
        status = {
            "total_value_usd": 12500.0,
            "balances": {"WETH": 2.5, "USDC": 3000.0, "CBBTC": 0.05},
            "portfolio": {
                "assets": {
                    "WETH": {"current_weight": 0.50},
                    "USDC": {"current_weight": 0.24},
                    "CBBTC": {"current_weight": 0.26},
                }
            },
            "sentiment": {"value": 30, "classification": "Fear"},
        }
        db_utils.log_snapshot(status)
        recent = db_utils.get_recent_snapshots(limit=5)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["total_value_usd"], 12500.0)
        self.assertEqual(recent[0]["fng_value"], 30)

    def test_log_operation_and_get_recent(self):
        action = {
            "operation": "dca",
            "total_usd": 75.0,
            "reason": "DCA schedulato Fear 1.5x",
        }
        result = {
            "status": "success",
            "total_spent_usd": 75.0,
        }
        db_utils.log_operation(action, result)
        ops = db_utils.get_recent_operations(limit=5)
    def test_release_funds_paper(self):
        client = MagicMock()
        client.balance_of_float.return_value = 0.0
        client.eth_balance.return_value = 0.0
        manager = DcaManager(client=client)
        manager._prices = {
            "USDC": 1.0, "WETH": 2500.0, "CBBTC": 60000.0, "ETH": 2500.0,
            "LINK": 15.0, "UNI": 10.0, "AERO": 1.0,
        }
        # In paper mode with initial balance
        res = manager.release_funds(target_usdc=50.0)
        self.assertIn(res.get("status"), ["success", "no_action"])
        self.assertIn("released_usd", res)


if __name__ == "__main__":
    unittest.main()

