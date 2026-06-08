"""
utils/corporate_actions.py — NSE corporate action safety checks.

Why this matters for a swing trading system:
  Stock splits, bonus issues, and large dividends cause the share price to gap
  down by a defined amount on the ex-date. This is NOT a price signal — it is
  a mechanical accounting adjustment. A 20/50 SMA crossover system will see
  this as a bearish breakdown and fire spurious sell signals or prematurely
  trigger the Chandelier stop, even though no real selling occurred.

  Skipping signal generation in the 2-trading-day window around these events
  prevents false exits and false entries that have nothing to do with real
  supply/demand dynamics.

Action types and skip logic:
  SPLIT    — always skip. Price halved (or worse), all MAs, stops reset.
  BONUS    — always skip. Free shares issued, price adjusts proportionally.
  RIGHTS   — always skip. Entitlement-price discount distorts the chart.
  DEMERGER — always skip. Business carved out, residual price unknown.
  DIVIDEND — skip only if dividend > DIVIDEND_SKIP_THRESHOLD_PCT of price.
             Small dividends (< 1%) are noise for a swing system and don't
             materially move the chart. Large dividends DO.
  BUYBACK  — never skip. Buybacks are positive signals and don't cause sudden
             price-distortion gaps the way splits/bonuses do.
  AGM      — never skip. AGMs alone have no price impact.

Usage:
    from utils.corporate_actions import check_all_stocks
    ca = check_all_stocks(["TMPV.NS", "SIEMENS.NS"], current_prices={"TMPV.NS": 390})
    if ca["TMPV.NS"]["skip"]:
        print(ca["TMPV.NS"]["reason"])
"""

import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.market_calendar import next_trading_day

# ── Configuration ──────────────────────────────────────────────────────────────

# Skip dividend signals if dividend/price ratio >= this threshold.
# 1% = a Rs 100 dividend on a Rs 10,000 stock — genuinely material.
# 0.2% = Rs 4 dividend on a Rs 2,000 stock — irrelevant noise, no skip.
DIVIDEND_SKIP_THRESHOLD_PCT = 0.01

# Seconds to pause between NSE API calls to stay under rate limits.
_API_DELAY_SECS = 2

# Local cache directory for the NSE library's internal downloads.
# A fixed directory avoids temp-file accumulation across runs.
_NSE_CACHE = Path(__file__).parent.parent / "auth" / "nse_cache"


# =============================================================================
# Parsing helpers
# =============================================================================

def _parse_exdate(exdate_str: str) -> Optional[date]:
    """
    Parse the NSE API's exDate string into a Python date.
    Observed format: "29-May-2026" (day-MonthAbbrev-year).
    """
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(exdate_str.strip(), fmt).date()
        except ValueError:
            pass
    return None   # unrecognised format — caller skips this record


def _classify_subject(subject: str) -> str:
    """
    Derive a normalised action type from the free-text `subject` field.
    The NSE API has no structured `purpose` column — only subject text.
    """
    s = subject.lower()

    # Order matters: check most specific patterns first.
    if "face value split" in s or ("split" in s and "bonus" not in s):
        return "SPLIT"
    if "bonus" in s:
        return "BONUS"
    if "rights" in s:
        return "RIGHTS"
    if "demerger" in s or ("merger" in s and "demerger" not in s):
        return "DEMERGER"
    if "buyback" in s or "buy back" in s:
        return "BUYBACK"
    # Dividend catches "Interim Dividend", "Final Dividend", "Special Dividend" etc.
    if "dividend" in s or re.search(r'\bdiv\b', s):
        return "DIVIDEND"
    if "agm" in s or "annual general meeting" in s:
        return "AGM"
    return "OTHER"


def _parse_dividend_per_share(subject: str, face_val: float) -> Optional[float]:
    """
    Extract the rupee amount per share from a dividend subject string.

    Handles:
      "Dividend - Rs 6 Per Share"          → 6.0
      "Dividend - Re 0.20/- Per Share"     → 0.20
      "Interim Dividend - Rs 27.50/- ..."  → 27.50
      "Agm/Dividend - 150%"               → 150% × face_val → e.g. 3.0 for FV=2
      "Div Fin-100% + Spl-25%"            → first match 100% × face_val

    Returns None if no amount can be parsed.
    """
    # Pattern 1: explicit Rs / Re rupee amount (most common in modern records)
    match = re.search(r'\bR[se]s?\.?\s*(\d+(?:\.\d+)?)', subject, re.IGNORECASE)
    if match:
        return float(match.group(1))

    # Pattern 2: percentage of face value (older records e.g. "150%")
    match = re.search(r'(\d+(?:\.\d+)?)\s*%', subject)
    if match:
        pct = float(match.group(1))
        return (pct / 100.0) * face_val

    return None


# =============================================================================
# Skip decision
# =============================================================================

def _skip_decision(
    action_type: str,
    subject: str,
    face_val: float,
    current_price: Optional[float],
) -> Tuple[bool, str]:
    """
    Return (should_skip: bool, human_readable_reason: str).
    The reason string is used both for logging and for the signal report.
    """
    # ── Always skip ────────────────────────────────────────────────────────────
    if action_type == "SPLIT":
        return True, "STOCK_SPLIT (price gap — all MAs and stops reset)"

    if action_type == "BONUS":
        return True, "BONUS_ISSUE (free shares issued, price adjusts)"

    if action_type == "RIGHTS":
        return True, "RIGHTS_ISSUE (entitlement-price discount distorts chart)"

    if action_type == "DEMERGER":
        return True, "DEMERGER (residual price structure unknown)"

    # ── Never skip ────────────────────────────────────────────────────────────
    if action_type in ("BUYBACK", "AGM", "OTHER"):
        return False, f"{action_type} — no price distortion expected"

    # ── Dividend: check against threshold ─────────────────────────────────────
    if action_type == "DIVIDEND":
        amount = _parse_dividend_per_share(subject, face_val)

        if amount is None:
            # Cannot parse amount — be conservative and skip
            return True, "DIVIDEND (amount unparseable — skipping conservatively)"

        if current_price is None or current_price <= 0:
            # No price data — be conservative and skip
            return True, f"DIVIDEND Rs {amount:.2f} (current price unknown — skipping conservatively)"

        ratio = amount / current_price
        if ratio >= DIVIDEND_SKIP_THRESHOLD_PCT:
            return True, (
                f"DIVIDEND Rs {amount:.2f} "
                f"({ratio * 100:.2f}% of ₹{current_price:,.0f} "
                f"≥ {DIVIDEND_SKIP_THRESHOLD_PCT * 100:.0f}% threshold)"
            )
        else:
            return False, (
                f"DIVIDEND Rs {amount:.2f} "
                f"({ratio * 100:.3f}% of ₹{current_price:,.0f} "
                f"< {DIVIDEND_SKIP_THRESHOLD_PCT * 100:.0f}% threshold — immaterial)"
            )

    return False, "unknown action type"


# =============================================================================
# Public API
# =============================================================================

def get_corporate_action_warning(
    ticker: str,
    check_date: Optional[date] = None,
    current_price: Optional[float] = None,
    nse_instance=None,
) -> dict:
    """
    Check if a stock has a material corporate action ex-date within 2 trading
    days of check_date. Returns immediately on the first skip-worthy action found.

    Args:
        ticker:        NSE ticker, e.g. "TMPV.NS". The .NS suffix is stripped.
        check_date:    Date to check from (default: today).
        current_price: Current stock price in ₹ (used for dividend threshold).
                       If None, current price is fetched from NSE quote API.
        nse_instance:  Pass a shared NSE object to avoid re-instantiation when
                       calling for multiple tickers in a loop.

    Returns:
        {
            "skip":        bool,           # True → do not generate signals
            "reason":      str | None,     # human-readable explanation
            "action_type": str | None,     # "SPLIT", "DIVIDEND", etc.
            "ex_date":     date | None,    # the action's ex-date
        }

    Never raises: if the NSE API is unavailable, logs a warning and returns
    skip=False so trading is not blocked by an infrastructure failure.
    """
    from nse import NSE

    symbol     = ticker.upper().removesuffix(".NS").removesuffix(".BSE")
    check_date = check_date or date.today()

    no_action = {
        "skip": False, "reason": None,
        "action_type": None, "ex_date": None,
    }

    # ── Danger window: check_date + next 2 trading days (3 dates total) ───────
    # We skip on the ex-date itself AND the day before it (signal day), because:
    #   Day before ex-date: last chance to trade ex-rights/ex-dividend, often
    #                       sees unusual volume/price distortion.
    #   Ex-date itself:     price opens gap-adjusted. MAs and stops are stale.
    #   Day after ex-date:  price has adjusted. Normal signal generation resumes.
    next_1 = next_trading_day(check_date)
    next_2 = next_trading_day(next_1)
    danger_window: set = {check_date, next_1, next_2}

    # ── Fetch corporate actions ────────────────────────────────────────────────
    own_nse = nse_instance is None
    try:
        _NSE_CACHE.mkdir(parents=True, exist_ok=True)
        if own_nse:
            nse = NSE(_NSE_CACHE)
        else:
            nse = nse_instance

        actions = nse.actions(segment="equities", symbol=symbol)

    except Exception as exc:
        print(
            f"  [CORP_ACTION] WARNING: NSE API unavailable for {ticker} — {exc}. "
            f"Proceeding without corporate action check."
        )
        return no_action

    # ── Fetch current price if not supplied ───────────────────────────────────
    # Needed for the dividend threshold comparison. An extra API call per stock,
    # but only triggered when current_price is not passed by the caller.
    if current_price is None:
        try:
            quote        = nse.quote(symbol)
            current_price = float(quote["tradeInfo"]["lastPrice"])
        except Exception:
            current_price = None   # non-fatal; _skip_decision handles None

    # ── Scan actions for danger-window ex-dates ───────────────────────────────
    for action in actions:
        ex_date_str = action.get("exDate", "")
        if not ex_date_str or ex_date_str.strip() == "-":
            continue

        ex_date = _parse_exdate(ex_date_str)
        if ex_date is None or ex_date not in danger_window:
            continue

        subject     = action.get("subject", "")
        action_type = _classify_subject(subject)

        # Parse face value for percentage-based dividend calculations
        try:
            face_val = float(action.get("faceVal", "1") or "1")
        except (ValueError, TypeError):
            face_val = 1.0

        should_skip, reason = _skip_decision(action_type, subject, face_val, current_price)

        if should_skip:
            full_reason = f"{reason} | ex-date {ex_date}"
            print(f"  [CORP_ACTION] {ticker}: SKIP — {full_reason}")
            return {
                "skip":        True,
                "reason":      full_reason,
                "action_type": action_type,
                "ex_date":     ex_date,
            }
        else:
            print(
                f"  [CORP_ACTION] {ticker}: {action_type} on {ex_date} — "
                f"below threshold, no skip ({reason})"
            )

    # No material action found in danger window
    print(
        f"  [CORP_ACTION] {ticker}: clear — "
        f"no material actions between {min(danger_window)} and {max(danger_window)}"
    )
    return no_action


def check_all_stocks(
    tickers: List[str],
    check_date: Optional[date] = None,
    current_prices: Optional[Dict[str, float]] = None,
) -> Dict[str, dict]:
    """
    Check every ticker in one call, sharing a single NSE session.

    Args:
        tickers:        List of NSE tickers with .NS suffix.
        check_date:     Date to check from (default: today).
        current_prices: {ticker: close_price} from today's OHLCV fetch.
                        Passed to get_corporate_action_warning() so no
                        extra quote API call is needed per stock.

    Returns:
        {ticker: warning_dict} for every ticker.

    Example:
        {
            "TMPV.NS":      {"skip": False, "reason": None, ...},
            "SIEMENS.NS":   {"skip": True,  "reason": "STOCK_SPLIT ...", ...},
        }
    """
    from nse import NSE

    _NSE_CACHE.mkdir(parents=True, exist_ok=True)
    nse = NSE(_NSE_CACHE)

    results: Dict[str, dict] = {}
    for idx, ticker in enumerate(tickers):
        if idx > 0:
            # Polite pause between calls — NSE's unofficial API rate-limits aggressively
            time.sleep(_API_DELAY_SECS)

        price = (current_prices or {}).get(ticker)
        results[ticker] = get_corporate_action_warning(
            ticker,
            check_date=check_date,
            current_price=price,
            nse_instance=nse,
        )

    return results
