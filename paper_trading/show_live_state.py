"""
show_live_state.py — the one correct way to look at portfolio state.

WHY THIS EXISTS
---------------
paper_trading/portfolio_state.json exists in the local checkout, at the path
every doc says is correct, and reads as perfectly plausible live data. It is
not live. The local machine never runs the trading system; the Lightsail server
does. A local copy is therefore NEVER authoritative — not because it is stale,
but because it was never connected to anything. Age is irrelevant: a copy
synced an hour ago is just as wrong as one synced in June.

Two defences against reading it already existed and both were bypassed on
2026-08-26 during the Finding #3 write-up:

  1. PaperPortfolio.validate_state_integrity() catches exactly this, and fires
     correctly on the stale file (verified) — but only via load(). An ad-hoc
     `json.load(open(path))` bypasses it entirely, and that is what anyone
     inspecting state actually types.
  2. CLAUDE_CONTEXT.md carried a written warning naming that specific file —
     which was read in full and did not prevent the mistake.

So this module is not another detector. It is a named command, so that "how do
I look at state" has a one-command answer that CANNOT return the local file.

FAILS LOUD BY DESIGN
--------------------
If the server is unreachable, this raises. It does not fall back to a local
read, print a warning and continue, or return partial data. A fallback would
reintroduce the precise failure it exists to prevent — silently handing back a
plausible wrong number. No answer is strictly better than a confident wrong one.

Usage:
    python paper_trading/show_live_state.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Callable, Optional

# Infrastructure per CLAUDE_CONTEXT.md § Infrastructure
SERVER       = "ubuntu@13.205.133.169"
SSH_KEY      = "~/.ssh/LightsailDefaultKey-ap-south-1.pem"
REMOTE_STATE = "/home/ubuntu/algo-trading/paper_trading/portfolio_state.json"
SSH_TIMEOUT  = 30


class LiveStateUnavailable(RuntimeError):
    """
    Raised when live state could not be read from the server.

    Deliberately not caught anywhere in this module. The caller gets an
    exception rather than a fallback value, because the whole point is that a
    wrong-but-plausible number is worse than no number.
    """


def _ssh_command() -> list[str]:
    # `cat` only — this command is incapable of mutating server state.
    return [
        "ssh",
        "-o", f"ConnectTimeout={SSH_TIMEOUT}",
        "-o", "BatchMode=yes",
        "-i", SSH_KEY,
        SERVER,
        f"cat {REMOTE_STATE}",
    ]


def fetch_live_state(runner: Optional[Callable] = None) -> dict:
    """
    Read live portfolio state from the server. Read-only.

    Args:
        runner: injected for testing — must behave like subprocess.run with
                capture_output=True, text=True. Tests pass a fake so the suite
                never touches the network.

    Returns:
        The parsed state dict, exactly as the server holds it.

    Raises:
        LiveStateUnavailable: on any failure whatsoever. There is no partial
        success and no fallback path.
    """
    run = runner or subprocess.run
    try:
        proc = run(_ssh_command(), capture_output=True, text=True, timeout=SSH_TIMEOUT + 10)
    except Exception as exc:                       # network, timeout, missing ssh
        raise LiveStateUnavailable(
            f"could not reach {SERVER}: {type(exc).__name__}: {exc}"
        ) from exc

    if proc.returncode != 0:
        raise LiveStateUnavailable(
            f"ssh to {SERVER} exited {proc.returncode}. "
            f"stderr: {(proc.stderr or '').strip()[:400]}"
        )

    try:
        state = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LiveStateUnavailable(
            f"server returned unparseable JSON ({exc}). "
            f"first 200 chars: {(proc.stdout or '')[:200]!r}"
        ) from exc

    if not isinstance(state, dict) or "cash" not in state:
        raise LiveStateUnavailable(
            "server returned JSON that is not a portfolio state document "
            f"(type={type(state).__name__}, keys={list(state)[:8] if isinstance(state, dict) else 'n/a'})"
        )
    return state


def format_state(state: dict) -> str:
    """Render live state for a human. Pure — no I/O, no network."""
    cash      = state.get("cash", 0.0)
    etf_sh    = state.get("etf_shares", 0)
    etf_px    = state.get("etf_avg_price", 0.0)
    etf_value = etf_sh * etf_px

    positions = state.get("positions", {})
    # Only entries holding shares are open positions. The dict retains a key per
    # universe ticker, so len(positions) is NOT the open-position count — that
    # miscount is part of what this module exists to prevent.
    open_pos  = {t: p for t, p in positions.items() if p.get("shares")}
    stock_val = sum(p.get("shares", 0) * p.get("entry_price", 0.0) for p in open_pos.values())

    lines = [
        f"LIVE portfolio state — read from {SERVER}",
        f"  last run        {state.get('last_run_date')} {state.get('last_run_time') or ''}".rstrip(),
        f"  cash            Rs{cash:,.2f}",
        f"  ETF             {etf_sh} NIFTYBEES @ Rs{etf_px:,.2f} = Rs{etf_value:,.2f}",
        f"  etf_tier        {state.get('etf_tier')}",
        f"  total value     Rs{cash + stock_val + etf_value:,.2f}",
        f"  total_trades    {state.get('total_trades')}",
        f"  open positions  {len(open_pos)}  (positions dict has {len(positions)} keys)",
    ]
    for ticker, pos in open_pos.items():
        lines.append(
            f"      {ticker:<16} {pos.get('shares')} sh @ Rs{pos.get('entry_price', 0.0):,.2f}"
            f"  entry {pos.get('entry_date')}"
        )
    return "\n".join(lines)


def main() -> int:
    try:
        state = fetch_live_state()
    except LiveStateUnavailable as exc:
        print(f"[show_live_state] LIVE STATE UNAVAILABLE: {exc}", file=sys.stderr)
        print(
            "[show_live_state] NOT falling back to the local "
            "paper_trading/portfolio_state.json — it is never authoritative, "
            "at any age. Fix the connection and re-run.",
            file=sys.stderr,
        )
        return 1
    print(format_state(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
