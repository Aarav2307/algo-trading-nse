"""
paper_trading/test_resolve_buy_gate.py — Regression tests for resolve_buy_gate()
and can_open_position().

Root problem this closes: _process_stock()'s BUY branch used to be a ~50-line
inline cascade of three sequential gate checks (cooldown, NIFTY regime, live
Hurst), each with its own early return, mixed into a 323-line function that
also handles RM exits, strategy signals, sizing, and AMO queueing. Only the
Hurst gate had any test coverage (via _process_stock() end-to-end fixtures in
test_etf_overlay.py); cooldown and regime were untested anywhere.

resolve_buy_gate() extracts exactly this cascade into a pure function — three
scalars in, one decision out, no portfolio/df/I-O — so every branch is
testable directly with plain numbers instead of building PaperPortfolio
fixtures.

While extracting it, found and fixed a latent bug in the already-shipped
Hurst fail-open path: _process_stock() used to default a failed Hurst
computation to a 0.5 sentinel and pass it through the same "hurst < threshold"
check as a real measurement. That happens to allow entry today only because
HURST_THRESHOLD (0.48) is below 0.5 -- but CLAUDE_CONTEXT.md documents the
threshold was previously tuned as high as 0.55, and could be again. At 0.55,
the 0.5 sentinel would silently flip fail-open into fail-closed on a
computation error. resolve_buy_gate() now takes hurst as Optional[float];
None means "skip this check" unconditionally, independent of where the
threshold is set.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.signal_runner as sr
from paper_trading.signal_runner import resolve_buy_gate, can_open_position


# =============================================================================
# resolve_buy_gate() — cooldown gate
# =============================================================================

def test_cooldown_blocks_regardless_of_regime_or_hurst():
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=3, market_regime="BULL", hurst=0.70)
    assert allowed is False
    assert signal == "COOLDOWN"
    assert "3 bars remaining" in reason


def test_cooldown_zero_does_not_block():
    """remaining_bars == 0 means cooldown has elapsed -- matches
    PaperPortfolio.is_in_cooldown()'s `remaining_bars > 0` semantics exactly."""
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=0, market_regime="BULL", hurst=0.70)
    assert allowed is True
    assert signal == "" and reason == ""


# =============================================================================
# resolve_buy_gate() — regime gate
# =============================================================================

def test_bear_regime_blocks():
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=0, market_regime="BEAR", hurst=0.70)
    assert allowed is False
    assert signal == "REGIME_SKIP"


def test_unknown_regime_does_not_block():
    """UNKNOWN (data failure to compute NIFTY regime) must fail-open, same
    fail-open philosophy as the Hurst gate — only an explicit BEAR blocks."""
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=0, market_regime="UNKNOWN", hurst=0.70)
    assert allowed is True


def test_bull_regime_does_not_block():
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=0, market_regime="BULL", hurst=0.70)
    assert allowed is True


# =============================================================================
# resolve_buy_gate() — Hurst gate
# =============================================================================

def test_hurst_below_threshold_blocks():
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=0, market_regime="BULL", hurst=0.30)
    assert allowed is False
    assert signal == "HURST_SKIP"
    assert "0.300" in reason


def test_hurst_at_or_above_threshold_passes():
    allowed, signal, reason = resolve_buy_gate(
        cooldown_remaining=0, market_regime="BULL", hurst=sr.HURST_THRESHOLD
    )
    assert allowed is True


def test_hurst_none_fails_open_at_current_threshold():
    """The behavior _process_stock() actually relies on today: a failed
    Hurst computation (represented as None) must not block entry."""
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=0, market_regime="BULL", hurst=None)
    assert allowed is True


def test_hurst_none_fails_open_even_if_threshold_raised_above_half(monkeypatch):
    """The regression this extraction fixed: a 0.5 sentinel would pass this
    check today (0.5 > 0.48) but would silently start blocking entries if
    HURST_THRESHOLD were ever raised above 0.5 -- which CLAUDE_CONTEXT.md
    shows already happened once (0.55, before being tuned back down to 0.48).
    None must bypass the check unconditionally, not just at today's threshold."""
    monkeypatch.setattr(sr, "HURST_THRESHOLD", 0.99)
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=0, market_regime="BULL", hurst=None)
    assert allowed is True, "fail-open on Hurst error must not depend on HURST_THRESHOLD's value"


# =============================================================================
# resolve_buy_gate() — gate ordering (cheapest check short-circuits first)
# =============================================================================

def test_cooldown_checked_before_regime():
    """Cooldown must win even when regime would also block -- mirrors the
    original inline cascade's order (cheapest check first)."""
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=2, market_regime="BEAR", hurst=0.70)
    assert signal == "COOLDOWN"


def test_regime_checked_before_hurst():
    allowed, signal, reason = resolve_buy_gate(cooldown_remaining=0, market_regime="BEAR", hurst=0.10)
    assert signal == "REGIME_SKIP"


# =============================================================================
# can_open_position() — zero-risk companion: already pure/testable, had no
# test coverage anywhere (grep confirmed zero call sites outside
# signal_runner.py itself). No code change; tests only.
# =============================================================================

class _FakePortfolio:
    def __init__(self, cash: float, positions: dict):
        self.state = {"cash": cash, "positions": positions}


def test_can_open_position_blocks_at_position_limit(monkeypatch):
    monkeypatch.setattr(sr, "MAX_CONCURRENT_POSITIONS", 2)
    port = _FakePortfolio(
        cash=1_000_000,
        positions={
            "A.NS": {"shares": 10},
            "B.NS": {"shares": 5},
        },
    )
    allowed, reason = can_open_position(port, proposed_shares=1, proposed_price=100.0)
    assert allowed is False
    assert "Position limit" in reason


def test_can_open_position_blocks_on_insufficient_cash():
    port = _FakePortfolio(cash=100.0, positions={})
    allowed, reason = can_open_position(port, proposed_shares=10, proposed_price=100.0)
    assert allowed is False
    assert "Insufficient cash" in reason


def test_can_open_position_allows_when_room_and_cash_available():
    port = _FakePortfolio(cash=1_000_000, positions={"A.NS": {"shares": 10}})
    allowed, reason = can_open_position(port, proposed_shares=10, proposed_price=100.0)
    assert allowed is True
    assert reason == ""


def test_can_open_position_cash_check_includes_cost_buffer():
    """required_cash = shares * price * 1.001 -- exact break-even at the
    buffer boundary must block, not allow, since available < required."""
    proposed_shares, proposed_price = 100, 100.0
    exact_cost = proposed_shares * proposed_price  # 10,000, no buffer
    port = _FakePortfolio(cash=exact_cost, positions={})
    allowed, reason = can_open_position(port, proposed_shares, proposed_price)
    assert allowed is False, "cash exactly matching the unbuffered cost should fail the 0.1% cost buffer check"
