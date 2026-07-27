"""
universe.py — Single source of truth for the live paper-trading universe.

Both paper_trading/signal_runner.py and validation/walk_forward.py import
STOCKS from here. Previously each file declared its own STOCKS list, kept
in sync only by validation/add_validated_stock.py regex-rewriting both
files' source text. A ticker added via add_validated_stock.py now needs
one edit, not two.

walk_forward.py excludes a small number of these tickers from WF validation
(insufficient historical data) — see WF_EXCLUDED in validation/walk_forward.py.
That's a deliberate, documented exception, not something this list should
try to encode.

UPDATE THIS LIST whenever the live universe changes. Each addition should
be WF-gate validated first (see validation/add_validated_stock.py).
"""

from typing import List

STOCKS: List[str] = [
    "BAJAJ-AUTO.NS",
    "HCLTECH.NS",
    "COLPAL.NS",
    "ANURAS.NS",       # live since before WF gating existed — insufficient
                       # history for WF validation (~440 IS bars, listed post-2019)
    "BSOFT.NS",
    "PERSISTENT.NS",
    "CHOLAHLDNG.NS",   # WF validated Jul 8 2026 — 5/6 original OOS +15.4%, 5/6 extended OOS +9.1%
    "COHANCE.NS",      # WF validated Jul 9-10 2026 — 6/6 original OOS +8.0%, extended SKIPPED (insufficient data, only 20 bars in 2015-19 window)
    "MAPMYINDIA.NS",  # WF validated Jul 12 2026 — 5/6 original OOS +7.0%, extended SKIPPED (insufficient data, no bars in 2015-19 window)
    "EMAMILTD.NS",    # WF validated Jul 17 2026 — 6/6 original OOS +9.0%, 5/6 extended OOS +13.6%
]
