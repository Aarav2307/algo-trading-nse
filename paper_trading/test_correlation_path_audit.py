"""
paper_trading/test_correlation_path_audit.py — Finding #4.

Two default parameters resolved a bare relative path against the process cwd:
  paper_trading/correlation_check.py  check_entry_correlation(portfolio_state_path=)
  paper_trading/paper_portfolio.py    PaperPortfolio.__init__(state_file=)

Neither raises when the cwd is wrong. The first returns safe=True having checked
zero positions; the second treats the missing file as "first run" and fabricates
a fresh portfolio. Both are fail-OPEN wrong answers, not errors.

Naming follows this repo's established convention for this bug class —
test_*_is_absolute_and_cwd_independent — see test_signal_runner_fetch.py and
test_morning_fill_check.py, which use it a dozen times between them.

DELIBERATE DEVIATION from that convention: those tests also assert
`.exists()` from the project root. This file does NOT. portfolio_state.json is
gitignored, so that assertion fails in any fresh git worktree — it is exactly
why test_state_file_is_absolute_and_cwd_independent is red in every worktree run
of this audit. Absoluteness and cwd-independence are the properties under test;
existence is an environment fact and asserting it makes a test that fails for
reasons unrelated to the bug.

NEW FILE — does not modify or overwrite any existing test. No network calls.
"""

import ast
import inspect
import json
import os
import pathlib
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.correlation_check import check_entry_correlation  # noqa: E402
from paper_trading.paper_portfolio import PaperPortfolio             # noqa: E402


def _default_of(fn, arg):
    return inspect.signature(fn).parameters[arg].default


def _cwd_independent(path_str):
    """(exists_from_root, exists_from_tmp) for the same path string."""
    p = Path(path_str)
    original = os.getcwd()
    os.chdir(_ROOT)
    try:
        from_root = p.exists()
    finally:
        os.chdir(original)
    os.chdir("/tmp")
    try:
        from_tmp = p.exists()
    finally:
        os.chdir(original)
    return from_root, from_tmp


# ── The two sites ──────────────────────────────────────────────────────────

def test_correlation_state_path_default_is_absolute_and_cwd_independent():
    """
    check_entry_correlation's portfolio_state_path default must be absolute.
    Relative, it resolved against the cwd, so the documented CLI
    (`python paper_trading/correlation_check.py TICKER.NS`) run from anywhere
    but the repo root silently took the "No portfolio state file" branch and
    returned safe=True with zero open positions checked.
    """
    default = _default_of(check_entry_correlation, "portfolio_state_path")
    assert Path(default).is_absolute(), (
        f"portfolio_state_path default is not absolute: {default!r}"
    )
    from_root, from_tmp = _cwd_independent(default)
    assert from_root == from_tmp, (
        f"portfolio_state_path default is cwd-dependent: "
        f"from root={from_root}, from /tmp={from_tmp}"
    )


def test_paper_portfolio_state_file_default_is_absolute_and_cwd_independent():
    """
    PaperPortfolio.__init__'s state_file default must be absolute. Not currently
    exercised — signal_runner and morning_fill_check both pass str(STATE_FILE)
    explicitly — but its failure mode is worse than correlation's: load() treats
    a missing file as first-run and fabricates a fresh initial_capital
    portfolio, and a later save() would write real state to the wrong directory.
    """
    default = _default_of(PaperPortfolio.__init__, "state_file")
    assert Path(default).is_absolute(), (
        f"state_file default is not absolute: {default!r}"
    )
    from_root, from_tmp = _cwd_independent(default)
    assert from_root == from_tmp, (
        f"state_file default is cwd-dependent: "
        f"from root={from_root}, from /tmp={from_tmp}"
    )


# ── The hazard the absoluteness protects against ───────────────────────────

def test_missing_state_file_fails_open_rather_than_erroring():
    """
    Documents WHY the path must be absolute: a missing state file is not an
    error here. It returns safe=True, having checked nothing. That is the
    behaviour a wrong cwd used to trigger silently.
    """
    with tempfile.TemporaryDirectory() as td:
        result = check_entry_correlation(
            candidate="PERSISTENT.NS",
            portfolio_state_path=str(Path(td) / "does_not_exist.json"),
        )
    assert result["safe"] is True
    assert result["open_positions"] == []
    assert "No portfolio state file" in result["reason"]


def test_real_state_file_is_actually_read():
    """
    Counterpart to the above: with a state file that exists, the open positions
    are read rather than skipped. Without this, the test above would pass even
    if the function always returned safe=True.
    """
    state = {"positions": {"BAJAJ-AUTO.NS": {"shares": 1, "entry_price": 100.0,
                                             "pending_buy": False}}}
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "portfolio_state.json"
        f.write_text(json.dumps(state))
        result = check_entry_correlation(
            candidate="PERSISTENT.NS",
            portfolio_state_path=str(f),
            price_data={},          # no network: empty price data
        )
    assert result["open_positions"] == ["BAJAJ-AUTO.NS"], result
    assert "No portfolio state file" not in result["reason"]


# ── Structural: prevent the class from returning ───────────────────────────

def test_no_bare_relative_path_defaults_remain_in_production_code():
    """
    Repo-wide AST scan for the shape of this bug: a default parameter whose
    value is a relative path string. Structural, not a text scan — this audit
    has twice been bitten by regexes matching the right text in the wrong
    context.

    Note this is the exact shape system_health_check.py's Check 6 does NOT
    cover: its _REL_PATH_RE matches `= Path("relative")` assignments only, so
    it reported PASS / "0 relative-path constants" while both Finding #4 sites
    sat uncaught. See the Finding #4 write-up.
    """
    offenders = []
    for p in sorted(pathlib.Path(_ROOT).rglob("*.py")):
        rel = str(p.relative_to(_ROOT))
        if any(x in rel for x in (".git/", "venv/", "test_")):
            continue
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            a = fn.args
            if not a.defaults:
                continue
            for name, dflt in zip(a.args[-len(a.defaults):], a.defaults):
                if (isinstance(dflt, ast.Constant)
                        and isinstance(dflt.value, str)
                        and ("/" in dflt.value
                             or dflt.value.endswith((".json", ".csv", ".txt", ".log")))
                        and not dflt.value.startswith("/")):
                    offenders.append(f"{rel}:{fn.lineno} {fn.name}({name.arg}={dflt.value!r})")
    assert not offenders, (
        "relative-path default parameter(s) found — resolve against _ROOT:\n  "
        + "\n  ".join(offenders)
    )
