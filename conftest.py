"""
conftest.py — pytest collection policy for this repo.

`collect_ignore` below excludes two root-level files that LOOK like tests but
are manual verification scripts: they define zero test functions and run their
checks at MODULE level, so merely importing them executes the script.

Why this must be committed config rather than a local rename
------------------------------------------------------------
Both files are listed in .gitignore ("Test files with sensitive output") and
are untracked — they exist on the dev machine and the Lightsail server but in
no clone. Renaming them would therefore fix one machine and silently drift
from the other. A committed conftest.py applies everywhere the repo is checked
out, whether or not those local files happen to be present.

What went wrong without it (2026-08-22)
---------------------------------------
`test_kite_fetcher.py` calls kite_get_ohlcv() and yf_get_ohlcv() at module
level. pytest imports every `test_*.py` during collection, so a plain
`python -m pytest` fired LIVE Kite and yfinance requests on every run. When
Kite returned PermissionException, that surfaced as a COLLECTION error, which
aborts the whole session before any real test runs — all 387 tests blocked by
a file contributing zero of them.

`test_kite.py` is the same shape: no test functions, and its module body
prints a live TOTP code derived from ZERODHA_TOTP_SECRET straight into test
output. Excluded here for both reasons.

validation/add_validated_stock.py already passes the equivalent --ignore flags
to its own hard-gate subprocess. Those are left in place as defence in depth;
this file extends the same policy to every other invocation, including the
plain `python -m pytest` that README.md and CLAUDE_CONTEXT.md document.

To run either script deliberately:
    python test_kite_fetcher.py
    python test_kite.py
"""

collect_ignore = [
    "test_kite.py",
    "test_kite_fetcher.py",
]
