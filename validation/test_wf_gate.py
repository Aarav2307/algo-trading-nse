"""
validation/test_wf_gate.py — Tests for evaluate_wf_gate(), extracted from
walk_forward.py's __main__ block during the Jul 21 architecture review.

Before this extraction, the gate pass/fail decision only existed inline
inside `if __name__ == "__main__":`, so testing it required runpy.run_path()
with mocked sys.argv and captured stdout (see test_walk_forward_json_output.py).
These tests call the function directly — no CLI, no subprocess, no backtests.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from validation.walk_forward import evaluate_wf_gate


def test_gate_passes_when_both_windows_clear_thresholds():
    orig = {"score": 6, "oos_return": 9.0}
    ext  = {"score": 5, "oos_return": 13.6}
    gate = evaluate_wf_gate(orig, ext)

    assert gate["gate_pass"] is True
    assert gate["reasons"] == []
    assert gate["ext_status"] == "PASS"


def test_gate_fails_on_original_metric_score_below_min():
    orig = {"score": 3, "oos_return": 9.0}
    ext  = {"score": 5, "oos_return": 13.6}
    gate = evaluate_wf_gate(orig, ext)

    assert gate["gate_pass"] is False
    assert any("original metrics 3/6 < 4 required" in r for r in gate["reasons"])


def test_gate_fails_on_original_oos_return_below_min():
    orig = {"score": 6, "oos_return": 1.0}   # below +4.0% threshold
    ext  = {"score": 5, "oos_return": 13.6}
    gate = evaluate_wf_gate(orig, ext)

    assert gate["gate_pass"] is False
    assert any("OOS return +1.0% < +4% required" in r for r in gate["reasons"])


def test_gate_fails_on_extended_metric_score_below_min():
    orig = {"score": 6, "oos_return": 9.0}
    ext  = {"score": 2, "oos_return": -3.0}
    gate = evaluate_wf_gate(orig, ext)

    assert gate["gate_pass"] is False
    assert gate["ext_status"] == "FAIL"
    assert any("extended metrics 2/6 < 4 required" in r for r in gate["reasons"])


def test_insufficient_extended_data_does_not_block_gate():
    """ext_score is None (insufficient history) must not penalise the gate —
    only a real FAIL (score below threshold) should."""
    orig = {"score": 6, "oos_return": 9.0}
    ext  = {"score": "N/A", "oos_return": "N/A", "error": "no bars in 2015-2019 window"}
    gate = evaluate_wf_gate(orig, ext)

    assert gate["gate_pass"] is True
    assert gate["ext_status"] == "SKIPPED"
    assert gate["ext_error"] == "no bars in 2015-2019 window"


def test_no_extended_flag_never_penalises_gate():
    orig = {"score": 6, "oos_return": 9.0}
    gate = evaluate_wf_gate(orig, {}, no_extended=True)

    assert gate["gate_pass"] is True
    assert gate["ext_status"] == "NOT_REQUESTED"


def test_na_string_sentinels_normalize_to_none_not_typeerror():
    """run_walk_forward() uses the string "N/A" (not None) for insufficient
    original-window data too — must not raise TypeError comparing to metric_min."""
    orig = {"score": "N/A", "oos_return": "N/A", "error": "insufficient data"}
    ext  = {"score": 5, "oos_return": 13.6}

    gate = evaluate_wf_gate(orig, ext)   # must not raise

    assert gate["gate_pass"] is False
    assert gate["orig_score"] is None
    assert gate["orig_oos_ret"] is None
    assert any("N/A" in r for r in gate["reasons"])
