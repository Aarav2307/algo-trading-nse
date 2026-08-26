"""
Tests for paper_trading/show_live_state.py — Finding #14.

The module exists because a local portfolio_state.json reads as plausible live
data. Its single most important property is therefore NEGATIVE: on failure it
must not fall back to a local read. That claim is asserted behaviourally, by
recording every open() call, not by scanning source text — the audit has now
been bitten twice by text-matching assertions that passed on the wrong context.

No test here touches the network. subprocess.run is never called; a fake runner
is injected at the seam.
"""

import builtins
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from paper_trading.show_live_state import (  # noqa: E402
    LiveStateUnavailable,
    fetch_live_state,
    format_state,
    _ssh_command,
)


class _Proc:
    """Minimal stand-in for subprocess.CompletedProcess."""
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _runner(proc=None, raises=None):
    """Build a fake runner; records the argv it was handed."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if raises is not None:
            raise raises
        return proc

    run.calls = calls
    return run


# Shape of the real live document (server-verified 2026-08-26)
LIVE = {
    "cash": 243.49,
    "etf_shares": 359,
    "etf_avg_price": 272.44,
    "etf_tier": 1.0,
    "total_trades": 4,
    "last_run_date": "2026-08-25",
    "last_run_time": "15:45",
    "positions": {f"T{i}.NS": {"shares": 0, "entry_price": 0.0} for i in range(17)},
}

# Shape of the stale LOCAL document that caused the incident
STALE_LOCAL = {
    "cash": 29106.13,
    "total_trades": 3,
    "last_run_date": "2026-06-24",
    "positions": {
        **{f"Z{i}.NS": {"shares": 0, "entry_price": 0.0} for i in range(8)},
        "BAJAJ-AUTO.NS": {"shares": 1, "entry_price": 10286.14, "entry_date": "2026-06-02"},
    },
}


# ── Group 1: fails loud on every failure mode ──────────────────────────────

def test_nonzero_exit_raises():
    r = _runner(_Proc(returncode=255, stderr="Permission denied (publickey)."))
    with pytest.raises(LiveStateUnavailable) as e:
        fetch_live_state(runner=r)
    assert "255" in str(e.value)
    assert "publickey" in str(e.value), "stderr must survive into the message"


def test_unparseable_json_raises():
    r = _runner(_Proc(stdout="<html>proxy error</html>"))
    with pytest.raises(LiveStateUnavailable) as e:
        fetch_live_state(runner=r)
    assert "unparseable" in str(e.value)


def test_runner_exception_raises_live_state_unavailable():
    r = _runner(raises=subprocess.TimeoutExpired(cmd="ssh", timeout=30))
    with pytest.raises(LiveStateUnavailable) as e:
        fetch_live_state(runner=r)
    assert "TimeoutExpired" in str(e.value)


@pytest.mark.parametrize("payload", ["[1,2,3]", '{"unexpected": true}', "null"])
def test_valid_json_that_is_not_a_state_document_raises(payload):
    r = _runner(_Proc(stdout=payload))
    with pytest.raises(LiveStateUnavailable):
        fetch_live_state(runner=r)


# ── Group 2: never falls back to a local read (the load-bearing claim) ─────

@pytest.mark.parametrize(
    "proc,raises",
    [
        (_Proc(returncode=255, stderr="down"), None),
        (_Proc(stdout="not json"), None),
        (None, OSError("network unreachable")),
    ],
)
def test_failure_never_opens_a_local_state_file(proc, raises, monkeypatch):
    """
    Behavioural, not textual: record every path handed to open() and assert no
    local state file was touched on the failure path. A fallback read is the
    one thing this module must never do.
    """
    opened = []
    real_open = builtins.open

    def spy(path, *a, **kw):
        opened.append(str(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)

    with pytest.raises(LiveStateUnavailable):
        fetch_live_state(runner=_runner(proc, raises))

    offenders = [p for p in opened if "portfolio_state" in p]
    assert not offenders, f"fell back to a local state read: {offenders}"


def test_failure_raises_rather_than_returning_a_value():
    """No sentinel, no empty dict — the caller cannot mistake failure for data."""
    r = _runner(_Proc(returncode=1, stderr="boom"))
    try:
        result = fetch_live_state(runner=r)
    except LiveStateUnavailable:
        return
    pytest.fail(f"returned {result!r} instead of raising")


# ── Group 3: the command itself is read-only ───────────────────────────────

def test_ssh_command_is_read_only_and_non_interactive():
    cmd = _ssh_command()
    joined = " ".join(cmd)
    assert joined.count("cat ") == 1, "the remote command must be a single cat"
    for verb in ("rm ", "mv ", "cp ", "tee", ">", "python", "signal_runner", "morning_fill"):
        assert verb not in joined, f"remote command is not read-only: contains {verb!r}"
    assert "BatchMode=yes" in joined, "must not hang on an interactive prompt"


def test_happy_path_returns_parsed_state():
    r = _runner(_Proc(stdout=json.dumps(LIVE)))
    state = fetch_live_state(runner=r)
    assert state["cash"] == 243.49
    assert state["etf_shares"] == 359
    assert r.calls, "runner was never invoked"


# ── Group 4: the miscount that caused the incident ─────────────────────────

def test_open_position_count_is_not_the_positions_dict_length():
    """
    The incident reported 9 open positions from a 9-key dict in which only one
    held shares. format_state must never conflate the two.
    """
    out = format_state(STALE_LOCAL)
    assert "open positions  1" in out, out
    assert "9 keys" in out, "the dict size must still be visible, just not as the count"


def test_live_shape_reports_zero_open_positions_from_seventeen_keys():
    out = format_state(LIVE)
    assert "open positions  0" in out, out
    assert "17 keys" in out, out


def test_format_state_performs_no_io(monkeypatch):
    """Pure function — rendering must not read anything."""
    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1])
    format_state(LIVE)
    assert not opened, f"format_state opened files: {opened}"
