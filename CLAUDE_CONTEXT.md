# Claude Context — NSE Algo Trading System
Last updated: 2026-07-17

## Current Trading Universe (10 stocks)
BAJAJ-AUTO.NS, HCLTECH.NS, COLPAL.NS, ANURAS.NS, BSOFT.NS, PERSISTENT.NS, CHOLAHLDNG.NS, COHANCE.NS, MAPMYINDIA.NS, EMAMILTD.NS

## Universe History
- Original (Jun 2): TMPV, WHIRLPOOL, SIEMENS, BAJAJ-AUTO
- Added Jun 12: CUMMINSIND, HCLTECH
- Added Jun 16: BOSCHLTD, COLPAL, ANURAS, HEROMOTOCO
- Added Jun 17: NEWGEN, JKTYRE, BSOFT, RPOWER
- Removed Jun 17: RPOWER (governance risk), BOSCHLTD (walk-forward 1/5)
- Removed Jun 18: TMPV (Hurst degraded H=0.468, 2 consecutive screens), CUMMINSIND (Hurst degraded H=0.472)
- Added Jun 24: PERSISTENT.NS (replacing SIEMENS slot, screener validated H=0.519 ADX=30.8 corr=0.231)
- Removed Jun 24: WHIRLPOOL (fails min_abs_oos_ret +3.5% vs +4% threshold, payoff 1.48 below 1.5)
- Removed Jun 24: HEROMOTOCO (2/5 extended WF, never generated entry signal since added Jun 16)
- Removed Jun 24: SIEMENS (3/5 both WF windows, OOS return ~0%, position closed Jun 24 at Rs3,688)
- Removed Jul 7 2026: JKTYRE.NS — WF FAIL (2/6 original, OOS -0.3%,
  expectancy -Rs83/trade, H=0.214 mean-reverting)
  HURST_SKIP fired Jul 6 on golden cross — Hurst gate prevented bad entry
  Replace at next screener cycle with validated candidate
- Added Jul 9 2026: CHOLAHLDNG.NS (screener ADD recommendation Jul 8 2026,
  WF validated: original 5/6 OOS +15.4%, extended 5/6 OOS +9.1%)
  NOTE: crossed to golden cross the SAME DAY it was added, but was deployed
  to the server AFTER that day's 3:45 PM signal run had already executed —
  entry was missed. See "Deployment Timing Lesson" below.
- Added Jul 10 2026: COHANCE.NS (from Jul 8 screener WATCHLIST — already in death
  cross at screening time, not yet crossed as of Jul 10).
  WF validated: original 6/6 OOS +8.0% (strongest score of the week),
  extended SKIPPED — insufficient historical data (only 20 bars in 2015-2019
  window, needs ≥200). Deployed ~1hr before that day's signal run while still
  in death cross — zero timing gap (contrast with CHOLAHLDNG.NS above).
- Removed Jul 12 2026: NEWGEN.NS — 3 consecutive degradation flags (Jul 5, Jul 8,
  Jul 12), ADX=19.1 (below 22.0 degradation threshold and 25.0 screener entry bar),
  FALLING trend, Hurst still healthy (0.541) — an ADX/trend-strength decline, not
  a Hurst-driven removal like JKTYRE's.
  First two flags (Jul 5, Jul 8) coincided with NIFTY's Jul 7 regime transition and
  were deliberately held, per the regime-transition annotation feature (commit
  3044b88) — judged likely transition noise. Held on explicit condition that a third
  consecutive flag after the regime stabilized would change the assessment.
  NIFTY stable BULL for 5+ trading days by Jul 12; the Jul 12 flag carries NO
  regime-transition annotation, confirming the system does not attribute it to
  transition noise.
  Clean differential: BSOFT.NS was flagged on the identical two prior dates under
  the same regime conditions and recovered on this same Jul 12 screen ("regime
  healthy again"). NEWGEN did not — real evidence of stock-specific decay, not a
  shared external cause.
  NEWGEN's SMA20/SMA50 gap faded steadily since Jul 1 (+1.01% → +0.29%),
  consistent with this conclusion. Zero open position at removal — clean exit.
  Screener's own Jul 12 REMOVE recommendation concurred.
- Added Jul 12 2026: MAPMYINDIA.NS (screener ADD recommendation Jul 12, from a
  batch that also included HAL.NS, DMART.NS, HINDUNILVR.NS, CLEAN.NS — all four
  FAILED WF, only MAPMYINDIA passed).
  WF validated: original 5/6 OOS +7.0%, extended SKIPPED — insufficient historical
  data (no bars in 2015-2019 IS window, same pattern as COHANCE.NS and PAYTM.NS).
  Confirmed still in death cross at add time (SMA20=901.72, SMA50=906.37,
  gap -0.51%) — deployed same-day, catching the next golden cross live with zero
  timing gap.
- Added Jul 17 2026: EMAMILTD.NS (screener ADD recommendation Jul 15 2026,
  same batch as ESCORTS.NS, BRITANNIA.NS, HINDUNILVR.NS, CLEAN.NS — all
  four already-tested/FAILED from prior batches; EMAMILTD.NS was the one
  genuinely new candidate in this batch).
  WF validated: original 6/6 OOS +9.0%, extended 5/6 OOS +13.6%.
  Was DEATH cross (-0.25%) at WF validation time (Jul 15); crossover
  traced day-by-day and confirmed genuine/smooth: -2.17% (Jul 9) through
  +0.05% GOLDEN (Jul 16-17) — a real, monotonic crossover, not a data
  artifact. Live Hurst confirmed healthy at 0.641 (Jul 17), well clear
  of the 0.48 gate — unlike COHANCE.NS's razor-thin Hurst miss on its
  own crossover, this one should clear the live entry gate if the
  crossover holds through close.
  Deployed same-day for zero timing gap, per the lesson from
  CHOLAHLDNG.NS's Jul 9 miss.

### Deployment Timing Lesson (Jul 9-10 2026)
CHOLAHLDNG.NS was validated and decided on Jul 9, but crossed to golden cross
that same day — the code change wasn't deployed to the server until AFTER that
day's signal run had already executed, so the entry was missed. Lesson: when
adding a validated stock, deploy to the server IMMEDIATELY after committing,
not at a later point in the session. Applied successfully for COHANCE.NS on
Jul 10 (deployed ~1hr before that day's run, while still in death cross — no
timing gap).

## Paper Trading Status (as of 2026-07-05)
- Started: 2026-06-02 (33 trading days as of Jul 5)
- Portfolio: Rs243.49 cash + 359 NIFTYBEES units (verified from server Jul 5)
- ETF: 359 NIFTYBEES units @ 100% tier (0 open positions)
- Open positions: 0
- Total trades: 4 (confirmed from portfolio_state.json)
- NIFTY regime: BEAR as of last_run 2026-07-03
- NIFTY SMA gap closing rapidly — -0.07% on Jul 3, BULL flip imminent
- Confirmed completed trades: 4
  - WHIRLPOOL: -₹188 (Jun 3, death cross)
  - TMPV: -₹1,297 (Jun 17, Chandelier stop)
  - SIEMENS: -₹157 (Jun 24, death cross, filled at ₹3,688)
  - BAJAJ-AUTO: -₹441 gross (Jun 29, death cross, filled at ₹9,892 — gapped UP)
- All 7 stocks waiting for golden cross entry signal (JKTYRE removed Jul 7)
- repair_portfolio_state.py: no longer needed — BAJAJ-AUTO filled cleanly Jun 29

## Walk-Forward Validation Results
- Last run score: 17/24 (71%) — SYSTEM VALIDATED (threshold 65%)
  Note: 17/24 was run on original 4-stock universe with dynamic OOS (date.today())
  Has NOT been rerun on current 5-stock universe — next run due October 2026
- OOS end date is dynamic (date.today()) — always includes latest live data
- 6 metrics per stock: OOS>IS return, Sharpe>0, payoff>1.5, win rate>40%, expectancy>0, min OOS +4%
- Individual stock results:
  - BAJAJ-AUTO.NS: 6/6 original OOS +13.5% — WF validated (original validation)
  - COLPAL.NS: 10/12 both windows — WF validated Jun 24 2026
  - HCLTECH.NS: 6/6 original OOS +5.5%, 4/6 extended — WF validated Jul 6 2026
  - BSOFT.NS: 6/6 original OOS +8.5%, 5/6 extended — WF validated Jul 6 2026
  - PERSISTENT.NS: 5/6 original OOS +11.7%, 5/6 extended — WF validated Jul 6 2026
    Command: python validation/walk_forward.py --ticker PERSISTENT.NS
  - JKTYRE.NS: REMOVED Jul 7 2026 — WF FAIL 2/6 original (OOS -0.3%,
    expectancy -Rs83/trade). Do not re-add without new WF validation.
  - WHIRLPOOL: FAIL — OOS +3.5% below +4% floor, payoff 1.48 — removed Jun 24
  - SIEMENS: FAIL — OOS ~0%, rolling WARNING — removed Jun 24
  - NEWGEN.NS: 4/6 original OOS +10.0%, 5/6 extended OOS +17.6%
    WF validated Jul 7 2026. REMOVED Jul 12 2026 — 3 consecutive ADX flags,
    ADX=19.1 FALLING (stock-specific decay confirmed, not regime noise).
  - ANURAS.NS: listed Mar 24 2021, only ~440 IS bars in 2018-2022 window
    Cannot validate with current IS window definition
    Re-evaluate at October 2026 quarterly review with updated IS window
  - CHOLAHLDNG.NS: 5/6 original OOS +15.4%, 5/6 extended OOS +9.1%
    WF validated Jul 8 2026 — added to universe Jul 9 2026
  - COHANCE.NS: 6/6 original OOS +8.0%, extended SKIPPED (insufficient data,
    only 20 bars in 2015-2019 window) — WF validated Jul 9-10 2026, added Jul 10 2026
  - MAPMYINDIA.NS: 5/6 original OOS +7.0%, extended SKIPPED (insufficient data,
    no bars in 2015-2019 window) — WF validated Jul 12 2026, added Jul 12 2026
- walk_forward.py STOCKS updated Jul 12: BAJAJ-AUTO, HCLTECH, COLPAL, BSOFT, PERSISTENT, CHOLAHLDNG, COHANCE, MAPMYINDIA
- Run walk_forward.py quarterly — next run due October 2026

## Infrastructure
- AWS Lightsail Mumbai: ubuntu@13.205.133.169
- SSH key: ~/.ssh/LightsailDefaultKey-ap-south-1.pem
- Cron: 15 10 * * 1-5 (3:45 PM IST signal run), 50 3 * * 1-5 (9:20 AM IST morning fill check), 30 12 * * 0,3 (6 PM IST Wed+Sun screener)
- Server path: /home/ubuntu/algo-trading/
- Local path: /Users/aaravagarwal/algo-trading/
- Python on server: python3 (not python)
- Venv on server: ~/algo-trading/venv

## Key Workflow Notes
- Always SCP files from server before git push (server is source of truth)
- CRITICAL: Before SCP-ing ANY file to the server that touches paper_trading/,
  ALWAYS run the backup script first:
  ssh -i ~/.ssh/LightsailDefaultKey-ap-south-1.pem ubuntu@13.205.133.169 \
    "bash ~/algo-trading/paper_trading/backup_state.sh"
- This creates a timestamped backup in paper_trading/state_backups/
- Never SCP portfolio_state.json directly — it is in .gitignore and
  the server version is always the source of truth
- correlation_check.py is a full module — checks candidate against live open positions
  CLI: python paper_trading/correlation_check.py TICKER.NS
  In signal_runner: uses in-memory state (not file) — catches same-day BUY pairs correctly
- repair_portfolio_state.py — one-time emergency repair script, keep for future use
  Located at: paper_trading/repair_portfolio_state.py
  BAJAJ-AUTO filled cleanly Jun 29 — script was not needed
- universe_expansion.py does not exist on server
- bars_held=0 mid-day is normal — updates at 3:45 PM signal run
- morning_fill_check.py and corporate_actions.py are fully dynamic
- Check logs at: ~/algo-trading/paper_trading/logs/YYYY-MM-DD.log
- Screener logs at: ~/algo-trading/paper_trading/logs/screen_YYYY-MM-DD.log
- Screener now uses dynamic NIFTY 500 (504 stocks) — no hardcoded universe
- ADD recommendations = death cross stocks closest to golden flip (system will catch entry)
- MONITOR = already in golden cross — wait for next cycle before adding
- Divergence detection: flags stocks where 2yr and 80d SMA windows disagree

### Mandatory WF Gate Before Universe Addition
NEVER add a stock to STOCKS in signal_runner.py without running WF validation first.
Screener ADD recommendation = candidate for testing, NOT approval to add.

Step 1 — Run single-stock validation:
  python validation/walk_forward.py --ticker CANDIDATE.NS

Step 2 — Gate criteria (both must pass):
  - Original window: ≥4/6 metrics AND OOS return ≥+4%
  - Extended window: ≥4/6 metrics (if sufficient history exists)

Step 3 — If PASS:
  - Add to STOCKS in paper_trading/signal_runner.py
  - Add to STOCKS in validation/walk_forward.py
  - Document in CLAUDE_CONTEXT Universe History with scores and date

Step 4 — If FAIL:
  - Do NOT add to universe
  - Note reason in CLAUDE_CONTEXT
  - Re-test after 2 screener cycles (minimum 4 weeks)

Additional flags:
  --no-extended: skip extended window (faster, use for quick checks)
  Example: python validation/walk_forward.py --ticker HCLTECH.NS --no-extended

Stocks validated Jul 6-7 2026 using this gate:
  PERSISTENT.NS: PASS (5/6 original OOS +11.7%, 5/6 extended OOS +10.2%)
  HCLTECH.NS:    PASS (6/6 original OOS +5.5%,  4/6 extended OOS +2.5%)
  BSOFT.NS:      PASS (6/6 original OOS +8.5%,  5/6 extended OOS +11.4%)
  NEWGEN.NS:     PASS (4/6 original OOS +10.0%, 5/6 extended OOS +17.6%)
  JKTYRE.NS:     FAIL (2/6 original OOS -0.3%,  expectancy -Rs83/trade)
                 → Removed from universe Jul 7 2026

Stocks validated Jul 8 2026 using this gate:
  CHOLAHLDNG.NS: PASS (5/6 original OOS +15.4%, 5/6 extended OOS +9.1%)
                 → Added to universe Jul 9 2026
  (Same 2026-07-08 screener batch — 4 other ADD candidates tested, all FAILED WF gate, not added:
  TMPV.NS:       FAIL (3/6 original OOS +0.5%,  5/6 extended OOS +6.2%)
  SOBHA.NS:      FAIL (3/6 original OOS +0.3%,  3/6 extended OOS +3.8%)
  GODREJCP.NS:   FAIL (4/6 both windows, OOS +0.8%/+1.8% below +4% floor)
  BDL.NS:        FAIL (1/6 original, 0/6 extended — clean rejection))

Stocks validated Jul 9-10 2026 using this gate:
  COHANCE.NS:    PASS (6/6 original OOS +8.0%, extended SKIPPED —
                 insufficient data, only 20 bars in 2015-19 window)
                 → Added to universe Jul 10 2026, still in death cross at
                 add time — zero timing gap
  ALKEM.NS:      PASS (4/6 original OOS +5.7%, 4/6 extended OOS +2.6%)
                 → NOT yet added — already in golden cross (+0.39% gap) as
                 of Jul 9; would need to wait for next cross cycle
  GLAXO.NS:      PASS (5/6 original OOS +8.5%, 4/6 extended OOS +0.6%)
                 → NOT yet added — already in golden cross (+1.24% gap)
  CASTROLIND.NS: PASS (5/6 original OOS +5.3%, 5/6 extended OOS +4.5%)
                 → NOT yet added — already in golden cross (+0.41% gap)
  PAYTM.NS:      PASS (4/6 original OOS +8.0%, extended SKIPPED —
                 insufficient data, IPO Nov 2021)
                 → NOT yet added — already in golden cross (+2.60% gap)
  (Same batch — 5 other candidates tested, all FAILED WF, not added:
  EIHOTEL.NS, CRISIL.NS, RAMCOCEM.NS, HAL.NS, DMART.NS from Jul 8 WATCHLIST)

Stocks validated Jul 12 2026 using this gate:
  MAPMYINDIA.NS: PASS (5/6 original OOS +7.0%, extended SKIPPED —
                 insufficient data, no bars in 2015-19 window)
                 → Added to universe Jul 12 2026, still in death cross
                 at add time (gap -0.51%) — zero timing gap
  HINDUNILVR.NS: FAIL (5/6 original OOS +1.4% — below +4% floor;
                 0/6 extended OOS -7.2% — clean rejection despite
                 deceptively solid-looking original window)
  CLEAN.NS:      FAIL (1/6 original OOS -3.0%; extended SKIPPED —
                 insufficient data)
  (HAL.NS and DMART.NS from this same batch were already tested Jul 9
  — both FAILED, see Jul 9-10 entry above)

## Going Live Checklist (future)
- Minimum capital: Rs50,000 recommended
- Wait for 6 months clean paper trading data (currently at ~33 days as of Jul 5)
  Note: NIFTY BULL flip imminent (SMA gap -0.07% on Jul 3) — entries expected soon
- Implement SL-M orders for exits before going live (gap-down protection)
- Implement ATR-based position sizing before going live
- Complete at least 30 full trade cycles (entry + exit) — currently 3 confirmed
- Flip PAPER_TRADING_MODE=False in signal_runner.py
- Set LIVE_TRADING_MODE=True in morning_fill_check.py
- Disable dry-run in engine/order_manager.py
- Reset portfolio_state.json with real capital
- Archive current paper trading logs before reset
- MAX_CONCURRENT_POSITIONS=4 already set for Rs50,000 capital
- Position sizer calibrated for Rs50,000 — BAJAJ-AUTO at Rs10,000+ uses ~20% cap per trade

## System Limitations
- No news/merger monitoring — corporate_actions.py only checks scheduled NSE ex-dates
- No F&O support (future project)
- ANURAS.NS: listed Mar 24 2021, only ~440 IS bars in 2018-2022 window — cannot validate
  Re-evaluate at October 2026 quarterly review with updated IS window
- HEROMOTOCO removed Jun 24 — 2/5 extended WF, never generated entry signal
- SIEMENS removed Jun 24 — 3/5 both windows, position closed Jun 24 at Rs3,688
- Gap-down circuit breaker: COMPLETED (Jun 26) — GAP_BREAKER_THRESHOLD=3%
  If open price >3% below AMO limit: exit at open (GAP_EXIT), not requeued
  9/9 unit tests passing (paper_trading/test_gap_breaker.py)
  Note: SL-M orders still needed for real-money live trading (paper trading uses simulation)
- Position sizing not volatility-adjusted — all positions get equal capital regardless of volatility
- Correlation threshold 0.60 may be too loose for stress scenarios — review after 6 months
- Hurst threshold stays at 0.48 — raising to 0.55 filters entire current universe to zero stocks
  (verified Jun 26: all 6 live stocks have H between 0.415-0.549, none pass 0.55)
- NEWGEN.NS: WF validated Jul 7 2026 — 4/6 original OOS +10.0%, 5/6 extended OOS +17.6%
  Listed Jan 2018, sufficient IS history confirmed at validation

### Stocks Removed (Jun 24 2026)
- WHIRLPOOL: OOS return +3.5% fails min_abs_oos_ret threshold (+4%),
  payoff ratio 1.48 below 1.5 threshold, rolling WARNING.
  Walk-forward score 10/12 but fails minimum return floor.
- HEROMOTOCO: 2/5 extended walk-forward, weak payoff and expectancy.
  Never generated entry signal since added Jun 16.
- SIEMENS: pending removal — exit AMO queued, position closes Jun 25.
  3/5 both windows, OOS return ~0%, rolling WARNING.

## Quant Research Fixes (Jun 17)
### Fix 1: Exit pricing bias — COMPLETED
- Problem: RM exits (Chandelier, Hard Stop, Time Stop) were booking at today's close price, but real execution happens at next morning's open via AMO
- Fix: Deferred RM exits to next-day open in both engine/backtester.py and paper_trading/signal_runner.py, matching how strategy entries already worked
- Walk-forward rerun after fix: 15/20 PASS — identical to original
- Detail changes: TMPV OOS return -1.5pp (expected, realistic), BAJAJ-AUTO unchanged, WHIRLPOOL -0.4pp
- Net verdict: fix is safe, backtest and paper trading now model AMO fills identically end-to-end

### Remaining fixes from quant review (priority order):
2. Add portfolio-level NIFTY regime filter — if NIFTY SMA20 < SMA50, reduce position sizes or go to cash
3. Validate cooldown period — backtest 10/15/20/25 bar cooldowns, pick best
4. Fix transaction costs — add SEBI fees, exchange charges, GST, stamp duty
5. Remove RPOWER — governance risk unquantifiable
6. Extend paper trading — minimum 6 months before going live

### Fix 2: NIFTY portfolio-level regime filter — COMPLETED
- Problem: system entered long positions even during NIFTY bear markets, swimming against the tide
- Fix: added NIFTY SMA20/SMA50 check before every BUY signal in backtester.py and signal_runner.py
- If NIFTY SMA20 < SMA50: suppress all new entries, log "Market regime filter: NIFTY in death cross"
- Walk-forward result: 15/20 → 17/20 (75% → 85%) — WHIRLPOOL and SIEMENS each gained 1 pass
- Config flag: nifty_regime_filter=True in walk_forward.py line 79, toggleable
- Market regime shown in daily report header and persisted in portfolio_state.json

### Fix 3: Cooldown validation — COMPLETED
- Tested 10/15/20/25 bars across 9 qualifying stocks (ANURAS skipped, only 440 IS bars)
- Results: 10=33/45, 15=33/45, 20=31/45, 25=34/45
- Decision: keep 15 bars — ties with 10 on score but better trade quality (+4.39% vs +4.81% with 8 fewer low-quality trades)
- 25 bars marginal +1 point but within statistical noise
- Side finding: BOSCHLTD scored 1/5 — low vol compounder, not SMA crossover candidate, flag for removal
- Side finding: ANURAS has insufficient history for validation — monitor carefully

### Fix 4: Transaction costs — COMPLETED
- Old model: brokerage ₹20 + STT 0.025% + slippage 0.05% = ₹45 per ₹1,00,000 round-trip
- New model: added SEBI fee 0.0001%, NSE exchange 0.00335%, GST 18% on brokerage, stamp duty 0.015% buy-side
- New total: ₹94.09 per ₹1,00,000 round-trip — old model understated by 2x
- Walk-forward: 17/20 unchanged — strategy survives realistic costs
- OOS returns dipped 0.1-0.3pp per stock, within rounding on PASS thresholds

### Fix 5: Removed RPOWER and BOSCHLTD — COMPLETED
- RPOWER removed: governance risk (promoter pledging, debt restructuring history) — asymmetric downside unquantifiable by the model
- BOSCHLTD removed: walk-forward score 1/5 — low volatility compounder, not SMA crossover candidate
- Both had 0 open positions at time of removal — clean exit
- Universe now 12 stocks: TMPV, WHIRLPOOL, SIEMENS, BAJAJ-AUTO, CUMMINSIND, HCLTECH, COLPAL, ANURAS, HEROMOTOCO, NEWGEN, JKTYRE, BSOFT

### Fix 6: Data source mismatch — COMPLETED
- Problem: screener used 2-year window for SMA gap, signal_runner uses 120-day window — different calculations
- Fix: added compute_sma_gap_short() using last 120 calendar days, matching signal_runner exactly
- Both gap_short (80-day) and gap_long (2-year) now shown in email and dry-run
- cross classification now uses gap_short — what the trading system actually sees
- Divergence detection added: flags stocks where 2yr and 80d windows disagree on cross direction
- Step 6 verified: SIGNAL_LOOKBACK_DAYS == LOOKBACK_CALENDAR_DAYS == 120 ✅
- Today's screen: 0 divergent stocks — short and long windows agree on all candidates

### Fix 7: Simultaneous signal allocation — COMPLETED
- Problem: when multiple BUY signals fire same day, list-order bias determined which stocks got capital
- Fix: added two-phase pipeline — Phase 1 collects all BUY signals, Phase 2 allocates in rank order
- Ranking weights: Hurst 40%, gap proximity 40%, volatility 20%
- MAX_CONCURRENT_POSITIONS = 4 (conservative for ₹50,000 capital)
- can_open_position() gates: position limit + cash availability check
- RM exits are NEVER gated — always execute regardless of position count
- Skipped signals logged to signal_log.csv with reason and rank score
- Walk-forward: 17/20 unchanged (ranking only affects paper trading execution path)
- Test verified: STOCK_C (91.4) → STOCK_A (85.2) → STOCK_B (62.1) correct sort order

### Fix 10 Update: Extended test on 10 stocks — COMPLETED
- 10-stock extended (2015-19 IS / 2020-23 OOS): 37/50 (74%) — SYSTEM VALIDATED
- All 10 stocks delivered positive OOS returns through COVID crash and 2022 selloff
- BSOFT standout: +44.5% OOS, 4/5, 76.9% win rate during hardest macro period
- HEROMOTOCO weak: 2/5, poor payoff and expectancy — flag for future removal
- SIEMENS consistent 3/5 underperformer across both windows — flag for removal
- NEWGEN and ANURAS excluded: insufficient history (listed 2018 and post-2019)
- Conclusion: strategy is genuinely robust across bull and bear market periods

### Fix 9: AMO verification in morning fill check — COMPLETED
- Problem: paper trading assumed fills based on open price; live trading needs actual Zerodha confirmation
- Fix: added LIVE_TRADING_MODE flag (default False) to morning_fill_check.py
- When True: queries kite.orders() for actual order status (COMPLETE/REJECTED/CANCELLED)
- When False: existing simulation logic unchanged — no paper trading regression
- Added _check_circuit_breaker(): flags orders where open moved >=20% from prev close
- Added _fetch_live_order_status(): queries Zerodha API with graceful fallback to simulation
- Added REJECTED/CANCELLED section in morning report requiring manual attention
- order_id column added to amo_orders.csv via _ensure_csv_header() migration
- LIVE_TRADING_MODE = False confirmed via assertion test
- To activate for live trading: set LIVE_TRADING_MODE = True in morning_fill_check.py

### Fix 12: Degradation tracker integrity — COMPLETED
- Problem: tracker had no protection against corruption, server restarts, stale entries, or non-consecutive flags
- Fix: full integrity system with 4 protections:
  1. Atomic writes (write to .tmp then rename — no partial writes)
  2. Backup chain (degradation_tracker.backup.json always has previous good version)
  3. Corrupt file recovery (saves .corrupt_DATE.json backup, starts fresh)
  4. Schema v1→v2 migration (adds last_screen_date, flag_history fields)
- Stale entry cleanup: stocks removed from universe auto-deleted from tracker
- Consecutive gap check: if last screen > 5 days ago, reset counter (not truly consecutive)
- Open position guard: never recommends REMOVE if shares > 0
- All 4 integrity tests passed: empty file, corrupt file, save/reload, backup existence
- v1→v2 migration ran automatically on first dry-run: 13 entries migrated, BOSCHLTD cleaned up

### Fix 11: Remove TMPV and CUMMINSIND — COMPLETED
- Both flagged by degradation tracker for 2 consecutive screens (H < 0.52)
- TMPV: already exited via Chandelier stop Jun 17, 0 shares at removal
- CUMMINSIND: never generated entry signal after being added, 0 shares at removal
- Universe now 10 stocks: WHIRLPOOL, SIEMENS, BAJAJ-AUTO, HCLTECH, COLPAL, ANURAS, HEROMOTOCO, NEWGEN, JKTYRE, BSOFT

### Minimum Absolute OOS Return Metric — COMPLETED (Jun 18)
- Problem: strategy losing money could pass validation if OOS lost less than IS
- Fix: added 6th walk-forward metric — OOS total return must be ≥+4% cumulative to PASS
- Updated _pass() with "min_abs_oos_ret" case, row() calls in both WF functions, METRICS_KEYS list
- Max score updated from 20→24 (4 stocks × 6 metrics), validation threshold 17/24
- Unit tests confirmed: SIEMENS +0.1% FAIL, WHIRLPOOL +3.5% FAIL, TMPV +9.2% PASS, BAJAJ-AUTO +13.5% PASS
- New overall score: 19/24 (79%) — SYSTEM VALIDATED
- WHIRLPOOL and SIEMENS correctly penalised — confirms existing flag for future removal

### Auto Token Refresh — COMPLETED (Jun 18)
- Problem: Kite token expires midnight IST daily, requiring manual kite_login.py locally
- Fix: added _auto_refresh_token() to data/kite_fetcher.py
- When TokenException caught: runs auth/auto_login.py as subprocess, retries API call once
- Works on both server (automated TOTP) and local machine (same .env TOTP secret)
- Zero manual intervention needed — fully transparent to all callers
- Side fix: .env was missing newline between ZERODHA_TOTP_SECRET and SENDGRID_API_KEY — TOTP was 118 chars instead of 32, causing pyotp failures. Fixed on both local and server.
- Tested: expired token → auto-refresh → retry → 12 bars loaded ✅

### Finding #1 Fix: Double cash deduction on BUY fills — COMPLETED (Jun 19)
- Problem: signal_runner deducted cash at close price; morning_fill_check deducted again at open price
- Fix: signal_runner now calls queue_pending_buy() — no cash deduction, no position opened
- Cash deducted exactly once when morning_fill_check calls confirm_buy_fill() at actual open price
- New methods in paper_portfolio.py: queue_pending_buy(), cancel_pending_buy(), confirm_buy_fill()
- pending_buy field added to all position dicts (migration runs automatically on load)
- MISSED BUY AMOs call cancel_pending_buy() — position reset to flat, stock re-eligible for signals
- Unit tests: 3/3 passed (no cash deduction on queue, correct deduction on confirm, cancel resets)
- Walk-forward: 19/24 unchanged — no regression

### Finding #2 Fix: Orphaned RM SELL position — COMPLETED (Jun 19)
- Problem: MISSED RM SELL AMO left position stuck with pending_rm_exit=True forever
- Fix: morning_fill_check re-queues SELL AMO at updated limit (today close × 0.995) after MISS
- Max 3 requeues before CRITICAL alert for manual intervention
- RM check_exit() continues running during pending_rm_exit — stops remain active
- New methods: requeue_rm_sell() in paper_portfolio.py
- New fields: rm_sell_requeue_count in position dict (migration runs on load)
- Unit tests: 3/3 passed

### Finding #3 Fix: Missing NSE holidays — COMPLETED (Jun 19)
- Problem: Jan-May 2026 holidays deleted from market_calendar.py causing wrong trading day checks
- Fix: restored all 2026 holidays (Republic Day, Holi, Good Friday, Ambedkar Jayanti, Maharashtra Day)
- Added NEVER REMOVE comment to holiday list — no performance reason to delete past holidays
- Added verify_holiday_coverage() — checks 4 fixed annual holidays at startup
- Signal_runner calls verify_holiday_coverage() at startup and warns if any missing
- All 8 holiday tests passed ✅
- Note: Aug 15 2026 is Saturday — weekend guard makes holiday entry redundant but harmless

### Finding #6 Fix: Auth check kills before auto-refresh — COMPLETED (Jun 19)
- Problem: _check_auth() called sys.exit(1) on stale token before kite_fetcher auto-refresh could help
- Fix: replaced sys.exit(1) with _attempt_auto_refresh() — runs auto_login.py as subprocess
- sys.exit(1) only fires if auto-refresh also fails (true unrecoverable error)
- Tested: 10-hour-old token → auto-refresh → _check_auth completes cleanly ✅
- Walk-forward: 19/24 unchanged
- Combined with kite_fetcher auto-refresh: token expiry is now fully transparent everywhere

### Finding #13 Fix: Duplicate AMO orders — COMPLETED (Jun 19)
- Problem: signal_runner run twice same day creates duplicate DRY_RUN rows; morning_fill_check processes both, double-deducting cash
- Fix 1: _load_pending_orders() deduplicates by (date, ticker, order_type) — keeps most recent row, warns on duplicates
- Fix 2: SELL branch in _update_portfolio_fill() has idempotency guard — skips if shares already 0
- BUY duplicates also caught by pending_buy=False guard from Finding #1 fix
- Unit tests: 2/2 passed (duplicate removed, non-duplicate preserved, most recent kept)
- Walk-forward: 19/24 unchanged

### Finding #5 Fix: Trade P&L inflated by buy-side costs — COMPLETED (Jun 20)
- Problem: net_pnl in trade log only subtracted sell-side costs; buy-side costs (~Rs25 per Rs100k) were deducted from cash but not from net_pnl
- Fix: added entry_cost field to position dict, stored at open_position() and confirm_buy_fill()
- close_position() and morning_fill_check now compute: net_pnl = gross_pnl - sell_cost - entry_cost
- entry_cost added to trade log dict for full transparency
- Migration: existing positions get entry_cost=0.0 (conservative — won't retroactively fix old trades)
- Unit test: Rs-36.44 (old) → Rs-61.89 (new) — Rs25.45 overstatement eliminated ✅
- Walk-forward: 19/24 unchanged

### Finding #8 Fix: Walk-forward OOS window stale — COMPLETED (Jun 20)
- Problem: OOS hardcoded to end 2026-01-01, missing 120+ days of live performance
- Fix: OOS end date now dynamic (_TODAY = date.today()) — always extends to today
- Score change: 19/24 → 17/24 (79% → 71%) — more honest, includes 2026 live data
- WHIRLPOOL payoff ratio dropped 2.46→1.48 (below 1.5 threshold) in extended window
- SIEMENS OOS return essentially 0% — confirms existing removal flag
- System still VALIDATED at 71% (threshold 65%)
- Added run_rolling_live_check(): 90-day early warning system
- Rolling check (last 300 days): 6 HEALTHY, 4 WARNING (WHIRLPOOL, SIEMENS, COLPAL, BSOFT), 0 CRITICAL
- Run walk_forward.py quarterly to keep validation current

### Finding #4 Fix: ADD list sorted by wrong gap — COMPLETED (Jun 20)
- Problem: ADD/MONITOR lists sorted by gap_long (2-year window) but signal_runner uses gap_short (80-day)
- Fix: both sort keys changed to gap_short with None guard
- Unit test confirmed: STOCK_B (gap_short=-0.5%) now correctly ranks above STOCK_A (gap_short=-8.0%)
- ADD list now correctly predicts which stocks will cross soonest in live trading

### Finding #7 Fix: Hurst computed on prices not returns — COMPLETED (Jun 20)
- Problem: auto_screener.py and signal_runner.py computed Hurst on raw prices (non-stationary, biased upward); regime_classifier.py correctly used log returns
- Fix: both now use log returns — np.diff(np.log(close)) — matching regime_classifier.py exactly
- Calibration: log-return Hurst is 0.05-0.11 lower than raw-price Hurst for same stocks
- Thresholds recalibrated: HURST_THRESHOLD 0.55→0.48, HURST_DEGRADE 0.52→0.45
- Random walk verification: H=0.469 (no upward bias confirmed)
- ADD/MONITOR/WATCH lists now show H values 0.48-0.53 (correctly calibrated)
- Walk-forward: 17/24 unchanged

### ETF Overlay — COMPLETED (Jun 21)
- Backtest: all_core_pass=true (Sharpe Δ+8.996, overlay DD -14.3% vs threshold -17.2%, ETF cost 0.094%/yr)
- DD criterion revised: overlay_dd < niftybees_dd + 2pp (old 8pp threshold was against artificially-low cash baseline)
- Rebalance guard implemented: tier changes only on open_position count crossing tier boundary (~26 trades per stock vs 9,001 bug)
- Phase 2: NIFTYBEES paper tracking live in signal_runner.py as of 2026-06-21
- Files changed: paper_portfolio.py (ETF_TIERS, get_etf_target_tier, rebalance_etf), signal_runner.py (ETF block + report line), morning_fill_check.py (comment only)
- 5/5 unit tests passing on local and server (paper_trading/test_etf_overlay.py)
- Overlay tiers (D_aggressive, grid-search validated): 0 positions=100% ETF, 1-2=80% ETF, 3=50% ETF, 4=0% ETF
- Tier grid search: 6 configs tested (A-F), D_aggressive wins Sharpe 0.280 vs A_current 0.213, all 6 pass go/no-go criteria
- Grid search results: validation/etf_tier_grid_result.json
- Regime filter decision: ETF runs independently of NIFTY death cross — regime filter blocks stock entries only
- Phase 3: deploy ₹25,000 real capital after 6 months clean paper validation (target: Dec 2026)

### System Audit — Jun 26 2026 (13 findings, 11 fixed)

#### Fixes applied (Jun 26):
- Finding #2 CRITICAL: get_portfolio_value() now includes ETF — position sizer was receiving ~Rs500 instead of ~Rs98,000
- Finding #1 CRITICAL: missed STRATEGY_SIGNAL SELLs now requeued (was silently dropped after Fix 1 regression)
- Finding #3: pending_buy positions excluded from ETF tier count; pending_rm_exit included
- Finding #4: all reporting (daily report, weekly summary, signal_log.csv) includes ETF value
- Finding #6: integrity validator derives valid_tiers from ETF_TIERS dynamically (not hardcoded)
- Finding #7: weekend crash fixed — trading day guard runs before portfolio load in morning_fill_check
- Finding #9: BUY_QUEUED signal check replaces dead BUY check — EXECUTED log line now fires
- Finding #10: backfill mode shows correct ETF value using etf_avg_price fallback
- Finding #5: walk_forward.py universe updated to current 5 validated stocks
- Finding #8: correlation check passes in-memory state — same-day BUY pairs checked against each other
- Finding #13: notes prefix matching — "CHANDELIER [REQUEUED]" correctly identified as RM exit

#### Death cross deferral fix (Jun 26):
- Problem: death cross SELL called close_position() immediately at 3:45 PM before AMO fill
- Fix: death cross now sets pending_rm_exit=True — position stays open overnight like RM exits
- morning_fill_check closes position at actual open fill price — P&L computed correctly
- Note: BAJAJ-AUTO exit (Jun 25) predates this fix — its P&L was computed at close price
- ETF rebalance no longer fires prematurely on pending death cross exits

#### Remaining fixes not yet implemented:
- Finding #11: gap-down circuit breaker — COMPLETED (Jun 26)
  GAP_BREAKER_THRESHOLD = 3% in morning_fill_check.py
  GAP_EXIT path: closes position at open, records in trade_log, does not requeue
  Small gaps (<3%) still requeue as before — behavior unchanged
  6/6 unit tests passing (paper_trading/test_gap_breaker.py)
- Finding #12: RESOLVED (Jul 5) — correlation check now uses pre-fetched dfs from signal_runner
  Path A (signal_runner): zero API calls — uses dfs dict already in memory at 3:45 PM
  Path B (CLI): lazy yfinance import — token-free, works on weekends
  Architecturally correct: same data used for signals used for correlation check

#### Second System Audit — Jul 2 2026 (25 findings total)
Fixed from second audit:
- Audit2 Finding #1: rebalance_etf() now mirrors get_etf_target_tier() exactly
  pending_buy excluded, pending_rm_exit included — ETF no longer sells prematurely on queued BUY
  23/23 unit tests passing
- Audit2 Finding #4: missed_count no longer double-counts GAP_EXIT orders
  Summary correctly shows FILLED | MISSED | GAP_EXIT
  9/9 unit tests passing
- pandas FutureWarning fixed in strategies/sma_crossover.py
  fill_value=False replaces .fillna(False) — 16 fewer warning lines per daily log

#### Additional fixes applied (Jul 2-5 2026):
- Audit2 Finding #3: RESOLVED (Jul 5)
  Cash floor raised Rs40 → Rs1,000 (MIN_CASH_TO_ATTEMPT_BUY)
  Combined with iterative floor in sizer + pending_buy in position count
- Audit2 Finding #5: RESOLVED (Jul 5)
  build_extended_universe() computes STOCKS_EXTENDED dynamically from actual bar counts
  15 candidates checked via Kite — zero manual maintenance going forward
- Audit2 Finding #8: RESOLVED (Jul 5)
  Dynamic NSE holiday fetch from nseindia.com/api/holiday-master?type=trading
  Cached in utils/nse_holiday_cache.json — auto-updates each year
  To pre-warm 2027: python3 -c 'from utils.market_calendar import refresh_holiday_cache; refresh_holiday_cache([2027])'
- Audit2 Finding #10: RESOLVED (Jul 2)
  time.sleep(1.1) added to screener fetch loop — 55 req/min (safe under Kite 60/min limit)
  Skip count logged with explicit warnings if >5% tickers dropped
- Audit2 Finding #21: RESOLVED (Jul 2)
  Transaction costs corrected: STT 0.1% delivery (was 0.025% intraday), brokerage Rs0 delivery
  DP charge Rs15.34 added per sell. Round-trip Rs37.66 on Rs10,000 (verified Zerodha Jun 2026)
  Position sizer uses exact buy-side cost + iterative floor check
  7/7 unit tests passing (utils/test_costs.py)
- Audit2 Finding #22: RESOLVED (Jul 2)
  NIFTY 500 fetch now has cache fallback (screener/nifty500_cache.json)
  Email subject shows ⚠️ STALE DATA if cache was used
  Cache pre-populated with 500 tickers

#### Audit1 findings resolved (Jul 7 2026):
- Audit1 Finding #16: RESOLVED (Jul 7)
  Circuit breaker threshold corrected: pct_move >= 19.0 → pct_move >= 20.0
  in _check_circuit_breaker() (paper_trading/morning_fill_check.py)
  NSE's actual upper/lower circuit band is 20%; 19% was misclassifying
  large-but-ordinary gaps as circuit breaker events
  5 new unit tests added (test_gap_breaker.py tests 10-14), all passing
- Audit1 Finding #17: RESOLVED (Jul 7)
  Kite API timeout added: KITE_REQUEST_TIMEOUT_SECONDS = 15 passed to
  KiteConnect(api_key=..., timeout=...) constructor in _load_kite()
  (data/kite_fetcher.py). Prevents indefinite hang if Zerodha API stalls
  during the 3:45 PM signal run.
  Verified end-to-end: a Kite timeout raises an exception that is NOT a
  ConnectionError/FileNotFoundError, so it correctly falls through
  signal_runner.py's generic except Exception handler in
  _fetch_stock_data() (skip ticker + continue) rather than the FATAL
  sys.exit(1) branch — no new whole-run crash risk introduced.
  Bonus fix found during testing: _load_kite() had a pre-existing
  IndexError bug on an empty access_token.txt file (bare .splitlines()[0]
  raised IndexError before the intended ValueError guard could ever fire).
  Fixed to check for empty lines first; ValueError with the original
  helpful message now fires correctly.
  4 new unit tests added (data/test_kite_fetcher_timeout.py), all passing
- Audit1 Finding #19: RESOLVED (Jul 7)
  Rate limiting added to signal_runner.py's _fetch_stock_data(): time.sleep(1.1)
  after every Kite API call, matching screener/auto_screener.py's existing
  pattern (~55 req/min, safe under Kite's 60 req/min cap). Previously this
  loop had zero pacing between per-ticker fetches.
  Adds ~7.7s to the current 7-stock universe's evening run; scales linearly
  as the universe grows (noted in-code).
  3 new unit tests added (paper_trading/test_signal_runner_fetch.py), all passing
- Bonus fix (not from either audit, found during Finding #17 testing): Jul 7
  test_gap_breaker.py's test_5_gap_exit_pnl_correct was calling
  transaction_costs(...)["total"] — but transaction_costs() returns a bare
  float, not a dict (transaction_cost_breakdown() is the dict-returning
  function). This was a stale test bug, not a library regression. Fixed to
  call transaction_costs(exec_price, shares, "sell", "delivery") directly.

#### Regime-transition annotation — COMPLETED (Jul 9 2026):
Context: 2026-07-05 and 2026-07-08 screener runs flagged NEWGEN.NS and
  BSOFT.NS for degradation (ADX below threshold), triggering the
  "2 consecutive screens = REMOVE recommendation" path — coinciding with the
  2026-07-07 NIFTY BULL flip. Investigation confirmed the flags were likely
  mechanical ADX dip from the regime transition, not structural stock decay.
  Neither stock was removed.
Decision: annotate flags that coincide with a recent NIFTY regime transition,
  never suppress them. The REMOVE recommendation and consecutive_flags counter
  fire exactly as before — a human sees the context immediately in the email
  report and in the tracker JSON, and makes the call.
Implementation:
  - signal_runner.py now writes regime_transition_date to portfolio_state.json
    ONLY when market_regime actually changes (previously last_regime_date was
    written unconditionally on every run, making it useless as a transition marker)
  - _days_since_regime_transition() helper in auto_screener.py reads this field;
    returns None (fail-open) if the field is absent or the file is unreadable
  - flag_annotations parallel field added to degradation_tracker.json entries:
    {"2026-07-08": {"regime_transition_nearby": true, "days_since_transition": 1}}
    Kept separate from existing flat flag_history list to avoid breaking readers
  - Email report shows ⚠️ CAUTION row below any REMOVE-recommended stock whose
    flags were annotated — visible, not blocking
  - Known asymmetry (accepted): live annotation only catches post-transition flags
    (days_since_transition ≥ 0). Pre-transition proximity requires retroactive
    backfill, as was done manually for the Jul 5 flags on Jul 9.
  - REGIME_TRANSITION_WINDOW = 5 calendar days (matches EARNINGS_DAYS_AHEAD precedent)
  9 new unit tests added (screener/test_degradation_annotation.py), all passing
  NEWGEN.NS and BSOFT.NS backfilled in degradation_tracker.json — both remain
  in the universe, both flags preserved in tracker

#### Walk-forward extended-window crash — RESOLVED (Jul 10 2026):
run_extended_walk_forward() returned {"score": "N/A", ...} (a string, not
  int or None) for candidates with insufficient data in the 2015-2019 extended
  IS window (e.g. recent IPOs, or stocks with too few pre-2015 bars). The gate
  verdict section assumed score was always int or None, so "N/A" >= METRIC_MIN
  raised an unhandled TypeError, crashing the entire WF run for that candidate.
  Confirmed on PAYTM.NS (IPO Nov 2021 — zero bars in 2015-2019 IS window) and
  COHANCE.NS (only 20 bars available in Jan 2015).
Fix: normalize "N/A" → None immediately after extracting score from the return
  dict, before any numeric comparison. Extended window now displays as
  "SKIPPED — insufficient historical data (N bars available, need ≥ M)" instead
  of crashing or silently passing/failing. Gate correctly evaluates on the
  original window alone in this case, per the existing documented rule
  ("extended window ≥4/6 IF sufficient history exists"). Also surfaces the
  error string in both return dicts (run_walk_forward and run_extended_walk_forward)
  so callers receive structured diagnostics instead of losing the error detail.
  4 new regression tests added (validation/test_walk_forward_insufficient_data.py),
  91/91 tests passing.

Remaining from second audit (not yet fixed):
- Audit2 Finding #2/#11: ETF overlay at 100% during NIFTY BEAR — architectural decision needed
  Risk: full NIFTY exposure while stock entries suppressed
- Audit2 Finding #13: sector concentration — up to 4 IT stocks simultaneously possible
  Design decision needed before implementing
- Audit2 Finding #14: RESOLVED (Jul 5)
  _print_verdict() rewritten with per-stock primary gate (PER_STOCK_MIN=4/6)
  Aggregate score shown as informational only — not used for verdict
  Statistical limitation note added: 5 correlated stocks = ~15 independent observations
  Dynamic 'n stocks × 6 metrics' replaces hardcoded '4 stocks × 24'
- Audit2 Finding #23: RESOLVED (Jul 5)
  ETF avg_price now uses VWAP: new_avg = (old_shares × old_avg + delta × price) / new_shares
  First buy: avg = purchase price. Partial sell: avg unchanged. Full sell: resets to 0.0
  4 new unit tests added (Tests 24-27), all passing
- Audit2 Finding #12: RESOLVED (Jul 5) — see Finding #12 above

#### Pre-trade Risk Monitor — COMPLETED (Jul 5 2026)
New feature: utils/news_monitor.py — nightly risk check before signal run

Sources (both verified working from Mumbai server):
  - NSE surveillance: nsearchives.nseindia.com/content/equities/sec_list.csv
    Band in ('2','5') or 'GSM' in Remarks → SURVEILLANCE flag → auto-block entry
  - NSE board meetings: nseindia.com/api/corporate-board-meetings?index=equities&...
    Board meeting within 5 trading days with results keywords → EARNINGS_RISK → warn only

Architecture:
  - Runs at 7 PM IST Mon-Fri (cron: 0 13 * * 1-5)
  - Writes utils/news_flags.json atomically after each run
  - signal_runner.py reads flags at startup before Phase 2 BUY loop
  - SURVEILLANCE: auto-block (objective regulatory fact, no human review)
  - EARNINGS_RISK: warning only, entry proceeds (fail-open — missed trade worse than early entry)
  - Manual override: utils/manual_blocks.json — human-edited, time-limited
  - All network failures fail-open — never block trading on data failure
  - 9/9 unit tests passing (utils/test_news_monitor.py)

First live detection verified:
  - HCLTECH: board meeting Jul 13 2026 (Q1 results) — will flag Monday Jul 6 evening
  - All 8 universe stocks surveillance-clean as of Jul 5 2026

Root-caused and fixed (Jul 17 2026): the Jul 13 "[news_monitor] No flags
  file found" anomaly (initially documented earlier today as unresolved/
  single-occurrence) was found to have a SECOND occurrence on Jul 8, disproving
  the "single anomaly" conclusion. Root cause confirmed:
    signal_runner.py's NEWS_FLAGS_FILE was defined as a RELATIVE path
    (Path("utils/news_flags.json")), while news_monitor.py correctly used an
    ABSOLUTE path. A relative path only resolves correctly if the process's
    working directory happens to be the project root at check time — any
    invocation from a different cwd causes .exists() to return False even though
    the real file was present and correctly written the entire time. This
    explains both incidents: news_monitor.py ran and wrote successfully both
    nights (confirmed via its own execution logs, Jul 7 13:00 and Jul 10 13:00),
    yet the file appeared "missing" to signal_runner.py on the following read.
  Fixed: NEWS_FLAGS_FILE changed to _ROOT / "utils" / "news_flags.json" in
    signal_runner.py, matching news_monitor.py's existing absolute-path pattern.
    Regression test added (test_17 in test_news_monitor.py) that changes cwd to
    /tmp before checking NEWS_FLAGS_FILE.exists() and asserts the result is
    stable — this is the test that would have caught this bug originally.
  Lesson: the initial Jul 13 investigation correctly followed the evidence
    available at the time and correctly refused to guess at an unconfirmed cause.
    The initial data point (single occurrence) was genuinely insufficient to find
    this. Finding the Jul 8 second occurrence during a broader week-level log
    review was what made the pattern, and therefore the real cause, visible.

#### WF Batch Automation — COMPLETED (Jul 14-15 2026)
Problem: testing screener ADD/WATCHLIST candidates required manually running
  walk_forward.py --ticker X once per stock, scrolling past ~150-200 lines of
  cooldown-sensitivity-analysis noise per run to find the verdict, then manually
  checking crossover state for anything that passed. Pure mechanical overhead,
  repeated 4 times this week (Jul 8, 9, 9-10, 12 batches), including wasted
  re-testing of the same tickers (HAL.NS, DMART.NS tested twice).

Built in three pieces:
- validation/walk_forward.py --json flag: suppresses human-readable output
  (including the cooldown-sensitivity diagnostic, which is not load-bearing for
  the single-ticker gate verdict and makes its own live API calls), emits one
  JSON line with the gate verdict. Added skip_diagnostics param to
  run_walk_forward(). Human-readable path completely unchanged when --json is
  not passed. 5 new tests (validation/test_walk_forward_json_output.py).
- validation/post_screener_pipeline.py (new): takes --tickers from a screener
  email, caches results in validation/wf_test_history.json (skips re-testing
  within --retest-after-days, default 3), runs each via walk_forward.py --json
  as a subprocess (not in-process, so --json remains the single source of truth
  for gate logic), checks crossover state for every PASS, writes a dated markdown
  report. Read-only w.r.t. the live system — never modifies STOCKS, never
  commits, never deploys. Deliberately does NOT call run_screen() directly, since
  that function writes degradation_tracker.json unconditionally and takes ~9 min
  for a full 500-stock fetch — unsuitable for free/frequent orchestration.
  27 tests total (validation/test_post_screener_pipeline.py), including hardening:
  180s subprocess timeout, explicit returncode check, malformed-JSON handling,
  persistent error logging to validation/wf_gate_errors.log.
  Bug caught during development: three tests calling main() without --dry-run were
  writing real report files into the actual validation/ directory (REPORT_DIR was
  never patched to tmp_path). Fixed with patch.object; added regression test
  proving no file ever lands in the real directory.
- screener/auto_screener.py + validation/run_scheduled_wf_batch.py (new):
  auto_screener.py now writes screener/latest_candidates.json atomically
  (tmp + rename) alongside every real screen's email send. The new wrapper reads
  that file, rejects it if missing or >1 day stale, caps processing to
  MAX_CANDIDATES_PER_RUN=10 (hardcoded constant, NOT a CLI flag — deliberately
  not silently bypassable), calls pipeline_main() directly, and writes
  validation/scheduled_run_status.json with pass/fail counts.
  4 tests (screener/test_candidates_file.py) + 7 tests
  (validation/test_run_scheduled_wf_batch.py), including a proactive "never
  writes to real directory" test applying the lesson from the REPORT_DIR bug.

DELIBERATE DECISION — no cron trigger: considered and explicitly rejected adding
  run_scheduled_wf_batch.py to cron for automatic triggering after the 6 PM
  screener run. Reasoning: this system had TWO separate silent unattended-job
  failures in the same week this automation was built — the auto_screener.py
  NameError crash that killed the Jul 12 Sunday screener with zero output, and
  the get_current_universe() stale-data bug that silently corrupted correlation
  checks for an unknown period. A third unattended job before this system has a
  track record of reliable unattended operation is not an acceptable risk-reward
  trade. Trigger stays manual. Revisit after a longer period of demonstrated
  reliability.

Live-verified end to end (Jul 14 2026 close):
  - COHANCE.NS: GOLDEN +0.14%, PASS — thin crossover; Hurst was 0.471 on Jul 14
    signal run (below 0.48 threshold → HURST_SKIP). Check Hurst recovery before
    assuming this will produce a live entry.
  - MAPMYINDIA.NS: GOLDEN +1.96%, PASS

Total new tests from this work: 5 (--json flag) + 27 (pipeline + hardening) +
  4 (candidates file) + 7 (scheduled wrapper) = 43. Suite grew from 94 → 137.

Test suite (verified locally Jul 15 2026):
- test_etf_overlay.py: 30/30 ✅
- test_post_screener_pipeline.py: 27/27 ✅ (new — WF batch orchestrator + hardening)
- test_news_monitor.py: 14/14 ✅ (was 9/9 — added tests 10-14 for weekday-aware staleness check)
- test_gap_breaker.py: 14/14 ✅ (was 9/9 — added tests 10-14 for circuit breaker threshold)
- test_degradation_annotation.py: 9/9 ✅ (new — regime-transition annotation tests)
- test_run_scheduled_wf_batch.py: 7/7 ✅ (new — manual WF batch wrapper)
- test_costs.py: 7/7 ✅
- test_correlation_check.py: 6/6 ✅
- test_walk_forward_json_output.py: 5/5 ✅ (new — --json flag + skip_diagnostics)
- test_walk_forward_insufficient_data.py: 4/4 ✅ (new — WF extended-window crash fix)
- test_candidates_file.py: 4/4 ✅ (new — screener candidates file atomic writer)
- test_kite_fetcher_timeout.py: 4/4 ✅ (new)
- test_run_screen_integration.py: 3/3 ✅ (new — run_screen() NameError + get_current_universe regression)
- test_signal_runner_fetch.py: 3/3 ✅ (new)
- Total: 137/137 tests passing

---

## Key Implementation Notes

### Hurst Exponent — CRITICAL
- Always use compute_hurst() from screener/auto_screener.py — never reimplement
- Input: raw closing prices (numpy array) — function handles log transformation internally
- Do NOT pass log returns as input — compute_hurst() does np.log(ts) internally
- Correct usage:
    from screener.auto_screener import compute_hurst
    h = compute_hurst(df['close'].values)  # raw close prices, not log returns
- Wrong usage (produces nonsensical results near 0 or negative):
    log_returns = np.diff(np.log(prices))
    h = compute_hurst(log_returns)  # WRONG — double-differencing

### Hurst Readings (2026-06-26, 337 bars, bear market regime)
- BAJAJ-AUTO:  H=0.422 — below 0.48 threshold (bear market effect, strong WF performer)
- HCLTECH:     H=0.549 — passes 0.48, fails 0.55
- COLPAL:      H=0.524 — passes 0.48, fails 0.55
- JKTYRE:      H=0.536 — passes 0.48, fails 0.55
- BSOFT:       H=0.415 — below 0.48 threshold
- PERSISTENT:  H=0.500 — passes 0.48, fails 0.55
- Decision: keep HURST_THRESHOLD=0.48 — raising to 0.55 filters entire universe to zero
- Revisit threshold after October 2026 with full bull+bear cycle data

### NSE Holiday Calendar
- Dynamic fetch from: nseindia.com/api/holiday-master?type=trading (CM segment)
- Requires session cookie — auto_login not needed, just a plain requests.Session()
- Cached in: utils/nse_holiday_cache.json (auto-updated on each successful fetch)
- 2026: hardcoded verified list in market_calendar.py (NSE circular confirmed)
- 2027+: fetched from NSE API automatically on first is_trading_day() call for that year
- To pre-warm next year in November 2026:
  python3 -c 'from utils.market_calendar import refresh_holiday_cache; refresh_holiday_cache([2027])'
- Never hardcode future years — let the API provide them

### Live Hurst Quality Gate (signal_runner.py)
- compute_hurst() called at BUY entry time in _process_stock()
- Gate fires AFTER regime filter, BEFORE BUY_CANDIDATE collection
- If H < HURST_THRESHOLD (0.48): signal = HURST_SKIP, entry suppressed
- Fail-open: if computation errors, entry proceeds (never block on error)
- HURST_THRESHOLD imported from screener.auto_screener — single source of truth
- Motivation: stocks can degrade after universe addition (BAJAJ-AUTO H=0.371,
  JKTYRE H=0.214, BSOFT H=0.388 in current BEAR market)

BEAR market Hurst suppression pattern (verified Jul 7 2026):
  Most universe stocks show H below 0.48 in BEAR markets and recover
  above 0.48 in BULL markets — this is normal regime behavior, not
  structural failure. Do NOT remove stocks based on current low Hurst alone.
  Verified pattern:
  - BAJAJ-AUTO: H=0.374 (2022 BEAR) → H=0.571 (2024 BULL) — temporary
  - BSOFT:      H=0.394 (2022 BEAR) → H=0.543 (2024 BULL) — temporary
  - PERSISTENT: H=0.476 (2022 BEAR) → H=0.536 (2024 BULL) — mildest effect
  - NEWGEN:     H=0.501 (current), strong H=0.624 seen Mar 2025 — OK
  JKTYRE was a genuine structural failure (H=0.214, negative expectancy)
  — different from temporary BEAR suppression.

### Transaction Costs (verified Zerodha Jun 2026, zerodha.com/charges)
- Delivery is used for all trades (CNC orders via AMO)
- Brokerage: Rs0 (Zerodha delivery is free)
- STT: 0.1% on BOTH buy and sell sides (was wrongly set to 0.025% intraday rate)
- DP charge: Rs15.34 per sell (CDSL Rs3.50 + Zerodha Rs9.50 + GST Rs2.34)
- Stamp duty: 0.015% buy-side only
- Total buy-side on Rs10,000: ~Rs11.91
- Total sell-side on Rs10,000: ~Rs25.75 (includes DP charge)
- Round-trip on Rs10,000: ~Rs37.66
- See utils/costs.py — transaction_costs(price, shares, side, trade_type='delivery')

### Kite Fetcher
- Correct signature: get_ohlcv(ticker, start='YYYY-MM-DD', end='YYYY-MM-DD')
- Does NOT accept 'days' parameter
- Always use date strings, not timedelta objects directly

### General Rule
- Before reimplementing any function, check if it already exists in the codebase
- grep -rn 'def function_name' ~/algo-trading/ --include='*.py'
- Use the existing implementation — it is already tested and validated
