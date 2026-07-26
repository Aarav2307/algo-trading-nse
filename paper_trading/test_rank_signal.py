"""
paper_trading/test_rank_signal.py — Regression tests for rank_signal().

Root problem this closes: rank_signal() used to reimplement
screener.auto_screener.compute_hurst()'s variogram estimator inline, byte-
for-byte identical to the algorithm _process_stock() already calls via the
imported compute_hurst() moments earlier on the same df -- meaning Hurst was
computed twice per stock, per run, by two independently-maintained copies of
the same formula with zero structural guarantee they'd stay in sync.

Fix: rank_signal() now takes `hurst` as a parameter (matching the existing
convention already used by screener/universe_scan.py's sibling scoring
function, _compute_scan_score) instead of recomputing it from df.

These tests prove the decoupling actually took effect -- not just "the
function still returns a number," but "the Hurst portion of the score
responds only to the `hurst` parameter, never to df's price action."
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.signal_runner import rank_signal, SIGNAL_RANK_WEIGHTS


def _make_df(n=60, daily_vol_pct: float = 0.0) -> pd.DataFrame:
    """Flat-ish price series with a given daily return volatility."""
    rng = np.random.default_rng(42)
    if daily_vol_pct == 0.0:
        closes = np.full(n, 100.0)
    else:
        rets = rng.normal(0, daily_vol_pct / 100.0, n)
        closes = 100.0 * np.cumprod(1 + rets)
    return pd.DataFrame({"close": closes})


def test_rank_signal_uses_passed_hurst_not_recomputed_from_df():
    """The core guarantee: two calls with identical df/gap but different
    `hurst` parameters must produce different scores -- proving hurst is
    actually consumed as a parameter, not silently recomputed from df."""
    df = _make_df(daily_vol_pct=1.5)
    score_low_hurst  = rank_signal("TEST.NS", df, gap_pct=-1.0, hurst=0.50)
    score_high_hurst = rank_signal("TEST.NS", df, gap_pct=-1.0, hurst=0.70)
    assert score_high_hurst > score_low_hurst


def test_rank_signal_hurst_score_ignores_df_price_action():
    """With `hurst` held constant, changing df's price action must only move
    the volatility component of the score -- if rank_signal were still
    internally recomputing Hurst from df, this would also shift the score
    unpredictably via the (now-removed) inline variogram estimator."""
    flat_df  = _make_df(daily_vol_pct=0.0)
    noisy_df = _make_df(daily_vol_pct=3.0)

    score_flat  = rank_signal("TEST.NS", flat_df,  gap_pct=-1.0, hurst=0.60)
    score_noisy = rank_signal("TEST.NS", noisy_df, gap_pct=-1.0, hurst=0.60)

    # Both use the same hurst=0.60 and gap_pct=-1.0 -- only volatility differs,
    # weighted at 20% of the total score, so the two scores must differ but
    # by no more than the full volatility weight's contribution.
    assert score_flat != score_noisy
    assert abs(score_flat - score_noisy) <= 100.0 * SIGNAL_RANK_WEIGHTS["volatility"] + 0.01


def test_rank_signal_known_inputs_produce_expected_score():
    """Pin the exact weighted-sum formula with round-number inputs."""
    df = _make_df(daily_vol_pct=0.0)   # zero volatility -> vol_score = 0
    # hurst=0.70 -> hurst_score = (0.70-0.50)/0.20*100 = 100
    # gap_pct=0.0 -> gap_score = 100
    # zero vol    -> vol_score = 0 (clamped, since (0-20)/30*100 is negative)
    score = rank_signal("TEST.NS", df, gap_pct=0.0, hurst=0.70)
    expected = (
        SIGNAL_RANK_WEIGHTS["hurst"] * 100.0 +
        SIGNAL_RANK_WEIGHTS["gap_proximity"] * 100.0 +
        SIGNAL_RANK_WEIGHTS["volatility"] * 0.0
    )
    assert score == round(expected, 2)


def test_rank_signal_gap_proximity_scores_zero_gap_highest():
    df = _make_df(daily_vol_pct=1.0)
    score_no_gap   = rank_signal("TEST.NS", df, gap_pct=0.0,  hurst=0.55)
    score_wide_gap = rank_signal("TEST.NS", df, gap_pct=-10.0, hurst=0.55)
    assert score_no_gap > score_wide_gap


def test_rank_signal_returns_zero_on_malformed_df():
    """Missing 'close' column must fail safe (0.0), not raise -- matches
    the function's documented safe-default contract."""
    bad_df = pd.DataFrame({"not_close": [1, 2, 3]})
    assert rank_signal("TEST.NS", bad_df, gap_pct=0.0, hurst=0.55) == 0.0
