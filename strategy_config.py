"""
strategy_config.py — Single source of truth for the frozen, walk-forward-
validated strategy parameters (risk management, position sizing, cooldown,
AMO limit buffer).

Previously these were declared four times: paper_trading/signal_runner.py's
RM_CONFIG/PS_CONFIG/COOLDOWN_BARS, paper_trading/morning_fill_check.py's
_COOLDOWN_BARS/_AMO_LIMIT_BUFFER, validation/walk_forward.py's PARAMS, and
validation/portfolio_backtest.py's PARAMS — kept in sync only by "must
match X" comments at each site. A parameter re-tune (this has already
happened live: circuit breaker 19%->20%, cooldown bars picked from a
10/15/20/25 sweep, Hurst threshold recalibration) required editing all four
by hand. One missed file meant live trading on a config that was never
actually re-validated, with nothing to notice.

Mirrors the same fix already applied to the live universe — see
universe.py's docstring for that precedent.

UPDATE THESE VALUES only after a walk-forward re-validation (see
validation/walk_forward.py). Document the change in CLAUDE_CONTEXT.md the
same way every past parameter change has been recorded (e.g. "Cooldown
validation" and "Fix circuit breaker threshold" entries), so the reasoning
behind the current numbers stays visible.

Not consumers of this file, on purpose: run_backtest.py's CONFIG (a
deliberately adjustable single-run sandbox — e.g. its cooldown_bars=7 is
for A/B comparison, not drift) and walk_forward.py's BASE_PARAMS inside
run_parameter_stability_test() (a sensitivity sweep that starts from these
values and deliberately perturbs them). Forcing either to import this file
would remove the one place this system is allowed to ask "what if?" before
a change is promoted here.
"""

# ── Risk management (4-layer exit system) ─────────────────────────────────────
# Hard stop / ATR / max-bars-held: validated as part of the original quant
# research fixes (see CLAUDE_CONTEXT.md "Quant Research Fixes"). Confirmed
# unchanged across every walk-forward run since.
RISK_MANAGEMENT = {
    "enabled":                True,
    "hard_stop_pct":         -0.20,
    "atr_period":             22,
    "atr_multiplier":          3.0,
    "max_bars_held":          60,
    "round_number_offset_pct": 0.01,
    "enable_layer_1":         True,
    "enable_layer_2":         True,
    "enable_layer_3":         True,
    "enable_layer_4":         True,
}

# ── Cooldown gate ──────────────────────────────────────────────────────────────
# 15 bars: tested 10/15/20/25 across 9 qualifying stocks (CLAUDE_CONTEXT.md
# "Fix 3: Cooldown validation") — 15 ties with 10 on score but better trade
# quality (+4.39% vs +4.81% with 8 fewer low-quality trades).
COOLDOWN = {
    "enabled":                True,
    "cooldown_bars":          15,
    "cooldown_after_reasons": ["HARD_STOP", "CHANDELIER", "TIME_STOP"],
    "reset_on_strategy_exit": True,
}

# ── Position sizing (fixed-fractional) ─────────────────────────────────────────
POSITION_SIZING = {
    "enabled":            True,
    "method":             "fixed_fractional",
    "risk_per_trade_pct": 0.015,
    "max_position_pct":   0.20,
    "fallback_stop_pct":  0.20,
}

# ── AMO limit buffer ────────────────────────────────────────────────────────────
# 0.5% buffer: BUY limit above signal close, SELL limit below. Drives both
# signal_runner.py's next-day AMO placement and morning_fill_check.py's
# gap-down requeue limit price — both must use the identical value.
AMO_LIMIT_BUFFER_PCT = 0.005
