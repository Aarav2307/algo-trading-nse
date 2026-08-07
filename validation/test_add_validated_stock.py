"""
validation/test_add_validated_stock.py

Tests for add_validated_stock.py.

All file I/O uses tmp_path — no writes to the real project files.
run_wf_gate is mocked (no subprocess calls to walk_forward.py, no Kite API calls).
subprocess.run (the pytest invocation inside main()) is mocked in tests that reach it.
"""

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import validation.add_validated_stock as avs


# ── Shared test fixtures ───────────────────────────────────────────────────────

_TICKER = "NEWTICK.NS"

# Gate result for a stock that passes: 6/6 original +9.0%, 5/6 extended +13.6%
_GATE_PASS = {
    "ticker":    _TICKER,
    "original":  {"score": 6, "total_metrics": 6, "oos_return_pct": 9.0},
    "extended":  {"status": "PASS", "score": 5, "oos_return_pct": 13.6, "error": None},
    "gate_pass": True,
    "reasons":   [],
}

# Gate result for a stock that fails the original OOS metric threshold
_GATE_FAIL = {
    "ticker":    _TICKER,
    "original":  {"score": 3, "total_metrics": 6, "oos_return_pct": -2.0},
    "extended":  {"status": "PASS", "score": 5, "oos_return_pct": 8.0, "error": None},
    "gate_pass": False,
    "reasons":   ["original metrics 3/6 < 4 required"],
}

# Minimal universe.py content with 2 stocks
_UNIVERSE_CONTENT = """\
STOCKS: List[str] = [
    "EXISTING.NS",
    "OTHER.NS",
]
"""


# ── Setup helper ──────────────────────────────────────────────────────────────

def _setup(
    stack: ExitStack,
    tmp_path: Path,
    gate_result=None,
    current_stocks=None,
    ticker: str = _TICKER,
) -> Path:
    """Write a fake universe.py to tmp_path and enter all standard patches."""
    universe_file = tmp_path / "universe.py"
    universe_file.write_text(_UNIVERSE_CONTENT)

    stack.enter_context(patch.object(avs, "UNIVERSE_FILE", universe_file))
    stack.enter_context(patch.object(avs, "PENDING_ENTRY_DIR", tmp_path))
    stack.enter_context(patch.object(avs, "run_wf_gate",
                                     return_value=(gate_result or _GATE_PASS)))
    stack.enter_context(patch.object(avs, "_get_current_stocks",
                                     return_value=(current_stocks or ["EXISTING.NS", "OTHER.NS"])))
    stack.enter_context(patch("sys.argv", ["add_validated_stock.py", ticker]))
    return universe_file


# ── Test 1: WF gate fails — file unchanged, no draft ─────────────────────────

def test_wf_gate_fails_file_unchanged_no_draft(tmp_path):
    """When the WF gate returns gate_pass=False, main() must return non-zero,
    leave universe.py completely unchanged, and create no draft entry."""
    with ExitStack() as stack:
        universe_file = _setup(stack, tmp_path, gate_result=_GATE_FAIL)
        rc = avs.main()

    assert rc != 0
    assert universe_file.read_text() == _UNIVERSE_CONTENT, "universe.py must not be modified on gate failure"

    ticker_short = _TICKER.replace(".NS", "")
    draft = tmp_path / f"pending_context_entry_{ticker_short}.txt"
    assert not draft.exists(), "No draft entry must be created when gate fails"


# ── Test 2: Gate passes — file updated, draft created ────────────────────────

def test_gate_passes_file_updated(tmp_path):
    """When gate_pass=True and ticker is not already present, main() must:
    - insert the ticker+comment into universe.py
    - create a draft CLAUDE_CONTEXT entry containing the WF validation data
    - return 0
    """
    with ExitStack() as stack:
        universe_file = _setup(stack, tmp_path)
        mock_subproc = stack.enter_context(
            patch("validation.add_validated_stock.subprocess")
        )
        mock_subproc.run.return_value = MagicMock(returncode=0)
        rc = avs.main()

    assert rc == 0, "main() must return 0 on success"

    universe_text = universe_file.read_text()
    assert _TICKER in universe_text, "New ticker must appear in universe.py"

    # Draft entry created with WF data
    ticker_short = _TICKER.replace(".NS", "")
    draft = tmp_path / f"pending_context_entry_{ticker_short}.txt"
    assert draft.exists(), "Draft CLAUDE_CONTEXT entry must be created"
    draft_text = draft.read_text()
    assert "original 6/6 OOS +9.0%" in draft_text, "Draft must contain original OOS result"
    assert "extended 5/6 OOS +13.6%" in draft_text, "Draft must contain extended OOS result"
    assert _TICKER in draft_text, "Draft must name the ticker"

    # Regression guard (2026-08-07): the internal test-suite invocation had
    # gone stale, hardcoded to a subset of directories ("paper_trading/",
    # "data/", "utils/", "screener/", "validation/") that silently missed
    # engine/ and root-level test_*.py files — 269 tests instead of the real
    # 301. Pin the exact command so it can never drift from the full-suite
    # invocation used everywhere else in this project (no positional path
    # args, just the two live-Kite ignores).
    cmd = mock_subproc.run.call_args[0][0]
    assert "paper_trading/" not in cmd, "must not hardcode a stale directory subset"
    assert "--ignore=test_kite.py" in cmd
    assert "--ignore=test_kite_fetcher.py" in cmd


# ── Test 3: Ticker already present — no-op, exit 0 ──────────────────────────

def test_ticker_already_present_is_noop(tmp_path):
    """When the ticker is already in STOCKS (as returned by _get_current_stocks),
    main() must return 0 (not an error), leave universe.py unchanged, and create
    no draft entry."""
    # Return current_stocks that already includes the ticker being added
    with ExitStack() as stack:
        universe_file = _setup(
            stack, tmp_path,
            current_stocks=[_TICKER, "EXISTING.NS"],
        )
        rc = avs.main()

    assert rc == 0
    assert universe_file.read_text() == _UNIVERSE_CONTENT, "universe.py must be unchanged — ticker already present"

    ticker_short = _TICKER.replace(".NS", "")
    draft = tmp_path / f"pending_context_entry_{ticker_short}.txt"
    assert not draft.exists(), "No draft entry must be created for an already-present ticker"


# ── Test 4: Test suite failure — file reverted, draft deleted ───────────────

def test_suite_failure_reverts_edit_and_deletes_draft(tmp_path):
    """When the post-edit test suite (mocked subprocess.run) returns non-zero,
    main() must revert universe.py to its exact original content, delete
    the draft entry file, and return non-zero."""
    with ExitStack() as stack:
        universe_file = _setup(stack, tmp_path)
        mock_subproc = stack.enter_context(
            patch("validation.add_validated_stock.subprocess")
        )
        mock_subproc.run.return_value = MagicMock(returncode=1)
        rc = avs.main()

    assert rc != 0, "main() must return non-zero when tests fail"

    assert universe_file.read_text() == _UNIVERSE_CONTENT, (
        "universe.py must be reverted to original content after test failure"
    )

    ticker_short = _TICKER.replace(".NS", "")
    draft = tmp_path / f"pending_context_entry_{ticker_short}.txt"
    assert not draft.exists(), (
        "Draft CLAUDE_CONTEXT entry must be deleted when test suite fails"
    )
