"""
screener/sma_screener.py — SMA Crossover Suitability Screener

Ranks NSE stocks by how well-suited they are for an SMA crossover strategy.
Computes 5 metrics over the last 3 years and blends them into a composite
0-100 suitability score using percentile ranking within the universe.

Metrics:
    volatility      — annualised stddev of daily returns × √252
                      (higher = bigger price swings = more exploitable trends)
    beta            — covariance with Nifty 50 / variance of Nifty 50
                      (higher = amplified market moves = deeper trends)
    max_drawdown    — worst peak-to-trough decline in the window
                      (deeper = bigger cycles = more trend runway)
    trend_duration  — average consecutive days above/below the 50-day SMA
                      (longer = price trends persist = crossover signals last)
    mr_score        — lag-1 autocorrelation of (close − SMA50) / SMA50
                      (higher → deviations persist = trending behaviour;
                       negative → deviations reverse quickly = mean-reverting)

Composite score weights (must sum to 1.0):
    trend_duration  25 %   ← most direct proxy for SMA crossover profitability
    volatility      20 %
    beta            20 %
    max_drawdown    20 %
    mr_score        15 %

API surface — all pure functions, no side effects, designed for a future
FastAPI / Flask endpoint:
    compute_metrics(df, nifty_df)   → dict of raw metric floats
    compute_scores(results_df)      → DataFrame + normalised scores + composite
    screen_universe(universe, ...)  → fully ranked DataFrame (no printing)
    print_results(df)               → formatted stdout output (separate from data)
"""

import io
import os
import sys
import warnings
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

# Allow running from the screener/ subdirectory OR the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.fetcher import get_ohlcv, flatten_yf_columns


# ── Stock universe ─────────────────────────────────────────────────────────────
# ~70 major Nifty 500 stocks covering all primary sectors.
# Tickers use yfinance .NS format (NSE). Known rename: TATAMOTORS.NS → TMPV.NS
# as of the 2026 Tata Motors demerger; TMPV.NS carries the full price history.

# ── Dynamic NIFTY 500 universe loader ────────────────────────────────────────
_INDUSTRY_TO_SECTOR: dict[str, str] = {
    "Banking":                    "Banking",
    "Financial Services":         "Finance",
    "Insurance":                  "Insurance",
    "Information Technology":     "IT",
    "IT":                         "IT",
    "Automobile":                 "Auto",
    "Auto Components":            "Auto Components",
    "Pharmaceuticals":            "Pharma",
    "Healthcare":                 "Pharma",
    "FMCG":                       "FMCG",
    "Consumer Staples":           "FMCG",
    "Metals & Mining":            "Metals",
    "Oil Gas & Consumable Fuels": "Energy",
    "Power":                      "Energy",
    "Capital Goods":              "Capital Goods",
    "Construction":               "Infra",
    "Realty":                     "Infra",
    "Infrastructure":             "Infra",
    "Cement & Cement Products":   "Cement",
    "Telecommunication":          "Telecom",
    "Consumer Durables":          "Consumer",
    "Chemicals":                  "Chemicals",
    "Diversified":                "Conglomerate",
    "Textiles":                   "Textiles",
    "Media Entertainment & Pub":  "Media",
    "Forest Materials":           "Materials",
    "Agri, Allied & Agro Proc":   "Agriculture",
    "Services":                   "Services",
    "Retailing":                  "Retail",
    "Transport Infrastructure":   "Infra",
    "Beverages":                  "FMCG",
}

def _load_nifty500_universe() -> dict[str, str]:
    """
    Fetch the live NIFTY 500 constituent list from NSE and return a
    {ticker.NS: sector} dict. Falls back to the hardcoded 73-stock list
    if the NSE request fails.
    """
    import requests, io
    try:
        url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        import pandas as pd
        df = pd.read_csv(io.StringIO(r.text))
        universe = {}
        for _, row in df.iterrows():
            ticker = str(row["Symbol"]).strip() + ".NS"
            industry = str(row["Industry"]).strip()
            sector = _INDUSTRY_TO_SECTOR.get(industry, industry)
            universe[ticker] = sector
        if len(universe) > 100:
            return universe
    except Exception as e:
        print(f"[screener] NIFTY 500 fetch failed ({e}), falling back to hardcoded universe.")
    # ── Fallback: original 73-stock hardcoded universe ────────────────────────
    return {
        "HDFCBANK.NS": "Banking", "ICICIBANK.NS": "Banking", "KOTAKBANK.NS": "Banking",
        "SBIN.NS": "Banking", "AXISBANK.NS": "Banking", "INDUSINDBK.NS": "Banking",
        "BANDHANBNK.NS": "Banking", "PNB.NS": "Banking", "FEDERALBNK.NS": "Banking",
        "CANBK.NS": "Banking", "BAJFINANCE.NS": "Finance", "BAJAJFINSV.NS": "Finance",
        "HDFCLIFE.NS": "Insurance", "SBILIFE.NS": "Insurance", "ICICIGI.NS": "Insurance",
        "TCS.NS": "IT", "INFY.NS": "IT", "WIPRO.NS": "IT", "HCLTECH.NS": "IT",
        "TECHM.NS": "IT", "LTIM.NS": "IT", "PERSISTENT.NS": "IT", "MPHASIS.NS": "IT",
        "MARUTI.NS": "Auto", "TMPV.NS": "Auto", "BAJAJ-AUTO.NS": "Auto",
        "HEROMOTOCO.NS": "Auto", "EICHERMOT.NS": "Auto", "ASHOKLEY.NS": "Auto",
        "BALKRISIND.NS": "Auto", "SUNPHARMA.NS": "Pharma", "DRREDDY.NS": "Pharma",
        "CIPLA.NS": "Pharma", "DIVISLAB.NS": "Pharma", "AUROPHARMA.NS": "Pharma",
        "LUPIN.NS": "Pharma", "HINDUNILVR.NS": "FMCG", "ITC.NS": "FMCG",
        "NESTLEIND.NS": "FMCG", "BRITANNIA.NS": "FMCG", "DABUR.NS": "FMCG",
        "MARICO.NS": "FMCG", "GODREJCP.NS": "FMCG", "TATASTEEL.NS": "Metals",
        "HINDALCO.NS": "Metals", "JSWSTEEL.NS": "Metals", "VEDL.NS": "Metals",
        "COALINDIA.NS": "Metals", "NMDC.NS": "Metals", "SAIL.NS": "Metals",
        "RELIANCE.NS": "Energy", "ONGC.NS": "Energy", "NTPC.NS": "Energy",
        "POWERGRID.NS": "Energy", "BPCL.NS": "Energy", "IOC.NS": "Energy",
        "GAIL.NS": "Energy", "LT.NS": "Infra", "ADANIPORTS.NS": "Infra",
        "DLF.NS": "Infra", "ULTRACEMCO.NS": "Cement", "GRASIM.NS": "Cement",
        "ABB.NS": "Capital Goods", "SIEMENS.NS": "Capital Goods", "BHEL.NS": "Capital Goods",
        "BHARTIARTL.NS": "Telecom", "TITAN.NS": "Consumer", "HAVELLS.NS": "Consumer",
        "VOLTAS.NS": "Consumer", "WHIRLPOOL.NS": "Consumer", "TATACHEM.NS": "Chemicals",
        "PIDILITIND.NS": "Chemicals", "ADANIENT.NS": "Conglomerate",
        "CUMMINSIND.NS": "Capital Goods", "HCLTECH.NS": "IT",
    }

UNIVERSE: dict[str, str] = _load_nifty500_universe()

BENCHMARK_TICKER = "^NSEI"

SCORE_WEIGHTS = {
    "trend_duration": 0.25,
    "volatility":     0.20,
    "beta":           0.20,
    "max_drawdown":   0.20,
    "mr_score":       0.15,
}
assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"


# ── Metric computation (pure, no I/O) ─────────────────────────────────────────

def compute_metrics(df: pd.DataFrame, nifty_df: pd.DataFrame) -> Optional[dict]:
    """
    Compute the 5 suitability metrics for one stock against the benchmark.

    Args:
        df:        OHLCV DataFrame for the stock (from data.fetcher)
        nifty_df:  OHLCV DataFrame for the benchmark index

    Returns:
        Dict with float values for volatility, beta, max_drawdown,
        trend_duration, mr_score. Returns None if data is insufficient.
    """
    if len(df) < 60:
        return None

    returns = df["close"].pct_change().dropna()

    # 1. Annualised volatility
    volatility = float(returns.std() * (252 ** 0.5))

    # 2. Beta vs benchmark — OLS beta = cov(stock, market) / var(market)
    nifty_returns = nifty_df["close"].pct_change().dropna()
    aligned = pd.concat([returns, nifty_returns], axis=1, join="inner")
    aligned.columns = ["stock", "nifty"]
    nifty_var = float(aligned["nifty"].var())
    beta = float(aligned["stock"].cov(aligned["nifty"]) / nifty_var) if nifty_var > 1e-12 else 1.0

    # 3. Maximum drawdown (returns a negative number; deeper = more negative)
    prices = df["close"]
    rolling_peak = prices.cummax()
    max_drawdown = float(((prices - rolling_peak) / rolling_peak).min())

    # 4. Average trend duration — mean length of consecutive runs above/below SMA50
    #    A stock that rides trends will have long runs (e.g., 30–90 days).
    #    A mean-reverting stock will have short runs (e.g., 5–12 days).
    sma50 = df["close"].rolling(50).mean()
    valid = sma50.notna()
    close_v = df["close"][valid].values
    sma50_v = sma50[valid].values

    if len(close_v) < 20:
        return None

    above = (close_v > sma50_v).astype(int)
    run_lengths: list[int] = []
    run_len = 1
    for i in range(1, len(above)):
        if above[i] == above[i - 1]:
            run_len += 1
        else:
            run_lengths.append(run_len)
            run_len = 1
    run_lengths.append(run_len)
    trend_duration = float(np.mean(run_lengths))

    # 5. Mean reversion score — lag-1 autocorrelation of normalised SMA deviation.
    #    Positive autocorr → today's deviation predicts tomorrow's (trending).
    #    Near-zero or negative → deviations don't persist (mean-reverting).
    deviation = pd.Series((close_v - sma50_v) / sma50_v)
    mr_score = float(deviation.autocorr(lag=1))
    if np.isnan(mr_score):
        mr_score = 0.0

    return {
        "volatility":     volatility,
        "beta":           beta,
        "max_drawdown":   max_drawdown,
        "trend_duration": trend_duration,
        "mr_score":       mr_score,
    }


# ── Scoring (pure, no I/O) ────────────────────────────────────────────────────

def compute_scores(results: pd.DataFrame) -> pd.DataFrame:
    """
    Add normalised component scores (0-100) and a weighted composite score
    to a DataFrame of raw metrics.

    Normalisation uses percentile rank within the live universe to avoid
    sensitivity to outliers. Each component score independently ranges 0-100.

    Direction convention (100 = best for SMA crossover):
        volatility      → higher is better   (rank ascending)
        beta            → higher is better   (rank ascending)
        max_drawdown    → more negative is better → negate, then rank ascending
        trend_duration  → higher is better   (rank ascending)
        mr_score        → higher is better   (rank ascending)

    Args:
        results: DataFrame indexed by ticker with columns matching SCORE_WEIGHTS keys.

    Returns:
        Augmented DataFrame with component score columns, composite_score,
        and a human-readable verdict string.
    """
    df = results.copy()
    n = len(df)

    df["vol_score"]  = df["volatility"].rank(ascending=True)         / n * 100
    df["beta_score"] = df["beta"].rank(ascending=True)               / n * 100
    df["dd_score"]   = (-df["max_drawdown"]).rank(ascending=True)    / n * 100
    df["td_score"]   = df["trend_duration"].rank(ascending=True)     / n * 100
    df["mrs_score"]  = df["mr_score"].rank(ascending=True)           / n * 100

    df["composite_score"] = (
        SCORE_WEIGHTS["volatility"]     * df["vol_score"]  +
        SCORE_WEIGHTS["beta"]           * df["beta_score"] +
        SCORE_WEIGHTS["max_drawdown"]   * df["dd_score"]   +
        SCORE_WEIGHTS["trend_duration"] * df["td_score"]   +
        SCORE_WEIGHTS["mr_score"]       * df["mrs_score"]
    )

    df["verdict"] = df["composite_score"].apply(
        lambda s: "Strong fit" if s >= 65 else ("Moderate fit" if s >= 35 else "Poor fit")
    )

    return df


# ── Data acquisition ──────────────────────────────────────────────────────────

def _fetch_quiet(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV via data.fetcher, suppressing its stdout progress print and
    yfinance's stderr warnings. Returns None on any error.
    """
    buf = io.StringIO()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with redirect_stdout(buf), redirect_stderr(buf):
                return get_ohlcv(ticker, start, end)
    except Exception:
        return None


def _fetch_benchmark(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """
    Fetch the benchmark index. Falls back to a direct yf.download call if the
    fetcher rejects the ticker (some index tickers don't have a Volume column).
    """
    df = _fetch_quiet(ticker, start, end)
    if df is not None:
        return df

    # Direct fallback for index tickers like ^NSEI
    try:
        buf = io.StringIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with redirect_stdout(buf), redirect_stderr(buf):
                raw = yf.download(ticker, start=start, end=end,
                                  progress=False, auto_adjust=True)
        if raw.empty:
            return None
        raw = flatten_yf_columns(raw)
        out = raw[["Close"]].rename(columns={"Close": "close"}).copy()
        out.index = pd.DatetimeIndex(out.index).tz_localize(None)
        out.index.name = "date"
        return out.dropna()
    except Exception:
        return None


# ── Universe screener (pure data pipeline, no printing) ───────────────────────

def screen_universe(
    universe: dict[str, str],
    start: str,
    end: str,
    benchmark_ticker: str = BENCHMARK_TICKER,
) -> pd.DataFrame:
    """
    Download price data for every stock in the universe, compute metrics,
    score within the peer group, and return a ranked DataFrame.

    This function is the intended API endpoint — it has no print() calls.
    Progress updates are written to sys.stdout as a side channel so callers
    can redirect stdout to suppress them if needed.

    Args:
        universe:          {ticker: sector} dict
        start:             ISO date string, inclusive
        end:               ISO date string, exclusive
        benchmark_ticker:  yfinance ticker for the market benchmark

    Returns:
        DataFrame indexed by ticker, sorted by composite_score descending,
        with columns: sector, volatility, beta, max_drawdown, trend_duration,
        mr_score, vol_score, beta_score, dd_score, td_score, mrs_score,
        composite_score, verdict, rank.
    """
    # ── Step 1: benchmark ──────────────────────────────────────────────────
    print(f"  [{benchmark_ticker}] Fetching benchmark...", end="  ", flush=True)
    nifty_df = _fetch_benchmark(benchmark_ticker, start, end)
    if nifty_df is None:
        raise RuntimeError(f"Could not download benchmark data for {benchmark_ticker}")
    print(f"✓  ({len(nifty_df)} days)")

    # ── Step 2: stocks ─────────────────────────────────────────────────────
    total = len(universe)
    records: list[dict] = []
    skipped: list[str] = []

    for idx, (ticker, sector) in enumerate(universe.items(), 1):
        progress_label = f"[{idx:>2}/{total}] {ticker:<18} ({sector:<14})"
        print(f"  {progress_label}", end="  ", flush=True)

        df = _fetch_quiet(ticker, start, end)
        if df is None:
            print("✗  download failed — skipped")
            skipped.append(ticker)
            continue

        metrics = compute_metrics(df, nifty_df)
        if metrics is None:
            print("✗  insufficient data — skipped")
            skipped.append(ticker)
            continue

        print(f"✓  ({len(df)} days | vol {metrics['volatility']*100:.1f}% | β{metrics['beta']:.2f})")
        records.append({"ticker": ticker, "sector": sector, **metrics})

    if not records:
        raise RuntimeError("No stocks produced valid metrics. Check ticker symbols and date range.")

    if skipped:
        print(f"\n  Skipped {len(skipped)}: {', '.join(skipped)}")

    # ── Step 3: score and rank ─────────────────────────────────────────────
    results_df = pd.DataFrame(records).set_index("ticker")
    scored_df  = compute_scores(results_df)
    scored_df  = scored_df.sort_values("composite_score", ascending=False)
    scored_df.insert(0, "rank", range(1, len(scored_df) + 1))
    return scored_df


# ── Output formatting ─────────────────────────────────────────────────────────

_VERDICT_MARKER = {"Strong fit": "▲", "Moderate fit": "●", "Poor fit": "▼"}
_TABLE_WIDTH = 104


def _rule(char="─"):
    return char * _TABLE_WIDTH


def print_results(df: pd.DataFrame) -> None:
    """
    Print the full ranked table, top/bottom 10 lists, and sector summary.
    Completely separated from data logic — safe to replace with a JSON
    serialiser for an API response.
    """
    n = len(df)

    # ── Ranked table ───────────────────────────────────────────────────────
    print("\n" + "=" * _TABLE_WIDTH)
    print("  SMA CROSSOVER SUITABILITY SCREENER — NSE / NIFTY 500 UNIVERSE")
    print("=" * _TABLE_WIDTH)
    header = (
        f"  {'#':>3}  {'Ticker':<18}  {'Sector':<14}  "
        f"{'Vol %':>6}  {'Beta':>5}  {'MaxDD %':>7}  "
        f"{'TrendDur':>9}  {'MR Score':>9}  {'Score':>6}  Verdict"
    )
    print(header)
    print(_rule())

    for _, row in df.iterrows():
        marker = _VERDICT_MARKER.get(row["verdict"], " ")
        print(
            f"  {int(row['rank']):>3}  {row.name:<18}  {row['sector']:<14}  "
            f"{row['volatility']*100:>5.1f}%  {row['beta']:>5.2f}  "
            f"{row['max_drawdown']*100:>6.1f}%  "
            f"{row['trend_duration']:>8.1f}d  {row['mr_score']:>9.3f}  "
            f"{row['composite_score']:>6.1f}  {marker} {row['verdict']}"
        )

    print(_rule())

    verdicts = df["verdict"].value_counts()
    print(
        f"\n  Universe: {n} stocks  |  "
        f"Strong fit: {verdicts.get('Strong fit', 0)}  "
        f"Moderate fit: {verdicts.get('Moderate fit', 0)}  "
        f"Poor fit: {verdicts.get('Poor fit', 0)}"
    )

    # ── Top 10 ─────────────────────────────────────────────────────────────
    print("\n" + _rule("─"))
    print("  ▲  TOP 10 — Best candidates for SMA crossover strategy")
    print(_rule("─"))
    for _, row in df.nlargest(10, "composite_score").iterrows():
        print(
            f"  {int(row['rank']):>2}. {row.name:<18} [{row['sector']:<14}]  "
            f"Score {row['composite_score']:>5.1f}  |  "
            f"Vol {row['volatility']*100:>4.1f}%  "
            f"β {row['beta']:>4.2f}  "
            f"DD {row['max_drawdown']*100:>5.1f}%  "
            f"Trend {row['trend_duration']:>5.1f}d  "
            f"MR {row['mr_score']:>5.3f}"
        )

    # ── Bottom 10 ──────────────────────────────────────────────────────────
    print("\n" + _rule("─"))
    print("  ▼  BOTTOM 10 — Worst for SMA crossover (try mean reversion instead)")
    print(_rule("─"))
    for _, row in df.nsmallest(10, "composite_score").iterrows():
        print(
            f"  {int(row['rank']):>2}. {row.name:<18} [{row['sector']:<14}]  "
            f"Score {row['composite_score']:>5.1f}  |  "
            f"Vol {row['volatility']*100:>4.1f}%  "
            f"β {row['beta']:>4.2f}  "
            f"DD {row['max_drawdown']*100:>5.1f}%  "
            f"Trend {row['trend_duration']:>5.1f}d  "
            f"MR {row['mr_score']:>5.3f}"
        )

    # ── Sector averages ────────────────────────────────────────────────────
    print("\n" + _rule("─"))
    print("  ◆  SECTOR AVERAGES (sorted by composite SMA suitability score)")
    print(_rule("─"))
    sector_stats = (
        df.groupby("sector")
        .agg(
            n=("composite_score", "count"),
            score=("composite_score", "mean"),
            vol=("volatility", "mean"),
            beta=("beta", "mean"),
            dd=("max_drawdown", "mean"),
            trend=("trend_duration", "mean"),
            mr=("mr_score", "mean"),
        )
        .sort_values("score", ascending=False)
    )

    sec_header = (
        f"  {'Sector':<16}  {'N':>3}  {'Score':>6}  "
        f"{'Avg Vol':>7}  {'Avg β':>6}  {'Avg DD':>7}  "
        f"{'Trend':>7}  {'MR':>7}"
    )
    print(sec_header)
    print("  " + "─" * (_TABLE_WIDTH - 2))
    for sector, row in sector_stats.iterrows():
        print(
            f"  {sector:<16}  {int(row['n']):>3}  {row['score']:>6.1f}  "
            f"{row['vol']*100:>6.1f}%  {row['beta']:>6.2f}  "
            f"{row['dd']*100:>6.1f}%  "
            f"{row['trend']:>6.1f}d  {row['mr']:>7.3f}"
        )

    print("\n" + "=" * _TABLE_WIDTH)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Use the shared fixed window from config.py so this screener and
    # regime_classifier.py measure over an identical date range.
    # Previously this used datetime.today() - 3 years (a rolling window),
    # which made the two screeners' outputs impossible to compare directly.
    try:
        from screener.config import START_DATE, END_DATE, BENCHMARK_TICKER as _BM
    except ImportError:
        from config import START_DATE, END_DATE, BENCHMARK_TICKER as _BM

    print(f"\nSMA Screener  |  {len(UNIVERSE)} stocks  |  {START_DATE} → {END_DATE}")
    print(f"Benchmark: {_BM}  |  Window: fixed (see screener/config.py)")
    print(_rule())
    print("Downloading & computing metrics...\n")

    results = screen_universe(UNIVERSE, START_DATE, END_DATE, benchmark_ticker=_BM)
    print_results(results)
