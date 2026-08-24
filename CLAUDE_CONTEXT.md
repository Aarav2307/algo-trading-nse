# Claude Context — NSE Algo Trading System
Last updated: 2026-07-19

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
- Added Jul 27 2026: ENGINERSIN.NS
  Screener flagged Jul 26 2026 (Sunday batch: FINCABLES.NS,
  CUMMINSIND.NS, MAZDOCK.NS, IDEA.NS, ENGINERSIN.NS — only
  ENGINERSIN.NS passed WF gate).
  WF validated: original 5/6 OOS +11.5%, extended 5/6 OOS +10.5%.
  Crossover at add time: SMA20=234.01, SMA50=236.96, gap -1.24%
  (DEATH) — approaching golden, not yet crossed.
- Added Jul 30 2026: NAVA.NS
  Screener flagged Jul 29 2026 (Wednesday batch, ADD list:
  LTM.NS, MGL.NS, CUMMINSIND.NS, NATCOPHARM.NS, TCS.NS — all
  failed WF gate). NAVA.NS was on the WATCHLIST that day, not
  the ADD list — tested proactively, not a screener ADD
  recommendation.
  WF validated: original 5/6 OOS +21.9%, extended 5/6 OOS +19.5%.
  Crossover at add time: DEATH (-1.66%) — approaching golden,
  not yet crossed.
- Added Jul 30 2026: SUZLON.NS
  Tested from a compiled list of unique tickers across the last 3
  screener watchlists (Jul 22, 26, 29 — not a single day's batch).
  Most had already been tested and failed as ADD candidates on
  other dates; SUZLON.NS was the one new pass. Proactive test, not
  a screener ADD recommendation.
  WF validated: original 5/6 OOS +13.0%, extended 5/6 OOS +30.1%
  (strongest extended return seen in any batch to date).
  Crossover at add time: DEATH (-3.25%) — gap widening, not
  narrowing (price fell ~10.7% over the two sessions before add:
  Jul 27 ₹53.15 → Jul 29 ₹47.45). No signal of an imminent flip;
  added on WF strength alone, per the same-treatment rule for any
  gate-passing candidate regardless of proximity to crossover.
- Added Aug 10 2026: MOTILALOFS.NS
  Screener flagged Aug 9 2026 (Sunday batch: JUBLPHARMA.NS,
  BAYERCROP.NS, MOTILALOFS.NS, TATAINVEST.NS, ITC.NS — only
  MOTILALOFS.NS passed WF gate).
  WF validated: original 5/6 OOS +9.6%, extended 4/6 OOS +3.7%.
  Crossover at add time: DEATH (-0.42%), but the trajectory is a
  reversal, not an approach: was comfortably GOLDEN through Aug 3-6
  (+1.21% -> +0.69% -> +0.51% -> +0.12%), flipped to DEATH on Aug 7
  and kept weakening. Numerically close to zero, but trending away
  from golden, not toward it — do not read the small gap as
  imminent. Added on WF strength alone, same standard as SUZLON.NS.
- Added Aug 13 2026: COCHINSHIP.NS
  Screener flagged Aug 12 2026 (Wednesday batch: JUBLPHARMA.NS,
  HINDZINC.NS, TATAINVEST.NS, WHIRLPOOL.NS, AAVAS.NS — all failed;
  COCHINSHIP.NS was on the WATCHLIST that day, not the ADD list).
  WF validated: original 5/6 OOS +6.7%, extended 5/6 OOS +8.7%.
  Crossover at add time: DEATH (-0.81%), and — unlike MOTILALOFS.NS
  — a genuine, steady approach: 8 straight trading days of
  monotonic narrowing, Aug 3 through Aug 12 (-2.62% → -2.54% →
  -2.32% → -1.98% → -1.63% → -1.36% → -1.09% → -0.81%), no
  reversal. Closest true approach-trend seen for any addition to
  date; added specifically to catch the live crossover if the
  trend holds, not on WF strength alone.
- Added Aug 17 2026: BEML.NS
  Screener flagged Aug 16 2026 (Sunday batch: CEATLTD.NS,
  DATAPATTNS.NS, TATAINVEST.NS, BEML.NS, JPPOWER.NS — only
  BEML.NS passed WF gate; CEATLTD.NS notable near-miss, 5/6
  original OOS +16.2% but failed extended 3/6, +2.7%).
  WF validated: original 5/6 OOS +10.7%, extended 5/6 OOS +12.3%.
  Crossover at add time: DEATH (-0.36%), a mixed trend — crossed
  from GOLDEN into DEATH on Aug 6, widened to a trough of -0.46%
  (Aug 12), then narrowed slightly the last 2 sessions (-0.38% ->
  -0.36%). Not a clean approach like COCHINSHIP.NS, not a clear
  ongoing reversal like MOTILALOFS.NS — stabilizing after an
  initial widen. Underlying price rose ~12% over the same 8
  sessions (Rs1,671.60 -> Rs1,874.40); the narrowing gap looks like
  SMA50 catching up to a genuinely bullish move, not noise.
- Removed Aug 22 2026: EMAMILTD.NS
  Found via manual audit of screener/degradation_tracker.json, not
  a screener email review — the automated REMOVE recommendation
  (>=2 consecutive flags) has no console/log output in a real run,
  only in the emailed HTML report (same as ADD/WATCH being
  dry-run-console-only), so this had gone unreviewed. Tracker
  showed consecutive_flags=4, flagged every screen since Aug 9
  (Aug 9, 12, 16, 19) -- well past the >=2 threshold, with zero
  open position (confirmed HOLD every day in this stretch).
  Re-verified live rather than trusting the persisted flag alone:
  Hurst 0.574 (healthy, not the trigger), ADX 19.5 vs the 22.0
  ADX_DEGRADE threshold -- a genuine, current loss of trend
  strength, not a stale/resolved flag. Recent closes choppy and
  range-bound (Rs397-413 over 10 sessions, no sustained direction),
  consistent with the ADX reading.
  No automated removal script exists (mirrors add_validated_stock.py's
  design -- manual review and action, not automatic). universe.py
  edited directly; no corresponding WF-gate re-run needed since this
  isn't an addition. Full test suite re-run to confirm the removal
  broke nothing.
  Process gap this surfaces: REMOVE recommendations need the same
  standing review habit as ADD/WATCH lists, or they'll keep going
  unreviewed the way this one did for 4 cycles.

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
- repair_portfolio_state.py — removed Jul 21 (architecture review): it was a
  one-time BAJAJ-AUTO repair script that never ran (fill was clean) and didn't
  touch positions/trade_log directly, so it wasn't part of the fill-confirmation
  seam. If a similar manual correction is ever needed again, write a fresh
  one-off script rather than reviving this one.
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
NEVER add a stock to universe.py without running WF validation first.
Screener ADD recommendation = candidate for testing, NOT approval to add.

STOCKS now lives in one place: universe.py (root). Both signal_runner.py and
walk_forward.py import it from there — walk_forward.py filters out a small,
explicit WF_EXCLUDED set (tickers with insufficient WF history) before use.

Step 1 — Run single-stock validation:
  python validation/walk_forward.py --ticker CANDIDATE.NS

Step 2 — Gate criteria (both must pass):
  - Original window: ≥4/6 metrics AND OOS return ≥+4%
  - Extended window: ≥4/6 metrics (if sufficient history exists)

Step 3 — If PASS:
  - Run: python3 validation/add_validated_stock.py CANDIDATE.NS
    (adds to universe.py, drafts the Universe History entry, runs the test suite)
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

#### Silent-failure hardening — COMPLETED (Jul 18 2026)
- utils/alerts.py added (send_crash_alert()) and wired into the `__main__`
  try/except of all three unattended entry points: signal_runner.py,
  auto_screener.py, morning_fill_check.py — a crash now writes to
  utils/alerts.log and stderr before re-raising, instead of dying silently
  with cron only recording a non-zero exit.
- signal_runner.py: AMO_CONFIG["order_log_file"] changed from a relative
  string ("paper_trading/amo_orders.csv") to an absolute path
  (str(_ROOT / "paper_trading" / "amo_orders.csv")) — same cwd-dependency
  bug class as the six Path() constants fixed earlier this week, but this
  one was a dict value, not a `= Path(...)` literal, so the health check's
  regex scan didn't catch it.
- run_morning_check.sh: removed the `&&` between `auth/auto_login.py` and
  `morning_fill_check.py --apply`. A login failure no longer silently skips
  the entire fill check — it now logs a warning and still attempts the fill
  check with the existing token.
- morning_fill_check.py: `_update_portfolio_fill()` now runs before
  `_update_csv_row()` in both the FILLED and GAP_EXIT paths (was reversed).
  A crash between the two calls now leaves the CSV row as DRY_RUN
  (reprocessable) instead of FILLED with the portfolio never updated
  (previously irrecoverable without manual intervention).
- Commits: 083c6b9, 3e6840c, 27eca36 (all 2026-07-18), pushed to
  origin/main. New/updated tests: utils/test_alerts.py (2),
  paper_trading/test_signal_runner_fetch.py (+1, AMO path
  cwd-independence), paper_trading/test_fill_ordering.py (4, including an
  explicit test proving the OLD ordering left an irrecoverable state).

#### Kite tz-aware index bug — found live in production, fixed and deployed (Jul 19 2026)
Root cause: data/kite_fetcher.py's tz-stripping used
`DatetimeIndex.map(lambda dt: dt.replace(tzinfo=None))` on the index after
set_index("date"). On the Lightsail server's pandas 2.3.3, this silently
reconstructed the mapped result back into the original tz-aware dtype —
each individual Timestamp.replace(tzinfo=None) call correctly returned a
naive value, but .map() discarded that and re-attached the +05:30 offset,
with no exception or warning. Confirmed live: fetching BAJAJ-AUTO.NS
2023-01-01 → 2026-07-19 (877 rows) on the server showed df.index.tz still
equal to tzoffset(None, 19800) after the old code ran — production has been
silently returning tz-aware data. Not reproducible on pandas 3.0.3 (local
dev), which is why this was invisible until tested directly against the
server's actual pandas version.

Fix: strip tz on the df["date"] column via .dt.tz_localize(None) before
set_index(), not via .map() on the index — verified correct on both pandas
2.3.3 (server) and 3.0.3 (local). Deployed directly to the server
(commit 4dfcba8 on the server's own git history; 6ef1602 on origin/main)
and re-verified live post-deploy: the 877-row BAJAJ-AUTO.NS case now shows
df.index.tz is None, and test_kite_fetcher.py shows timezone-naive: True
against the real Kite API. Added data/test_kite_fetcher_tz.py — 4 mocked
tests, pandas-version-portable (doesn't depend on which pandas happens to
be installed), passing on both environments.

All downstream consumers (signal_runner.py, auto_screener.py,
walk_forward.py, backtester.py) exclusively source data through this one
get_ohlcv() function — fixed at the single ingestion point, no separate
changes needed. Checked utils/market_calendar.py and
utils/corporate_actions.py: both operate only on plain datetime.date from
date.today()/date.fromisoformat(), never receive a Kite Timestamp at any
current call site — not affected. data/fetcher.py (yfinance path) already
used .tz_localize(None), never had this bug.

#### walk_forward.py stray duplicates — actually removed (Jul 19 2026)
A commit message on the server (1711759, "Sync production with
origin/main...") claimed it "removes stray walk_forward.py duplicates," but
this was false — as of this morning, `find . -name "walk_forward.py"` on
the server still showed three tracked files (./walk_forward.py,
./paper_trading/walk_forward.py, ./validation/walk_forward.py) with
different sizes/dates (Jun 25, Jul 7, Jul 19). The "checkout from
origin/main restored them" theory floated for this doesn't hold up either:
`git log --oneline --all -- walk_forward.py paper_trading/walk_forward.py`
on the local clone shows these two files have never existed anywhere in
origin/main's history — they were captured only in the server's own orphan
snapshot commit (c6ec08a) and no commit ever removed them. Whatever
deletion was attempted, it was never actually committed against the
server's own history.

Re-verified dead before removing, fresh (not reusing the earlier check):
no references in any *.sh file on the server (`find . -name "*.sh"` →
5 files, none mention walk_forward), nothing in `crontab -l`, and the only
two `from walk_forward`/`import walk_forward`-shaped grep hits
(screener/universe_scan.py:237, validation/portfolio_backtest.py:11) are
prose mentions in comments/docstrings, not real imports — every actual
subprocess/path reference (post_screener_pipeline.py,
add_validated_stock.py, test_walk_forward_json_output.py) explicitly
targets `validation/walk_forward.py` only.

Removed via `git rm` + commit (801015a) on the server. Verified after:
`find . -name "walk_forward.py"` now shows exactly one file
(validation/walk_forward.py); full suite re-run, 202 passed, zero
regressions.

#### Server ↔ origin/main disconnected git histories — merged (Jul 19 2026)
Confirmed live: `git merge-base master origin/main` on the server returned
nothing — the two histories shared zero common ancestors. Root cause:
the server's repo was built from a fresh orphan commit (c6ec08a) populated
by a working-tree snapshot, never a real `git merge`/`pull` from origin.

Before merging, checked how different the actual content was:
`git diff --stat master origin/main` showed 172 differing files, but 171
of those were pure deletions of `__pycache__`/binary/state artifacts that
existed only on the server (not real source divergence), and exactly 1 file
had real content differences — `.gitignore`, differing by one line
(`utils/alerts.log`, present on server only). Given how minor the actual
divergence was, merged rather than squashing or leaving it disconnected:
`git merge --allow-unrelated-histories --no-commit --no-ff origin/main`,
which produced exactly one add/add conflict (.gitignore, resolved by
keeping the union of both sides — no lines lost, no duplicates).

Because there's no common ancestor, the merge treated every one of the 171
artifact files as "added only on our side" and staged them to be kept —
which would have silently re-tracked exactly the credential/pycache/state
files an earlier commit (a72dbf5, "Untrack venv, __pycache__, and
credential files retroactively") was meant to remove. Checking further
showed that commit had only partially delivered on its own stated scope in
the first place — venv/.env/access_token/nse_instruments were genuinely
untracked, but `__pycache__/*.pyc` (47 files), `portfolio_state.json` and
its ~20 backups, `degradation_tracker.json`+backup, `utils/news_flags.json`,
`auth/login_error.png`, and `paper_trading/logs/*` were still tracked the
whole time. Re-ran the untracking against the merged .gitignore in one
pass: `git ls-files -ci --exclude-standard | xargs git rm --cached`,
removing 151 tracked-but-ignored paths from the index (nothing deleted from
disk — confirmed portfolio_state.json, degradation_tracker.json, and
login_error.png all still present on disk after). Confirmed zero credential
paths tracked post-merge (`git ls-files | grep -E '\.env$|access_token|
nse_instruments|venv/'` → no matches).

Merge committed as d5073a3. `git merge-base master origin/main` now
returns 6ef1602 (a real common ancestor) instead of nothing. Full suite
re-run post-merge: 202 passed, zero regressions. `git status` clean.

Not yet pushed. Checked the push mechanics without executing: origin/main
IS an ancestor of the server's master post-merge, so a push would be a
valid fast-forward (git wouldn't require --force) — but the server has no
GitHub push credentials configured at all (`git push --dry-run` fails with
"could not read Username for 'https://github.com'": the remote is HTTPS
with no token/credential helper set up). Separately, a fast-forward push
would carry all 7 of the server's operational commits (the orphan
snapshot, the sync, the untracking, the gitignore fix, the tz fix, the
walk_forward cleanup, and this merge) into origin/main's permanent public
history — worth a deliberate decision, not a default action.

Test count: 202 passing, confirmed on both the local Mac and the server
(re-run on each after every step today — the walk_forward.py removal, the
merge, and once more just now — all showing `202 passed`, zero failures) —
up from 198 before this week's tz-fix test additions.

Update (Jul 19-20 2026): the merge above (`d5073a3`) was later amended to
strip the leftover sensitive/stale files it would otherwise have introduced
(root-level portfolio_state.json + 21 backups, a stale nested
paper_trading/CLAUDE_CONTEXT.md), then pushed — and rejected twice, first
for a 122.9 MB venv/ binary exceeding GitHub's 100 MB limit, then for a live
.env (Zerodha password, TOTP secret, Kite API key/secret, SendGrid key)
GitHub's secret scanner caught in the same orphan snapshot commit
(`c6ec08a`). Both never-should-have-been-tracked payloads were confirmed to
have never reached the real origin/main (`git merge-base --is-ancestor`
returned false), then stripped from all 140 commits in one
`git filter-repo --path venv/ --path .env --invert-paths` pass against a
fresh clone — verified zero dangling objects after `reflog expire` +
`gc --prune=now --aggressive`, all 140 commit messages/authors/dates
identical before/after, 202/202 tests passing. Pushed clean as `8a5301a`;
Mac, server, and GitHub confirmed identical. See "Credential-leak
prevention" below for what's now in place to stop this recurring.

#### Credential-leak prevention — COMPLETED (Jul 20 2026)
Root cause of the incident above: the server's git repo was born from
`git add -A` on a working directory that predated `.gitignore` — nothing
stopped `.env` and `venv/` from being staged in that first commit. Four
layers added, each verified working (not just configured) before moving to
the next:

1. **File permissions**: `chmod 600 .env` on the server (`-rw-------`,
   confirmed via `ls -l`). Was `-rw-r--r--` (world-readable) before.
2. **Local pre-commit hook** (`.git/hooks/pre-commit`): blocks staging
   `.env` or anything under `venv/`, at any depth, regardless of
   `.gitignore` state. Verified live: a force-staged file under `venv/`
   was blocked (exit 1, correct message); a normal file committed cleanly
   (exit 0). **Limitation, not a footnote**: `.git/hooks/` is not
   version-controlled — this protects only the machine it's installed on.
   A fresh clone gets nothing until layer 3 runs.
3. **`scripts/install-hooks.sh`** (checked into the repo): reinstalls the
   same guard, plus registers git-secrets patterns if git-secrets is
   present. Documented in README.md's Setup & Installation as the first
   command after `git clone` — deliberately sequenced *before* creating
   `.env`, so even a `git add -A` on a brand-new clone can't stage it.
   Verified against a genuine fresh `git clone` (not a reused working
   copy): hook absent before running the script, present and working
   after. A first draft of this script had a bug — `git secrets --install
   -f` silently overwrote the custom pre-commit hook instead of only
   adding commit-msg — caught by testing the actual installed hook content
   rather than trusting the script ran without error; fixed by writing the
   commit-msg hook directly instead of delegating to `--install -f`.
   Considered the `pre-commit` (pre-commit.com) framework first — rejected
   because this project has never had a requirements.txt/pyproject.toml/
   any dependency manifest in 140 commits of history; adding a Python
   framework dependency for a 6-line bash check would be a bigger change
   than the problem warrants.
4. **git-secrets** (pattern-based scanning — catches a secret even in a
   file not named `.env`): installed via `apt-get install git-secrets` on
   the server (available in Ubuntu 22.04's default repos) and via
   `brew install git-secrets` on the Mac. Registered AWS's built-in
   patterns plus four project-specific ones (KITE_API_SECRET,
   ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET, SENDGRID_API_KEY). Verified live
   on both machines: a file containing a fake credential-shaped line was
   blocked with the exact matched line shown; every throwaway test file
   and test commit made during verification was cleaned up afterward
   (both Mac and server confirmed back at `8a5301a`, clean, after each
   round). Real false positive hit immediately: committing this very
   change was blocked because README.md's own env-var documentation
   (`your_kite_api_secret`, etc.) matches the same KEY=VALUE patterns.
   Fixed correctly — `git secrets --add --allowed 'your_[a-z_]+'` — rather
   than bypassing with `--no-verify`, and added to install-hooks.sh so
   future clones get the same exception, not just this one.

**Runbook — re-initializing git on a fresh clone or new server** (the exact
sequence that failed this time): `.gitignore` must exist and be committed
*before* the first `git add -A`/`git add .`, and `scripts/install-hooks.sh`
must run before `.env` is created. Correct order:
```
git init                          # or git clone <repo>
# .gitignore must already be tracked from the FIRST commit if initializing fresh —
# never run a broad `git add -A` against an untracked working directory that
# contains .env or venv/, even "temporarily" or "just to snapshot state."
bash scripts/install-hooks.sh      # second line of defense, before any secrets exist
cp .env.example .env               # or otherwise create .env — only after the above
# ... fill in .env, python3 -m venv venv, pip install ...
```
If a repo is ever re-initialized on a machine that already has a populated
working directory (e.g. an existing server being brought under version
control for the first time — exactly this incident's scenario): run
`git status --short` and `git diff --cached` before every commit until
`.gitignore` is confirmed correctly excluding `.env`/`venv`/state files,
not after. Do not trust a single `git add -A` followed by inspection — the
damage (data committed to a git object) happens at `git add`/`git commit`,
not at push; by the time you're looking at `git status`, the object may
already be written to `.git/objects` even if you `git reset` before
committing further.

**Residual risk — what still depends on human/AI discipline, not structural
enforcement**: (a) the four layers above only protect *this specific repo*
once installed — a completely different project, or this repo re-cloned
onto a machine where `scripts/install-hooks.sh` is never run, has zero
protection; (b) git-secrets' patterns are a fixed list — a new credential
type (e.g. a future third-party API key) added to `.env` without a
corresponding `git secrets --add` pattern is caught only by the path-based
`.env`/`venv/` guard, not by pattern matching; (c) `--no-verify` bypasses
every hook here — nothing stops a determined or rushed `git commit
--no-verify`; (d) none of this protects against a secret pasted directly
into a commit *message* body beyond what git-secrets' commit-msg hook
covers with the same fixed pattern list; (e) GitHub's own push protection
(which is what actually caught the `.env` leak this time, not any of these
four layers, since none of them existed yet) remains the last line of
defense for anything that slips past all of the above — worth knowing it
exists, not worth relying on as the primary control.

---

#### confirm_buy_fill() never persisted state — CORRECTED (Aug 4 2026): the cited historical incident was false
**Correction, Aug 4 2026:** The BAJAJ-AUTO.NS incident described below was
verified false via a direct, explicit SSH pull of the live server's
portfolio_state.json (not a local checkout): trade_log contains a complete,
internally consistent BAJAJ-AUTO.NS entry (bought Rs10,286.14 Jun 2, sold
Rs9,887.05 Jun 29, exit_reason STRATEGY_SIGNAL), matching amo_orders.csv's
SELL fill for the same date, with the current position correctly flat
(shares=0, no pending flags). The trade was never lost. total_trades=4
matches trade_log's length exactly.

The original finding below was built on incorrect data -- most likely a
local Mac file mistaken for the server's actual state, the same class of
SSH-session mixup caught and corrected multiple other times this session.
The note in the original entry telling readers a local file showing this
trade as healthy was "stale" and "not evidence against the incident" was
itself backwards: that data was closer to correct than what the original
investigation actually used.

What remains valid: the underlying code change --
confirm_buy_fill() now calling self.save() before returning, and
close_position() being routed through PaperPortfolio instead of a raw
JSON bypass -- is kept. An in-memory mutation that isn't persisted is a
real bug class worth guarding against on its own merits, independent of
whether this specific historical trade ever actually hit it. The
regression test (test_buy_fill_persists_to_disk) is also kept as a test
of that general pattern.

What does NOT hold: every claim in the "Confirmed historical instance"
paragraph below about BAJAJ-AUTO.NS specifically. No paper P&L is actually
missing. Left the original text below, uncorrected in place, rather than
deleting it, so the full record (including how the error happened) stays
visible -- per this file's own practice for prior reversals (see the
pending_buy Finding #1/#3 correction below).

---

#### [RETRACTED CLAIM] confirm_buy_fill() never persisted state — BUY fills silently lost — originally marked FIXED (Jul 27 2026), incident retracted (Aug 4 2026)
Root cause: `PaperPortfolio.confirm_buy_fill()` (paper_trading/paper_portfolio.py)
mutated `self.state` in memory — deducting cash, clearing `pending_buy`, setting the
real entry price — but never called `self.save()`. A confirmed BUY fill existed only
for the lifetime of that one `morning_fill_check.py` process. The moment the process
exited, the fill was gone: the next state load showed `pending_buy` still True and
cash still undeducted, with nothing on disk indicating a fill had ever been confirmed.

Confirmed historical instance: BAJAJ-AUTO.NS was bought June 3 2026 (exec price
₹10,286.14, per paper_trading/signal_log.csv's BUY row for that date). The other three
same-day BUYs (WHIRLPOOL.NS, TMPV.NS, SIEMENS.NS) persisted correctly and have since
closed. Per the reported server state, BAJAJ-AUTO.NS's fill did not persist: no cash
was deducted for it, the position was never recorded as open, and — because it was
never recorded — no risk management (chandelier stop, hard stop, time stop) was ever
applied to it, and it will never produce a trade_log entry. True historical paper P&L
may differ from what portfolio_state.json and signal_log.csv show as a result. This
gap is not reconstructable from existing records; no attempt has been made to guess
what the position "would have" done, and none should be.

Note for anyone reading this from the local dev checkout: as of this writing, the
local portfolio_state.json (last synced ~Jun 24 2026, an older schema without
pending_buy/entry_cost fields) still shows BAJAJ-AUTO.NS as an open position with the
correct entry price — apparently contradicting the above. This is a stale, disconnected
snapshot, not evidence against the incident. Per this file's own rule ("the server
version is always the source of truth"), the live server's actual state is
authoritative, not this local artifact — this note exists so the discrepancy isn't
mistaken for a correction later.

Fix: `confirm_buy_fill()` now calls `self.save()` before returning.
`close_position()` (the SELL-side counterpart) had the same class of exposure from a
different angle — `morning_fill_check.py` used to bypass `PaperPortfolio` entirely for
SELL fills (raw JSON read/mutate/write) instead of calling it. Now routed through
`close_position()`, which also self-saves and carries its own idempotency guard
(raises if called on an already-flat position, so a duplicate fill confirmation can't
double-close). Regression test:
`paper_trading/test_fill_ordering.py::test_buy_fill_persists_to_disk`.

---

#### First 4 trades (WHIRLPOOL/TMPV/SIEMENS/BAJAJ-AUTO, Jun 2026) — VERIFIED GENUINE, not seeded (Aug 4 2026)
The system's first 4 trades — WHIRLPOOL.NS, TMPV.NS, SIEMENS.NS, BAJAJ-AUTO.NS,
all entered Jun 2-3 2026 — don't match today's fill logic: `entry_price` is exactly
`close × 1.0005` (e.g. SIEMENS: `3729.70 × 1.0005 = 3731.5648...`), not
`morning_fill_check.py`'s current next-day-open fill. Investigated and confirmed
**genuine same-day-close fills from the system's original code, not seeded/demo
data.** If this discrepancy resurfaces, it does not need re-investigating.

Two fill models, two commits: `cb678a1` (the repo's first commit) filled BUYs
immediately, same-day, at `apply_slippage(close_price, "buy")` — i.e. close × 1.0005
(`SLIPPAGE_RATE=0.0005` in `utils/costs.py`, still live today, just no longer applied
to a close price for entries). `986ecee` (Jun 19 2026, "Fix double cash deduction")
replaced that with the deferred AMO model live today: `queue_pending_buy()` at signal
time, `confirm_buy_fill()` at next morning's actual open. These 4 trades were entered
under the original model, before that commit existed.

Strongest evidence (of several independently checked): the server's actual
`portfolio_state.json` (pulled via `server_master`, a local branch fetched directly
from the live server) stores SIEMENS's entry as the raw float `3731.5648499999998`.
Running `3729.7 * 1.0005` in Python produces that exact value, byte-for-byte —
IEEE-754 rounding noise no one hand-types into seeded data. A genuine dated cron log
(`paper_trading/logs/2026-06-03.log`, real Kite auto-login/TOTP/OAuth output) and a
repo-wide history grep turning up these prices nowhere outside the actual state/log
files corroborate it.

Unresolved, doesn't affect the verdict: `portfolio_state.json`'s `entry_date` reads
`2026-06-02`, but the real log and every `signal_log.csv` row for these BUYs are
dated `2026-06-03` — a one-day label offset with no confirmed root cause. Flagged,
not solved.

**Update (Aug 5 2026):** WHIRLPOOL's and TMPV's exit prices are now also confirmed
exact, closing out this investigation. Both use the same `check_exit()` →
`apply_slippage(close, "sell")` path, unchanged across `cb678a1`→`986ecee` — no
exit-reason-dependent formula (STRATEGY_SIGNAL and CHANDELIER both just set
`exit_price = current_close`). WHIRLPOOL (STRATEGY_SIGNAL): `820.00 × 0.9995 =
819.59` exact. TMPV (CHANDELIER): `361.65 × 0.9995 = 361.469175`, rounds to
`361.4692` exact. Both inputs are `signal_log.csv`'s own logged `close_price`, not
external data. As further corroboration that the risk-manager logic itself (not
just the slippage arithmetic) is genuine, TMPV's Jun 17 chandelier stop was
independently reproduced bar-by-bar from a sliding 120-day OHLC window and matched
the logged value exactly: `372.3770`. One residual item, not affecting the
verdict: Kite's historical API today reports both tickers' closes ~0.18-0.19%
below what's logged in `signal_log.csv`, consistently across two unrelated
tickers two weeks apart — most likely a retroactive data revision. `signal_log.csv`
is authoritative here since it's what the live system actually processed.

---

#### ETF overlay capital-funding deadlock — confirmed frozen 33 days live — FIXED (Jul 27 2026)
Root cause: `rebalance_etf()` ran AFTER Phase 2's cash gate ([signal_runner.py](paper_trading/signal_runner.py)),
using the position count as of *yesterday*. A stock BUY could only be funded from
whatever cash already existed; the ETF only shrank in response to a position that
had *already* opened. Once the portfolio went fully flat (0 positions, 100% ETF
tier), there was no path back: opening the next position required cash, cash
required the ETF to shrink, and the ETF only shrank once a position was already
open. A structural deadlock, not a timing lag — it does not self-resolve.

Confirmed live via the server's actual daily logs (not inferred from snapshots):
**frozen continuously from Jun 25 2026 through Jul 27 2026 — 33 unbroken calendar
days** at 100% ETF tier / 0 open positions / ~₹243 cash. In that entire window
exactly one stock signal fired at all: COLPAL.NS, Jul 27, correctly ranked and
gated, skipped only for "Insufficient cash ₹243 (minimum ₹1,000 required)." This
is one confirmed real incident, not a month of missed trades — it's a month of a
live structural deadlock that happened to only be tested once. Whether that one
skip would have been profitable is unknown and, per this file's own standard, not
guessed at here.

This diverges from the actually-validated backtest
(`validation/etf_overlay_backtest.py`'s `_simulate_stock_scenario()`), which steps
the ETF tier to `ETF_TIERS[n_active]` the same day a position becomes active —
same-day rebalancing was already the approved, validated design; the live
implementation just never matched it.

**Fix — restore same-day rebalancing, not new behavior:**
`paper_trading/signal_runner.py`'s Phase 2 loop, for each ranked candidate that
has already cleared position-limit/news/correlation gates: compute what the ETF
tier would be if this candidate's position becomes committed
(`committed_open_count() + 1`), and if that shrinks the tier, unwind the ETF to
it via `rebalance_etf(..., projected_open_positions=...)` *before* sizing/
execution — funding the entry from the ETF the same day, exactly as the
validated backtest models. The cash-floor check now credits this projected
unwind (`projected_tier_unwind_cash()`) before deciding to skip. Multi-candidate
runs step naturally: a 2nd candidate in the same run projects
`committed_open_count()+1` off an *already-incremented* count, so if the tier at
that count is unchanged (`ETF_TIERS[1] == ETF_TIERS[2] == 0.8`) it frees nothing
further — funded only from residual cash, matching the backtest's per-day
`n_active` accumulation exactly. No new conviction/rank threshold was invented,
per explicit instruction.

**Required reversal of a prior audited decision (Finding #1/#3, Jun 21):**
that fix excluded `pending_buy` positions from the ETF tier count, reasoning
that a merely-queued BUY shouldn't shrink the ETF since it wasn't yet funded
from it. That reasoning was correct *at the time* — BUYs were funded from
pre-existing free cash, so selling ETF for a queued-but-unfunded BUY served no
purpose and risked churn on a missed fill. It became the exact mechanism that
silently undid the naive version of this fix: with `pending_buy` excluded, the
same end-of-day `rebalance_etf()` call that already runs after Phase 2 would see
0 committed positions (the queued BUY not yet counted), snap the tier back to
100%, and rebuy the ETF with the cash the proactive unwind had just freed —
reinstating the deadlock within the same run. `pending_buy` now counts as
committed for ETF tier purposes only. It remains excluded from cash deduction
(unchanged — the double-deduction fix above) and from `rebalance_etf()`'s
portfolio-value sum (now aligned with `get_portfolio_value()`, which already
excluded it for the identical double-counting reason — a consistency fix, not
new invention). Tests 9, 22, and 23 in `test_etf_overlay.py` encoded the old
exclusion directly and were rewritten in place with the reasoning above stated
in each; no other existing test changed.

**Missed-fill self-heal:** if tomorrow's AMO gaps beyond the limit buffer,
`cancel_pending_buy()` resets the position to flat (untouched cash, since
`pending_buy` was never deducted). The next cycle's end-of-day `rebalance_etf()`
then sees 0 committed positions again and rebuys the ETF on its own — no new
code path was needed for this; it was already correct once the tier-change
detection sees reality accurately.

**Truncation bug (separate, smaller fix, same file):** `delta_shares =
int(delta_value / niftybees_price)` truncated toward zero on every rebalance.
Changed to `round()`. This improves tier-targeting accuracy on the sell-side
unwinds this fix introduces — it does **not** eliminate the ~₹243 residual.
At the 100% tier, the last sub-share of cash cannot be invested regardless of
rounding vs. truncation (can't buy a fractional ETF share, can't exceed cash);
the residual was always a harmless, un-investable remainder, never the cause of
the deadlock. The deadlock was rebalance *timing*, addressed above.

**Also found and corrected, not part of the live deadlock:**
`validation/etf_overlay_backtest.py` hardcodes tier schedule `{1: 0.6, 2: 0.6,
3: 0.3}` (`A_current`) as its default. The grid search that actually validated
this overlay (`validation/etf_tier_grid_result.json`, Jun 21) tested six
schedules and selected **D_aggressive** (`{1: 0.8, 2: 0.8, 3: 0.5}`, best
Sharpe 0.280) — which is what's live in `paper_portfolio.py`. So there was no
live-vs-validated *tier value* divergence, only a stale default in a standalone
harness that isn't wired into anything live. Flagged for a separate cleanup;
not touched as part of this fix.

**Cost accounting (real numbers, not abstract percentages)** — using COLPAL.NS's
actual Jul 27 price (₹2,135.20) and the frozen portfolio's actual composition
(₹243.49 cash + 405 NIFTYBEES @ ₹243 ≈ ₹98,658 total):
- Unwinding 100%→80% frees ~₹19,440 (80 NIFTYBEES shares), funding a 9-share
  COLPAL.NS position (~₹19,217) — this fix would have unwound the ETF and
  funded that trade had it existed today.
- AMO fill-delay exposure gap (the ETF sells today, the stock buy fills
  tomorrow's open — a gap the same-day-everything backtest doesn't model): the
  ~₹19,440 slice sits out of the market for ~1 trading day. Expected cost ≈ ₹0
  (a single day's expected return is ~0), with a typical ±₹136 one-day swing at
  NIFTYBEES' ~0.7% daily volatility. Small and bounded, not free.
- Missed-fill round-trip cost, using the real constants in
  `etf_overlay_backtest.py` (`ETF_SELL_COST_PCT=0.0003`, `ETF_BUY_COST_PCT=
  0.0001`): sell + rebuy on ~₹19,440 ≈ ₹7.78 (0.008% of portfolio value). Note:
  the live paper overlay does not currently model any ETF transaction cost
  (`rebalance_etf()` moves cash at exactly `shares × price`) — ₹7.78 is what
  this would cost with real Zerodha charges applied, not what the paper system
  currently records. Pre-existing gap, unrelated to this fix.
- This fix captures signals the system is designed to act on. It does not
  guarantee any of them profit — COLPAL.NS's outcome is unknown and it's fine
  that it's unknown; sizing the deadlock's cost was never about second-guessing
  that specific trade.

Regression tests: `paper_trading/test_etf_unwind_funding.py` (7 new tests: 0→1
cash-and-tier accuracy, the exact confirmed-live deadlock scenario, missed-fill
self-heal, multi-candidate stepping, rounding-shortfall bound, and validated-tier
+ same-day-stepping parity against the grid-search artifact).

**Not done as part of this fix, by explicit instruction:** `portfolio_state.json`
was not modified to correct the currently-frozen ₹243 state. If the fix is
correct, the next qualifying signal resolves it naturally — no manual state
edit was made or is believed necessary.

---

#### Dry-run ledger contract — Findings #1 / #1b — FIXED (Aug 22-24 2026, DEPLOYED Aug 24 2026)

Branch: fix/dry-run-ledger-contract. From the full audit in AUDIT_REPORT_2026-08-22.md.

**Finding #1 (CRITICAL) — a dry run silently destroyed pending AMO fills.** The
module docstring promises "Dry-run mode (the default): portfolio state is NOT
modified". The portfolio write was guarded by `if apply_fills:`; the
amo_orders.csv write was not (4 unguarded `_update_csv_row()` sites: 576, 619,
627, 645). `_load_pending_orders()` selects on `status == "DRY_RUN"`, so a dry
run flipped the row to FILLED/MISSED/CANCELLED_CA without updating the
portfolio, making the order invisible to every later run including the 9:20 AM
cron. A BUY consumed this way left pending_buy=True, cash never deducted,
position never opened, and — since `_process_stock()` returns early on
pending_buy — no risk management ever applied. Trigger was the exact command the
docstring advertises, including `--date YYYY-MM-DD` for inspecting a past date.

**Finding #1b (HIGH) — every GAP_EXIT was logged as MISSED.** `_update_csv_row()`
only matches rows still at DRY_RUN. The SELL-miss path wrote "MISSED" at 645
before the gap-exit branch ran, so the "GAP_EXIT" write at 682 matched nothing
and no-opped. Portfolio was correct throughout (trade_log had GAP_EXIT at the
right price) — this was audit-trail corruption, not a money error. It matters
because amo_orders.csv is what this project reaches for during forensics: the
Aug 4 BAJAJ-AUTO reconciliation cross-checked trade_log against "amo_orders.csv's
SELL fill for the same date". After any gap-exit those disagree, and an
investigator applying that method would conclude a trade was lost — the exact
false positive that entry's own retraction warns about.

**Fix — plan / report / execute split, not more guards.** Guarding a fifth call
site relocates the problem to the sixth; that discipline is what failed. Instead:
`plan_fill()` is read-only and contains no writer; the report loop renders
`decision.detail` and contains no writer; `execute_decision()` is the ONLY writer,
called from the ONLY `if apply_fills:` guard. Ledger write sites 5 -> 1.
AST-verified: every writer lives in `execute_decision()` or `_requeue_sell_amo()`;
the latter is called only from the former; the former only at line 831 inside the
single guard at 829. #1b closes by the same change — each FillDecision carries
exactly one csv_status written exactly once, and all 9 returns in `plan_fill()`
terminate the function. Jul 18 portfolio-before-CSV ordering preserved.

**CAVEAT 1 — mutual exclusivity holds; TOTALITY DOES NOT.** Stated plainly
because an earlier write-up of this fix claimed the counters "derive from a
partition", which implies totality and is wrong. Machine-checked over all 8
actions:
    MUTUAL EXCLUSIVITY: HOLDS — no action lands in 2 buckets
    TOTALITY: sum(counters)=6 vs total_decisions=8 -> NOT TOTAL
              uncounted: ['NO_DATA', 'REJECT']
Audit2 Finding #4-style double-counting is now structurally impossible — that
part is real. But NO_DATA and REJECT fall into no bucket, so the summary line can
under-report. PRE-EXISTING at baseline (on main, `open_px is None` hits `continue`
before any counter; the REJECTED branch increments nothing). NOT fixed here;
separate backlog item. The in-code comment was corrected to state exactly this.

**CAVEAT 2 — deliberate dry-run behaviour change.** Gap-exit/requeue
classification now runs in both modes; previously reachable only under
apply_fills, so a dry run showed a 3%+ gap-down SELL as a plain MISS. Measured
(instrumented counter, mocked fetches; 5 pending SELLs = 2 fills, 2 requeue-
eligible, 1 gap-exit):
    baseline dry run: 10 calls | baseline --apply: 12
    patched  dry run: 12 (+2)  | patched  --apply: 12 (unchanged)
The +2 is one read-only close-price fetch per requeue-eligible SELL, on manual
inspection only. The 9:20 AM cron path is unchanged.

**REVIEW FINDING — `results` membership changed; one consumer, inert.** `results`
now includes CANCEL_CA and NO_DATA entries (old code `continue`d before
`results.append`). Checked every consumer: exactly one (the manual-attention
section, lines 856/860, same string filter both times). `run_morning_check()`
returns None on all paths; no external caller consumes it (utils/watchdog.py
matches only the job-name string). Verified "CANCELLED_CA" and "UNKNOWN" do not
match ("REJECTED","CANCELLED").

**FIXED IN REVIEW — `reason` regression introduced by this patch (Aug 24 2026).**
The split had `results` built with `"reason": d.detail.strip()`, the fully-rendered
report line, where the old code used `_process_order()`'s short `reason`. A
manual-attention line therefore read
  X.NS  REJECTED: ✗ REJECTED   X.NS   BUY   10 shr | Order was cancelled
instead of
  X.NS  REJECTED: Order was cancelled
Fixed by adding `reason: str = ""` to FillDecision and populating it at ALL NINE
return sites in plan_fill(), not just the observed-broken REJECT path — AST-verified
that no branch is left unpopulated. The four sites that already call _process_order()
carry its value through verbatim. The rest use purpose-written strings in the same
house style — a short factual phrase with no ticker/side/shares prefix, since the
consumer already prints those columns:
  CANCEL_CA      "Corporate action ex-date today — fill cancelled, open price is adjusted"
  NO_DATA        "Open price unavailable — order left pending for the next run"
  GAP_EXIT       "<base> — gap N% exceeds 3% breaker, exiting at open"
  REQUEUE_SELL   "<base> — SELL AMO requeued at Rs X for tomorrow's open"
                 "<base> — could not fetch today's close for requeue; MANUAL ACTION REQUIRED"
  UNMANAGED_MISS "<base> — not a managed exit, no automatic follow-up"
`results` now uses `d.reason`. Output for the REJECT case is byte-identical to
pre-patch main.

**REVIEW FINDING — REJECTED/CANCELLED is dead code under LIVE_TRADING_MODE=False.**
`plan_fill()` calls `_process_order(..., kite=None)`; the live branch requires
`LIVE_TRADING_MODE and order_id and kite is not None`. Empirically the only
reachable statuses with kite=None are FILLED and MISSED. So the REJECT decision
path, the manual-attention section, and the `reason` regression above are all
unreachable in the current paper configuration. Fixed for correctness before live,
not because it was observable today.

**REVIEW FINDING — circuit-breaker message is now uniform.** Old code printed
"Verify position manually in Zerodha dashboard" under FILLED and MISSED but not
REJECTED/CANCELLED. The unified loop prints it for any decision with circuit_msg.
This was a consequence of unifying the loop, not a considered decision. Kept
deliberately: a >=20% move from prev close is exactly when a human should check
the dashboard, and a rejected order during a circuit event is more alarming, not
less. The old asymmetry looks like drift (the REJECTED branch was added later in
Fix 9). Zero live effect today per the dead-code finding above.

**SURFACED, NOT FIXED — morning_fill_check.py has no rate-limit pacing.** Unlike
signal_runner.py:371 and screener/auto_screener.py:692 (both `time.sleep(1.1)`,
~0.9 req/sec), this module has none. Total calls per run bound at 2xN..3xN
(<=36 at N=12), which never accumulates 60 in a rolling minute, so the 60/min
model this repo codes to is not breached. BUT Kite documents 3 req/sec on the
historical-data endpoint, which an unpaced 36-call burst exceeds. Pre-existing;
this patch adds 2 to the burst in dry-run mode. Own backlog item.

**SURFACED — Finding #12: CANCELLED_CA strands a position.** Out of scope, tracked
separately. The CANCEL_CA branch marks the ledger row terminal but performs no
portfolio reset, in this patch AND at baseline (byte-identical results):
  BUY  -> CANCELLED_CA, pending_buy STILL True, shares 10. Ticker frozen at
       PENDING_BUY forever, same bricking as audit Finding #3 by another route.
  SELL -> CANCELLED_CA, pending_rm_exit STILL True. RM keeps ratcheting but no
       new SELL AMO is ever queued — the position can never exit. This is
       Finding #2 (Jun 19, "Orphaned RM SELL position") resurfacing via the
       corporate-action path, which the Jun 19 requeue fix never covered.
Trigger: corporate_actions fails open, so NSE flaky at 3:45 PM means no skip and
an AMO gets queued; NSE healthy at 9:20 AM detects the ex-date and cancels.

**Test results (branch fix/dry-run-ledger-contract):**
- test_dry_run_contract_audit.py: 15/15 PASS (new). Against UNPATCHED baseline:
  14 failed, 1 passed. The one passing either way is
  test_apply_still_performs_every_write, which guards against a "fix" that
  disables the real write path.
- test_morning_fill_check_audit.py: 3/3 PASS (was 3/3 FAIL).
- Combined re-run after the comment correction and the reason fix: 18 passed.
- Full suite, plain `python -m pytest -v`, no --ignore, with conftest.py from the
  sibling branch also present: 387 collected, 6 failed, 381 passed, zero
  live-call markers. All 6 failures are unrelated open audit findings:
    4x test_position_sizer_never_returns_negative_shares  (audit Finding #3)
    1x test_correlation_cli_default_path_is_cwd_dependent (audit Finding #4)
    1x test_gate_estimate_and_real_unwind_must_agree      (audit Finding #2)
  Those three test files are deliberately NOT staged on this branch — they
  document unrelated unfixed defects. Zero pre-existing tests regressed.
- test_state_file_is_absolute_and_cwd_independent passes in BOTH copies
  (test_morning_fill_check.py and test_signal_runner_fetch.py). It failed only in
  a throwaway worktree because portfolio_state.json is gitignored and absent
  there while the test asserts .exists(). Confirmed environmental — the two
  tracebacks were byte-identical patched vs unpatched, differing only in pytest's
  duration line.
- Live state files sha256-identical before/after every run.

**Deployment (Aug 24 2026):** committed as `6e400f4` on
fix/dry-run-ledger-contract, fast-forwarded into main, then merged with the
collection-hygiene branch as `c4b14eb` (one conflict, CLAUDE_CONTEXT.md only,
resolved by keeping both blocks). Pushed `6ebe151..c4b14eb`. Server pulled via
`git merge --ff-only origin/main` after a state backup
(portfolio_state_20260824_134036.json); server master now c4b14eb. Server suite:
331 passed, 0 failed (313 pre-existing + 15 + 3 from the two new test files;
the Mac's 387 adds 56 from three audit files that remain untracked and were
never committed). portfolio_state.json byte-identical to the pre-deploy backup
after the pull — Rs243.49 cash, 359 ETF units, 4 trades, no open positions.
First cron run on the new code path: 9:20 AM IST Aug 25 2026.

---

#### pytest collection hygiene — live Kite calls on every `pytest` run — FIXED (Aug 22-24 2026, DEPLOYED Aug 24 2026)

Branch: fix/kite-fetcher-collection-hygiene (separate from the dry-run fix —
unrelated defects, independently reviewable and revertable).

Root cause: `test_kite_fetcher.py` and `test_kite.py` at the repo root define
ZERO test functions. They are manual verification scripts whose checks run at
MODULE level. pytest imports every `test_*.py` during collection, so a plain
`python -m pytest` executed them — firing LIVE Kite and yfinance requests, and
printing a live TOTP code derived from ZERODHA_TOTP_SECRET into test output.

On 2026-08-22 Kite began returning PermissionException for the historical-data
call. Because that surfaced during COLLECTION, pytest aborted with "Interrupted:
1 error during collection" and ZERO of the 387 tests ran — the entire suite
blocked by a file contributing none of them.

Both files are in .gitignore ("# Test files with sensitive output") and were
UNTRACKED — they exist on the Mac and the Lightsail server, in no clone. That is
why renaming was rejected: a rename is not committable, so it would fix one
machine and silently drift from the other. Chose a committed root conftest.py
with `collect_ignore = ["test_kite.py", "test_kite_fetcher.py"]`, which applies
wherever the repo is checked out regardless of local file state.

validation/add_validated_stock.py:194 already passed the equivalent --ignore
flags to its own hard-gate subprocess — the project already knew about these two
files; only the plain `python -m pytest` documented in README.md and this file
was unprotected. Those flags are left in place as defence in depth.

**conftest.py verified cwd-independent.** Given this repo's history with relative
paths that only resolve from the project root (news_flags.json Jul 17,
AMO_CONFIG order_log_file Jul 18, correlation_check default arg — audit Finding
#4), the new conftest was explicitly checked from a non-root cwd. pytest resolves
collect_ignore entries relative to the conftest file's own directory, not the
process cwd — a structurally different mechanism from Path("relative"). Confirmed:
  cd paper_trading && pytest -v   -> 152 passed, 2 known failures, no collection error
  cd paper_trading && pytest ..   -> 381 passed, 6 known failures, no collection error
  collect-only from subdir        -> 387 collected; zero live-call markers
Not a finding. Recorded so a future audit does not have to re-derive it.

**test_kite.py TOTP print masked, then un-gitignored and tracked.** The script did
`print("\nCurrent TOTP code:", totp.now())` — a live, usable second factor echoed
to stdout and (until conftest.py) into pytest logs on every suite run. Now prints
"TOTP generation: OK (code redacted — ends in ****NN)", which still lets a human
confirm the generator matches their authenticator app. Verified by direct run:
zero 6-digit codes emitted.
.gitignore line 28 ("test_kite.py") was then removed and the masked file tracked.
Reason: the mask alone was a LOCAL edit to an untracked file — it would have fixed
the Mac and left the server still printing a full live code, with no branch diff
to review and nothing to revert. That is precisely the drift argument that ruled
out renaming test_kite_fetcher.py two days earlier: a fix living only in one
machine's working directory is not a fix, it is a divergence. Tracking makes the
masked version the single source of truth, so the server converges on the next
pull instead of needing a remembered manual edit.
Safe to track because the file contains no secrets — it reads KITE_API_KEY,
KITE_API_SECRET, ZERODHA_USER_ID, ZERODHA_PASSWORD and ZERODHA_TOTP_SECRET from
.env at runtime and prints only their truthiness ("YES"/"NO"). All 7 print() calls
audited individually: five booleans, one redacted 2-of-6 digits of an ephemeral
code, one static string. Nothing echoes a value.
test_kite_fetcher.py deliberately NOT un-ignored — excluded for a different reason
(live API calls at import), already handled by collect_ignore. .gitignore lines 28
and 29 were two separate literal entries (verified via git check-ignore before
editing, no wildcard), so removing one could not affect the other.
Residual: the .gitignore comment "# Test files with sensitive output" now sits
above only test_kite_fetcher.py, which is a live-API-call exclusion rather than a
credential one. Slightly inaccurate; left unchanged deliberately to keep the edit
to exactly one line.

**Branch verified in isolation** (existing worktree; a fresh `git worktree add`
would have been meaningless since nothing was committed at the time — the branch
ref still equalled main, and `git show <branch>:conftest.py` failed):
    313 collected, 313 passed, exit 0
    zero occurrences of "Fetching TMPV" / "=== Kite Connect ===" / "TOTP" /
    "kiteconnect.exceptions" / "ERROR collecting" / "errors during collection"
with test_kite.py, test_kite_fetcher.py and portfolio_state.json all present on
disk, morning_fill_check.py verified identical to main's, and all five branch-1
audit files absent. The 6 known audit failures cannot appear here — those three
test files are untracked and on neither branch.

**Correction to the audit record:** an earlier note claimed every full-suite
number in this audit (313, 312, 381) was silently making live calls. That is false
and is corrected here. Runs performed in throwaway git worktrees never had these
gitignored files present and were clean. Only runs in the PRIMARY checkout — the
original 313 baseline and the 387-item run on 2026-08-22 — made live calls.

**Deployment (Aug 24 2026):** committed as `4651dda` on
fix/kite-fetcher-collection-hygiene, merged into main as `c4b14eb`, pushed and
deployed alongside the dry-run fix (same server pull). Verified live on the
server: the full suite ran with zero occurrences of "Fetching TMPV",
"=== Kite Connect ===", "Current TOTP code", "kiteconnect.exceptions" or
"ERROR collecting" — the first server-side suite run in this project's history
confirmed to make no live API calls. test_kite.py on the server is now the
masked version (grep 'redacted' = 1, grep 'Current TOTP code' = 0); the
pre-deploy unmasked original is preserved there as
test_kite.py.pre-deploy-backup.

---

#### morning_fill_check.py rate limiting — backlog item from the Aug 22 audit — FIXED (Aug 24 2026, branch only)

Branch: fix/morning-check-rate-limiting. Surfaced while measuring the +2-call
cost of the dry-run contract fix; filed then as a backlog item, fixed here.

Problem: `morning_fill_check.py` had NO pacing between Kite API calls, unlike
`signal_runner.py:371` and `screener/auto_screener.py:692`, which both
`time.sleep(1.1)` (~0.9 req/sec) after every call. Total calls per morning run
are bounded at 2xN..3xN for N pending orders, so the ~60 req/min model this repo
codes to was never breached — but Kite documents 3 req/sec on the
historical-data endpoint, and an unpaced 36-call burst exceeds that.

**A fourth Kite call site was found that earlier notes in this session missed.**
Prior write-ups named three (`_fetch_open_price`, `_fetch_prev_close`,
`_fetch_close_price`). A grep for every call — not just the three already
believed to exist — turned up `_fetch_live_order_status()` calling
`kite.orders()` at line 226. It is unreachable while LIVE_TRADING_MODE is False,
but it is a real Kite call and is now paced too. Recorded because "the three
fetch helpers" appears in earlier notes and is wrong.

Fix: `time.sleep(1.1)` after each of the four call sites, matching the existing
convention exactly — placed immediately after the call and BEFORE any result
guards, since the API call consumed quota regardless of what happens to the
response; an exception skips it, because a failed call consumed none. The
kite.orders() site hits the orderbook endpoint, which Kite rate-limits more
generously than historical-data; 1.1s there is deliberately conservative rather
than tuned, so all four sites share one rule.

**Measured cost, not estimated** (get_ohlcv patched so no network; time.sleep
left real; 12 pending SELLs all requeue-eligible = the worst case where every
order needs all three fetches):
    WITHOUT pacing :   0.01s   (36 Kite calls)
    WITH pacing    :  39.78s   (36 Kite calls)
    ADDED          :  39.77s   (expected 36 x 1.1 = 39.6s — linear, as designed)
Scaling is exactly linear, so the typical case is far smaller: with
MAX_CONCURRENT_POSITIONS=4 a normal morning has <=4 pending orders, i.e. 8-12
calls, i.e. ~9-13s. The 36-call case needs weekend/holiday carry-forward to
stack unfilled rows. ~40s on the 9:20 AM run is harmless — the run reads an open
price that was already established at 9:15, so there is no deadline being
approached, and signal_runner already spends ~16.5s (1.1s x 15 stocks) on the
same pacing at 3:45 PM.

Tests: `paper_trading/test_morning_fill_rate_limit.py`, 13 tests mirroring
test_signal_runner_fetch.py's sleep tests — one sleep per successful fetch for
each helper, sleep still fires when a result guard returns early (empty
DataFrame), no sleep on the exception path, all three cases for kite.orders()
(success / no order_id early return / raises), plus a guard test that counts
Kite call sites against sleep calls in the source so a future unpaced call site
fails the suite.

Confirmed the earlier API-call-count measurements in this session are unaffected:
test_dry_run_contract_audit.py and test_morning_fill_check_audit.py patch at the
HELPER level (`_fetch_open_price` etc.), so the real functions containing the
sleeps never execute in those tests — 18 passed in 0.29s, unchanged.

Full suite: 394 passed, 6 failed. The 6 are the unrelated open audit findings
(#2 ETF gate, #3 sizer, #4 correlation CLI path), unchanged. Zero regressions.

NOT merged, NOT pushed, NOT deployed.

---

#### screener/nifty500_cache.json untracked — backlog item from the Aug 24 deploy — FIXED (Aug 24 2026, branch only)

Branch: fix/untrack-nifty500-cache. Surfaced during the Aug 24 deploy, when the
server's pre-pull `git status --short` showed ` M screener/nifty500_cache.json`
and correctly triggered a STOP-and-investigate before the pull.

Problem: the file is tracked, but `fetch_nifty500()` in auto_screener.py rewrites
it on every successful live fetch — i.e. on the Wed/Sun screener cron. So the
server's working tree went dirty on a fixed schedule, forever. Same class as the
151 runtime artifacts the Jul 19 cleanup untracked
(`git ls-files -ci --exclude-standard | xargs git rm --cached`); this one escaped
that sweep because it was never in .gitignore, so it was genuinely tracked rather
than tracked-but-ignored. The Aug 24 investigation confirmed the diff was benign
(fetched_at 2026-07-12 -> 2026-08-23, one constituent swapped) and that the
incoming commits did not touch the file — but the cost of that investigation is
exactly the recurring tax being removed here.

Fix: `git rm --cached screener/nifty500_cache.json` (file stays on disk
everywhere it currently exists) plus a .gitignore entry with the reasoning inline.

**Trade-off, accepted deliberately.** The file was originally committed on
purpose: Audit2 Finding #22 (Jul 2 2026) built the cache fallback and
"pre-populated with 500 tickers" so a fresh clone would have something to fall
back on. Untracking removes that. The exposure is narrow: a brand-new checkout
whose FIRST screener run ALSO hits an NSE fetch failure gets neither live data
nor cache, and `fetch_nifty500()` returns `{}, True` after printing
"CRITICAL: NIFTY 500 fetch failed AND no valid cache found. Screener cannot run."
That is a loud, graceful, self-healing failure — the next successful run
repopulates the cache permanently. Weighed against a working tree that goes dirty
twice a week on the live server forever, the recurring cost is the larger one.

**The seed-file alternative was considered and REJECTED as worse, not skipped.**
Keeping a tracked seed at a separate path (e.g. screener/nifty500_seed.json) with
a code change to fall back to it would need the seed to be permanently frozen in
git. A stale-but-present seed is more dangerous than a loud absence: the screener
would run to completion on an outdated constituent list and emit only a "results
may be slightly stale" warning, whereas no seed at all stops the run with a
CRITICAL. Note the tracked copy was already stale — the Mac's committed version
read `fetched_at: 2026-07-12` while the server's live copy read `2026-08-23`, so
a fresh clone today would already have seeded from six-week-old data. If the
fresh-clone case ever needs covering, the correct fix is a documented one-line
bootstrap command, not a frozen artifact in version control.

**DEPLOY PROCEDURE — this one cannot be a plain `git merge --ff-only`.** The
commit deletes the path from the tree, and the server's copy is MODIFIED, so the
merge will refuse with "Your local changes to the following files would be
overwritten by merge". Required sequence on the server:
    cp screener/nifty500_cache.json /tmp/nifty500_cache.json.keep
    git checkout -- screener/nifty500_cache.json     # clean the working tree
    git merge --ff-only origin/main                  # removes it from tracking
    cp /tmp/nifty500_cache.json.keep screener/nifty500_cache.json
The final copy-back matters: without it the live cache is gone until the next
successful Wed/Sun fetch, which reintroduces exactly the fresh-clone exposure
described above on a machine that did not need to have it.

NOT merged, NOT pushed, NOT deployed.

---

#### CANCELLED_CA strands the position it belongs to — Finding #12 — FIXED (Aug 25 2026, branch only)

Branch: fix/cancelled-ca-position-reset. Surfaced Aug 24 while reviewing
fix_01_dry_run_contract.patch — specifically while answering why CANCELLED_CA is
excluded from the manual-attention section. NOT caused by that patch and not
fixed by it; verified byte-identical behaviour on baseline HEAD and patched.

Problem: `plan_fill()` returns a bare CANCEL_CA decision and `execute_decision()`
has NO branch for it. The ledger write at the bottom is unconditional on
`d.csv_status`, but position resolution is dispatched per-action — so the order
is marked terminal (invisible to `_load_pending_orders()`, which selects on
status == "DRY_RUN") while the position is left exactly as it was. Every other
terminal action resolves its position: FILL -> confirm_buy_fill/close_position,
GAP_EXIT -> close_position, MISS_CANCEL_BUY and REJECT -> cancel_pending_buy,
REQUEUE_SELL -> requeue_rm_sell. CANCEL_CA was the only one that resolved nothing.

No documented intent exists for the position-state side and none was invented.
The only statement anywhere is README.md:78 ("Corporate actions — cancel fills if
ex-date today"), which covers the FILL. The standard applied is internal
consistency with the five sibling branches above.

Two failure modes, different in kind:
  BUY  — pending_buy stays True. _process_stock() returns early on it every run,
         so the ticker produces no signal, gets no risk management, emits no AMO.
         Same outcome as audit Finding #3 by a different route. Bounded: no
         capital committed, since queue_pending_buy() never deducts cash.
  SELL — pending_rm_exit stays True with shares > 0 and NO code path can close
         the position. Verified both sides: morning_fill_check sees no DRY_RUN
         row, and `grep -c needs_amo_order` over signal_runner's pending_rm_exit
         branch returns 0 while Step 13 emits orders only where that field is
         set. The RM keeps ratcheting a chandelier stop that can never fire an
         order. This is Finding #2 (Jun 19 2026, "Orphaned RM SELL position")
         reappearing through the corporate-action path — the Jun 19
         requeue_rm_sell() fix covered the missed-fill route only, because the
         corporate-action cancel did not exist when it was written.

Trigger confirmed reachable, not asserted: utils/corporate_actions.py fails open
(`except Exception: ... return no_action` with skip=False). NSE transiently down
at the 3:45 PM signal run -> no skip -> AMO queued; NSE healthy at 9:20 AM ->
ex-date correctly detected -> CANCELLED_CA -> strand. Note WHY an outage is
required: the signal-time danger window is {check_date, next_1, next_2}, so an
ex-date on the fill day would normally be caught at 3:45 PM. The fail-open path
is what lets it through, which makes this conditional on an NSE outage rather
than routine.

Severity HIGH, with CRITICAL considered and rejected. The SELL side is the
strongest case for CRITICAL — a position with no path that can close it is worse
than a frozen ticker, which at least has a cost floor. Still HIGH because (a) no
value is silently wrong: cash, shares, entry_price, trade_log and P&L stay
correct and internally consistent — it is a stuck state machine, not corrupted
state, where Finding #1 had the recorded portfolio diverge from reality; and
(b) it is not silent: the position prints as PENDING_RM_EXIT in the daily report
every day with its chandelier level.
WHERE THIS WOULD BE REVISED: argument (b) collapses if the daily report is not
read day to day, in which case a recurring PENDING_RM_EXIT line is
indistinguishable from a normal one-day pending exit and this is CRITICAL in
practice. This file already records that exact pattern — the Aug 22 EMAMILTD.NS
removal went unreviewed for four screener cycles because REMOVE recommendations
have no console output in a real run.

Fix — reuses both existing seams; no new machinery, no new threshold:
  BUY  -> cancel_pending_buy(), identical to the MISS_CANCEL_BUY/REJECT handling.
  SELL -> requeue_rm_sell() via the existing _requeue_sell_amo() helper.

On whether requeue_rm_sell() is semantically valid here — checked before reusing,
not assumed. Its two runtime guards are pending_rm_exit == True and shares > 0;
CANCEL_CA leaves exactly that, so the STATE contract fits exactly. Its DOCSTRING
was narrower than its guards ("Only call this after a confirmed MISSED RM SELL")
and a cancel is not a miss. Rather than call it outside its documented contract
or fork a near-duplicate method, the docstring was WIDENED to name both callers
and explain why they share the counter. The 3-requeue-then-CRITICAL cap is
preserved unchanged and is tested.
Consequence stated plainly: a CA cancel now consumes one of the three retries.
Slightly conservative — an ex-date is a scheduled, self-resolving, one-day event
unlike the liquidity/circuit problems the cap was designed for — so it alerts
marginally earlier than strictly necessary. A shared counter was judged better
than a parallel one; a second threshold would need a new state field, a
migration, and its own tests to guard a rarer case.

Cost: one extra _fetch_close_price() call, only on an ex-date SELL, paced at 1.1s
by the Aug 24 rate-limiting fix. Today's close is already post-adjustment, so it
is the correct basis for tomorrow's limit — the same basis the MISSED-SELL
requeue uses.

Manual-attention section deliberately UNCHANGED. Working out which case is which:
  - Routine cancel (BUY reset to flat, SELL requeued) is now a HANDLED outcome —
    log line only. Adding CANCELLED_CA to the ("REJECTED","CANCELLED") filter
    would surface every routine cancel as noise.
  - Retry cap exhausted: requeue_rm_sell() already prints a CRITICAL block with
    MANUAL ACTION REQUIRED, automatically, regardless of cancel reason — tested.
  - Close fetch fails so the SELL cannot be requeued: still a genuine strand, so
    plan_fill() renders an explicit "MANUAL ACTION REQUIRED: position is still
    open with no exit order." line, matching the MISSED-SELL treatment of the
    same condition.
Both genuinely-bad cases already produce loud, distinct output.

Test results:
- paper_trading/test_corp_action_cancel_audit.py, 5 tests.
    BASELINE HEAD : 4 failed, 1 passed
    PATCHED       : 5 passed
  The one baseline pass is deliberate — it is the supporting evidence that the
  ledger row really is terminal, which is what makes the strand permanent.
- Full suite, patched: 349 passed, 0 failed. 349 = the server's 344 baseline + 5
  new. Zero live-call markers. Verified in a throwaway worktree.

Hygiene finding, flagged not fixed: TWO pairs of duplicate test names exist
across the suite, not one. Both pairs live in the same two files —
test_state_file_is_absolute_and_cwd_independent and
test_token_file_is_absolute_and_cwd_independent, each defined in both
paper_trading/test_morning_fill_check.py and
paper_trading/test_signal_runner_fetch.py. This already cost time once, when a
command targeting one resolved to the other. A _mfc/_sr suffix resolves both.

NOT merged, NOT pushed, NOT deployed.

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
- tz-stripping must use df["date"].dt.tz_localize(None) on the column before
  set_index(), never DatetimeIndex.map(lambda dt: dt.replace(tzinfo=None))
  on the index — the latter silently no-ops on pandas 2.3.3 (verified live
  on the Lightsail server, Jul 19 2026), leaving the index tz-aware

### General Rule
- Before reimplementing any function, check if it already exists in the codebase
- grep -rn 'def function_name' ~/algo-trading/ --include='*.py'
- Use the existing implementation — it is already tested and validated
