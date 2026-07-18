"""Tests for utils/alerts.py crash alerting."""
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import utils.alerts as alerts


def test_send_crash_alert_writes_to_log(tmp_path):
    log = tmp_path / "alerts.log"
    with patch.object(alerts, "ALERT_LOG", log):
        try:
            raise RuntimeError("intentional test crash")
        except RuntimeError as exc:
            alerts.send_crash_alert("test_script.py", exc)
    content = log.read_text()
    assert "CRASH ALERT [test_script.py]" in content
    assert "intentional test crash" in content
    assert "RuntimeError" in content


def test_send_crash_alert_survives_unwritable_log():
    with patch.object(alerts, "ALERT_LOG", Path("/nonexistent/path/alerts.log")):
        try:
            raise ValueError("log write will fail")
        except ValueError as exc:
            alerts.send_crash_alert("test_script.py", exc)   # must not raise
