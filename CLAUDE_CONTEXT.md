# Claude Context — NSE Algo Trading System
Last updated: 2026-06-16

## Current Trading Universe (10 stocks)
TMPV.NS, WHIRLPOOL.NS, SIEMENS.NS, BAJAJ-AUTO.NS, CUMMINSIND.NS, HCLTECH.NS, BOSCHLTD.NS, COLPAL.NS, ANURAS.NS, HEROMOTOCO.NS

## Universe Addition History
- Original (Jun 2): TMPV, WHIRLPOOL, SIEMENS, BAJAJ-AUTO
- Added Jun 12: CUMMINSIND (H=0.559, ADX=27.3), HCLTECH (H=0.614, ADX=26.3)
- Added Jun 16: BOSCHLTD (H=0.576, fresh golden cross), COLPAL (H=0.569, fresh golden cross), ANURAS (H=0.608, fresh golden cross), HEROMOTOCO (H=0.572, death cross -2.84%)

## Watchlist (not yet added)
- BHEL.NS: H=0.558, ADX=22.8, UNCLASSIFIED — re-check in 3-4 weeks
- AUBANK: Gap=-0.76%, close to golden cross
- PFIZER: Gap=-2.66%, low vol quality pharma

## SMA Gap Status (as of Jun 16)
- CUMMINSIND: already in golden cross before we added it — waiting for fresh cross
- HCLTECH: death cross -6.60% — waiting
- BOSCHLTD: golden cross +1.05% — will enter soon
- COLPAL: golden cross +0.36% — will enter soon
- ANURAS: golden cross +0.32% — will enter soon
- HEROMOTOCO: death cross -2.84% — waiting

## Paper Trading Status (as of 2026-06-16)
- Started: 2026-06-02
- Portfolio: Rs99,221 (-0.78%)
- Cash: Rs61,073
- Open positions: TMPV (+0.7%), SIEMENS (-2.6%), BAJAJ-AUTO (-3.3%)
- WHIRLPOOL: CLOSED since Jun 3 (BUY + SELL same day via strategy signal, -Rs188). No open position. No cooldown. Will re-enter on next golden cross.
- Completed trades: 1 (WHIRLPOOL intraday exit Jun 3, -Rs188)

## Infrastructure
- AWS Lightsail Mumbai: ubuntu@13.205.133.169
- SSH key: ~/.ssh/LightsailDefaultKey-ap-south-1.pem
- Cron: 15 10 * * 1-5 (3:45 PM IST), 50 3 * * 1-5 (9:20 AM IST)
- Server path: /home/ubuntu/algo-trading/
- Local path: /Users/aaravagarwal/algo-trading/
- Python on server: python3 (not python)
- Venv on server: ~/algo-trading/venv

## Key Workflow Notes
- Always SCP files from server before git push (server is source of truth)
- correlation_check.py has hardcoded CANDIDATES list — must edit manually
- universe_expansion.py does not exist on server
- bars_held=0 mid-day is normal — updates at 3:45 PM signal run
- morning_fill_check.py and corporate_actions.py are fully dynamic
- Check logs at: ~/algo-trading/paper_trading/logs/YYYY-MM-DD.log

## Going Live Checklist (future)
- Minimum capital: Rs50,000 recommended
- Wait for 20-25 days clean paper trading data (currently at ~10 days)
- Flip PAPER_TRADING_MODE=False in signal_runner.py
- Disable dry-run in engine/order_manager.py
- Reset portfolio_state.json with real capital
- Archive current paper trading logs before reset
- Recalibrate position sizer — BAJAJ-AUTO at Rs10,000+ needs >Rs50,000 capital

## Correlation Results (Jun 16)
- All pairs < 0.70 (safe)
- Highest pair: HEROMOTOCO/BAJAJ-AUTO r=0.53 (both two-wheelers, acceptable)
- Best diversifiers: ANURAS (avg r=0.11), COLPAL (avg r=0.19)

## System Limitations
- No news/merger monitoring — only scheduled NSE ex-dates
- No F&O support (future project)
- Screener sector map only covers original 73 stocks — new stocks show as Unknown

## Screener Universe (updated Jun 16)
- sma_screener.py and regime_classifier.py now dynamically fetch NIFTY 500 (504 stocks) from NSE
- Falls back to hardcoded 73-stock list if NSE fetch fails
- Both screeners automatically use the expanded universe on every run
- sector map covers all major NSE industries via _INDUSTRY_TO_SECTOR mapping

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
