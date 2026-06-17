"""
screener/emailer.py — HTML email report formatter and SendGrid sender.

Called by auto_screener.py after the screening pipeline completes.
Generates a clean HTML email with color-coded ADD/WATCH/REMOVE sections.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# ── SendGrid config (from .env) ───────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

SENDGRID_API_KEY   = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM      = os.getenv("SENDGRID_FROM_EMAIL", "aaravpagarwal07@gmail.com")
SENDGRID_TO        = os.getenv("SENDGRID_TO_EMAIL", "aaravpagarwal07@gmail.com")

# ── HTML template ─────────────────────────────────────────────────────────────

def build_html(results: dict) -> str:
    meta     = results["meta"]
    adds     = results["adds"]
    monitors = results.get("monitors", [])
    watches  = results["watches"]
    removes  = results["removes"]

    run_date   = meta["run_date"]
    screened   = meta["screened"]
    passed     = meta["passed_filters"]
    universe   = meta["current_universe"]
    coverage   = meta["data_coverage"]

    # ── Helper: stock row ─────────────────────────────────────────────────────
    def add_row(s: dict, rank: int) -> str:
        gap_short   = s.get("gap_short")
        gap_long    = s.get("gap_long")
        divergent   = s.get("divergent", False)
        cross_col   = "#27ae60" if s.get("cross") == "GOLDEN" else "#e74c3c"
        cross_lbl   = "🟢 GOLDEN (80d)" if s.get("cross") == "GOLDEN" else "🔴 DEATH (80d)"
        short_str   = f"{gap_short:+.2f}%" if gap_short is not None else "N/A"
        long_str    = f"{gap_long:+.2f}%"  if gap_long  is not None else "N/A"
        row_bg      = "background: #fef9e7;" if divergent else ""
        status_cell = (
            '<td style="padding: 10px; color: #e67e22; font-weight: bold;">⚠️ DIVERGENT — verify</td>'
            if divergent else
            f'<td style="padding: 10px; font-size: 12px; color: {cross_col};">{cross_lbl}</td>'
        )
        return f"""
        <tr style="border-bottom: 1px solid #ecf0f1; {row_bg}">
            <td style="padding: 10px; font-weight: bold; color: #2c3e50;">#{rank} {s['ticker']}</td>
            <td style="padding: 10px; color: #7f8c8d;">{s['industry']}</td>
            <td style="padding: 10px; text-align: center;">{s['hurst']}</td>
            <td style="padding: 10px; text-align: center;">{s['adx']}</td>
            <td style="padding: 10px; text-align: center; color: {cross_col}; font-weight: bold;">{short_str}</td>
            <td style="padding: 10px; text-align: center; color: #7f8c8d;">{long_str}</td>
            <td style="padding: 10px; text-align: center;">{s['vol']}%</td>
            <td style="padding: 10px; text-align: center;">{s['avg_corr']}</td>
            {status_cell}
        </tr>"""

    def watch_row(s: dict, rank: int) -> str:
        gap_str = f"{s['gap']}%"
        return f"""
        <tr style="border-bottom: 1px solid #ecf0f1;">
            <td style="padding: 10px; font-weight: bold; color: #2c3e50;">#{rank} {s['ticker']}</td>
            <td style="padding: 10px; color: #7f8c8d;">{s['industry']}</td>
            <td style="padding: 10px; text-align: center;">{s['hurst']}</td>
            <td style="padding: 10px; text-align: center;">{s['adx']}</td>
            <td style="padding: 10px; text-align: center; color: #e67e22; font-weight: bold;">{gap_str}</td>
            <td style="padding: 10px; text-align: center;">{s['vol']}%</td>
            <td style="padding: 10px; text-align: center;">{s['avg_corr']}</td>
            <td style="padding: 10px; font-size: 12px; color: #e67e22;">⚠️ WATCH</td>
        </tr>"""

    def monitor_row(s: dict, rank: int) -> str:
        gap_short  = s.get("gap_short")
        gap_long   = s.get("gap_long")
        divergent  = s.get("divergent", False)
        short_str  = f"{gap_short:+.2f}%" if gap_short is not None else "N/A"
        long_str   = f"{gap_long:+.2f}%"  if gap_long  is not None else "N/A"
        row_bg     = "background: #fef9e7;" if divergent else ""
        status_cell = (
            '<td style="padding: 10px; color: #e67e22; font-weight: bold;">⚠️ DIVERGENT — verify</td>'
            if divergent else
            '<td style="padding: 10px; font-size: 12px; color: #3498db;">👁 IN CROSS (80d)</td>'
        )
        return f"""
        <tr style="border-bottom: 1px solid #ecf0f1; {row_bg}">
            <td style="padding: 10px; font-weight: bold; color: #2c3e50;">#{rank} {s['ticker']}</td>
            <td style="padding: 10px; color: #7f8c8d;">{s['industry']}</td>
            <td style="padding: 10px; text-align: center;">{s['hurst']}</td>
            <td style="padding: 10px; text-align: center;">{s['adx']}</td>
            <td style="padding: 10px; text-align: center; color: #27ae60; font-weight: bold;">{short_str}</td>
            <td style="padding: 10px; text-align: center; color: #7f8c8d;">{long_str}</td>
            <td style="padding: 10px; text-align: center;">{s['vol']}%</td>
            <td style="padding: 10px; text-align: center;">{s['avg_corr']}</td>
            {status_cell}
        </tr>"""

    def remove_row(s: dict) -> str:
        flags     = s.get("consecutive_flags", 1)
        flags_str = f"Flagged {flags} consecutive screen{'s' if flags != 1 else ''}"
        gap_val   = s.get("gap")
        gap_disp  = f"{gap_val}%" if gap_val is not None else "N/A"
        return f"""
        <tr style="border-bottom: 1px solid #ecf0f1;">
            <td style="padding: 10px; font-weight: bold; color: #e74c3c;">{s['ticker']}</td>
            <td style="padding: 10px; text-align: center; color: #e74c3c;">{s['hurst']}</td>
            <td style="padding: 10px; text-align: center; color: #e74c3c;">{s['adx']}</td>
            <td style="padding: 10px; text-align: center;">{gap_disp}</td>
            <td style="padding: 10px; color: #e74c3c;">{s['reason']}</td>
            <td style="padding: 10px; color: #7f8c8d; font-size: 12px;">{flags_str}</td>
        </tr>"""

    # ── Table headers ─────────────────────────────────────────────────────────
    stock_table_header = """
        <tr style="background: #2c3e50; color: white;">
            <th style="padding: 10px; text-align: left;">Stock</th>
            <th style="padding: 10px; text-align: left;">Industry</th>
            <th style="padding: 10px; text-align: center;">Hurst</th>
            <th style="padding: 10px; text-align: center;">ADX</th>
            <th style="padding: 10px; text-align: center;">Gap (80d)</th>
            <th style="padding: 10px; text-align: center;">Gap (2yr)</th>
            <th style="padding: 10px; text-align: center;">Vol%</th>
            <th style="padding: 10px; text-align: center;">Avg Corr</th>
            <th style="padding: 10px; text-align: center;">Status</th>
        </tr>"""

    monitor_table_header = """
        <tr style="background: #2980b9; color: white;">
            <th style="padding: 10px; text-align: left;">Stock</th>
            <th style="padding: 10px; text-align: left;">Industry</th>
            <th style="padding: 10px; text-align: center;">Hurst</th>
            <th style="padding: 10px; text-align: center;">ADX</th>
            <th style="padding: 10px; text-align: center;">Gap (80d)</th>
            <th style="padding: 10px; text-align: center;">Gap (2yr)</th>
            <th style="padding: 10px; text-align: center;">Vol%</th>
            <th style="padding: 10px; text-align: center;">Avg Corr</th>
            <th style="padding: 10px; text-align: center;">Status</th>
        </tr>"""

    remove_table_header = """
        <tr style="background: #c0392b; color: white;">
            <th style="padding: 10px; text-align: left;">Stock</th>
            <th style="padding: 10px; text-align: center;">Hurst</th>
            <th style="padding: 10px; text-align: center;">ADX</th>
            <th style="padding: 10px; text-align: center;">SMA Gap</th>
            <th style="padding: 10px; text-align: left;">Reason</th>
            <th style="padding: 10px; text-align: left;">Flags</th>
        </tr>"""

    # ── ADD section ───────────────────────────────────────────────────────────
    if adds:
        add_rows_html = "".join(add_row(s, i+1) for i, s in enumerate(adds))
        divergence_note = (
            """<p style="color: #e67e22; font-size: 13px; background: #fef9e7; padding: 10px;
                border-left: 3px solid #e67e22; margin-bottom: 20px;">
                ⚠️ <strong>DIVERGENT stocks</strong>: Regime confirmed on 2-year data but the SMA cross
                direction differs on the 80-day window the trading system actually uses for entry signals.
                Verify current price action before adding these stocks to the universe.
            </p>"""
            if any(s.get("divergent") for s in adds) else ""
        )
        add_section = f"""
        <h2 style="color: #27ae60; border-left: 4px solid #27ae60; padding-left: 12px;">
            ✅ ADD Recommendations ({len(adds)})
        </h2>
        <p style="color: #7f8c8d; font-size: 14px;">
            These stocks pass all filters: TRENDING_STRONG regime, H &gt; 0.55, ADX &gt; 25,
            correlation safe, and are currently in or near a golden cross.
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 10px;">
            {stock_table_header}
            {add_rows_html}
        </table>
        <p style="color: #7f8c8d; font-size: 12px; margin-bottom: 30px;">
            <strong>Gap (80d)</strong> = SMA gap over the last 120 calendar days — what the live trading
            system sees. &nbsp;
            <strong>Gap (2yr)</strong> = SMA gap over the full screener window — used for regime classification only.
        </p>
        {divergence_note}"""
    else:
        add_section = """
        <h2 style="color: #27ae60; border-left: 4px solid #27ae60; padding-left: 12px;">
            ✅ ADD Recommendations
        </h2>
        <p style="color: #7f8c8d;">No new additions recommended this cycle. Market conditions may be choppy.</p>"""

    # ── MONITOR section ───────────────────────────────────────────────────────
    if monitors:
        monitor_rows_html = "".join(monitor_row(s, i+1) for i, s in enumerate(monitors))
        monitor_section = f"""
        <h2 style="color: #3498db; border-left: 4px solid #3498db; padding-left: 12px;">
            👁 MONITOR — Already in Golden Cross ({len(monitors)})
        </h2>
        <p style="color: #7f8c8d; font-size: 14px;">
            These stocks already crossed before this screen ran — the system would miss
            the current entry. Wait for the next death cross cycle before adding to the universe.
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px;">
            {monitor_table_header}
            {monitor_rows_html}
        </table>"""
    else:
        monitor_section = ""

    # ── WATCH section ─────────────────────────────────────────────────────────
    if watches:
        watch_rows_html = "".join(watch_row(s, i+1) for i, s in enumerate(watches))
        watch_section = f"""
        <h2 style="color: #e67e22; border-left: 4px solid #e67e22; padding-left: 12px;">
            ⚠️ WATCHLIST ({len(watches)})
        </h2>
        <p style="color: #7f8c8d; font-size: 14px;">
            These stocks pass regime filters but are in death cross. Monitor for golden cross flip.
            SMA Gap shows how far SMA20 is from crossing SMA50.
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px;">
            {stock_table_header}
            {watch_rows_html}
        </table>"""
    else:
        watch_section = ""

    # ── REMOVE section ────────────────────────────────────────────────────────
    if removes:
        remove_rows_html = "".join(remove_row(s) for s in removes)
        remove_section = f"""
        <h2 style="color: #e74c3c; border-left: 4px solid #e74c3c; padding-left: 12px;">
            ❌ REMOVE Recommendations ({len(removes)})
        </h2>
        <p style="color: #7f8c8d; font-size: 14px;">
            These stocks in your current universe have degraded below regime thresholds.
            Consider removing after current position closes naturally.
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px;">
            {remove_table_header}
            {remove_rows_html}
        </table>"""
    else:
        remove_section = """
        <h2 style="color: #e74c3c; border-left: 4px solid #e74c3c; padding-left: 12px;">
            ❌ REMOVE Recommendations
        </h2>
        <p style="color: #27ae60;">✓ All current universe stocks maintain healthy regime metrics.</p>"""

    # ── Current universe pills ────────────────────────────────────────────────
    universe_pills = " ".join(
        f'<span style="background: #3498db; color: white; padding: 4px 10px; border-radius: 12px; font-size: 13px; margin: 2px;">{t}</span>'
        for t in universe
    )

    # ── Full HTML ─────────────────────────────────────────────────────────────
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #f8f9fa;">

    <!-- Header -->
    <div style="background: #2c3e50; color: white; padding: 24px; border-radius: 8px 8px 0 0; margin-bottom: 0;">
        <h1 style="margin: 0; font-size: 22px;">📈 NSE Algo Trading — Universe Screen Report</h1>
        <p style="margin: 8px 0 0 0; color: #bdc3c7; font-size: 14px;">{run_date} &nbsp;|&nbsp; NIFTY 500 Screen &nbsp;|&nbsp; {screened} candidates evaluated &nbsp;|&nbsp; {passed} passed filters</p>
    </div>

    <!-- Current Universe -->
    <div style="background: white; padding: 20px; border: 1px solid #ecf0f1; margin-bottom: 20px;">
        <h3 style="margin-top: 0; color: #2c3e50;">Current Trading Universe ({len(universe)} stocks)</h3>
        <div>{universe_pills}</div>
    </div>

    <!-- Main content -->
    <div style="background: white; padding: 24px; border: 1px solid #ecf0f1; border-radius: 0 0 8px 8px;">

        {add_section}
        {monitor_section}
        {watch_section}
        {remove_section}

        <!-- Footer -->
        <hr style="border: none; border-top: 1px solid #ecf0f1; margin: 30px 0;">
        <p style="color: #bdc3c7; font-size: 12px; text-align: center;">
            NSE Algo Trading System &nbsp;|&nbsp; AWS Lightsail Mumbai &nbsp;|&nbsp;
            Data: {coverage} stocks screened &nbsp;|&nbsp; Min history: 744 bars (2 years)<br>
            Thresholds: Hurst &gt; 0.55 &nbsp;|&nbsp; ADX &gt; 25 &nbsp;|&nbsp; Max avg correlation: 0.60<br>
            Next screen: Wednesday or Sunday 6 PM IST
        </p>
    </div>

</body>
</html>"""


# ── SendGrid sender ───────────────────────────────────────────────────────────

def send_report(results: dict) -> bool:
    """Send HTML email report via SendGrid. Returns True on success."""
    if not SENDGRID_API_KEY:
        print("[emailer] ERROR: SENDGRID_API_KEY not set in .env")
        return False

    html_body  = build_html(results)
    run_date   = results["meta"]["run_date"]
    adds_count = len(results["adds"])
    subject    = f"NSE Algo — Universe Screen {run_date[:10]} | {adds_count} ADD recommendation{'s' if adds_count != 1 else ''}"

    payload = {
        "personalizations": [{"to": [{"email": SENDGRID_TO}]}],
        "from":    {"email": SENDGRID_FROM, "name": "NSE Algo Trading"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }

    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type":  "application/json",
    }

    try:
        import requests as req
        r = req.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers=headers,
            timeout=15
        )
        if r.status_code in (200, 202):
            print(f"[emailer] Email sent successfully → {SENDGRID_TO}")
            return True
        else:
            print(f"[emailer] SendGrid error {r.status_code}: {r.text}")
            return False
    except Exception as e:
        print(f"[emailer] Send failed: {e}")
        return False


if __name__ == "__main__":
    # Test: send a dummy email to verify SendGrid setup
    test_results = {
        "adds": [{
            "ticker": "TEST.NS", "industry": "Test", "hurst": 0.612,
            "adx": 27.4, "gap": 0.45, "vol": 28.3,
            "avg_corr": 0.21, "max_corr": 0.33, "cross": "GOLDEN"
        }],
        "watches": [],
        "removes": [],
        "meta": {
            "run_date": "2026-06-16 18:00 IST",
            "screened": 504, "passed_filters": 1,
            "current_universe": ["TMPV.NS", "WHIRLPOOL.NS", "SIEMENS.NS", "BAJAJ-AUTO.NS",
                                  "CUMMINSIND.NS", "HCLTECH.NS", "BOSCHLTD.NS", "COLPAL.NS",
                                  "ANURAS.NS", "HEROMOTOCO.NS"],
            "data_coverage": 380,
        }
    }
    print("[emailer] Sending test email...")
    send_report(test_results)
