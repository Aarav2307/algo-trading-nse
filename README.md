# NSE Algo Trading System

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-Lightsail%20Mumbai-FF9900?logo=amazon-aws&logoColor=white)
![Zerodha](https://img.shields.io/badge/Broker-Zerodha%20Kite%20Connect-387ED1)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Paper%20Trading-yellow)

Automated end-of-day swing trading system for NSE India equity markets. Built entirely in Python, deployed on AWS, connected to live Zerodha Kite Connect API. Generates daily signals after market close, places AMO limit orders, and runs a morning fill-check at market open — fully unattended.

Currently in paper trading phase (started Jun 2 2026, 33 trading days).
Walk-forward validated across 8 years of NSE data. 4 completed trades,
portfolio Rs243.49 cash + 359 NIFTYBEES units as of Jul 5 2026.
All capital deployed in NIFTYBEES ETF overlay while awaiting BULL regime flip.

**Current universe (8 stocks):** BAJAJ-AUTO.NS, HCLTECH.NS, COLPAL.NS,
ANURAS.NS, NEWGEN.NS, JKTYRE.NS, BSOFT.NS, PERSISTENT.NS

**Screener universe:** Dynamic NIFTY 500 (504 stocks) fetched live from NSE.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Risk Management](#risk-management)
- [Backtesting & Validation Results](#backtesting--validation-results)
- [Project Structure](#project-structure)
- [Infrastructure & Automation](#infrastructure--automation)
- [Setup & Installation](#setup--installation)
- [Disclaimer](#disclaimer)

---

## Tech Stack

| Layer             | Technology                                      |
| ----------------- | ----------------------------------------------- |
| Language          | Python 3.10+                                    |
| Data & Execution  | Zerodha Kite Connect API                        |
| Data Fallback     | yfinance (backtesting only)                     |
| Core Libraries    | pandas, numpy, kiteconnect, pyotp, requests     |
| Auth Automation   | HTTP-based TOTP login (no browser required)     |
| Cloud Deployment  | AWS Lightsail Ubuntu 22.04 (ap-south-1, Mumbai) |
| Scheduling        | Linux cron, two jobs per trading day            |
| Corporate Actions | NSE India unofficial API (`nse` library)        |
| Backtesting       | Custom bar-by-bar simulation engine             |

---

## Architecture

The system runs in two daily phases, fully automated:

```
3:45 PM IST — Evening run (signal_runner.py)
┌──────────────────────────────────────────────────────────┐
│  1. Auth check — verify Kite token is fresh (<8 hrs)     │
│  2. Market day check — NSE holiday calendar (dynamic)    │
│  3. Fetch NIFTYBEES price — ETF reporting + rebalance    │
│  4. Fetch OHLCV — last 120 calendar days per stock       │
│  5. NIFTY regime filter — suppress entries in bear mkt   │
│  6. Corporate actions check — ex-dates in next 2 days    │
│  7. Phase 1: collect BUY candidates (golden cross)       │
│  8. Risk manager — check stops on all open positions     │
│  9. Phase 2: rank BUY candidates, correlation check,     │
│     cash gate (min Rs1,000), execute in rank order       │
│ 10. ETF overlay rebalance — NIFTYBEES tier adjustment    │
│ 11. AMO orders — limit orders for tomorrow's open        │
│ 12. Signal report — terminal + email + CSV log           │
└──────────────────────────────────────────────────────────┘

9:20 AM IST — Morning run (morning_fill_check.py)
┌──────────────────────────────────────────────────────────┐
│  1. Market day check — skip if holiday/weekend           │
│  2. Corporate actions — cancel fills if ex-date today    │
│  3. Fetch open prices — today's opening bar per stock    │
│  4. Fill check — did the open price beat our limit?      │
│  5. Gap-down circuit breaker — if gap >3%, GAP_EXIT      │
│     at open instead of requeue                           │
│  6. Portfolio update — record actual fill prices         │
│  7. State integrity check — validates portfolio vs       │
│     50% floor, trade log, ETF tier validity              │
│  8. Morning report — filled / missed / gap_exit          │
└──────────────────────────────────────────────────────────┘
```

### Component Map

```
Data Layer
  data/kite_fetcher.py        → Kite Connect OHLCV (daily bars, NSE)
  data/fetcher.py             → yfinance fallback (backtesting only)

Strategy Layer
  strategies/sma_crossover.py → Golden/Death cross (20/50 SMA)
  strategies/mean_reversion.py→ RSI + Bollinger Bands

Risk & Execution Engine
  engine/risk_manager.py      → 4-layer exit system
  engine/cooldown.py          → Post-exit re-entry suppression
  engine/position_sizer.py    → Fixed-fractional sizing
  engine/portfolio.py         → Cash, shares, P&L ledger
  engine/backtester.py        → Bar-by-bar simulation loop
  engine/order_manager.py     → AMO order logging (dry-run / live)

Paper Trading Layer
  paper_trading/signal_runner.py   → Daily EOD runner
  paper_trading/morning_fill_check.py → Fill confirmation at open
  paper_trading/paper_portfolio.py → Persistent portfolio state (JSON)

Utilities
  utils/costs.py              → Transaction cost model (delivery: ₹0 brokerage, STT 0.1% both sides, DP ₹15.34/sell)
  utils/market_calendar.py    → NSE holiday calendar (dynamic API fetch + local cache), trading day checks
  utils/corporate_actions.py  → Live NSE ex-date checks (splits, bonuses, dividends)

Validation
  validation/walk_forward.py       → IS vs OOS walk-forward, 6 metrics, dynamic OOS end date
  validation/etf_overlay_backtest.py → ETF overlay backtest (349 stocks)
  validation/etf_tier_grid_search.py → Tier configuration grid search

Auth
  auth/auto_login.py          → HTTP-based automated Kite Connect login
  auth/kite_login.py          → Manual login (local development)
```

---

## Risk Management

Every open position is evaluated bar-by-bar through four independent exit layers, checked in order before the strategy signal is ever evaluated. If any layer fires, the position closes at today's close and a 15-bar cooldown gate prevents immediate re-entry.

| Layer                        | Mechanism                                                                              | Rationale                                                                                                       |
| ---------------------------- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **L1 — Hard Stop**           | −20% from slippage-adjusted entry. Never overridden.                                   | Catastrophic loss prevention. Fires even during ATR warm-up.                                                    |
| **L2 — ATR Chandelier**      | `highest_high − 3 × Wilder_ATR(22)`. Stop ratchets up only, never down.                | Trend-following trailing stop. Locks in profit as trade moves in favour.                                        |
| **L3 — Time Stop**           | Exit after 60 bars regardless of P&L.                                                  | Frees capital from stalled positions. Prevents dead-money tie-up.                                               |
| **L4 — Round Number Offset** | Shifts L2 stop 1% below the nearest NSE psychological level (₹50/₹100/₹500 intervals). | HFT and algorithmic traders probe round numbers for stop clusters. This avoids the most common stop-hunt zones. |

**Position Sizing** uses fixed-fractional Kelly-inspired sizing: each trade risks exactly 1.5% of current portfolio value. Stop distance is computed from the L2 Chandelier level at entry. After a loss, position size shrinks; after a gain, it grows — built-in anti-martingale compounding.

**Corporate Action Safety**: before any signal is generated, the system queries the NSE API for upcoming ex-dates (splits, bonus issues, rights issues, dividends > 1% of price) within the next 2 trading days. Affected stocks are skipped entirely — their price action is driven by accounting adjustments, not supply and demand.

---

## ETF Overlay (NIFTYBEES Cash Drag Reduction)

The strategy sits in cash 60-70% of the time waiting for golden cross signals.
To reduce this idle cash drag, a tiered NIFTYBEES ETF overlay deploys idle
capital inversely proportional to active stock positions.

### Tier Configuration (D_aggressive — grid-search validated)

| Open Positions | ETF Allocation |
|----------------|----------------|
| 0              | 100%           |
| 1–2            | 80%            |
| 3              | 50%            |
| 4 (max)        | 0%             |

### Validation Results (OOS 2023-01-01 → today, 349 stocks)

| Metric            | Strategy Only | + ETF Overlay |
|-------------------|---------------|---------------|
| Total return      | +4.8%         | +32.2%        |
| Sharpe (rf=6.5%)  | -8.804        | +0.219        |
| Max drawdown      | -0.8%*        | -14.3%        |
| ETF cost/year     | —             | 0.094%        |

*Strategy-only DD is artificially low due to 68% cash holding.
Overlay DD (-14.3%) vs NIFTYBEES B&H (-15.2%) — overlay adds no extra tail risk.

### Design Decisions
- ETF rebalances only when position tier changes — not on every bar
- ETF runs independently of NIFTY regime filter (regime blocks stock entries only)
- Pending BUY positions excluded from tier count (cash not yet deployed)
- Pending SELL positions included in tier count (position not yet closed)
- Gap-down circuit breaker applies to stock exits only — ETF holds through gaps

---

## Backtesting & Validation Results

### Walk-Forward Validation

Two independent windows tested with identical frozen parameters.
No re-optimisation between windows.

| Window   | IS Period               | OOS Period              |
|----------|-------------------------|-------------------------|
| Original | 2018-01-01 → 2022-12-31 | 2023-01-01 → today      |
| Extended | 2015-01-01 → 2019-12-31 | 2020-01-01 → 2023-01-01 |

**Overall result: 17/24 metrics PASS (71%) — System Validated**
*(threshold: ≥65% = Validated)*

6 metrics per stock per window: OOS return > IS return, OOS Sharpe > 0,
payoff ratio > 1.5, win rate > 40%, expectancy > 0, min OOS return ≥ +4%.

| Stock         | OOS Return | OOS Max DD | Score  | Status  |
|---------------|------------|------------|--------|---------|
| BAJAJ-AUTO.NS | +13.5%     | -3.1%      | 6/6    | ✅ KEEP  |
| COLPAL.NS     | +9.1%      | -4.9%      | 10/12  | ✅ KEEP  |
| HCLTECH.NS    | part of extended 37/50 | — | —  | ✅ KEEP  |
| JKTYRE.NS     | part of extended 37/50 | — | —  | ✅ KEEP  |
| BSOFT.NS      | +44.5% (ext OOS) | -4.5% | 4/5  | ✅ KEEP  |
| WHIRLPOOL.NS  | +3.5%      | -3.0%      | FAIL   | ❌ REMOVED Jun 24 |
| SIEMENS.NS    | ~0%        | -7.9%      | FAIL   | ❌ REMOVED Jun 24 |

Extended walk-forward (2015-19 IS / 2020-23 OOS) across 10 stocks:
**37/50 (74%)** — strategy validated through COVID crash and 2022 rate hike selloff.

> OOS end date is dynamic (`date.today()`) — validation always extends to today.
> Next quarterly walk-forward run: October 2026.

### Stress Tests

| Scenario | Result |
|----------|--------|
| COVID crash (Feb–Apr 2020) | Max portfolio DD: 1.8–3.1% while NIFTY fell 35–40% |
| AMO-realistic fills (next-day open vs same-day close) | Score unchanged at 17/24 |
| ETF overlay crash test (COVID 2020) | Overlay DD -34.8% vs NIFTYBEES B&H -36.3% |

### Transaction Cost Model

All backtests use verified Zerodha delivery costs (confirmed Jun 2026,
zerodha.com/charges):

```
Brokerage    : ₹0 — Zerodha equity delivery is free
STT          : 0.1% on BOTH buy and sell sides (delivery)
NSE exchange : 0.00335% on turnover
SEBI fee     : 0.0001% on turnover
GST          : 18% on (exchange + SEBI fees)
Stamp duty   : 0.015% on buy-side only
DP charge    : ₹15.34 flat per sell (CDSL + Zerodha + GST)
Slippage     : 0.05% on execution price (both sides)

Total buy-side  : ~₹11.91 per ₹10,000 trade
Total sell-side : ~₹25.75 per ₹10,000 trade
Round-trip      : ~₹37.66 per ₹10,000 (old model ₹34.09 was wrong)
```

---

## Project Structure

```
algo-trading/
│
├── auth/
│   ├── auto_login.py           # Automated Kite Connect login via HTTP + TOTP
│   └── kite_login.py           # Manual OAuth login (local development)
│
├── data/
│   ├── kite_fetcher.py         # Live OHLCV from Kite Connect (NSE daily bars)
│   └── fetcher.py              # yfinance wrapper (backtesting fallback)
│
├── engine/
│   ├── backtester.py           # Bar-by-bar simulation loop, supports AMO fills
│   ├── risk_manager.py         # 4-layer exit system (Hard Stop, Chandelier, Time, RoundNum)
│   ├── cooldown.py             # 15-bar post-RM-exit re-entry suppression
│   ├── position_sizer.py       # Fixed-fractional 1.5% risk sizing
│   ├── portfolio.py            # Cash, shares, trade log, transaction costs
│   └── order_manager.py        # AMO order logger (dry-run / live mode)
│
├── strategies/
│   ├── sma_crossover.py        # 20/50 SMA golden/death cross signals
│   └── mean_reversion.py       # RSI + Bollinger Bands signals
│
├── screener/
│   ├── auto_screener.py        # Full NIFTY 500 screener — Hurst, ADX, correlation, gap
│   ├── emailer.py              # HTML email report with ADD/MONITOR/WATCH/REMOVE sections
│   ├── regime_classifier.py    # Hurst exponent + ADX stock regime detection
│   ├── sma_screener.py         # Universe scan for crossover candidates
│   └── config.py               # Screener parameters and universe definition
│
├── paper_trading/
│   ├── signal_runner.py        # Daily EOD runner — signals, ETF rebalance, AMO orders
│   ├── morning_fill_check.py   # 9:20 AM fill check with gap-down circuit breaker
│   ├── paper_portfolio.py      # JSON-persisted portfolio state, ETF overlay, integrity validator
│   ├── correlation_check.py    # Entry-time correlation check vs open positions
│   ├── repair_portfolio_state.py # Emergency state repair script
│   ├── portfolio_state.json    # Live portfolio state (positions, cash, ETF, cooldowns)
│   ├── signal_log.csv          # Append-only daily signal history
│   ├── amo_orders.csv          # AMO order log with fill tracking
│   ├── backup_state.sh         # Timestamped state backup before any SCP
│   ├── run_daily.sh            # Cron wrapper — 3:45 PM IST
│   ├── run_morning_check.sh    # Cron wrapper — 9:20 AM IST (--apply flag enabled)
│   ├── test_etf_overlay.py     # 23 unit tests — ETF overlay, portfolio integrity
│   ├── test_gap_breaker.py     # 9 unit tests — gap-down circuit breaker
│   └── test_correlation_check.py # 6 unit tests — correlation check
│
├── utils/
│   ├── costs.py                # Transaction costs (delivery: ₹0 brokerage, STT 0.1%, DP ₹15.34/sell)
│   ├── market_calendar.py      # NSE holiday calendar — dynamic API fetch + local cache
│   ├── corporate_actions.py    # Live NSE ex-date checks (splits, bonuses, dividends)
│   ├── test_costs.py           # 7 unit tests — transaction cost model
│   └── nse_holiday_cache.json  # Cached NSE holiday data (auto-updated from API)
│
├── validation/
│   ├── walk_forward.py              # IS vs OOS walk-forward, 6 metrics, dynamic OOS end date
│   ├── etf_overlay_backtest.py      # ETF overlay validation (349 stocks, 2023-today)
│   ├── etf_overlay_result.json      # ETF overlay go/no-go result (all_core_pass=true)
│   ├── etf_tier_grid_search.py      # 6-config tier grid search (D_aggressive winner)
│   ├── etf_tier_grid_result.json    # Grid search results
│   └── crash_scenario_sim.py        # COVID 2020 stress test for ETF overlay
│
├── run_backtest.py             # Backtesting entry point with CONFIG
└── test_corporate_actions.py   # Corporate actions utility test suite
```

---

## Infrastructure & Automation

The system runs unattended on a **AWS Lightsail Ubuntu 22.04 instance** in the Mumbai region (`ap-south-1`), co-located with NSE for low-latency data access.

### Cron Schedule (server time = UTC)

```
# 3:45 PM IST = 10:15 AM UTC — evening signal run
15 10 * * 1-5 /home/ubuntu/algo-trading/paper_trading/run_daily.sh

# 9:20 AM IST = 03:50 AM UTC — morning fill check
50 3 * * 1-5 /home/ubuntu/algo-trading/paper_trading/run_morning_check.sh

# 6:00 PM IST = 12:30 PM UTC — universe screener (Wed + Sun)
30 12 * * 0,3 /home/ubuntu/algo-trading/paper_trading/run_screen.sh
```

### Authentication

Zerodha Kite Connect tokens expire daily. The automated login flow:

1. `auth/auto_login.py` POSTs credentials to `kite.zerodha.com/api/login`
2. Receives `request_id`, generates a fresh 6-digit TOTP via `pyotp`
3. POSTs to `/api/twofa` — no browser, no Playwright, pure HTTP
4. Follows the OAuth redirect chain to capture `request_token`
5. Exchanges for `access_token` via `kite.generate_session()`
6. Writes token to `auth/access_token.txt` with timestamp

No manual intervention required. The token is refreshed automatically before each evening run.

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- Zerodha account with Kite Connect API subscription
- AWS account (for server deployment)

### Local Setup

```bash
git clone https://github.com/Aarav2307/algo-trading.git
cd algo-trading
python3 -m venv venv
source venv/bin/activate
pip install pandas numpy kiteconnect pyotp python-dotenv requests nse
```

### Environment Variables

Create `.env` in the project root:

```
KITE_API_KEY=your_kite_api_key
KITE_API_SECRET=your_kite_api_secret
ZERODHA_USER_ID=your_zerodha_client_id
ZERODHA_PASSWORD=your_zerodha_password
ZERODHA_TOTP_SECRET=your_totp_base32_secret
```

> **TOTP secret**: The Base32 key behind your authenticator app, not the 6-digit code.
> Found in Zerodha → My Profile → Security → 2FA → "Can't scan? Enter manually."

### Run a Backtest

```bash
# Edit CONFIG in run_backtest.py to set ticker, date range, capital
python run_backtest.py
```

### Run Walk-Forward Validation

```bash
python validation/walk_forward.py
```

### Generate Today's Signals (dry run)

```bash
python auth/auto_login.py              # refresh Kite token
python paper_trading/signal_runner.py  # generate signals, queue AMO orders
```

### Check Morning Fills

```bash
python paper_trading/morning_fill_check.py --apply
```

---

## Disclaimer

This is a personal research and educational project built to learn quantitative finance, systematic trading, and production Python engineering. It is **not financial advice**. The system is currently in paper trading mode — no real capital has been deployed. Past backtesting results do not guarantee future performance. Indian equity markets carry significant risk.

---

_Built by Aarav Agarwal · [aaravpagarwal07@gmail.com](mailto:aaravpagarwal07@gmail.com)_
