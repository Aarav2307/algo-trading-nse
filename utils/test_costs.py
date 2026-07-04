"""
Unit tests for utils/costs.py — 7 tests covering the corrected Zerodha cost model.
Run: python utils/test_costs.py
"""

import sys
import unittest

sys.path.insert(0, ".")
from utils.costs import transaction_costs, transaction_cost_breakdown


class TestTransactionCosts(unittest.TestCase):

    def test1_delivery_buy_stt_on_10k(self):
        """Delivery buy STT must be >= ₹10 on ₹10,000 turnover (0.1% both sides)."""
        bd = transaction_cost_breakdown(10000, 1, "buy", "delivery")
        self.assertGreaterEqual(bd["stt"], 10.0, f"STT was {bd['stt']:.4f}")

    def test2_delivery_sell_dp_charge(self):
        """Delivery sell must include DP charge >= ₹15.34."""
        bd = transaction_cost_breakdown(10000, 1, "sell", "delivery")
        self.assertGreaterEqual(bd["dp_charge"], 15.34, f"DP was {bd['dp_charge']:.4f}")

    def test3_delivery_buy_no_brokerage(self):
        """Delivery buy total must be < ₹30 — no ₹20 flat brokerage."""
        cost = transaction_costs(10000, 1, "buy", "delivery")
        self.assertLess(cost, 30.0, f"Delivery buy cost was {cost:.4f}")

    def test4_intraday_sell_stt(self):
        """Intraday sell STT must be >= ₹2.50 on ₹10,000 (0.025% sell only)."""
        bd = transaction_cost_breakdown(10000, 1, "sell", "intraday")
        self.assertGreaterEqual(bd["stt"], 2.50, f"Intraday STT was {bd['stt']:.4f}")

    def test5_intraday_buy_less_than_sell(self):
        """Intraday buy < intraday sell because STT only applies on the sell side."""
        buy  = transaction_costs(10000, 1, "buy",  "intraday")
        sell = transaction_costs(10000, 1, "sell", "intraday")
        self.assertLess(buy, sell, f"buy={buy:.4f} sell={sell:.4f}")

    def test6_delivery_sell_greater_than_buy(self):
        """Delivery sell > delivery buy because of the ₹15.34 DP charge."""
        buy  = transaction_costs(10000, 1, "buy",  "delivery")
        sell = transaction_costs(10000, 1, "sell", "delivery")
        self.assertGreater(sell, buy, f"buy={buy:.4f} sell={sell:.4f}")

    def test7_same_turnover_same_cost(self):
        """Same turnover must produce identical cost regardless of price/shares split."""
        cost_a = transaction_costs(10000, 1, "buy", "delivery")
        cost_b = transaction_costs(5000,  2, "buy", "delivery")
        cost_c = transaction_costs(100,  100, "buy", "delivery")
        self.assertAlmostEqual(cost_a, cost_b, places=6,
                               msg=f"10000×1={cost_a:.6f} vs 5000×2={cost_b:.6f}")
        self.assertAlmostEqual(cost_a, cost_c, places=6,
                               msg=f"10000×1={cost_a:.6f} vs 100×100={cost_c:.6f}")


if __name__ == "__main__":
    loader  = unittest.TestLoader()
    loader.sortTestMethodsUsing = None
    suite   = loader.loadTestsFromTestCase(TestTransactionCosts)
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
