r"""
validation/test_health_check_ast_scan_audit.py — Finding #15.

Check 6 was a line regex, `=\s*Path\(['"](?!/|__)`, matching only an ASSIGNMENT
wrapping Path(). Finding #4 found two relative-path DEFAULT PARAMETERS it could
not see, while it reported "PASS — 0 relative-path constants found in 50 scanned
files". A green check over a class it cannot detect.

These tests pin the closed gap. The regression test that matters most scans the
REAL pre-fix source of both Finding #4 sites, taken from git history, rather than
a synthetic sample that happens to have the right shape.

NEW FILE — does not modify or overwrite any existing test. No network calls.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import validation.system_health_check as shc  # noqa: E402


def _scan(tmp_path, source, name="mod.py"):
    f = tmp_path / name
    f.write_text(source)
    with patch.object(shc, "_discover_scan_files", return_value=[f]):
        return shc.check_relative_path_constants()


# ── The gap Finding #4 walked through ──────────────────────────────────────

def test_relative_default_parameter_is_now_caught(tmp_path):
    """The exact shape the regex missed: a bare relative string as a default."""
    r = _scan(tmp_path, 'def f(state_path: str = "paper_trading/portfolio_state.json"):\n    pass\n')
    assert r.status == "WARN", "relative default parameter not detected"
    assert "portfolio_state.json" in r.details["hits"][0]


def test_keyword_only_default_is_caught(tmp_path):
    """
    The first draft of this scan zipped only args[-len(defaults):] and would
    have missed kwonlyargs entirely. One function in the repo uses them.
    """
    r = _scan(tmp_path, 'def f(*, cfg: str = "utils/news_flags.json"):\n    pass\n')
    assert r.status == "WARN", "keyword-only default not detected"


def test_open_with_relative_literal_is_caught(tmp_path):
    r = _scan(tmp_path, 'def f():\n    return open("paper_trading/amo_orders.csv")\n')
    assert r.status == "WARN"


def test_the_original_path_assignment_shape_still_caught(tmp_path):
    """No regression: the AST scan must be a superset of the regex it replaced."""
    r = _scan(tmp_path, 'SOME_FILE = Path("utils/some.json")\n')
    assert r.status == "WARN"


# ── Regression against the real historical source ──────────────────────────

@pytest.mark.parametrize(
    "path,symbol",
    [("paper_trading/correlation_check.py", "portfolio_state_path"),
     ("paper_trading/paper_portfolio.py",   "state_file")],
)
def test_scan_catches_the_real_pre_fix_finding_4_sites(path, symbol):
    """
    Scans the ACTUAL pre-fix source from git (commit 90fd7fa, the last commit
    before fix/correlation-cli-path), not a synthetic sample. If the scan cannot
    catch the real thing it was built for, nothing else here matters.
    """
    src = subprocess.run(
        ["git", "-C", str(_ROOT), "show", f"90fd7fa:{path}"],
        capture_output=True, text=True,
    ).stdout
    if not src:
        pytest.skip("git history for 90fd7fa unavailable in this checkout")
    hits = shc._relative_path_hits(src, path)
    assert any(symbol in h for h in hits), f"pre-fix {symbol} not caught; hits={hits}"


def test_current_tree_is_clean():
    """The two sites are fixed, so a scan of the real repo must be green."""
    hits = []
    for f in shc._discover_scan_files():
        try:
            hits.extend(shc._relative_path_hits(f.read_text(), f))
        except (SyntaxError, UnicodeDecodeError):
            pass
    assert hits == [], f"unexpected relative-path hits in the current tree: {hits}"


# ── No false positives, without any docstring-masking machinery ────────────

@pytest.mark.parametrize("src", [
    'FOO = Path("/absolute/path")\n',
    'FOO = Path(__file__).parent / "data"\n',
    'def f(url: str = "https://example.com/api/v1"):\n    pass\n',
    'def f(fmt: str = "%Y/%m/%d"):\n    pass\n',
    'def f(tpl: str = "{base}/{name}.json"):\n    pass\n',
    'def f(side: str = "delivery"):\n    pass\n',
])
def test_non_paths_are_not_flagged(tmp_path, src):
    assert _scan(tmp_path, src).status == "PASS", f"false positive on: {src!r}"


def test_docstrings_and_comments_need_no_masking(tmp_path):
    """
    The regex needed _masked_spans() to avoid matching its own docstring. The
    AST cannot make that mistake: a docstring is not a Path() argument and not a
    parameter default, so there is nothing to mask. _masked_spans is gone.
    """
    src = (
        '"""Example: FOO = Path("utils/some.json") — this is prose."""\n'
        '# FOO = Path("utils/other.json")   <- a comment\n'
        'SAMPLE = \'FOO = Path("utils/third.json")\'   # string test data\n'
        'REAL = Path(__file__).parent / "data"\n'
    )
    assert _scan(tmp_path, src).status == "PASS"
    assert not hasattr(shc, "_masked_spans"), "_masked_spans should be gone"


# ── Unparseable files are reported, not silently dropped ───────────────────

def test_unparseable_file_is_reported_not_silently_skipped(tmp_path):
    """
    An AST scan cannot read a file that does not parse, where the old regex
    would still have scanned it line-by-line. Skipping silently would trade one
    blind spot for another, so the count is surfaced.
    """
    r = _scan(tmp_path, 'def (((:\nFOO = Path("relative/x")\n', name="broken.py")
    assert r.status == "WARN"
    assert "could not be parsed" in r.message
    assert r.details["unparseable"], "unparseable files must be listed"


# ── Dict-literal values: AMO_CONFIG["order_log_file"], the motivating case ──

def test_dict_literal_value_under_a_path_naming_key_is_caught(tmp_path):
    """
    The ORIGINAL case Check 6 was built for, and the one it never covered: a
    dict value is neither an assignment wrapping Path() nor a parameter default.
    """
    r = _scan(tmp_path, 'AMO_CONFIG = {\n    "order_log_file": "paper_trading/amo_orders.csv",\n}\n')
    assert r.status == "WARN", "dict-literal path value not detected"
    assert "amo_orders.csv" in r.details["hits"][0]


@pytest.mark.parametrize("src", [
    'H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}\n',
    'H = {"Content-Type": "application/x-www-form-urlencoded"}\n',
    'H = {"type": "text/html"}\n',
    'H = {"Accept": "application/json"}\n',
    'L = {"AGREE_AVOID": "OK/  AGREEMENT — AVOID SMA / use mean reversion"}\n',
])
def test_dict_values_under_non_path_keys_are_not_flagged(tmp_path, src):
    """
    The key gate is load-bearing, not cosmetic. Measured on the real repo,
    ungated "any dict value that looks like a path" produced 25 hits, ALL noise
    of exactly these shapes; gated it produces 0.
    """
    assert _scan(tmp_path, src).status == "PASS", f"false positive on: {src!r}"


def test_dict_literal_inside_a_docstring_is_not_flagged(tmp_path):
    """The new shape must inherit the same docstring/comment immunity."""
    src = (
        '''"""Example config: {"order_log_file": "paper_trading/amo_orders.csv"}."""\n'''
        '# CFG = {"order_log_file": "paper_trading/amo_orders.csv"}\n'
        'SAMPLE = \'{"order_log_file": "paper_trading/amo_orders.csv"}\'\n'
    )
    assert _scan(tmp_path, src).status == "PASS"


# ── Backtest against the real history that motivated this check ────────────

@pytest.mark.parametrize("commit,path,needle", [
    # AMO_CONFIG["order_log_file"] — the original motivating case
    ("cb678a1", "paper_trading/signal_runner.py", "order_log_file"),
    ("3ed2966", "paper_trading/signal_runner.py", "order_log_file"),
    # the sharpest case: Path() constants ALREADY fixed here, dict value still
    # relative, old regex reported 0 hits over it
    ("c6ec08a", "paper_trading/signal_runner.py", "order_log_file"),
    # news_flags.json — the Jul 17 relative-path incident
    ("3ed2966", "paper_trading/signal_runner.py", "news_flags.json"),
    # Finding #4's two function defaults
    ("90fd7fa", "paper_trading/correlation_check.py", "portfolio_state_path"),
    ("90fd7fa", "paper_trading/paper_portfolio.py", "state_file"),
])
def test_backtest_against_real_pre_fix_source(commit, path, needle):
    """
    Would this scanner have caught the bugs that motivated building it? Run
    against the ACTUAL pre-fix source from git, not synthetic fixtures.
    """
    src = subprocess.run(
        ["git", "-C", str(_ROOT), "show", f"{commit}:{path}"],
        capture_output=True, text=True,
    ).stdout
    if not src:
        pytest.skip(f"git history for {commit} unavailable in this checkout")
    hits = shc._relative_path_hits(src, path)
    assert any(needle in h for h in hits), (
        f"{commit}:{path} — {needle} not caught; hits={hits}"
    )
