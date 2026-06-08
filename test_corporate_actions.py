"""
test_corporate_actions.py — End-to-end test of the corporate action safety check.

Tests:
  1. Check all 4 paper trading stocks for upcoming corporate actions.
  2. Verify API failure handling (invalid symbol → no crash).
  3. Print forthcoming actions in the next 30 days for human review.

Run:
    python test_corporate_actions.py
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.corporate_actions import (
    check_all_stocks,
    get_corporate_action_warning,
    _parse_exdate,
    _classify_subject,
    _parse_dividend_per_share,
    DIVIDEND_SKIP_THRESHOLD_PCT,
    _NSE_CACHE,
)
from utils.market_calendar import next_trading_day

STOCKS = ["TMPV.NS", "WHIRLPOOL.NS", "SIEMENS.NS", "BAJAJ-AUTO.NS"]

# ── Test helpers ──────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")


def _pass(msg: str) -> None:
    print(f"  ✓ PASS  {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ FAIL  {msg}")


# ── Test 1: Check all 4 stocks for next-2-trading-day danger window ───────────

def test_danger_window_check() -> None:
    _section("TEST 1 — 2-trading-day danger window check (all 4 stocks)")

    today = date.today()
    print(f"\n  check_date: {today}")
    next_1 = next_trading_day(today)
    next_2 = next_trading_day(next_1)
    print(f"  danger window: {today} → {next_1} → {next_2}")
    print()

    results = check_all_stocks(STOCKS, check_date=today)

    print()
    any_skip = False
    for ticker, r in results.items():
        status = "SKIP" if r["skip"] else "CLEAR"
        reason = r["reason"] or "—"
        print(f"  {ticker:<15}: {status}  {reason}")
        if r["skip"]:
            any_skip = True

    print()
    _pass("check_all_stocks() completed without raising an exception")
    _pass(f"All 4 tickers returned a result dict with 'skip' key")
    if any_skip:
        print(f"  NOTE: at least one stock has a material corporate action coming up!")
    else:
        _pass("No skips triggered — all 4 stocks are clear for the next 2 trading days")


# ── Test 2: API failure handling (invalid symbol) ─────────────────────────────

def test_api_failure_handling() -> None:
    _section("TEST 2 — API failure handling (invalid symbol)")

    print("\n  Passing 'INVALID_SYMBOL_XYZ_123.NS' to get_corporate_action_warning()...")
    try:
        result = get_corporate_action_warning(
            "INVALID_SYMBOL_XYZ_123.NS",
            check_date=date.today(),
        )
        if result["skip"] is False and result["reason"] is None:
            _pass("Invalid symbol returned skip=False (default safe response) — trading not blocked")
        else:
            # Might also return skip=True with a parse error — still valid if it didn't crash
            _pass(f"Invalid symbol handled without exception — result: {result}")
    except Exception as exc:
        _fail(f"Exception raised for invalid symbol: {exc}")

    print()
    _pass("API failure handling test complete")


# ── Test 3: Next-30-day upcoming actions for human review ─────────────────────

def test_next_30_days_actions() -> None:
    _section("TEST 3 — Upcoming corporate actions (next 30 days, for human review)")

    from nse import NSE
    import re

    _NSE_CACHE.mkdir(parents=True, exist_ok=True)
    nse = NSE(_NSE_CACHE)

    today    = date.today()
    end_date = today + timedelta(days=30)

    print(f"\n  Window: {today} → {end_date}")
    print()

    any_found = False

    for idx, ticker in enumerate(STOCKS):
        if idx > 0:
            time.sleep(2)

        symbol = ticker.removesuffix(".NS")
        try:
            actions = nse.actions(segment="equities", symbol=symbol)
        except Exception as exc:
            print(f"  {ticker}: ERROR fetching actions — {exc}")
            continue

        # Get current price for dividend threshold calc
        try:
            price = float(nse.quote(symbol)["tradeInfo"]["lastPrice"])
        except Exception:
            price = None

        ticker_actions = []
        for a in actions:
            ex_str = a.get("exDate", "")
            ex_date = _parse_exdate(ex_str)
            if ex_date and today <= ex_date <= end_date:
                ticker_actions.append((ex_date, a))

        if not ticker_actions:
            print(f"  {ticker:<15}: no corporate actions in next 30 days")
        else:
            any_found = True
            for ex_date, a in sorted(ticker_actions):
                subject     = a.get("subject", "").strip()
                action_type = _classify_subject(subject)
                try:
                    face_val = float(a.get("faceVal", "1") or "1")
                except Exception:
                    face_val = 1.0

                amount_str = ""
                if action_type == "DIVIDEND" and price:
                    amt = _parse_dividend_per_share(subject, face_val)
                    if amt:
                        pct = amt / price * 100
                        skip_flag = " ← WOULD SKIP" if pct >= DIVIDEND_SKIP_THRESHOLD_PCT * 100 else ""
                        amount_str = f"  [Rs {amt:.2f} = {pct:.2f}% of ₹{price:,.0f}{skip_flag}]"

                print(
                    f"  {ticker:<15}: {str(ex_date):<12}  {action_type:<10}  "
                    f"{subject[:45]:<45}{amount_str}"
                )

    if not any_found:
        print("  All 4 stocks: no corporate actions in the next 30 days.")

    print()
    _pass("30-day scan completed without exception")


# ── Unit tests for parsing helpers ─────────────────────────────────────────────

def test_parsing_helpers() -> None:
    _section("TEST 4 — Parsing helper unit tests")
    print()

    cases = [
        ("Dividend - Rs 6 Per Share",               6.0),
        ("Dividend - Re 0.20/- Per Share",          0.20),
        ("Interim Dividend - Rs 27.50/- Per Share", 27.50),
        ("Agm/Dividend - 150%",                     3.0),  # 150% of FV=2
        ("Div Fin-100% + Spl-25%",                  2.0),  # 100% of FV=2
        ("Annual General Meeting",                  None),
    ]

    all_ok = True
    for subject, expected in cases:
        face_val = 2.0   # TMPV face value
        got = _parse_dividend_per_share(subject, face_val)
        ok = (got == expected) or (got is None and expected is None)
        tag = "✓" if ok else "✗"
        if not ok:
            all_ok = False
        print(f"  {tag}  parse({repr(subject)[:52]})  got={got}  expected={expected}")

    classify_cases = [
        ("Face Value Split From Rs.10/- To Rs.2/-",  "SPLIT"),
        ("Bonus 1:1",                                "BONUS"),
        ("Rights 6:109 @ Premium",                   "RIGHTS"),
        ("Demerger",                                 "DEMERGER"),
        ("Buy Back",                                 "BUYBACK"),
        ("Dividend - Rs 10 Per Share",               "DIVIDEND"),
        ("Interim Dividend - Rs 5 Per Share",        "DIVIDEND"),
        ("Annual General Meeting",                   "AGM"),
    ]
    print()
    for subject, expected in classify_cases:
        got = _classify_subject(subject)
        ok = got == expected
        tag = "✓" if ok else "✗"
        if not ok:
            all_ok = False
        print(f"  {tag}  classify({repr(subject)[:52]})  → {got}  (expected {expected})")

    print()
    if all_ok:
        _pass("All parsing helper tests passed")
    else:
        _fail("Some parsing tests failed — see above")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nCORPORATE ACTIONS SAFETY CHECK — TEST SUITE")
    print(f"Run date: {date.today()}")
    print(f"Dividend skip threshold: {DIVIDEND_SKIP_THRESHOLD_PCT * 100:.0f}% of stock price")

    test_parsing_helpers()
    test_danger_window_check()
    test_api_failure_handling()
    test_next_30_days_actions()

    print("\nAll tests complete.\n")
