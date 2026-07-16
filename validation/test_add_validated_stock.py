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

# Minimal signal_runner.py content with 2 stocks and the rate-limit comment
_SR_CONTENT = """\
STOCKS: List[str] = [
    "EXISTING.NS",
    "OTHER.NS",
]

# NB: adds ~1.1s × len(STOCKS) to each run (2 stocks ≈ 2.2s today;
# scales linearly as the universe grows).
"""

# Minimal walk_forward.py content with the same 2 stocks
_WF_CONTENT = """\
STOCKS = [
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
) -> tuple[Path, Path]:
    """Write fake STOCKS files to tmp_path and enter all standard patches."""
    sr = tmp_path / "signal_runner.py"
    wf = tmp_path / "walk_forward.py"
    sr.write_text(_SR_CONTENT)
    wf.write_text(_WF_CONTENT)

    stack.enter_context(patch.object(avs, "SIGNAL_RUNNER_FILE", sr))
    stack.enter_context(patch.object(avs, "WALK_FORWARD_FILE",  wf))
    stack.enter_context(patch.object(avs, "PENDING_ENTRY_DIR",  tmp_path))
    stack.enter_context(patch.object(avs, "run_wf_gate",
                                     return_value=(gate_result or _GATE_PASS)))
    stack.enter_context(patch.object(avs, "_get_current_stocks",
                                     return_value=(current_stocks or ["EXISTING.NS", "OTHER.NS"])))
    stack.enter_context(patch("sys.argv", ["add_validated_stock.py", ticker]))
    return sr, wf


# ── Test 1: WF gate fails — files unchanged, no draft ────────────────────────

def test_wf_gate_fails_files_unchanged_no_draft(tmp_path):
    """When the WF gate returns gate_pass=False, main() must return non-zero,
    leave both STOCKS files completely unchanged, and create no draft entry."""
    with ExitStack() as stack:
        sr, wf = _setup(stack, tmp_path, gate_result=_GATE_FAIL)
        rc = avs.main()

    assert rc != 0
    assert sr.read_text() == _SR_CONTENT, "signal_runner.py must not be modified on gate failure"
    assert wf.read_text() == _WF_CONTENT, "walk_forward.py must not be modified on gate failure"

    ticker_short = _TICKER.replace(".NS", "")
    draft = tmp_path / f"pending_context_entry_{ticker_short}.txt"
    assert not draft.exists(), "No draft entry must be created when gate fails"


# ── Test 2: Gate passes — both files updated, rate-limit correct, draft created

def test_gate_passes_both_files_updated(tmp_path):
    """When gate_pass=True and ticker is not already present, main() must:
    - insert the ticker+comment into both STOCKS files
    - update the rate-limit comment in signal_runner.py (2 → 3 stocks, 2.2s → 3.3s)
    - create a draft CLAUDE_CONTEXT entry containing the WF validation data
    - return 0
    """
    with ExitStack() as stack:
        sr, wf = _setup(stack, tmp_path)
        mock_subproc = stack.enter_context(
            patch("validation.add_validated_stock.subprocess")
        )
        mock_subproc.run.return_value = MagicMock(returncode=0)
        rc = avs.main()

    assert rc == 0, "main() must return 0 on success"

    sr_text = sr.read_text()
    wf_text = wf.read_text()

    # Ticker appears in both files
    assert _TICKER in sr_text, "New ticker must appear in signal_runner.py"
    assert _TICKER in wf_text, "New ticker must appear in walk_forward.py"

    # Rate-limit comment updated correctly: 2 stocks → 3, 2.2s → 3.3s
    assert "2 stocks ≈ 2.2s" not in sr_text, "Old rate-limit comment must be replaced"
    assert "3 stocks ≈ 3.3s" in sr_text, "Rate-limit comment must reflect new count (3 × 1.1 = 3.3)"

    # Draft entry created with WF data
    ticker_short = _TICKER.replace(".NS", "")
    draft = tmp_path / f"pending_context_entry_{ticker_short}.txt"
    assert draft.exists(), "Draft CLAUDE_CONTEXT entry must be created"
    draft_text = draft.read_text()
    assert "original 6/6 OOS +9.0%" in draft_text, "Draft must contain original OOS result"
    assert "extended 5/6 OOS +13.6%" in draft_text, "Draft must contain extended OOS result"
    assert _TICKER in draft_text, "Draft must name the ticker"


# ── Test 3: Ticker already present — no-op, exit 0 ──────────────────────────

def test_ticker_already_present_is_noop(tmp_path):
    """When the ticker is already in STOCKS (as returned by _get_current_stocks),
    main() must return 0 (not an error), leave both files unchanged, and create
    no draft entry."""
    # Return current_stocks that already includes the ticker being added
    with ExitStack() as stack:
        sr, wf = _setup(
            stack, tmp_path,
            current_stocks=[_TICKER, "EXISTING.NS"],
        )
        rc = avs.main()

    assert rc == 0
    assert sr.read_text() == _SR_CONTENT, "signal_runner.py must be unchanged — ticker already present"
    assert wf.read_text() == _WF_CONTENT, "walk_forward.py must be unchanged — ticker already present"

    ticker_short = _TICKER.replace(".NS", "")
    draft = tmp_path / f"pending_context_entry_{ticker_short}.txt"
    assert not draft.exists(), "No draft entry must be created for an already-present ticker"


# ── Test 4: Test suite failure — both files reverted, draft deleted ───────────

def test_suite_failure_reverts_edits_and_deletes_draft(tmp_path):
    """When the post-edit test suite (mocked subprocess.run) returns non-zero,
    main() must revert BOTH STOCKS files to their exact original content, delete
    the draft entry file, and return non-zero."""
    with ExitStack() as stack:
        sr, wf = _setup(stack, tmp_path)
        mock_subproc = stack.enter_context(
            patch("validation.add_validated_stock.subprocess")
        )
        mock_subproc.run.return_value = MagicMock(returncode=1)
        rc = avs.main()

    assert rc != 0, "main() must return non-zero when tests fail"

    # Both files must be fully reverted
    assert sr.read_text() == _SR_CONTENT, (
        "signal_runner.py must be reverted to original content after test failure"
    )
    assert wf.read_text() == _WF_CONTENT, (
        "walk_forward.py must be reverted to original content after test failure"
    )

    # Draft entry must not exist
    ticker_short = _TICKER.replace(".NS", "")
    draft = tmp_path / f"pending_context_entry_{ticker_short}.txt"
    assert not draft.exists(), (
        "Draft CLAUDE_CONTEXT entry must be deleted when test suite fails"
    )


# ── Test 5: Both STOCKS edits are byte-identical ─────────────────────────────

def test_stocks_edits_are_byte_identical(tmp_path):
    """The ticker+comment line inserted into signal_runner.py and walk_forward.py
    must be byte-for-byte identical — the single shared _insert_into_stocks()
    function guarantees this structurally, but we verify it explicitly."""
    with ExitStack() as stack:
        sr, wf = _setup(stack, tmp_path)
        mock_subproc = stack.enter_context(
            patch("validation.add_validated_stock.subprocess")
        )
        mock_subproc.run.return_value = MagicMock(returncode=0)
        avs.main()

    sr_lines = sr.read_text().splitlines()
    wf_lines = wf.read_text().splitlines()

    sr_ticker_line = next((l for l in sr_lines if _TICKER in l), None)
    wf_ticker_line = next((l for l in wf_lines if _TICKER in l), None)

    assert sr_ticker_line is not None, "New ticker must appear in signal_runner.py"
    assert wf_ticker_line is not None, "New ticker must appear in walk_forward.py"
    assert sr_ticker_line == wf_ticker_line, (
        "The ticker+comment line must be byte-identical in both STOCKS files.\n"
        f"  signal_runner.py: {sr_ticker_line!r}\n"
        f"  walk_forward.py:  {wf_ticker_line!r}"
    )
