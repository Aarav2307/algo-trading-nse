"""
Backtester — drives the simulation.

Bar-by-bar execution order (critical — do not reorder):

  ① If position is open:
       Run RiskManager.check_exit() FIRST.
       If any layer fires → sell, trigger cooldown, advance_bar, skip ②③.

  ② Strategy signal evaluation (only if RM did not exit this bar).

  ③ Cooldown gate (NEW):
       If signal == +1 and no position:
         If CooldownTracker.is_in_cooldown() → suppress buy, log it.
         Else → execute buy, seed RiskManager state.

  ④ Advance cooldown counter EXACTLY ONCE per bar (unconditionally, always
     the last step). Never placed inside branches that use `continue`.

Why ④ is always last and always unconditional
──────────────────────────────────────────────
The cooldown counter must tick forward by exactly 1 per trading day regardless
of what happened during the day. Placing advance_bar() inside branches (or
omitting it on certain bars) would cause the cooldown window to drift.
The current loop uses an `rm_exited` flag instead of `continue` statements,
so there is only ONE place where advance_bar() can be called.
"""

import pandas as pd
import numpy as np
from engine.portfolio import Portfolio
from utils.costs import apply_slippage


def run(
    df: pd.DataFrame,
    signals: pd.Series,
    portfolio: Portfolio,
    risk_manager=None,              # engine.risk_manager.RiskManager | None
    cooldown_tracker=None,          # engine.cooldown.CooldownTracker | None
    position_sizer=None,            # engine.position_sizer.PositionSizer | None
    fill_on_next_open: bool = False,
    use_next_day_fills: bool = False,
    nifty_regime_df: pd.DataFrame = None,   # NIFTY 50 OHLCV (must have "close" column)
    nifty_regime_filter: bool = False,      # toggle: suppress BUY when NIFTY in death cross
) -> pd.DataFrame:
    """
    Run the backtest simulation.

    Args:
        df:                  OHLCV DataFrame (DatetimeIndex, lowercase columns)
        signals:             Signal series aligned to df's index (+1/-1/0)
        portfolio:           Initialised Portfolio instance
        risk_manager:        Optional RiskManager. None → no RM.
        cooldown_tracker:    Optional CooldownTracker. None → no cooldown gate.
        position_sizer:      Optional PositionSizer. None → all-in sizing.
        fill_on_next_open:   Legacy flag — close-fill (False) or next-open (True).
                             Cooldown and RM are NOT integrated in that path.
                             Kept for backward compatibility only.
        use_next_day_fills:  AMO-realistic fill mode. Signal is generated at
                             day N's close; buy/sell/RM-exit fills at day N+1's
                             open. Full cooldown, RM, and sizing integration
                             included. RM exits are deferred via _pending_fill
                             with type="rm_exit" so the simulation matches real
                             AMO behaviour end-to-end. Edge case: last bar falls
                             back to close-fill so no signal is silently dropped.

    Returns:
        equity_curve: DataFrame with columns [portfolio_value, benchmark_value,
                      drawdown] indexed by date.
    """
    # ── Banner ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("BACKTEST RUNNING")
    if risk_manager is not None:
        cfg = risk_manager._cfg
        layers = []
        if cfg["enable_layer_1"]: layers.append(f"L1:HardStop({cfg['hard_stop_pct']*100:.0f}%)")
        if cfg["enable_layer_2"]: layers.append(f"L2:Chandelier(ATR{cfg['atr_period']}×{cfg['atr_multiplier']})")
        if cfg["enable_layer_3"]: layers.append(f"L3:Time({cfg['max_bars_held']}bars)")
        if cfg["enable_layer_4"]: layers.append("L4:RoundNumOffset")
        print(f"Risk Mgmt : {' | '.join(layers) if layers else 'ALL DISABLED'}")
    else:
        print("Risk Mgmt : DISABLED")

    if cooldown_tracker is not None and cooldown_tracker.enabled:
        print(f"Cooldown  : {cooldown_tracker.cooldown_bars}-bar window after "
              f"{cooldown_tracker.cooldown_after_reasons}")
    else:
        print("Cooldown  : DISABLED")

    if position_sizer is not None and position_sizer.enabled:
        print(
            f"Sizing    : {position_sizer.method} | "
            f"{position_sizer.risk_per_trade_pct*100:.1f}% risk/trade | "
            f"{position_sizer.max_position_pct*100:.0f}% max position"
        )
    else:
        print("Sizing    : DISABLED (all-in)")
    if use_next_day_fills:
        print("Fill mode : NEXT-DAY OPEN (AMO-realistic — signals defer to tomorrow's open)")
    elif fill_on_next_open:
        print("Fill mode : NEXT-OPEN (legacy, no cooldown/sizing integration)")
    else:
        print("Fill mode : SAME-DAY CLOSE (default)")

    # ── NIFTY regime filter: pre-compute bull/bear dict before the main loop ──
    # Key: date (Python date object) → True = NIFTY SMA20 > SMA50 (bull), False = bear
    # Empty dict means filter is disabled or data unavailable → all dates default to bull.
    _nifty_bull: dict = {}
    if nifty_regime_filter:
        if nifty_regime_df is not None and len(nifty_regime_df) >= 50:
            _nc   = nifty_regime_df["close"]
            _ns20 = _nc.rolling(20).mean()
            _ns50 = _nc.rolling(50).mean()
            _bull = _ns20 > _ns50
            _nifty_bull = {
                ts.date(): bool(v)
                for ts, v in _bull.items()
                if not pd.isna(v)
            }
            _n_bull = sum(_nifty_bull.values())
            print(
                f"NIFTY Filter: ACTIVE — {_n_bull}/{len(_nifty_bull)} bull days "
                f"({_n_bull/len(_nifty_bull)*100:.0f}% in uptrend)"
            )
        else:
            print("NIFTY Filter: ACTIVE but no data provided — filter disabled for this run")
    else:
        print("NIFTY Filter: DISABLED")
    print("="*60)

    # Pending deferred action from the previous bar's signal (use_next_day_fills only).
    # None  → no pending action
    # dict  → {"action": "buy",  "chandelier": float|None}           (from a +1 signal)
    #          {"action": "sell", "reason":     str}                  (from a -1 signal)
    #          {"action": "sell", "type": "rm_exit", "reason": str}  (deferred RM exit)
    # When use_next_day_fills=True, RM exits are also deferred to next day's open.
    # Last-bar edge case always falls back to close-fill so no signal is silently dropped.
    _pending_fill = None

    portfolio_values  = []
    dates             = []

    # Benchmark: buy-and-hold from the first bar with a valid signal
    first_valid_idx = signals.first_valid_index()
    bm_entry_price  = df.loc[first_valid_idx, "close"] if first_valid_idx else df["close"].iloc[0]
    bm_shares       = int(portfolio.initial_capital / bm_entry_price)
    benchmark_values = []

    n_bars = len(df)

    for i, (date, row) in enumerate(df.iterrows()):
        # Slice history up to and including the current bar.
        # Passed to RiskManager so ATR uses no lookahead.
        # Only construct when needed (RM or RM seeding requires it).
        ohlcv_so_far = df.iloc[: i + 1]

        signal         = signals.loc[date]
        rm_exited      = False

        # ── ⓪ Execute pending next-day fill (deferred from yesterday's signal) ──
        # This runs BEFORE the RM check so the position is open when RM evaluates
        # today's close. The fill price is today's open (AMO-realistic).
        # RM seeds (on_position_open) use the actual open fill price, not the
        # signal-day close, so stop levels are computed from the true entry price.
        if use_next_day_fills and _pending_fill is not None:
            _action = _pending_fill["action"]

            if _action == "buy" and portfolio.shares_held == 0:
                _sizing_active = (position_sizer is not None and position_sizer.enabled)

                if _sizing_active:
                    # Use yesterday's pre-computed chandelier (signal-day decision).
                    # Slippage is applied internally by portfolio.buy(); exec_price
                    # here is only used for RM seeding and sizing — must match.
                    exec_price = apply_slippage(row["open"], "buy")
                    _chan_pf    = _pending_fill["chandelier"]

                    # Seed RM with the actual fill price before sizing
                    if risk_manager is not None:
                        risk_manager.on_position_open(exec_price, date, ohlcv_so_far)

                    port_value = portfolio.current_value(row["open"])
                    shares, sz = position_sizer.calculate_shares(
                        exec_price, port_value, _chan_pf, portfolio.cash
                    )

                    _chan_str = (
                        f"₹{_chan_pf:.2f}" if _chan_pf is not None else "N/A"
                    )

                    if shares == 0:
                        position_sizer.skipped_count += 1
                        print(
                            f"  [SIZING] {date.date()} | SKIPPED_SIZING (AMO next-day) "
                            f"| chan={_chan_str} ({sz.get('stop_source','?')}) "
                            f"| dist=₹{sz.get('stop_distance',0):.2f} "
                            f"| risk=₹{sz.get('risk_amount',0):.0f}"
                        )
                        if risk_manager is not None:
                            risk_manager.on_position_close()
                    else:
                        print(
                            f"  [SIZING] {date.date()} (AMO open)"
                            f"| entry=₹{exec_price:.2f} "
                            f"| chan={_chan_str} ({sz['stop_source']}) "
                            f"| dist=₹{sz['stop_distance']:.2f} "
                            f"| risk=₹{sz['risk_amount']:.0f} "
                            f"→ {shares} shr [{sz['binding']}]"
                        )
                        portfolio.buy(date, row["open"], shares=shares)
                        if portfolio.shares_held == 0 and risk_manager is not None:
                            risk_manager.on_position_close()
                else:
                    # All-in next-day fill at open
                    portfolio.buy(date, row["open"])
                    if risk_manager is not None and portfolio.shares_held > 0:
                        risk_manager.on_position_open(
                            portfolio.entry_price, date, ohlcv_so_far
                        )

            elif _action == "sell" and portfolio.shares_held > 0:
                fill_type = _pending_fill.get("type", "strategy_exit")
                portfolio.sell(date, row["open"], reason=_pending_fill["reason"])
                if risk_manager is not None:
                    risk_manager.on_position_close()
                # RM exits trigger cooldown even when deferred; strategy exits do not.
                if fill_type == "rm_exit" and cooldown_tracker is not None:
                    cooldown_tracker.trigger_cooldown(_pending_fill["reason"], date)

            _pending_fill = None

        # ── ① Risk management ────────────────────────────────────────────────
        # When use_next_day_fills=True: RM exits defer to tomorrow's open via
        # _pending_fill (type="rm_exit"), exactly like strategy entries.
        # Last-bar falls back to close-fill so no exit is silently lost.
        # When use_next_day_fills=False (close-fill): execute at current close.
        if (risk_manager is not None
                and portfolio.shares_held > 0
                and not fill_on_next_open):

            exit_decision = risk_manager.check_exit(row, ohlcv_so_far)

            if exit_decision["should_exit"]:
                _is_last_bar_rm = (i == n_bars - 1)
                if use_next_day_fills and not _is_last_bar_rm:
                    # Defer RM exit to tomorrow's open (AMO-realistic)
                    _pending_fill = {
                        "action": "sell",
                        "type":   "rm_exit",
                        "reason": exit_decision["exit_reason"],
                    }
                    fill_date = df.index[i + 1].date()
                    print(
                        f"  [AMO] {date.date()} | RM EXIT ({exit_decision['exit_reason']}) "
                        f"deferred → fill at {fill_date} open"
                    )
                    # on_position_close() and cooldown are deferred to fill execution
                else:
                    # Close-fill mode or last bar: sell at current close immediately
                    portfolio.sell(
                        date,
                        exit_decision["exit_price"],
                        reason=exit_decision["exit_reason"],
                    )
                    if cooldown_tracker is not None:
                        cooldown_tracker.trigger_cooldown(exit_decision["exit_reason"], date)
                    risk_manager.on_position_close()
                rm_exited = True

        # ── ② + ③ Strategy signal + cooldown gate (skip if RM exited) ────────
        if not rm_exited:
            if use_next_day_fills:
                # ── AMO deferred-fill path ────────────────────────────────────
                # Signal is evaluated at today's close. Execution is deferred to
                # tomorrow's open via _pending_fill (processed in ⓪ above).
                # RM exits always fire at today's close — never deferred.
                # Last-bar edge case: fill at today's close so no signal is lost.
                _is_last_bar = (i == n_bars - 1)

                if signal == 1 and portfolio.shares_held == 0 and _pending_fill is None:
                    _regime_ok = not _nifty_bull or _nifty_bull.get(date.date(), True)
                    if not _regime_ok:
                        print(
                            f"  [REGIME] {date.date()} | BUY suppressed "
                            f"— NIFTY in death cross (SMA20 < SMA50)"
                        )
                    elif cooldown_tracker is not None and cooldown_tracker.is_in_cooldown():
                        cooldown_tracker.record_suppressed_signal(date, float(row["close"]))
                        print(
                            f"  [COOLDOWN] {date.date()} | buy suppressed "
                            f"({cooldown_tracker.cooldown_remaining} bars remaining)"
                        )
                    elif _is_last_bar:
                        # Last bar: fall back to close-fill so the trade is captured
                        _sizing_active = (
                            position_sizer is not None and position_sizer.enabled
                        )
                        exec_price = apply_slippage(row["close"], "buy")
                        entry_high = float(ohlcv_so_far["high"].iloc[-1])
                        chandelier_for_sizing = (
                            risk_manager.compute_chandelier(ohlcv_so_far, entry_high)
                            if risk_manager is not None else None
                        )
                        if risk_manager is not None:
                            risk_manager.on_position_open(exec_price, date, ohlcv_so_far)
                        if _sizing_active:
                            port_value = portfolio.current_value(row["close"])
                            shares, sz = position_sizer.calculate_shares(
                                exec_price, port_value, chandelier_for_sizing, portfolio.cash
                            )
                            if shares == 0:
                                position_sizer.skipped_count += 1
                                if risk_manager is not None:
                                    risk_manager.on_position_close()
                            else:
                                portfolio.buy(date, row["close"], shares=shares)
                                if portfolio.shares_held == 0 and risk_manager is not None:
                                    risk_manager.on_position_close()
                        else:
                            portfolio.buy(date, row["close"])
                            if risk_manager is not None and portfolio.shares_held == 0:
                                risk_manager.on_position_close()
                    else:
                        # Normal case: defer buy to tomorrow's open
                        entry_high = float(ohlcv_so_far["high"].iloc[-1])
                        chandelier_for_sizing = (
                            risk_manager.compute_chandelier(ohlcv_so_far, entry_high)
                            if risk_manager is not None else None
                        )
                        _pending_fill = {"action": "buy", "chandelier": chandelier_for_sizing}
                        fill_date = df.index[i + 1].date()
                        print(
                            f"  [AMO] {date.date()} | BUY deferred → "
                            f"fill at {fill_date} open "
                            f"| chan=₹{chandelier_for_sizing:.2f}"
                            if chandelier_for_sizing is not None else
                            f"  [AMO] {date.date()} | BUY deferred → fill at {fill_date} open"
                        )

                elif signal == -1 and portfolio.shares_held > 0:
                    if _is_last_bar:
                        portfolio.sell(date, row["close"], reason="STRATEGY_SIGNAL")
                        if risk_manager is not None:
                            risk_manager.on_position_close()
                    else:
                        _pending_fill = {"action": "sell", "reason": "STRATEGY_SIGNAL"}
                        fill_date = df.index[i + 1].date()
                        print(f"  [AMO] {date.date()} | SELL deferred → fill at {fill_date} open")

            elif fill_on_next_open:
                # Next-open fills: yesterday's signal fills at today's open.
                # Cooldown is not integrated here (Phase 2 work).
                if i > 0:
                    prev_signal = signals.loc[df.index[i - 1]]
                    if prev_signal == 1:
                        portfolio.buy(date, row["open"])
                        if risk_manager is not None and portfolio.shares_held > 0:
                            risk_manager.on_position_open(
                                portfolio.entry_price, date, ohlcv_so_far
                            )
                    elif prev_signal == -1:
                        portfolio.sell(date, row["open"], reason="STRATEGY_SIGNAL")
                        if risk_manager is not None:
                            risk_manager.on_position_close()
            else:
                # ── Close-fill (default) ──────────────────────────────────────
                if signal == 1 and portfolio.shares_held == 0:
                    _regime_ok = not _nifty_bull or _nifty_bull.get(date.date(), True)
                    if not _regime_ok:
                        print(
                            f"  [REGIME] {date.date()} | BUY suppressed "
                            f"— NIFTY in death cross (SMA20 < SMA50)"
                        )
                    # ── ③ Cooldown gate ───────────────────────────────────────
                    elif cooldown_tracker is not None and cooldown_tracker.is_in_cooldown():
                        # Buy signal exists but cooldown is active — suppress.
                        cooldown_tracker.record_suppressed_signal(date, float(row["close"]))
                        print(
                            f"  [COOLDOWN] {date.date()} | buy suppressed "
                            f"({cooldown_tracker.cooldown_remaining} bars remaining)"
                        )
                    else:
                        _sizing_active = (
                            position_sizer is not None and position_sizer.enabled
                        )

                        if _sizing_active:
                            # ─ Fixed-fractional sizing path ───────────────────
                            # Steps: compute exec_price → estimate Chandelier
                            # (pure, no RM state change) → seed RM → calculate
                            # shares → buy with pre-computed quantity.
                            #
                            # compute_chandelier() is called BEFORE on_position_open()
                            # and modifies no RM state, so the all-in path (sizing
                            # disabled) is bit-identical — the two paths are fully
                            # independent.
                            exec_price  = apply_slippage(row["close"], "buy")
                            entry_high  = float(ohlcv_so_far["high"].iloc[-1])

                            chandelier_for_sizing = (
                                risk_manager.compute_chandelier(ohlcv_so_far, entry_high)
                                if risk_manager is not None else None
                            )

                            # Seed RM (identical call site as the all-in path)
                            if risk_manager is not None:
                                risk_manager.on_position_open(exec_price, date, ohlcv_so_far)

                            port_value = portfolio.current_value(row["close"])
                            shares, sz = position_sizer.calculate_shares(
                                exec_price, port_value, chandelier_for_sizing, portfolio.cash
                            )

                            _chan_str = (
                                f"₹{chandelier_for_sizing:.2f}"
                                if chandelier_for_sizing is not None else "N/A"
                            )

                            if shares == 0:
                                position_sizer.skipped_count += 1
                                print(
                                    f"  [SIZING] {date.date()} | SKIPPED_SIZING "
                                    f"| chan={_chan_str} ({sz.get('stop_source','?')}) "
                                    f"| dist=₹{sz.get('stop_distance',0):.2f} "
                                    f"| risk=₹{sz.get('risk_amount',0):.0f}"
                                )
                                if risk_manager is not None:
                                    risk_manager.on_position_close()
                            else:
                                print(
                                    f"  [SIZING] {date.date()} "
                                    f"| entry=₹{exec_price:.2f} "
                                    f"| chan={_chan_str} ({sz['stop_source']}) "
                                    f"| dist=₹{sz['stop_distance']:.2f} "
                                    f"| risk=₹{sz['risk_amount']:.0f} "
                                    f"→ {shares} shr [{sz['binding']}]"
                                )
                                portfolio.buy(date, row["close"], shares=shares)
                                # If buy failed for any reason, reset RM
                                if portfolio.shares_held == 0 and risk_manager is not None:
                                    risk_manager.on_position_close()
                        else:
                            # ─ Original all-in path (unchanged) ──────────────
                            portfolio.buy(date, row["close"])
                            if risk_manager is not None and portfolio.shares_held > 0:
                                risk_manager.on_position_open(
                                    portfolio.entry_price, date, ohlcv_so_far
                                )

                elif signal == -1 and portfolio.shares_held > 0:
                    portfolio.sell(date, row["close"], reason="STRATEGY_SIGNAL")
                    if risk_manager is not None:
                        risk_manager.on_position_close()
                    # Cooldown is NOT triggered for strategy exits.
                    # The strategy exited cleanly; no reason to suppress future entries.

        # ── Invariant: cooldown active ⟹ no open position ─────────────────────
        # If this fires, it means a buy slipped through while cooldown was active.
        if (cooldown_tracker is not None
                and cooldown_tracker.is_in_cooldown()
                and portfolio.shares_held > 0):
            raise AssertionError(
                f"BUG on {date.date()}: cooldown is active "
                f"(remaining={cooldown_tracker.cooldown_remaining}) "
                f"but portfolio still holds {portfolio.shares_held} shares. "
                f"A buy executed during a cooldown window — check the loop logic."
            )

        # ── ④ Advance cooldown counter — EXACTLY ONCE PER BAR ─────────────────
        # Unconditional. This is the only call site. advance_bar() is NOT called
        # inside any branch above. The `rm_exited` flag + if/else above ensure
        # we reach this line on every bar without `continue` bypassing it.
        if cooldown_tracker is not None:
            cooldown_tracker.advance_bar()

        # ── Mark-to-market ─────────────────────────────────────────────────────
        portfolio_values.append(portfolio.current_value(row["close"]))
        dates.append(date)
        benchmark_values.append(bm_shares * row["close"])

    equity_curve = pd.DataFrame(
        {"portfolio_value": portfolio_values, "benchmark_value": benchmark_values},
        index=dates,
    )
    rolling_peak = equity_curve["portfolio_value"].cummax()
    equity_curve["drawdown"] = (
        (equity_curve["portfolio_value"] - rolling_peak) / rolling_peak * 100
    )
    return equity_curve


# ── Print helpers ─────────────────────────────────────────────────────────────

def print_summary(equity_curve: pd.DataFrame, portfolio: Portfolio) -> None:
    """Print a human-readable performance report to stdout."""
    trades   = portfolio.get_trade_log()
    initial  = portfolio.initial_capital
    final    = equity_curve["portfolio_value"].iloc[-1]
    bm_final = equity_curve["benchmark_value"].iloc[-1]

    total_return_pct = (final / initial - 1) * 100
    bm_return_pct    = (bm_final / equity_curve["benchmark_value"].iloc[0] - 1) * 100
    max_drawdown     = equity_curve["drawdown"].min()

    print("\n" + "="*70)
    print("BACKTEST SUMMARY")
    print("="*70)
    print(f"  Initial capital     : ₹{initial:>12,.2f}")
    print(f"  Final value         : ₹{final:>12,.2f}")
    print(f"  Total return        : {total_return_pct:>+.2f}%")
    print(f"  Benchmark (B&H)     : {bm_return_pct:>+.2f}%")
    print(f"  Max drawdown        : {max_drawdown:.2f}%")

    if trades.empty:
        print("  No completed trades.")
        print("="*70)
        return

    n_trades  = len(trades)
    n_wins    = (trades["net_pnl"] > 0).sum()
    win_rate  = n_wins / n_trades * 100
    avg_win   = trades.loc[trades["net_pnl"] > 0,  "net_pnl"].mean() if n_wins > 0 else 0.0
    avg_loss  = trades.loc[trades["net_pnl"] <= 0, "net_pnl"].mean() if (n_trades - n_wins) > 0 else 0.0
    payoff    = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
    total_pnl = trades["net_pnl"].sum()

    print(f"\n  Completed trades    : {n_trades}")
    print(f"  Win rate            : {win_rate:.1f}% ({n_wins}W / {n_trades - n_wins}L)")
    print(f"  Avg winning trade   : ₹{avg_win:>+,.2f}")
    print(f"  Avg losing trade    : ₹{avg_loss:>+,.2f}")
    print(f"  Payoff ratio        : {payoff:.2f}:1")
    print(f"  Expectancy/trade    : ₹{expectancy:>+,.2f}")
    print(f"  Total net P&L       : ₹{total_pnl:>+,.2f}")

    if "exit_reason" in trades.columns:
        reason_counts = trades["exit_reason"].value_counts()
        print(f"\n  Exit reasons:")
        for reason, count in reason_counts.items():
            pnl = trades.loc[trades["exit_reason"] == reason, "net_pnl"].sum()
            print(f"    {reason:<20}: {count:>2} trade(s)  P&L ₹{pnl:>+,.2f}")

    has_reason = "exit_reason" in trades.columns
    hdr = (
        f"  {'#':>3}  {'Entry':>12}  {'Exit':>12}  "
        f"{'Entry ₹':>10}  {'Exit ₹':>10}  {'Shrs':>6}  "
        f"{'Net P&L':>10}  {'Ret%':>7}"
    )
    if has_reason:
        hdr += "  Exit Reason"

    print(f"\n  TRADE LOG:")
    print(hdr)
    print("  " + "-" * (82 + (24 if has_reason else 0)))

    for idx, t in trades.iterrows():
        line = (
            f"  {idx+1:>3}  {str(t['entry_date'].date()):>12}  {str(t['exit_date'].date()):>12}  "
            f"₹{t['entry_price']:>9.2f}  ₹{t['exit_price']:>9.2f}  "
            f"{t['shares']:>6}  ₹{t['net_pnl']:>+9.2f}  {t['return_pct']:>+6.2f}%"
        )
        if has_reason:
            line += f"  {t.get('exit_reason', 'STRATEGY_SIGNAL')}"
        print(line)

    print("="*70)


def print_cooldown_analysis(
    cooldown_tracker,       # CooldownTracker instance
    df: pd.DataFrame,
    ticker: str,
    forward_bars: int = 5,
) -> None:
    """
    Print a post-backtest audit of cooldown events and suppressed buy signals.

    For each suppressed buy signal, looks forward `forward_bars` bars to report
    whether the stock declined (cooldown SAVED a loss) or rose (cooldown COST
    a gain). This is the key metric for evaluating whether the cooldown is
    calibrated correctly.
    """
    if cooldown_tracker is None or not cooldown_tracker.enabled:
        return

    print("\n" + "="*70)
    print(f"COOLDOWN ANALYSIS  ({cooldown_tracker.cooldown_bars}-bar window)")
    print("="*70)

    # ── Trigger events ────────────────────────────────────────────────────────
    print(f"\n  Cooldown trigger events: {len(cooldown_tracker.cooldown_history)}")
    for ev in cooldown_tracker.cooldown_history:
        print(f"    {str(ev['trigger_date'].date() if hasattr(ev['trigger_date'], 'date') else ev['trigger_date']):>12}"
              f"  {ev['exit_reason']:<18}  window: {ev['cooldown_bars']} bars")

    # ── Suppressed signals ────────────────────────────────────────────────────
    suppressed = cooldown_tracker.suppressed_signals
    print(f"\n  Suppressed buy signals: {len(suppressed)}")

    if not suppressed:
        print("  (None)")
        print("="*70)
        return

    saved = 0   # cooldown prevented a loss
    cost  = 0   # cooldown missed a gain

    print(f"\n  {'Date':>12}  {'Close':>8}  {'Remain':>7}  "
          f"{'Reason':>18}  {f'+{forward_bars}d return':>12}  Verdict")
    print("  " + "-"*80)

    for sig in suppressed:
        sig_date   = sig["date"]
        sig_close  = sig["close_price"]
        sig_remain = sig["cooldown_remaining"]
        sig_reason = sig.get("last_exit_reason", "?")

        # Look up where this date falls in df
        try:
            idx = df.index.get_loc(sig_date)
        except KeyError:
            print(f"  {str(sig_date):<12}  {sig_close:>8.2f}  {sig_remain:>7}  date not in df")
            continue

        forward_idx = min(idx + forward_bars, len(df) - 1)
        future_close = float(df["close"].iloc[forward_idx])
        fwd_return   = (future_close - sig_close) / sig_close * 100

        if fwd_return < 0:
            verdict = "SAVED  ✓ (fell)"
            saved += 1
        else:
            verdict = "COST   ✗ (rose)"
            cost += 1

        date_str = str(sig_date.date()) if hasattr(sig_date, 'date') else str(sig_date)
        print(
            f"  {date_str:>12}  ₹{sig_close:>7.2f}  {sig_remain:>7}  "
            f"{sig_reason:>18}  {fwd_return:>+11.2f}%  {verdict}"
        )

    total_sup = len(suppressed)
    print("  " + "-"*80)
    print(f"\n  Summary: {saved}/{total_sup} suppressed signals SAVED a loss "
          f"({saved/total_sup*100:.0f}%)")
    print(f"           {cost}/{total_sup} suppressed signals COST a gain "
          f"({cost/total_sup*100:.0f}%)")
    print(f"\n  Interpretation:")
    if saved > cost:
        print(f"  → Cooldown was protective: it blocked more losses than gains missed.")
        print(f"    The {cooldown_tracker.cooldown_bars}-bar window appears well-calibrated.")
    elif cost > saved:
        print(f"  → Cooldown was costly: it blocked more gains than losses saved.")
        print(f"    Consider reducing cooldown_bars to reduce false negatives.")
    else:
        print(f"  → Neutral: equal saves and costs. Cooldown had minimal net impact.")

    print("="*70)
