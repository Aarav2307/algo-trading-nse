"""
engine/test_sizer_negative_shares.py — Finding #3.

PositionSizer.calculate_shares() could return -1 when cash was below the cost of
a single share: math.floor() of a small negative fraction is -1, not 0, and
min() propagated it.

The defect was never only the negative number. It was the COUPLING: three
separate places had to agree for it to stay harmless, and nothing documented or
tested that agreement.

  1. the sizer returning a non-negative count            (contract)
  2. signal_runner guarding `shares == 0`                 (caller)
  3. Portfolio.buy() guarding `shares == 0`               (money seam)

Each is fixed independently here, so no one of them depends on another holding.
The tests are grouped that way on purpose.

No network. Pure computation plus an in-memory Portfolio.
"""
import ast
import sys
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from engine.position_sizer import PositionSizer
from engine.portfolio import Portfolio
from strategy_config import POSITION_SIZING

# cash below the cost of one share — the trigger condition
_SUB_ONE_SHARE = [(1_000.0, 0.0), (1_000.0, 1.0), (2_135.0, 2.0), (300.0, 0.3)]


# =============================================================================
# 1. CONTRACT — the sizer itself
# =============================================================================

@pytest.mark.parametrize("price,cash", _SUB_ONE_SHARE)
def test_sizer_never_returns_a_negative_share_count(price, cash):
    """
    "A count of shares to buy" is never negative, whatever any caller does.
    max_from_cash = floor((cash - buy_cost_1share) / price) went to -1 here.
    """
    shares, _ = PositionSizer(POSITION_SIZING).calculate_shares(
        price, 100_000.0, price * 0.9, cash
    )
    assert shares >= 0, f"sizer returned {shares} for price Rs{price} / cash Rs{cash}"


@pytest.mark.parametrize("price,cash", _SUB_ONE_SHARE)
def test_sizer_reports_binding_consistently_with_the_count_it_returns(price, cash):
    """
    The sizer already classified `shares <= 0` as all_constraints_zero while
    returning -1 — it knew the count was degenerate and returned it anyway.
    """
    shares, log = PositionSizer(POSITION_SIZING).calculate_shares(
        price, 100_000.0, price * 0.9, cash
    )
    assert shares == 0 and log["binding"] == "all_constraints_zero"


def test_sizer_still_sizes_normally_when_cash_is_adequate():
    """The clamp must not suppress legitimate sizing."""
    shares, log = PositionSizer(POSITION_SIZING).calculate_shares(
        1_000.0, 100_000.0, 900.0, 50_000.0
    )
    assert shares > 0 and log["binding"] != "all_constraints_zero"


# =============================================================================
# 2. CALLER — signal_runner's guard must mean what the sizer means
# =============================================================================

def _shares_zero_guard_ops(relpath, fn_name=None):
    """
    The comparison operators of every `if shares <op> 0:` statement in a module
    (optionally restricted to one function), found via the AST.
    """
    tree = ast.parse((_ROOT / relpath).read_text())
    scope = tree
    if fn_name is not None:
        scope = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    ops = []
    for node in ast.walk(scope):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        cmp_ = node.test
        if (isinstance(cmp_.left, ast.Name) and cmp_.left.id == "shares"
                and len(cmp_.comparators) == 1
                and isinstance(cmp_.comparators[0], ast.Constant)
                and cmp_.comparators[0].value == 0):
            ops.append(type(cmp_.ops[0]).__name__)
    return ops


def test_backtester_sizing_guards_use_le_zero():
    """
    engine/backtester.py routes position_sizer.calculate_shares() straight into
    portfolio.buy(shares=...) at three sites, each originally guarded by
    `if shares == 0:` — the same defect as signal_runner's, in the module whose
    entire job is producing numbers that are believed.

    Measured on the unpatched code: Portfolio.buy(close_price=1000.0, shares=-1)
    took cash 100,000.00 -> 101,001.69 and shares_held to -1. A backtest that hit
    this did not crash; it reported a better result than the strategy earned.
    """
    ops = _shares_zero_guard_ops("engine/backtester.py")
    assert len(ops) == 3, f"expected 3 sizing guards, found {len(ops)}: {ops}"
    assert "Eq" not in ops, (
        f"an `if shares == 0:` guard remains in backtester.py — it falls through "
        f"to portfolio.buy(shares=-1). operators found: {ops}"
    )


def _sizing_guard_ops():
    """
    The comparison operators of every `if shares <op> 0:` statement inside
    signal_runner._process_stock(), found via the AST.

    Deliberately NOT a source-text regex. A first version asserted
    `re.search(r"^\s*if shares <= 0:", src)` and was shown to pass on a
    DOCSTRING containing that line — the same false-positive class as the
    health-check scanner that flagged its own docstring two days earlier. A
    string being present is not the same claim as it being the operative guard
    on the real code path, and this project has now been bitten by that
    distinction twice.
    """
    return _shares_zero_guard_ops("paper_trading/signal_runner.py", "_process_stock")


def test_signal_runner_guard_uses_le_zero_not_eq_zero():
    """
    Two definitions of "no shares" existed and only one was right:
    `if shares == 0` let -1 through to queue_pending_buy(), while the sizer's
    own `binding` field already classified `shares <= 0` as degenerate.

    Anchored to the actual `if` node inside _process_stock(), so a matching
    string in a comment, docstring or unrelated function cannot satisfy it.
    """
    ops = _sizing_guard_ops()
    assert ops, "no `if shares <op> 0:` guard found in _process_stock() at all"
    assert "LtE" in ops, f"guard is not `<= 0` — operators found: {ops}"
    assert "Eq" not in ops, (
        f"an `if shares == 0:` guard remains in _process_stock() — it disagrees "
        f"with the sizer's own `shares <= 0` binding classification. "
        f"operators found: {ops}"
    )


# =============================================================================
# 3. MONEY SEAM — Portfolio.buy() must reject a nonsensical quantity
# =============================================================================

def test_buy_rejects_a_negative_quantity_outright():
    """
    Independent of the sizer. A negative quantity here did not no-op: it opened
    a SHORT position in a long-only backtester and INCREASED cash, because
    transaction_costs() returns a negative cost for a negative quantity.
    """
    p = Portfolio(initial_capital=100_000.0)
    p.buy(datetime(2026, 8, 26), 1_000.0, shares=-1)
    assert p.shares_held == 0, f"opened a position of {p.shares_held} shares"
    assert p.cash == 100_000.0, f"cash changed to {p.cash} on a rejected buy"


def test_buy_still_accepts_a_normal_quantity():
    """Guard against over-correcting into rejecting valid buys."""
    p = Portfolio(initial_capital=100_000.0)
    p.buy(datetime(2026, 8, 26), 1_000.0, shares=10)
    assert p.shares_held == 10
    assert p.cash < 100_000.0


def test_buy_zero_is_still_a_noop():
    p = Portfolio(initial_capital=100_000.0)
    p.buy(datetime(2026, 8, 26), 1_000.0, shares=0)
    assert p.shares_held == 0 and p.cash == 100_000.0


# =============================================================================
# 4. DECOUPLING — the property that actually mattered
# =============================================================================

def test_each_layer_holds_without_relying_on_the_others():
    """
    The trap was that safety depended on three modules coincidentally agreeing.
    Assert each layer independently, so removing any one still leaves the other
    two correct rather than silently re-arming the defect.
    """
    shares, _ = PositionSizer(POSITION_SIZING).calculate_shares(
        1_000.0, 100_000.0, 900.0, 1.0
    )
    assert shares >= 0, "layer 1 (sizer contract) failed"

    assert "LtE" in _sizing_guard_ops(), "layer 2 (caller guard) failed"

    p = Portfolio(initial_capital=100_000.0)
    p.buy(datetime(2026, 8, 26), 1_000.0, shares=-5)
    assert p.shares_held == 0 and p.cash == 100_000.0, "layer 3 (money seam) failed"
