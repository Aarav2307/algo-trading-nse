"""
validation/add_validated_stock.py — Atomic single-command stock addition to the live universe.

Runs the WF gate live (no cache), adds the ticker to universe.py if it
passes, auto-drafts the CLAUDE_CONTEXT.md Universe History entry, then runs
the full test suite as a final hard gate. Reverts all edits on test failure.

Usage:
    python3 validation/add_validated_stock.py TICKER.NS

No --force flag: the WF gate is hard and non-bypassable by design.
Does not commit, push, or touch the server.
"""

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from validation.post_screener_pipeline import run_wf_gate

UNIVERSE_FILE      = _ROOT / "universe.py"
PENDING_ENTRY_DIR  = _ROOT / "validation"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_current_stocks() -> list:
    """Return the live STOCKS list from universe.py via direct import."""
    from universe import STOCKS
    return list(STOCKS)


def _build_comment(gate_data: dict, date_str: str) -> str:
    """Build the inline WF comment for the STOCKS list entry (same text in both files)."""
    orig  = gate_data["original"]
    ext   = gate_data["extended"]
    total = orig["total_metrics"]

    orig_ret  = orig["oos_return_pct"]
    orig_part = f"{orig['score']}/{total} original OOS {orig_ret:+.1f}%"

    status = ext["status"]
    if status == "PASS":
        ext_ret  = ext["oos_return_pct"]
        ext_part = f"{ext['score']}/{total} extended OOS {ext_ret:+.1f}%"
    elif status == "SKIPPED":
        ext_error = ext.get("error")
        ext_part = (
            f"extended SKIPPED (insufficient data, {ext_error})"
            if ext_error
            else "extended SKIPPED (insufficient data)"
        )
    else:
        ext_ret     = ext.get("oos_return_pct")
        ext_ret_str = f"{ext_ret:+.1f}%" if ext_ret is not None else "N/A"
        ext_part    = f"{ext['score']}/{total} extended OOS {ext_ret_str} (FAIL)"

    return f"WF validated {date_str} — {orig_part}, {ext_part}"


def _insert_into_stocks(file_path: Path, ticker: str, comment: str) -> None:
    """Insert ticker + comment before the closing ] of the STOCKS list in file_path."""
    text = file_path.read_text()
    m = re.search(r'(STOCKS(?::\s*List\[str\])?\s*=\s*\[.*?)(\n\])', text, re.DOTALL)
    if not m:
        raise RuntimeError(f"STOCKS list not found in {file_path.name}")
    # Align # comment to column 19 after the 4-space indent (same as CHOLAHLDNG/COHANCE)
    ticker_field = f'"{ticker}",'
    padding      = " " * max(2, 18 - len(ticker_field))
    new_line     = f"    {ticker_field}{padding}# {comment}"
    new_text     = text[: m.start(2)] + f"\n{new_line}" + text[m.start(2):]
    file_path.write_text(new_text)


def _write_draft_context_entry(ticker: str, gate_data: dict, date_str: str) -> Path:
    """Write a draft CLAUDE_CONTEXT.md Universe History entry to pending_context_entry_TICKER.txt.

    Generates the WF-data lines exactly; leaves TODOs for context-specific
    fields (screener batch, crossover state, deployment timing) that require
    human review before the entry is complete.
    """
    orig  = gate_data["original"]
    ext   = gate_data["extended"]
    total = orig["total_metrics"]

    orig_ret  = orig["oos_return_pct"]
    orig_line = f"original {orig['score']}/{total} OOS {orig_ret:+.1f}%"

    status = ext["status"]
    if status == "PASS":
        ext_ret  = ext["oos_return_pct"]
        ext_line = f"extended {ext['score']}/{total} OOS {ext_ret:+.1f}%."
    elif status == "SKIPPED":
        ext_error = ext.get("error")
        if ext_error:
            ext_line = f"extended SKIPPED — insufficient historical data ({ext_error})."
        else:
            ext_line = "extended SKIPPED — insufficient historical data."
    else:
        ext_ret     = ext.get("oos_return_pct")
        ext_ret_str = f"{ext_ret:+.1f}%" if ext_ret is not None else "N/A"
        ext_line    = f"extended {ext['score']}/{total} OOS {ext_ret_str} (FAIL)."

    content = (
        f"- Added {date_str}: {ticker}"
        f" (TODO: screener context — batch date, other candidates tested)\n"
        f"  WF validated: {orig_line}, {ext_line}\n"
        f"  TODO: note current crossover state (SMA20=X, SMA50=Y, gap Z%) at add time.\n"
        f"  TODO: deployment note — run backup_state.sh, scp universe.py to server.\n"
    )

    ticker_short = ticker.replace(".NS", "")
    out_path     = PENDING_ENTRY_DIR / f"pending_context_entry_{ticker_short}.txt"
    out_path.write_text(content)
    return out_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print(
            "Usage: python3 validation/add_validated_stock.py TICKER.NS",
            file=sys.stderr,
        )
        return 1

    ticker = sys.argv[1].upper().strip()
    if not ticker.endswith(".NS"):
        print(f"ERROR: ticker must end with .NS (got {ticker!r})", file=sys.stderr)
        return 1

    today     = date.today()
    today_str = f"{today.strftime('%b')} {today.day} {today.year}"

    # ── Step 2: WF gate — fresh run, no cache ────────────────────────────────
    print(f"[gate] Running WF gate for {ticker}...")
    try:
        gate_data = run_wf_gate(ticker)
    except RuntimeError as e:
        print(f"ERROR: WF gate subprocess failed:\n  {e}", file=sys.stderr)
        return 1

    if not gate_data.get("gate_pass"):
        reasons = gate_data.get("reasons", [])
        print(f"\nWF gate FAILED for {ticker}.")
        for r in reasons:
            print(f"  - {r}")
        print("\nNothing was modified. Fix the underlying failure before adding.")
        return 1

    orig  = gate_data["original"]
    ext   = gate_data["extended"]
    total = orig["total_metrics"]
    ext_detail = (
        f"PASS {ext['score']}/{total} OOS {ext['oos_return_pct']:+.1f}%"
        if ext["status"] == "PASS"
        else ext["status"]
    )
    print(f"[gate] PASS — {orig['score']}/{total} original OOS {orig['oos_return_pct']:+.1f}%, extended {ext_detail}")

    # ── Step 3: Already-present check ────────────────────────────────────────
    current_stocks = _get_current_stocks()
    if ticker in current_stocks:
        print(f"\n{ticker} is already in the live STOCKS universe — nothing to do.")
        return 0

    current_count = len(current_stocks)

    # ── Step 4: Build and apply file edit ────────────────────────────────────
    comment      = _build_comment(gate_data, today_str)
    universe_backup = UNIVERSE_FILE.read_text()

    try:
        _insert_into_stocks(UNIVERSE_FILE, ticker, comment)
    except Exception as e:
        print(f"ERROR during file edit: {e}", file=sys.stderr)
        UNIVERSE_FILE.write_text(universe_backup)
        return 1

    # ── Step 5: Draft CLAUDE_CONTEXT entry ───────────────────────────────────
    draft_path = _write_draft_context_entry(ticker, gate_data, today_str)

    # ── Step 6: Full test suite (hard gate) ──────────────────────────────────
    print("\n[tests] Running full test suite...")
    test_result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "paper_trading/", "data/", "utils/", "screener/", "validation/", "-v",
        ],
        cwd=str(_ROOT),
    )

    if test_result.returncode != 0:
        print(
            f"\nERROR: Test suite failed after adding {ticker}. Reverting file edit.",
            file=sys.stderr,
        )
        UNIVERSE_FILE.write_text(universe_backup)
        draft_path.unlink(missing_ok=True)
        return 1

    # ── Step 7: Summary ──────────────────────────────────────────────────────
    sep          = "=" * 70
    ticker_short = ticker.replace(".NS", "")
    print(f"""
{sep}
SUCCESS — {ticker} added to the live universe
{sep}

WF gate result (fresh run, no cache):
  original : {orig['score']}/{total} OOS {orig['oos_return_pct']:+.1f}%
  extended : {ext_detail}

File updated:
  ✓ universe.py  ({current_count} → {current_count + 1} stocks)

Draft CLAUDE_CONTEXT.md entry written to:
  {draft_path}
  Review and manually insert into the Universe History section of CLAUDE_CONTEXT.md.

NEXT MANUAL STEPS:
  1. git diff universe.py
  2. Review and insert {draft_path.name} into CLAUDE_CONTEXT.md Universe History
  3. git add universe.py CLAUDE_CONTEXT.md
  4. git commit
  5. Server deploy: run backup_state.sh first, then scp universe.py to server
  6. git push
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
