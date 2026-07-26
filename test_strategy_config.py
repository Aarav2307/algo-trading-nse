"""
test_strategy_config.py — Regression tests for strategy_config.py's single
source of truth guarantee.

Root problem this closes: RM_CONFIG/PS_CONFIG/COOLDOWN_BARS (signal_runner.py),
_COOLDOWN_BARS/_AMO_LIMIT_BUFFER (morning_fill_check.py), and PARAMS
(walk_forward.py, portfolio_backtest.py) used to be four hand-typed copies of
the same numbers, kept in sync only by "must match X" comments. A parameter
re-tune (already happened live more than once: circuit breaker 19%->20%,
cooldown bars picked from a 10/15/20/25 sweep) required editing all four by
hand — one missed file meant live trading on a config that was never
actually re-validated, with nothing to notice.

These tests assert every consumer's values are identical, sourced from
strategy_config.py — not just "tests still pass," but "the numbers agree."
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import strategy_config as sc


def test_signal_runner_rm_config_matches_shared_module():
    from paper_trading.signal_runner import RM_CONFIG
    assert RM_CONFIG == sc.RISK_MANAGEMENT


def test_signal_runner_ps_config_matches_shared_module():
    from paper_trading.signal_runner import PS_CONFIG
    assert PS_CONFIG == sc.POSITION_SIZING


def test_signal_runner_cooldown_bars_matches_shared_module():
    from paper_trading.signal_runner import COOLDOWN_BARS
    assert COOLDOWN_BARS == sc.COOLDOWN["cooldown_bars"]


def test_signal_runner_amo_limit_buffer_matches_shared_module():
    from paper_trading.signal_runner import AMO_CONFIG
    assert AMO_CONFIG["limit_buffer_pct"] == sc.AMO_LIMIT_BUFFER_PCT


def test_morning_fill_check_cooldown_bars_matches_shared_module():
    import paper_trading.morning_fill_check as mfc
    assert mfc._COOLDOWN_BARS == sc.COOLDOWN["cooldown_bars"]


def test_morning_fill_check_amo_limit_buffer_matches_shared_module():
    import paper_trading.morning_fill_check as mfc
    assert mfc._AMO_LIMIT_BUFFER == sc.AMO_LIMIT_BUFFER_PCT


def test_walk_forward_params_reference_shared_module():
    from validation.walk_forward import PARAMS
    assert PARAMS["risk_management"] == sc.RISK_MANAGEMENT
    assert PARAMS["cooldown"] == sc.COOLDOWN
    assert PARAMS["position_sizing"] == sc.POSITION_SIZING


def test_portfolio_backtest_params_reference_shared_module():
    from validation.portfolio_backtest import PARAMS
    assert PARAMS["risk_management"] == sc.RISK_MANAGEMENT
    assert PARAMS["cooldown"] == sc.COOLDOWN
    assert PARAMS["position_sizing"] == sc.POSITION_SIZING


def test_signal_runner_and_walk_forward_agree_end_to_end():
    """The actual guarantee that matters: what signal_runner.py trades live
    and what walk_forward.py validated are provably the same object's values,
    not two independently-maintained copies that happen to match today."""
    from paper_trading.signal_runner import RM_CONFIG, PS_CONFIG, COOLDOWN_BARS
    from validation.walk_forward import PARAMS as WF_PARAMS

    assert RM_CONFIG == WF_PARAMS["risk_management"]
    assert PS_CONFIG == WF_PARAMS["position_sizing"]
    assert COOLDOWN_BARS == WF_PARAMS["cooldown"]["cooldown_bars"]


def test_run_backtest_and_sensitivity_sweep_are_not_forced_to_match():
    """run_backtest.py's CONFIG and walk_forward.py's BASE_PARAMS sensitivity
    sweep are deliberate exploration tools, not consumers of the shared
    module -- this test documents that choice so a future "helpful" edit
    doesn't accidentally force them onto strategy_config.py and remove the
    one place this system is allowed to ask "what if?" before a change is
    promoted. run_backtest.py's own cooldown_bars=7 (for A/B comparison)
    proves the divergence is intentional.
    """
    import importlib
    run_backtest = importlib.import_module("run_backtest")
    assert run_backtest.CONFIG["cooldown"]["cooldown_bars"] != sc.COOLDOWN["cooldown_bars"], (
        "run_backtest.py's CONFIG should stay independent of strategy_config.py "
        "-- if this now matches, confirm it wasn't accidentally wired to the "
        "shared module (that would remove its A/B-comparison purpose)."
    )
