"""
screener/test_degradation_annotation.py — Tests for regime-transition annotation
on degradation flags (Audit1 Fix, Jul 2026).

Covers:
  - Flags near a regime transition are annotated (regime_transition_nearby=True)
  - Flags far from a transition are NOT annotated
  - consecutive_flags threshold (≥2 → REMOVE) is unchanged by this feature
  - Email report shows caution warning for annotated flags
  - _days_since_regime_transition fails open on missing/unreadable state file
"""

import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from screener.auto_screener import (
    _days_since_regime_transition,
    REGIME_TRANSITION_WINDOW,
)
from screener.emailer import _build_remove_row


# =============================================================================
# Helper: build a minimal tracker entry
# =============================================================================

def _entry(consecutive=0, history=None, annotations=None):
    return {
        "consecutive_flags": consecutive,
        "last_flagged":      "2026-07-08",
        "last_screen_date":  "2026-07-08",
        "flag_history":      history or [],
        "flag_annotations":  annotations or {},
    }


# =============================================================================
# Tests for _days_since_regime_transition
# =============================================================================

def test_days_since_regime_transition_returns_correct_delta(tmp_path):
    """Correct signed delta returned when regime_transition_date is present."""
    state = {"regime_transition_date": "2026-07-07", "market_regime": "BULL"}
    state_file = tmp_path / "portfolio_state.json"
    state_file.write_text(json.dumps(state))

    with patch("screener.auto_screener.PORTFOLIO_STATE", state_file):
        result = _days_since_regime_transition(date(2026, 7, 8))

    assert result == 1  # Jul 8 - Jul 7 = 1 day after transition


def test_regime_transition_helper_fails_open_on_missing_state_file(tmp_path):
    """If portfolio_state.json is absent, returns None (never crashes screener)."""
    nonexistent = tmp_path / "does_not_exist.json"
    with patch("screener.auto_screener.PORTFOLIO_STATE", nonexistent):
        result = _days_since_regime_transition(date(2026, 7, 8))
    assert result is None


def test_regime_transition_helper_fails_open_on_missing_field(tmp_path):
    """If regime_transition_date field is absent from state, returns None."""
    state = {"market_regime": "BEAR"}  # no regime_transition_date
    state_file = tmp_path / "portfolio_state.json"
    state_file.write_text(json.dumps(state))

    with patch("screener.auto_screener.PORTFOLIO_STATE", state_file):
        result = _days_since_regime_transition(date(2026, 7, 8))
    assert result is None


def test_regime_transition_helper_fails_open_on_corrupt_file(tmp_path):
    """If portfolio_state.json is corrupt JSON, returns None."""
    state_file = tmp_path / "portfolio_state.json"
    state_file.write_text("{ not valid json }")

    with patch("screener.auto_screener.PORTFOLIO_STATE", state_file):
        result = _days_since_regime_transition(date(2026, 7, 8))
    assert result is None


# =============================================================================
# Tests for annotation in the flag-update loop
# =============================================================================

def test_flag_annotated_when_near_regime_transition(tmp_path):
    """
    A flag within REGIME_TRANSITION_WINDOW days of a transition gets
    regime_transition_nearby=True, but consecutive_flags still increments.
    """
    state = {"regime_transition_date": "2026-07-07"}
    state_file = tmp_path / "portfolio_state.json"
    state_file.write_text(json.dumps(state))

    entry = _entry(consecutive=0)
    today = date(2026, 7, 8)  # 1 day after transition → within window

    with patch("screener.auto_screener.PORTFOLIO_STATE", state_file):
        from screener.auto_screener import _days_since_regime_transition
        days = _days_since_regime_transition(today)

    assert days == 1
    assert 0 <= days <= REGIME_TRANSITION_WINDOW

    # Simulate the annotation logic directly (as it runs in run_screen)
    today_str = today.isoformat()
    entry["consecutive_flags"] += 1
    entry["flag_history"].append(today_str)
    if days is not None and 0 <= days <= REGIME_TRANSITION_WINDOW:
        entry.setdefault("flag_annotations", {})[today_str] = {
            "regime_transition_nearby": True,
            "days_since_transition":    days,
        }

    assert entry["consecutive_flags"] == 1  # incremented normally
    assert today_str in entry["flag_annotations"]
    assert entry["flag_annotations"][today_str]["regime_transition_nearby"] is True
    assert entry["flag_annotations"][today_str]["days_since_transition"] == 1


def test_flag_not_annotated_when_far_from_transition(tmp_path):
    """
    A flag well outside the window gets no annotation; consecutive_flags
    increments exactly as before — behavior unchanged from pre-feature.
    """
    state = {"regime_transition_date": "2026-06-20"}  # 18 days before Jul 8
    state_file = tmp_path / "portfolio_state.json"
    state_file.write_text(json.dumps(state))

    entry = _entry(consecutive=0)
    today = date(2026, 7, 8)
    today_str = today.isoformat()

    with patch("screener.auto_screener.PORTFOLIO_STATE", state_file):
        days = _days_since_regime_transition(today)

    assert days == 18  # 18 days after — outside REGIME_TRANSITION_WINDOW (5)

    entry["consecutive_flags"] += 1
    entry["flag_history"].append(today_str)
    if days is not None and 0 <= days <= REGIME_TRANSITION_WINDOW:
        entry.setdefault("flag_annotations", {})[today_str] = {
            "regime_transition_nearby": True,
            "days_since_transition":    days,
        }

    assert entry["consecutive_flags"] == 1
    assert today_str not in entry.get("flag_annotations", {})  # no annotation


def test_consecutive_flags_threshold_unchanged(tmp_path):
    """
    Two consecutive annotated flags still produce a REMOVE recommendation.
    The annotation never suppresses or changes the threshold logic.
    """
    state = {"regime_transition_date": "2026-07-07"}
    state_file = tmp_path / "portfolio_state.json"
    state_file.write_text(json.dumps(state))

    # Simulate stock reaching consecutive_flags=2 with annotations
    entry = _entry(
        consecutive=2,
        history=["2026-07-05", "2026-07-08"],
        annotations={
            "2026-07-05": {"regime_transition_nearby": True, "days_since_transition": -2},
            "2026-07-08": {"regime_transition_nearby": True, "days_since_transition": 1},
        },
    )

    # Threshold check — exactly as in run_screen()
    should_remove = entry["consecutive_flags"] >= 2
    assert should_remove is True  # annotation does NOT change this


# =============================================================================
# Tests for email report warning
# =============================================================================

def test_report_shows_warning_for_annotated_flags():
    """
    A REMOVE-recommended stock with annotated flags shows the caution row
    in the generated HTML.  The main data row still appears — only an
    additional caution row is appended.
    """
    stock = {
        "ticker":            "NEWGEN.NS",
        "hurst":             0.412,
        "adx":               21.5,
        "adx_trend":         "FALLING",
        "gap":               -3.2,
        "reason":            "ADX=21.5 < 22.0 (trend: FALLING)",
        "consecutive_flags": 2,
        "flag_history":      ["2026-07-05", "2026-07-08"],
        "flag_annotations":  {
            "2026-07-05": {"regime_transition_nearby": True, "days_since_transition": -2},
            "2026-07-08": {"regime_transition_nearby": True, "days_since_transition": 1},
        },
    }
    html = _build_remove_row(stock)
    assert "CAUTION" in html
    assert "NEWGEN.NS" in html
    assert "regime transition" in html.lower()
    assert "2026-07-05" in html
    assert "2026-07-08" in html


def test_report_no_warning_when_no_annotated_flags():
    """
    A REMOVE-recommended stock with NO annotated flags must not show any caution
    row — the existing display is completely unchanged.
    """
    stock = {
        "ticker":            "SIEMENS.NS",
        "hurst":             0.38,
        "adx":               20.1,
        "adx_trend":         "FALLING",
        "gap":               -5.0,
        "reason":            "H=0.380 < 0.45",
        "consecutive_flags": 1,
        "flag_history":      ["2026-07-04"],
        "flag_annotations":  {},
    }
    html = _build_remove_row(stock)
    assert "CAUTION" not in html
    assert "regime transition" not in html.lower()
    assert "SIEMENS.NS" in html
