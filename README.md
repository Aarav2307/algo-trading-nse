# NSE Algo Trading System

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-Lightsail%20Mumbai-FF9900?logo=amazon-aws&logoColor=white)
![Zerodha](https://img.shields.io/badge/Broker-Zerodha%20Kite%20Connect-387ED1)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Paper%20Trading-yellow)

Automated end-of-day swing trading system for NSE India equity markets. Built entirely in Python, deployed on AWS, connected to live Zerodha Kite Connect API. Generates daily signals after market close, places AMO limit orders, and runs a morning fill-check at market open — fully unattended.

Currently in paper trading phase with walk-forward validated backtesting results across 8 years of NSE data.

**Current universe (10 stocks):** TMPV.NS, WHIRLPOOL.NS, SIEMENS.NS, BAJAJ-AUTO.NS, CUMMINSIND.NS, HCLTECH.NS, BOSCHLTD.NS, COLPAL.NS, ANURAS.NS, HEROMOTOCO.NS

**Screener universe:** Dynamic NIFTY 500 (504 stocks) fetched live from NSE.

**Current universe (10 stocks):** TMPV.NS, WHIRLPOOL.NS, SIEMENS.NS, BAJAJ-AUTO.NS, CUMMINSIND.NS, HCLTECH.NS, BOSCHLTD.NS, COLPAL.NS, ANURAS.NS, HEROMOTOCO.NS

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
│  2. Market day check — NSE holiday calendar              │
│  3. Fetch OHLCV — last 120 calendar days per stock       │
│  4. Corporate actions check — ex-dates in next 2 days    │
│  5. Strategy signals — SMA crossover on today's close    │
│  6. Risk manager — check stops on open positions         │
│  7. Cooldown gate — suppress entries post-RM-exit        │
│  8. Position sizer — fixed-fractional 1.5% risk          │
│  9. AMO orders — limit orders logged for tomorrow's open │
│ 10. Signal report — terminal + CSV log                   │
└──────────────────────────────────────────────────────────┘

9:20 AM IST — Morning run (morning_fill_check.py)
┌──────────────────────────────────────────────────────────┐
│  1. Corporate actions — cancel fills if ex-date today    │
│  2. Fetch open prices — today's opening bar per stock    │
│  3. Fill check — did the open price beat our limit?      │
│  4. Portfolio update — record actual fill prices         │
│  5. Morning report — filled / missed / cancelled         │
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
  utils/costs.py              → Transaction cost model (₹20 brokerage + STT + slippage)
  utils/market_calendar.py    → NSE holiday calendar, trading day checks
  utils/corporate_actions.py  → Live NSE ex-date checks (splits, bonuses, dividends)

Validation
  validation/walk_forward.py  → IS vs OOS performance comparison

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

## Backtesting & Validation Results

### Walk-Forward Validation

The most important test: run identical parameters on a completely independent out-of-sample dataset. No parameter re-fitting between windows.

| Window        | Period                  | Purpose                                                       |
| ------------- | ----------------------- | ------------------------------------------------------------- |
| In-sample     | 2018-01-01 → 2022-12-31 | Strategy "training" (only common sense used, no optimisation) |
| Out-of-sample | 2023-01-01 → 2025-12-31 | Genuine unseen data                                           |

**Overall result: 15/20 metrics PASS (75%) — System Validated**

> Threshold: ≥14/20 = Validated · 10–13/20 = Partial · <10/20 = Overfit

| Stock         | IS Return | OOS Return | IS Max DD | OOS Max DD | Score   |
| ------------- | --------- | ---------- | --------- | ---------- | ------- |
| TMPV.NS       | +9.1%     | +8.3%      | −7.8%     | −5.1%      | **4/5** |
| WHIRLPOOL.NS  | +1.8%     | +4.3%      | −4.3%     | −3.0%      | **4/5** |
| SIEMENS.NS    | +7.0%     | +2.0%      | −6.1%     | −7.9%      | **3/5** |
| BAJAJ-AUTO.NS | +1.6%     | +14.4%     | −4.9%     | −3.1%      | **4/5** |

- OOS returns are **positive on all 4 stocks** — no strategy collapse on unseen data
- OOS drawdowns are **equal to or better than** in-sample on 3/4 stocks
- The 1 FAIL on SIEMENS is a partial regime change (stock trended more strongly 2018–2022)

### Stress Tests

| Scenario                                              | Result                                                      |
| ----------------------------------------------------- | ----------------------------------------------------------- |
| COVID crash (Feb–Apr 2020)                            | Max portfolio drawdown: 1.8–3.1% while NIFTY fell 35–40%    |
| AMO-realistic fills (next-day open vs same-day close) | Score unchanged at 15/20; returns differ by < 1pp per stock |

### Transaction Cost Model

All backtests use realistic costs:

```
Brokerage : ₹20 flat per order (Zerodha model)
STT       : 0.025% on sell-side turnover
Slippage  : 0.05% on execution price (both sides)
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
│   ├── regime_classifier.py    # Hurst exponent + ADX stock regime detection
│   ├── sma_screener.py         # Universe scan for crossover candidates
│   └── config.py               # Screener parameters and universe definition
│
├── paper_trading/
│   ├── signal_runner.py        # Daily EOD runner — signals, sizing, AMO orders
│   ├── morning_fill_check.py   # 9:20 AM open-price fill confirmation
│   ├── paper_portfolio.py      # JSON-persisted portfolio state (atomic writes)
│   ├── portfolio_state.json    # Live portfolio state (positions, cash, cooldowns)
│   ├── signal_log.csv          # Append-only daily signal history
│   ├── amo_orders.csv          # AMO order log with fill tracking
│   ├── run_daily.sh            # Cron wrapper — 3:45 PM IST
│   └── run_morning_check.sh    # Cron wrapper — 9:20 AM IST
│
├── utils/
│   ├── costs.py                # Transaction cost model (brokerage, STT, slippage)
│   ├── market_calendar.py      # NSE 2026 holiday list, trading day utilities
│   └── corporate_actions.py    # Live NSE ex-date checks (splits, bonuses, dividends)
│
├── validation/
│   ├── walk_forward.py         # IS vs OOS walk-forward validation across 4 stocks
│   └── walk_forward_results.txt# Full results with trade-by-trade backtester output
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
