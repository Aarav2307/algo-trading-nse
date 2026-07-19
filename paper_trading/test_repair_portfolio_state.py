"""Regression tests for repair_portfolio_state.py path constants."""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import paper_trading.repair_portfolio_state as rps


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


def test_state_file_is_absolute_and_cwd_independent():
    _assert_cwd_independent("STATE_FILE", rps.STATE_FILE)


def test_state_file_exists_from_project_root():
    """STATE_FILE must resolve to the real portfolio state file — highest-stakes path."""
    assert rps.STATE_FILE.exists(), (
        f"STATE_FILE not found from project root — "
        f"portfolio state missing at {rps.STATE_FILE}"
    )


def test_backup_dir_is_absolute_and_cwd_independent():
    _assert_cwd_independent("BACKUP_DIR", rps.BACKUP_DIR)
