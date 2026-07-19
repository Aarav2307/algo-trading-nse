"""Regression tests for morning_fill_check.py path constants."""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.morning_fill_check as mfc


def _assert_cwd_independent(const_name: str, path: Path) -> None:
    assert path.is_absolute(), f"{const_name} is not absolute: {path!r}"
    original_cwd = os.getcwd()
    exists_from_root = path.exists()
    try:
        os.chdir("/tmp")
        exists_from_tmp = path.exists()
    finally:
        os.chdir(original_cwd)
    assert exists_from_root == exists_from_tmp, (
        f"{const_name}.exists() is cwd-dependent: "
        f"from root={exists_from_root}, from /tmp={exists_from_tmp}"
    )


def test_amo_csv_is_absolute_and_cwd_independent():
    _assert_cwd_independent("AMO_CSV", mfc.AMO_CSV)


def test_state_file_is_absolute_and_cwd_independent():
    _assert_cwd_independent("STATE_FILE", mfc.STATE_FILE)


def test_token_file_is_absolute_and_cwd_independent():
    _assert_cwd_independent("TOKEN_FILE", mfc.TOKEN_FILE)


def test_amo_order_log_is_absolute_and_cwd_independent():
    _assert_cwd_independent("_AMO_ORDER_LOG", mfc._AMO_ORDER_LOG)


def test_amo_csv_and_amo_order_log_point_to_same_file():
    """AMO_CSV and _AMO_ORDER_LOG name the same path — both must agree after the fix."""
    assert mfc.AMO_CSV == mfc._AMO_ORDER_LOG, (
        f"AMO_CSV={mfc.AMO_CSV!r} != _AMO_ORDER_LOG={mfc._AMO_ORDER_LOG!r}"
    )
