# System Audit & Stress Test — NSE Algo Trading System
**Date:** 2026-08-22
**Scope:** Full Component Map per `README.md` — Data → Strategy → Risk & Execution → Paper Trading → Utilities → Validation → Auth
**Method:** read intended behaviour from `README.md` / `CLAUDE_CONTEXT.md` / docstrings, read the implementation, then write new edge-case tests to find what the existing suite does not cover.

---

## Baseline

```
$ source venv/bin/activate && python -m pytest -v
======================== 313 passed in 72.21s (0:01:12) ========================
```

313 passed, 0 failed, 0 errors, 0 skipped.
(`CLAUDE_CONTEXT.md` last recorded 202 — the suite has grown by 111 since Jul 19.)

## After adding this audit's tests

```
$ python -m pytest -q
23 failed, 364 passed in 73.92s (0:01:13)
```

**All 313 pre-existing tests still pass.** 74 new tests added across 5 new files; 51 pass, 23 fail — every failure asserts a confirmed defect documented below.

New files (no existing test was edited or overwritten):
- `paper_trading/test_morning_fill_check_audit.py` — 3 tests, **3 fail**
- `paper_trading/test_paper_portfolio_audit.py` — 18 tests, **1 fail**
- `paper_trading/test_concurrency_audit.py` — 15 tests, **1 fail**
- `data/test_data_edge_audit.py` — 23 tests, **4 fail** (one parametrized defect)
- `paper_trading/test_dry_run_contract_audit.py` — 15 tests, **14 fail** (findings #1 and #1b; all 15 pass under the proposed patch)

**Guardrails observed.** `git status --short` shows only the 4 new untracked test files. No tracked file modified; `portfolio_state.json`, `.env`, `logs/`, `state_backups/`, `amo_orders.csv`, `signal_log.csv`, `news_flags.json` all untouched. Zero live Kite or NSE network calls — every fetch is monkeypatched, every state file is a `tmp_path` copy. No fix in this report has been applied.

---

# PRIORITISED FINDINGS

| # | Severity | Component | Finding |
|---|----------|-----------|---------|
| 1 | **CRITICAL** | Paper Trading | Dry-run `morning_fill_check.py` silently destroys pending AMO fills |
| 1b | **HIGH** | Paper Trading | Every `GAP_EXIT` is recorded in `amo_orders.csv` as `MISSED` with no fill price |
| 2 | **HIGH** | Paper Trading | ETF cash gate credits an unwind `rebalance_etf()` refuses to perform |
| 12 | **HIGH** | Paper Trading | `CANCELLED_CA` strands a position — BUY frozen, SELL can never exit |
| 3 | **MEDIUM** | Risk & Execution | `PositionSizer` returns −1 shares; bypasses `shares == 0` skip, bricks the ticker |
| 4 | **MEDIUM** | Paper Trading | `correlation_check.py` CLI default path is cwd-dependent, fails open silently |
| 5 | **MEDIUM** | Paper Trading | `validate_state_integrity()` counts un-funded `pending_buy` in the 50% floor |
| 6 | **MEDIUM** | Paper Trading | Daily report: `cash + invested ≠ total` when a BUY is pending |
| 7 | **MEDIUM** | Utilities | `NSE_HOLIDAYS_2027` empty — scheduled pre-warm due Nov 2026 |
| 8 | **LOW** | Validation | `check_universe_consistency()` is tautological — cannot ever fail |
| 9 | **LOW** | Data | No duplicate / out-of-order timestamp detection anywhere in the pipeline |
| 10 | **LOW** | Paper Trading | `close_position()` `ZeroDivisionError` on `entry_price == 0` after crediting cash |
| 11 | **LOW** | Paper Trading | `PaperPortfolio.state_file` default is a relative path (dead in production) |

**Read items 1–3 first.** Items 4–11 are real but bounded.

---

# 1. CRITICAL — Dry-run `morning_fill_check.py` silently destroys pending AMO fills

**Component:** Paper Trading Layer → `paper_trading/morning_fill_check.py`

### Intended behaviour
Module docstring, lines 17–18:
> *"Dry-run mode (the default): portfolio state is NOT modified — just shows what would have happened. Set `DRY_RUN_PORTFOLIO = False` only after confirming the logic is correct on a few real sessions."*

Report header, line 516, prints `MODE: DRY RUN — portfolio state will NOT be modified`.
`--help` epilog advertises the dry-run form as the primary usage.

### Actual behaviour
The **portfolio** write is guarded by `if apply_fills:`. The **AMO ledger** write is not.

```python
612:            if apply_fills:
613:                _update_portfolio_fill(...)                       # guarded ✅
619:            _update_csv_row(order_date, ticker, order_type,
620:                            "FILLED", str(round(open_px, 4)), today.isoformat())   # UNGUARDED ❌
```

Four unguarded call sites: line **576** (`CANCELLED_CA`), **619** (`FILLED`), **627** (`REJECTED`/`CANCELLED`), **645** (`MISSED`).

### Root cause
`_load_pending_orders()` (line 113) selects work by `row["status"] == "DRY_RUN"`. A dry run flips that field without updating the portfolio. The order is then **permanently invisible** to every subsequent run, including the 9:20 AM cron `--apply` pass.

### Tests
| Test | Result |
|---|---|
| `test_dry_run_does_not_mark_filled_order_as_filled_in_csv` | **FAIL** — status became `FILLED` |
| `test_dry_run_does_not_mark_missed_order_as_missed_in_csv` | **FAIL** — status became `MISSED` |
| `test_dry_run_then_apply_still_confirms_the_buy_fill` | **FAIL** — `pending_buy` still `True`, cash still ₹100,000.00 |

### Failure mode
A BUY consumed by a dry run leaves the position at `pending_buy=True`, `shares=N`, **cash never deducted**. `_process_stock()` returns early on `pending_buy` (line 610), so the stock emits `PENDING_BUY` forever and **no risk management ever runs on it** — no chandelier, no hard stop, no time stop. A SELL consumed the same way leaves `pending_rm_exit=True` with the position still open and no order left to reconcile it.

### Real-world trigger
Not an exotic input — the exact commands the module documents:
```
python paper_trading/morning_fill_check.py                   # dry run, yesterday's orders
python paper_trading/morning_fill_check.py --date 2026-06-05 # check a past date
```
Any operator inspecting fills before the cron fires, or re-checking a past date to investigate something, destroys that date's pending fill. The `--date` form is the natural tool for auditing history, and it consumes orders for whatever date it lands on.

This is the mirror image of the retracted `confirm_buy_fill()` incident: there the *state* mutated without persisting; here the *ledger* persists without the state.

### Proposed fix (not applied)
```diff
--- a/paper_trading/morning_fill_check.py
+++ b/paper_trading/morning_fill_check.py
@@ -573,7 +573,8 @@
                 f"| ex-date today — fill skipped"
             )
-            _update_csv_row(order_date, ticker, order_type, "CANCELLED_CA", "", "")
+            if apply_fills:
+                _update_csv_row(order_date, ticker, order_type, "CANCELLED_CA", "", "")
             continue
@@ -616,8 +617,9 @@
                     portfolio_obj=_portfolio,
                 )
-            _update_csv_row(order_date, ticker, order_type,
-                            "FILLED", str(round(open_px, 4)), today.isoformat())
+            if apply_fills:
+                _update_csv_row(order_date, ticker, order_type,
+                                "FILLED", str(round(open_px, 4)), today.isoformat())
 
         elif result["status"] in ("REJECTED", "CANCELLED"):
@@ -624,7 +626,8 @@
-            _update_csv_row(order_date, ticker, order_type, result["status"], "", "")
+            if apply_fills:
+                _update_csv_row(order_date, ticker, order_type, result["status"], "", "")
 
         else:
@@ -643,7 +646,8 @@
-            _update_csv_row(order_date, ticker, order_type, "MISSED", "", "")
+            if apply_fills:
+                _update_csv_row(order_date, ticker, order_type, "MISSED", "", "")
```
This only adds guards — it does not reorder, so the Jul 18 portfolio-before-CSV hardening is preserved.

---

# 1b. HIGH — Every `GAP_EXIT` is recorded in `amo_orders.csv` as `MISSED`

**Component:** Paper Trading Layer → `paper_trading/morning_fill_check.py:645` + `:682`
**Found while building the fix for #1** — same code path, independent defect.

### Intended behaviour
`CLAUDE_CONTEXT.md` (Finding #11, Jun 26):
> *"GAP_EXIT path: closes position at open, records in trade_log, does not requeue"*

The AMO ledger should record the order as `GAP_EXIT` with the actual fill price and date.

### Actual behaviour
`_update_csv_row()` only matches rows still at `DRY_RUN`:
```python
352:            if (row["date"] == target_date and row["ticker"] == ticker
353:                    and row["order_type"] == order_type
354:                    and row["status"] == "DRY_RUN"):
```
The SELL-miss path writes `MISSED` **first** (line 645, before the gap-exit branch is even evaluated), then the gap-exit branch attempts to write `GAP_EXIT` (line 682). By then the row is `MISSED`, not `DRY_RUN`, so **the second write silently matches nothing.**

Reproduced against the live code:
```
final status    : MISSED
final fill_price: (empty)
final fill_date : (empty)
EXPECTED: GAP_EXIT / 950.0 / 2026-08-21  (position WAS closed at the open)
```

### Root cause
Two sequential writes to a single mutable `status` column, where the first write invalidates the matcher the second one depends on. A symptom of the same design flaw behind #1: one mutable column serving as ledger, work queue, and fill record simultaneously.

### Blast radius
The **portfolio is correct** — `trade_log` records `GAP_EXIT` with the right price, cash and P&L are right. Only the order ledger is wrong. So this is not a money error; it is **audit-trail corruption**.

That still matters here specifically. `amo_orders.csv` is what this project actually reaches for during forensics — the Aug 4 BAJAJ-AUTO reconciliation explicitly cross-checked `trade_log` against *"amo_orders.csv's SELL fill for the same date"*. After any gap-exit, those two records **disagree**: the trade log says the position closed at ₹940, the order ledger says the order was missed and never filled. An investigator applying the Aug 4 method to a gap-exit would conclude a trade had been lost — the precise false-positive that file's own retraction warns about.

Rated HIGH, not CRITICAL: no wrong cash, position, or P&L. Rated HIGH, not MEDIUM: a 3%+ gap-down on an NSE mid-cap is an ordinary market event, and the corruption is silent and permanent.

### Test
`test_gap_exit_is_recorded_as_gap_exit_not_missed` in `paper_trading/test_dry_run_contract_audit.py` — **FAIL** on current code, **PASS** under the proposed patch.

### Fix
Closed structurally by the Phase 1 patch for #1 (`fix_01_dry_run_contract.patch`): a `FillDecision` carries exactly one `csv_status`, written exactly once, after the portfolio update. No sequence of writes can invalidate a later matcher because there is no sequence.


# 12. HIGH — `CANCELLED_CA` strands the position it belongs to

**Component:** Paper Trading Layer → `paper_trading/morning_fill_check.py`
**Found:** while answering why `CANCELLED_CA` is excluded from the manual-attention section during review of `fix_01_dry_run_contract.patch`. **Not caused by that patch and not fixed by it** — verified byte-identical behaviour on baseline HEAD and on the patched tree.

### Intended behaviour
**There is no documented intent for the position-state side, and I am not inventing one.** The only statement anywhere is `README.md:78` — *"Corporate actions — cancel fills if ex-date today"* — which describes cancelling the **fill**. The module docstring says nothing about cancellation at all (`grep` over lines 1–26 returns no match), and `CLAUDE_CONTEXT.md` has no entry predating this finding.

The standard being applied instead is **internal consistency**: every other terminal outcome in `execute_decision()` resolves its position. `FILL` → `confirm_buy_fill`/`close_position`; `GAP_EXIT` → `close_position`; `MISS_CANCEL_BUY` and `REJECT` → `cancel_pending_buy()`; `REQUEUE_SELL` → `requeue_rm_sell()`. `CANCEL_CA` is the only terminal outcome that resolves nothing.

### Actual behaviour
`plan_fill()` returns a bare decision — [morning_fill_check.py:588-596](paper_trading/morning_fill_check.py#L588):
```python
588:    if ex_today.get(ticker, False):
589:        return FillDecision(
590:            order=order, action="CANCEL_CA", csv_status="CANCELLED_CA",
591:            is_rm_exit=is_rm_exit, notes=notes, status="CANCELLED_CA",
...
```
`execute_decision()` has **no `CANCEL_CA` branch**. It falls through every `if/elif` and reaches only the ledger write:
```python
744:    if d.action == "FILL":            ...
750:    elif d.action == "GAP_EXIT":      ...
756:    elif d.action in ("MISS_CANCEL_BUY", "REJECT"):   ...
766:    elif d.action == "REQUEUE_SELL":  ...
                                          # ← no CANCEL_CA case
770:    if d.csv_status:                  # ledger marked terminal anyway
```

### Root cause
The ledger write is unconditional on `d.csv_status`, but position resolution is dispatched per-action. An action that sets `csv_status` without appearing in the dispatch chain therefore marks the order **terminal** — invisible to `_load_pending_orders()`, which selects on `status == "DRY_RUN"` — while leaving the position exactly as it was.

### Tests — `paper_trading/test_corp_action_cancel_audit.py`
| Test | Baseline | Patched |
|---|---|---|
| `test_cancelled_ca_buy_resets_position_to_flat` | **FAIL** | PASS |
| `test_cancelled_ca_buy_ledger_row_is_terminal_so_nothing_can_reprocess` | PASS | PASS |
| `test_cancelled_ca_sell_requeues_so_the_position_can_still_exit` | **FAIL** | PASS |
| `test_cancelled_ca_sell_preserves_the_three_attempt_cap` | **FAIL** | PASS |
| `test_every_cancel_ca_decision_is_handled_by_execute_decision` | **FAIL** | PASS |

Baseline messages, verbatim:
```
AssertionError: pending_buy still True after CANCELLED_CA — ticker is frozen:
_process_stock() returns PENDING_BUY early every run, so no signal, no risk
management, and no AMO will ever be emitted for it again
AssertionError: no re-queued SELL AMO was written — the position is stranded
with pending_rm_exit=True, shares=10, and no order that can ever close it
assert 3 == 4
AssertionError: CANCELLED_CA action(s) ['CANCEL_CA'] produce a terminal ledger
write but are not dispatched in execute_decision()
```
The second test passing on baseline is deliberate — it is the supporting evidence that the ledger row really is terminal, which is what makes the strand permanent.

### The two failure modes differ
**BUY — ticker frozen.** `pending_buy` stays `True`. `_process_stock()` returns early on it every run, so the ticker produces no signal, gets no risk management, and emits no AMO. Identical outcome to Finding #3, reached by a different route. Bounded: no capital is committed, since `queue_pending_buy()` never deducts cash.

**SELL — no code path can close the position.** `pending_rm_exit` stays `True` with `shares > 0`. Verified by reading both sides:
- `morning_fill_check` sees no `DRY_RUN` row — it was marked `CANCELLED_CA`.
- `signal_runner`'s `pending_rm_exit` branch runs `check_exit()` and `update_rm_state()`, then returns `PENDING_RM_EXIT`. A `grep -c "needs_amo_order"` over that branch returns **0**, and Step 13 emits an order only where `needs_amo_order` is set.

So the risk manager keeps ratcheting a chandelier stop and incrementing `bars_held` for a position that has no mechanism to sell. This is Finding #2 (Jun 19 2026, "Orphaned RM SELL position") reappearing through the corporate-action path — the Jun 19 `requeue_rm_sell()` fix covered the *missed-fill* route only, because the corporate-action cancel did not exist yet when it was written.

### Realistic trigger — confirmed reachable, not asserted
`utils/corporate_actions.py` fails open at the source:
```python
    except Exception as exc:
        print(f"  [CORP_ACTION] WARNING: NSE API unavailable for {ticker} — {exc}. "
              f"Proceeding without corporate action check.")
        return no_action          # skip=False
```
Sequence: NSE transiently unavailable at the 3:45 PM signal run → `skip=False` → AMO queued. NSE healthy at 9:20 AM → ex-date correctly detected → `CANCELLED_CA` → strand.

Worth noting *why* NSE must fail for this to fire: the signal-time danger window is `{check_date, next_1, next_2}` — three dates — so an ex-date on the fill day would normally be caught at 3:45 PM and the stock skipped. The fail-open path is what lets it through. That makes this **conditional on an NSE outage**, not a routine occurrence. A second, rarer route also exists: NSE publishing or amending an ex-date between 3:45 PM and 9:20 AM.

### Severity — **HIGH**, and I considered CRITICAL
The rubric's CRITICAL band is *"silent incorrect state (wrong P&L, wrong position, wrong cash) or data corruption that wouldn't be noticed without this audit."*

The SELL side is the strongest case for CRITICAL: a position with no code path that can ever close it is worse than a frozen ticker, because a frozen ticker has a floor on its cost — no capital is committed — whereas a stranded open position is fully exposed to the market with a stop that cannot fire. That is genuinely a stronger claim than Finding #3's.

I am still calling it **HIGH**, for two reasons:

1. **No value is silently wrong.** Cash, `shares`, `entry_price`, `trade_log` and P&L all remain correct and internally consistent. The defect is a stuck state machine, not corrupted state. Finding #1 was CRITICAL because a fill silently vanished and the recorded portfolio diverged from what actually happened; nothing diverges here.
2. **It is not silent.** The position appears as `PENDING_RM_EXIT` in the daily report every single day, with its chandelier level printed. It is visible to anyone reading the report — unlike Finding #1, which left no trace anywhere.

The trigger also requires an NSE outage to coincide with an ex-date on a stock holding an open pending order, which is materially narrower than Finding #1's "run the documented command."

**Where I would revise this:** if the daily report is not actually being read day-to-day, argument 2 weakens sharply, and the recurring `PENDING_RM_EXIT` line becomes indistinguishable from a normal one-day pending exit. `CLAUDE_CONTEXT.md` already records exactly this failure pattern — the Aug 22 EMAMILTD.NS removal went unreviewed for four screener cycles because REMOVE recommendations "have no console/log output in a real run." If `PENDING_RM_EXIT` lines get the same treatment, this is CRITICAL in practice.

### Fix — `fix_12_corp_action_cancel.patch`
Reuses both existing seams; no new machinery, no new threshold.

- **BUY** → `cancel_pending_buy()`, identical to the `MISS_CANCEL_BUY`/`REJECT` handling.
- **SELL** → `requeue_rm_sell()` via the existing `_requeue_sell_amo()` helper.

**On whether `requeue_rm_sell()` is semantically valid here** — checked before reusing. Its two runtime guards are `pending_rm_exit == True` and `shares > 0`; `CANCEL_CA` leaves exactly that state, so the **state contract fits exactly**. Its *docstring* was narrower than its guards ("Only call this after a confirmed MISSED RM SELL"), and a corporate-action cancel is not a miss. Rather than call it outside its documented contract or fork a near-duplicate method, the patch **widens the docstring** to name both callers and explain why they share the counter. The 3-requeue-then-CRITICAL cap is preserved unchanged and is tested.

One consequence worth stating: a CA cancel now consumes one of the three retry attempts. That is slightly conservative — an ex-date is a scheduled, self-resolving, one-day event, unlike the liquidity or circuit problems the cap was designed for — so it alerts marginally earlier than strictly necessary. I judged a shared counter better than a parallel one; a second threshold would need a new state field, a migration, and its own tests to guard a rarer case.

The SELL path adds **one** `_fetch_close_price()` call, only on an ex-date SELL, paced at 1.1s by the rate-limiting fix. Today's close is already post-adjustment, so it is the correct basis for tomorrow's limit — the same basis the MISSED-SELL requeue uses.

### Manual-attention section — deliberately unchanged
Working through which case is which:
- **Routine cancel** (BUY reset to flat, SELL requeued): now a *handled* outcome. Log line only. Adding `CANCELLED_CA` to the section's `("REJECTED", "CANCELLED")` filter would surface every routine cancel as noise.
- **Retry cap exhausted**: `requeue_rm_sell()` already prints a 🚨 CRITICAL block with `MANUAL ACTION REQUIRED`. That fires automatically regardless of cancel reason — tested.
- **Close fetch fails**, so the SELL cannot be requeued: this is still a genuine strand. `plan_fill()` renders an explicit `MANUAL ACTION REQUIRED: position is still open with no exit order.` line, matching the existing MISSED-SELL treatment of the same condition.

Both genuinely-bad cases already produce loud, distinct output. The string filter stays as-is.

### Verification
```
strand tests, BASELINE HEAD : 4 failed, 1 passed
strand tests, PATCHED       : 5 passed
full suite, PATCHED         : 349 passed, 0 failed
```
349 = the server's 344 baseline + 5 new. `git apply --check` clean against the real tree. Verified in a throwaway worktree; nothing applied, committed, pushed, or deployed.

### Addendum (Aug 25 2026) — "unmanaged SELL" sub-case confirmed unreachable
*Appended after the section above was reviewed and approved; the original text is unchanged.*

Raised in review of the patch: the SELL-side fix only fetches a close price and requeues when the order is *managed* (`is_rm_exit or notes_base in _STRATEGY_EXIT_NOTES`). If a SELL could ever fail both tests, `close_px` stays `None`, `_requeue_sell_amo()` silently no-ops, and — unlike the failed-close-fetch case — no `MANUAL ACTION REQUIRED` line is rendered. That would be this very defect surviving in one narrow sub-case of its own fix.

Confirmed unreachable by tracing the call graph, not by inference from documentation:

1. **Exactly two `place_sell_amo()` call sites exist repo-wide.** `morning_fill_check.py:755` passes `d.notes + " [REQUEUED]"`, preserving the original notes — and `notes_base` strips the suffix via `.split(" [")[0]`, so a requeued order round-trips to the same classification. The origin is `signal_runner.py:1612`, `notes=r.get("exit_reason", "STRATEGY_SIGNAL")`.
2. **Exactly two result blocks set `needs_amo_order: "SELL"`** — `signal_runner.py:702` (`RISK_EXIT`, `exit_reason` taken from `exit_decision`) and `:743` (`SELL`, the literal `"STRATEGY_SIGNAL"`).
3. **RiskManager emits `exit_reason` only as** `HARD_STOP` (`risk_manager.py:236`), `CHANDELIER` (`:278`), or `TIME_STOP` (`:291`).
4. **The one `None` cannot leak.** `risk_manager.py:299` returns `exit_reason: None`, but only on the `should_exit: False` branch, and `signal_runner.py:690` reads `exit_reason` exclusively inside `if exit_decision["should_exit"]`. This mattered: a `None` *value* would have defeated `.get()`'s `"STRATEGY_SIGNAL"` default — `.get()` only substitutes a default for a *missing key*, not a present-but-`None` one — and produced exactly the unmanaged SELL in question.

Union of reachable notes = `{HARD_STOP, CHANDELIER, TIME_STOP, STRATEGY_SIGNAL}`, which is precisely `_RM_EXIT_NOTES | _STRATEGY_EXIT_NOTES`. `is_managed` is therefore always `True` and the unmanaged branch is defensive only — the same status as `REJECTED`/`CANCELLED` under `LIVE_TRADING_MODE=False`.

**Residual risk, stated rather than dismissed:** this is a property of the current call graph, not an invariant the code enforces. A future SELL-AMO call site passing a note outside those four strings would make the branch live and reintroduce a silent strand. `test_every_cancel_ca_decision_is_handled_by_execute_decision` does not cover this — it checks dispatch coverage, not note vocabulary. Closing it properly would mean asserting the note vocabulary at the `place_sell_amo()` seam; not done here, and not required while only two call sites exist.

---

# 2. HIGH — ETF cash gate credits an unwind `rebalance_etf()` refuses to perform

**Component:** Paper Trading Layer → `paper_trading/paper_portfolio.py`, `paper_trading/signal_runner.py`

### Intended behaviour
Stated as an invariant twice. `_etf_rebalance_delta_shares()` docstring:
> *"This is the single source of the delta math — both the real unwind (`rebalance_etf`) and the cash-gate estimate (`projected_tier_unwind_cash`) call it, **so the check and the action can never disagree**."*

`signal_runner.py:1398`:
> *"Estimate the cash that unwind would free via the SAME plan the real unwind uses below, so the check and the action can never disagree."*

### Actual behaviour
They disagree by the full amount. Both call `_etf_rebalance_delta_shares()`, but `rebalance_etf()` applies an **additional guard the estimator does not**:

```python
790:        if new_tier == old_tier:
791:            return            # ← projected_tier_unwind_cash() has no equivalent
```

Reproduced (1 open position, `etf_tier=0.8`, 400 NIFTYBEES @ ₹245, cash ₹1,000):
```
GATE  credits unwind cash : Rs 14700.0
ACTUAL cash freed         : Rs 0.0
ACTUAL etf shares sold    : 0
DISAGREEMENT              : Rs 14700.0
```

### Root cause
`_etf_rebalance_delta_shares()` derives its delta from *current ETF value vs target value*, which is non-zero whenever the ETF has drifted inside its tier. `rebalance_etf()` deliberately suppresses intra-tier drift correction ("Only rebalance on a tier CHANGE — never on intra-tier drift"). The estimator inherits the drift-driven delta but not the suppression.

**The trigger is structural, not incidental.** `ETF_TIERS = {0:1.0, 1:0.8, 2:0.8, 3:0.5, 4:0.0}` — tiers 1 and 2 are *equal*. Every second same-day candidate projects `1→2`, i.e. `0.8 → 0.8`, hits the early return, and frees ₹0. Meanwhile drift accumulates permanently *because* rebalance only fires on tier change.

### Test
`test_gate_estimate_and_real_unwind_must_agree` — **FAIL**: `cash gate credited Rs14,700 but rebalance_etf() freed Rs0`

### Failure mode
The cash gate passes the candidate on phantom cash → the unwind no-ops → `_process_stock(defer_buy=False)` runs `PositionSizer` against the *real* ₹1,000 → `max_from_cash = 0` → `shares == 0` → line 826 records `signal: "HOLD"`, `reason: "Golden cross but sizing skipped — 0 shares"`.

Doubly bad for observability: the **top-ranked golden cross is logged to `signal_log.csv` as HOLD**, and because it never took a `continue` branch it is **never appended to `skipped_signals`** — so it does not appear in the SKIPPED rows either. A cash shortfall is recorded as a sizing anomaly.

This is the Jul 27 deadlock's signature — a correctly-ranked, correctly-gated signal silently not taken — re-entering through the estimator rather than through rebalance timing.

### Proposed fix (not applied)
```diff
--- a/paper_trading/paper_portfolio.py
+++ b/paper_trading/paper_portfolio.py
@@ -745,6 +745,13 @@ def projected_tier_unwind_cash(
         projected = self.committed_open_count() + additional_positions
         _tier, delta_shares = self._etf_rebalance_delta_shares(
             projected, niftybees_price, current_prices
         )
+        # rebalance_etf() no-ops unless the TIER changes (it never corrects
+        # intra-tier drift). Mirror that guard here or the gate credits cash the
+        # real unwind will refuse to free — note ETF_TIERS[1] == ETF_TIERS[2], so
+        # a 2nd same-day candidate always lands on this path.
+        if _tier == self.state["etf_tier"]:
+            return 0.0
         if delta_shares >= 0:
             return 0.0
```

**Secondary (decide separately):** when the sizer returns 0 shares in Phase 2, append that candidate to `skipped_signals` so it reaches `signal_log.csv` as `SKIPPED` rather than vanishing into `HOLD`.

---

### Addendum (Aug 26 2026) — re-derived, fixed, and one earlier claim corrected
*Appended after the section above was reviewed; the original text is unchanged.*

Re-derived against the current code (`a3c11c9`) rather than trusting the original read. The mechanism holds, but two things in the earlier description were wrong.

**The claim, quoted from where it actually lives.** `_etf_rebalance_delta_shares()`'s docstring, [paper_portfolio.py:701](paper_trading/paper_portfolio.py#L701):
> *"This is the single source of the delta math — both the real unwind (rebalance_etf) and the cash-gate estimate (projected_tier_unwind_cash) call it, so the check and the action can never disagree."*

**Where they diverge.** Both do share the delta math. They do not share the **decision**. `rebalance_etf()` adds a guard the estimator has no equivalent of:
```
40:        if new_tier == old_tier:
41:            return                     # never corrects intra-tier drift
```
`projected_tier_unwind_cash()` ([:747](paper_trading/paper_portfolio.py#L747)) goes straight from the shared delta to `if delta_shares >= 0: return 0.0`. Where the tier is unchanged but the ETF has drifted off target, the estimator credits drift-derived cash the action will never free.

**Root cause — confirmed, and it is the only disagreeing boundary.** Measured across every boundary (`ETF_TIERS = {0:1.0, 1:0.8, 2:0.8, 3:0.5, 4:0.0}`):
```
 N   cur  proj   GATE credits   ACTION frees   verdict
 0  1.00  0.80         17,885         17,885   AGREE
 1  0.80  0.80         10,045              0   DISAGREE by Rs10,045
 2  0.80  0.50         36,505         36,505   AGREE
 3  0.50  0.00         93,835         93,835   AGREE
```
Only `1 → 2` disagrees, because those two tiers are numerically equal. The coincidence is the *trigger*; the missing guard is the *cause*.

**Realistic trigger — narrower than first described.** Four conditions, all required:
1. Exactly **one** committed position (the sole equal-tier boundary).
2. **Cash below ₹1,000.** This one was missed originally and it matters most: the gate only *acts* on the credit when `cash < MIN_CASH_TO_ATTEMPT_BUY`. At or above ₹1,000 the gate passes regardless and the phantom credit is inert.
3. The ETF drifted off its 80% target — no drift, no phantom credit.
4. A second same-day candidate clearing rank, position-limit, news and correlation gates.

**Consequence — the earlier description was WRONG and is corrected here.** It was recorded as "silently drops a profitable signal." It does not. Traced end to end with cash ₹900:
```
gate credit (phantom)        : Rs10,045.00
gate: 10,945.00 < 1000 -> False  => PASSES
without the phantom credit   : would SKIP
rebalance_etf actually freed : Rs0.00
sizer -> shares = 0 (all_constraints_zero)  => HOLD "sizing skipped"
```
The trade was **never fundable** — the tier genuinely does not change at 1→2, so no correct behaviour funds it from the ETF. What the bug changes is the *record*: an accurate `SKIPPED — Insufficient cash ₹900` row in `signal_log.csv` becomes a misleading `HOLD — sizing skipped 0 shares`, and because no `continue` branch is taken it never reaches `skipped_signals` either. **No trade is lost; the reason is misreported.**

**The real teeth: this is the sole enabler of Finding #3.** Finding #3 (`PositionSizer` returning −1) needs `cash < buy_cost_1share`, roughly ₹1–3. The cash gate would normally skip long before that. Measured:
```
    cash   price  gate WITH bug  gate if FIXED  sizer shares
  900.00    1500           PASS           skip             0
    2.00    1500           PASS           skip             0
    1.00    1000           PASS           skip            -1
    0.50     300           PASS           skip             0
```
With the estimator fixed the gate skips first and the sizer never runs on sub-₹3 cash. **Fixing #2 closes #3's only reachable path**, without touching the sizer.

**Severity — MEDIUM, deliberately *not* matched to #12's HIGH.** Weighing the two failure shapes as asked:
- **#12** leaves an open position with **no code path that can close it** — real market exposure, a stop that cannot fire, unbounded downside while it persists.
- **#2** loses no money, corrupts no state, and drops no fundable trade. Cash, shares, ETF units and P&L all stay correct. It produces one misleading log line.

A misreported reason is strictly less severe than an un-exitable position, so matching #12's HIGH would be rating-by-association. MEDIUM also fits the rubric's own wording: "possible but requires an unusual sequence of events" — four conditions including sub-₹1,000 cash.

**Where I would revise upward:** the #3 linkage. If #3 is *not* fixed, #2 is the live precondition for permanently bricking a ticker, and the pair together behave like a HIGH. They are being fixed in order for that reason.

**Fix.** Mirror the action's guard in the estimator — no new tier logic, no duplicated math:
```python
        tier, delta_shares = self._etf_rebalance_delta_shares(...)
        if tier == self.state["etf_tier"]:
            return 0.0          # rebalance_etf() will no-op; promise nothing
        if delta_shares >= 0:
            return 0.0
```
Written against the **tier**, not against `n == 1`, so it survives a future retuning of `ETF_TIERS`: any new pair of equal adjacent tiers is covered, and a table with no equal pairs simply never takes the branch.

**Not changed, and worth a separate decision:** whether intra-tier drift *should* be corrected at all. An ETF that has drifted to ~94% while its tier says 80% is genuinely over-allocated. The current design deliberately rebalances only on tier change, and this fix makes the estimator agree with that design rather than quietly overriding it. If the drift policy is wrong, that is a strategy question for the overlay, not a consistency bug — filed separately rather than smuggled in here.

**Tests** — `paper_trading/test_etf_gate_agreement.py`, 9 tests, parametrized across all four boundaries so the fix is shown general rather than a patch for the 0.8/0.8 case. Baseline: 2 fail. Patched: 9 pass. The original `test_gate_estimate_and_real_unwind_must_agree` also flips, taking `test_paper_portfolio_audit.py` from 17-pass/1-fail to 18 passed. Full suite 391 → **400 passed, 0 failed**.

One fixture bug caught while writing them, recorded because it is the same class as two earlier ones this session: `etf_fraction=0.80` does not put the ETF at its 80% target, because the tier is a fraction of the *live* portfolio (cash + stock + ETF), not of the nominal ₹100,000. The at-target share count has to be solved: `etf = t·(cash + stock)/(1 − t)`. The first version failed at ₹7,105 — the fixture was wrong, not the code.

---

# 3. MEDIUM — `PositionSizer` returns −1 shares, bypassing the `shares == 0` skip and bricking the ticker

**Component:** Risk & Execution Engine → `engine/position_sizer.py`; consumed by `paper_trading/signal_runner.py`

### Intended behaviour
`position_sizer.py` module docstring:
> `shares = floor(risk_amount / stop_distance)` … *capped at* `min(shares_from_risk, max_position_pct × portfolio_value / entry_price, (cash − brokerage) / entry_price)`

`signal_runner.py:821`: `if shares == 0:` → skip the trade with a `HOLD` and a sizing explanation.

### Actual behaviour
```python
100:        max_from_cash = math.floor((cash_available - buy_cost_1share) / entry_price)
101:        while max_from_cash > 0:      # loop never entered when already negative
```
When `cash_available < buy_cost_1share`, the numerator is negative and `math.floor()` of a small negative fraction is **−1**, not 0. `min(...)` propagates it. `binding` correctly reports `all_constraints_zero` (it tests `shares <= 0`) but the **caller tests equality**, so −1 slips through.

Measured:
```
price Rs   1000 cash Rs 0.00  cost_1share Rs 1.19 -> shares=-1
price Rs   1000 cash Rs 1.00  cost_1share Rs 1.19 -> shares=-1
price Rs   2135 cash Rs 2.00  cost_1share Rs 2.54 -> shares=-1
price Rs    300 cash Rs 0.30  cost_1share Rs 0.36 -> shares=-1
price Rs   1000 cash Rs 5.00  cost_1share Rs 1.19 -> shares=0    ← correct
```

### Test
`test_position_sizer_never_returns_negative_shares` — **FAIL** on all 4 parametrizations.

### Failure mode (traced end to end)
`queue_pending_buy(shares=-1)` → `pending_buy=True`, `shares=-1`. Then:
- `get_open_positions()` filters `shares > 0` → **excluded**
- `committed_open_count()` → **excluded**, so the ETF tier never accounts for it
- Step 13's AMO writer requires `shares > 0` → **no AMO order row is ever written**
- Next day `_process_stock()` returns early on `pending_buy` → `PENDING_BUY` forever
- `morning_fill_check` only cancels `pending_buy` when it finds a matching AMO row — there is none

The ticker is **permanently frozen**: never trades again, never shows as an open position, and surfaces only as a recurring `PENDING_BUY` line. Recovery requires hand-editing `portfolio_state.json`.

### Precondition
`cash < buy_cost_1share` (≈ ₹1.19 on a ₹1,000 stock) at the moment a golden cross clears all gates. Reachable: `rebalance_etf()` at the 100% tier caps its buy with `int(cash / price)`, leaving a residual uniformly distributed in `[0, niftybees_price)` — roughly 0.5% of rebalances land under ₹1.19. **Finding #2 widens the door**: the phantom unwind credit lets a near-zero-cash candidate clear the ₹1,000 gate that would otherwise have stopped it.

Classified MEDIUM on precondition rarity, not on consequence — the consequence is silent and permanent.

### Proposed fix (not applied)
```diff
--- a/engine/position_sizer.py
+++ b/engine/position_sizer.py
@@ -97,7 +97,9 @@
         buy_cost_1share = transaction_costs(entry_price, 1, "buy", "delivery")
-        max_from_cash   = math.floor((cash_available - buy_cost_1share) / entry_price)
+        # max(0, ...): floor() of a small negative fraction is -1, not 0, when
+        # cash < the cost of a single share. A negative count must never escape.
+        max_from_cash   = max(0, math.floor((cash_available - buy_cost_1share) / entry_price))
         while max_from_cash > 0:
@@ -107,7 +109,7 @@
-        shares = min(shares_from_risk, max_from_pos_cap, max_from_cash)
+        shares = max(0, min(shares_from_risk, max_from_pos_cap, max_from_cash))
```
```diff
--- a/paper_trading/signal_runner.py
+++ b/paper_trading/signal_runner.py
@@ -821,7 +821,7 @@
-        if shares == 0:
+        if shares <= 0:
```
Apply both: the clamp fixes the source, `<=` makes the caller robust to any future non-positive return.

---

# 4. MEDIUM — `correlation_check.py` CLI default path is cwd-dependent and fails open silently

**Component:** Paper Trading Layer → `paper_trading/correlation_check.py:38`

### Intended behaviour
`CLAUDE_CONTEXT.md`:
> *"correlation_check.py is a full module — checks candidate against live open positions. CLI: `python paper_trading/correlation_check.py TICKER.NS`"*

### Actual behaviour
```python
38:    portfolio_state_path: str = "paper_trading/portfolio_state.json",
```
A **relative** default, resolved against process cwd. From any directory other than the repo root, `state_path.exists()` is False and the function returns:
```
{"safe": True, "open_positions": [], "reason": "No portfolio state file — correlation check skipped"}
```
A fail-open **wrong answer**, not an error.

### Root cause
Exactly the Jul 17 `news_flags.json` bug class. Note *why* it survived the hardening: `validation/system_health_check.py`'s relative-path scan matches `FOO = Path("relative")` literals. This is a **default parameter value**, so the regex never sees it — the same blind spot that let `AMO_CONFIG["order_log_file"]` (a dict value) slip through in July.

### Tests
| Test | Result |
|---|---|
| `test_correlation_cli_default_path_is_cwd_dependent` | **FAIL** — returns "No portfolio state file" from `/tmp` |
| `test_news_flags_path_is_cwd_independent` | PASS — the Jul 17 fix holds |
| `test_correlation_zero_open_positions_is_safe_and_makes_no_calls` | PASS |
| `test_correlation_one_open_position` | PASS |
| `test_correlation_at_max_concurrent_positions` | PASS — all 4 pairs measured |
| `test_correlation_pending_buy_excluded_from_file_path` | PASS |

### Blast radius
**Live automated path is insulated** — `signal_runner.py:1466` passes `portfolio_state_dict=corr_state` explicitly, so the default is never used at 3:45 PM. The exposure is the human CLI decision-support path used when evaluating a candidate for universe addition: it can report "SAFE TO ENTER" having checked nothing.

### Proposed fix (not applied)
```diff
--- a/paper_trading/correlation_check.py
+++ b/paper_trading/correlation_check.py
@@ -35,7 +35,7 @@
 def check_entry_correlation(
     candidate: str,
-    portfolio_state_path: str = "paper_trading/portfolio_state.json",
+    portfolio_state_path: str = str(_ROOT / "paper_trading" / "portfolio_state.json"),
     portfolio_state_dict: dict = None,   # overrides file read when provided
```
`_ROOT` is already defined at line 31.

**Related hardening worth considering:** extend `system_health_check.py`'s scan to catch relative paths in default arguments and dict values, not just `= Path("...")` literals. Both real escapes so far (July's `AMO_CONFIG`, this one) were in exactly those two shapes.

---

# 5. MEDIUM — `validate_state_integrity()` counts un-funded `pending_buy` shares in the 50% floor

**Component:** Paper Trading Layer → `paper_trading/paper_portfolio.py:113`

### Intended behaviour
> *"Check 1: total value must be >= 50% of initial capital. Catches stale file overwrites; allows legitimate large drawdowns."*

### Actual behaviour
The validator sums `shares × entry_price` over **all** positions, including `pending_buy` rows whose cash has not been deducted. `get_portfolio_value()` deliberately **excludes** `pending_buy` for exactly that double-count reason (line 645: *"cash not yet deducted, so adding their MTM value would double-count the capital"*).

The validator and the valuation function disagree, so the floor is computed on an inflated total.

### Test
`test_integrity_floor_counts_pending_buy_shares_that_have_no_cash_backing` — **PASS** (documents the exposure): with ₹30,000 cash and a 100-share `pending_buy` @ ₹400, the validator sees ₹70,000 and passes; `get_portfolio_value()` returns ₹30,000, which is **below** the ₹50,000 floor.

### Severity rationale
Fail-open, not fail-closed — it makes the guard too permissive, never too strict. It cannot corrupt state on its own, but it weakens the one check that exists against a stale-file SCP, which `CLAUDE_CONTEXT.md` records as a live-fire hazard.

### Proposed fix (not applied)
```diff
--- a/paper_trading/paper_portfolio.py
+++ b/paper_trading/paper_portfolio.py
@@ -113,7 +113,10 @@
-        # Stock value using entry_price as proxy (current price unavailable at load time)
+        # Stock value using entry_price as proxy (current price unavailable at
+        # load time). Excludes pending_buy for the same reason
+        # get_portfolio_value() does: their cash has not left the pool yet, so
+        # counting their provisional value inflates the total the floor tests.
         stock_value = sum(
             pos.get("shares", 0) * pos.get("entry_price", 0.0)
             for pos in self.state.get("positions", {}).values()
+            if not pos.get("pending_buy", False)
         )
```

---

# 6. MEDIUM — Daily report: `cash + invested ≠ total` when a BUY is pending

**Component:** Paper Trading Layer → `paper_trading/paper_portfolio.py:632` (`summary()`)

### Actual behaviour
`summary()` mixes two conventions:
- `invested_value` iterates `get_open_positions()` → **includes** `pending_buy`
- `total_value` calls `get_portfolio_value()` → **excludes** `pending_buy`
- `open_count` → **includes** `pending_buy`

Measured with one 10-share pending BUY @ ₹2,000:
```
cash            : 60000.0
invested_value  : 20000.0   <- includes pending_buy
total_value     : 60000.0   <- excludes pending_buy
cash + invested = 80000.0 vs total_value 60000.0   → inconsistency Rs 20,000
```

### Severity rationale
Display-only — no state, cash, or P&L is wrong. But these figures go into the terminal report, the emailed daily report, the weekly summary, and `signal_log.csv`'s `portfolio_value` column. A reader reconciling them by hand on any day a BUY is queued will find ₹20,000 unaccounted for and reasonably conclude the ledger is broken. Given this system's history of investigations launched from apparently-missing money, that is worth closing.

### Proposed fix (not applied)
Report the pending capital as its own line rather than silently folding it into `invested_value`:
```diff
--- a/paper_trading/paper_portfolio.py
+++ b/paper_trading/paper_portfolio.py
@@ -636,8 +636,15 @@
         invested_value = sum(
             pos["shares"] * current_prices.get(ticker, pos["entry_price"])
             for ticker, pos in open_pos.items()
+            if not pos.get("pending_buy", False)
         )
+        # Queued-but-unfilled BUYs: capital is committed but not yet deducted,
+        # so it belongs in neither cash nor invested. Surfaced separately so
+        # cash + invested == total_value always reconciles.
+        pending_value = sum(
+            pos["shares"] * current_prices.get(ticker, pos["entry_price"])
+            for ticker, pos in open_pos.items()
+            if pos.get("pending_buy", False)
+        )
```
and add `"pending_buy_value": pending_value` to the returned dict, rendering it as its own report row.

---

# 7. MEDIUM (operational) — `NSE_HOLIDAYS_2027` is empty; pre-warm due Nov 2026

**Component:** Utilities → `utils/market_calendar.py:61`

```python
NSE_HOLIDAYS_2027: List[date] = []  # Populate before December 2026
_HARDCODED_YEARS = {2026}
```

From 2027-01-01, every `is_trading_day()` call falls through to the live NSE API. On API failure **with no cache**, it fails open (line 225) and returns `True` — the system would run a full signal cycle on a market holiday, fetch a stale bar, and (per `_fetch_stock_data`'s `last_date != today` branch) *"Still use the data"*, generating signals from the previous session's close.

This is **documented, scheduled work**, not a defect: `CLAUDE_CONTEXT.md` specifies pre-warming in November 2026 via
```
python3 -c 'from utils.market_calendar import refresh_holiday_cache; refresh_holiday_cache([2027])'
```
Listed here so it does not slip — it is ~10 weeks out and the failure is silent.

### Tests
`utils/test_market_calendar.py` (existing, 5 tests) all pass. The fail-open branch is intentional per `CLAUDE_CONTEXT.md` ("All network failures fail-open — never block trading on data failure") and I did not change it.

---

# 8. LOW — `check_universe_consistency()` is tautological

**Component:** Validation → `validation/system_health_check.py:218`

```python
def _load_stocks():             return list(signal_runner.STOCKS)
def _load_screener_universe():  return get_current_universe()
```
and `screener/auto_screener.py:575`:
```python
def get_current_universe():
    from paper_trading.signal_runner import STOCKS as _LIVE_UNIVERSE
    return list(_LIVE_UNIVERSE)
```

Both sides resolve to the same list. The check is `sorted(set(x)) == sorted(set(x))` — it always passes and can never detect drift. Its own docstring claims it verifies *"signal_runner.STOCKS must match screener.get_current_universe()"*, which is true by construction.

This is a *consequence* of a correct earlier fix (unifying the universe into `universe.py` and routing the screener through it), not a bug — but the check now provides false assurance in the health report. Either delete it, or repoint it at something that can actually diverge, e.g. asserting `universe.py`'s STOCKS matches the tickers present in `degradation_tracker.json` and `portfolio_state.json`.

**No fix proposed** — this is a design call, not a defect.

---

# 9. LOW — No duplicate or out-of-order timestamp detection anywhere in the pipeline

**Component:** Data Layer → `data/kite_fetcher.py`; consumed everywhere

`get_ohlcv()` guards zero-price rows and strips timezone, but never asserts the index is unique or monotonic. Nothing downstream does either.

### Tests (both PASS — documenting exposure, not failure)
- `test_duplicate_timestamps_are_not_detected_anywhere` — a duplicated date passes straight through; the rolling SMA silently averages it, shifting both SMA20 and SMA50.
- `test_out_of_order_timestamps_are_not_detected_anywhere` — a descending index still produces signals. **Load-bearing consequence:** `signal_runner` reads "today's bar" as `df.iloc[-1]`, which would be the *oldest* bar.

Rated LOW because Kite's `historical_data` is ordered and de-duplicated in practice — I found no evidence of either condition occurring. It is listed because it is an unguarded assumption on the single ingestion point every consumer depends on, and the cost of asserting it is two lines:

```diff
--- a/data/kite_fetcher.py
+++ b/data/kite_fetcher.py
@@ -258,6 +258,13 @@
     df = df.set_index("date")
     df.index.name = "date"
+
+    # Every downstream consumer treats df.iloc[-1] as "today" and relies on
+    # rolling() windows being chronological. Kite has always returned ordered,
+    # unique daily bars — assert it rather than assume it.
+    if not df.index.is_unique:
+        df = df[~df.index.duplicated(keep="last")]
+    if not df.index.is_monotonic_increasing:
+        df = df.sort_index()
```

---

# 10. LOW — `close_position()` raises `ZeroDivisionError` after crediting cash

**Component:** Paper Trading Layer → `paper_trading/paper_portfolio.py:487`

```python
"return_pct": round((exec_price / entry_px - 1) * 100, 4),
```
No guard on `entry_px == 0`. Cash is credited at line 469 **before** this line executes, so the exception leaves cash mutated, the trade **not** appended, `total_trades` un-incremented, and `self.save()` never reached.

### Test
`test_close_position_with_zero_entry_price_divides_by_zero` — **PASS** (confirms the raise and that cash was already mutated).

Rated LOW: reaching it requires `entry_price == 0`, which needs a fill confirmed at a zero open price. `kite_fetcher` nulls rows with `close <= 0`, and the documented Kite garbage rows are all-zero OHLC, so `open == 0` with `close > 0` has never been observed. In-memory-only damage — the unsaved state is discarded on process exit.

```diff
--- a/paper_trading/paper_portfolio.py
+++ b/paper_trading/paper_portfolio.py
@@ -466,6 +466,11 @@
         shares     = pos["shares"]
         entry_px   = pos["entry_price"]
+
+        if entry_px <= 0:
+            raise ValueError(
+                f"close_position called for {ticker} with entry_price={entry_px} "
+                f"— cannot compute return_pct. State is corrupt; fix before closing."
+            )
```
Raising *before* any mutation keeps the ledger clean.

---

# 11. LOW — `PaperPortfolio.state_file` default is a relative path

**Component:** Paper Trading Layer → `paper_trading/paper_portfolio.py:47`

```python
state_file: str = "paper_trading/portfolio_state.json",
```
Same class as #4. **Dead in production** — both live entry points pass an absolute path (`signal_runner.py:1178` and `morning_fill_check.py:507` both use `str(STATE_FILE)`, built from `_ROOT`). Listed for completeness and because the class docstring's own usage example (line 38) demonstrates the relative form.

---

# COMPONENT-BY-COMPONENT RESULTS

### Data Layer — `data/kite_fetcher.py`, `data/fetcher.py`
**Intended:** *"Returned DataFrame … Index: DatetimeIndex (timezone-naive, IST dates)"*; zero-price garbage rows nulled and dropped; 15s timeout.

**Verified correct.** The Jul 19 tz fix is properly implemented — tz stripped on the `df["date"]` **column** via `.dt.tz_localize(None)` before `set_index()`, guarded by `if df["date"].dt.tz is not None`, with `pd.to_datetime()` first to guarantee `.dt` exists. Not the `.map()` form that no-ops on pandas 2.3.3.

**Timezone traced end to end, as requested — not just the fixed site.** Every consumer (`signal_runner`, `morning_fill_check`, `auto_screener`, `walk_forward`, `backtester`) sources exclusively through this one `get_ohlcv()`. `utils/market_calendar.py` and `utils/corporate_actions.py` operate only on plain `datetime.date` and never receive a Kite Timestamp. `data/fetcher.py` (yfinance) uses `.tz_localize(None)` and never had the bug. `is_trading_day()` additionally normalizes a `datetime` to `.date()` (line 204) with a comment explaining exactly why. **No second tz-stripping site exists to be wrong.**

**Findings:** #9 (LOW, timestamp ordering).
**Tests:** 23 in `data/test_data_edge_audit.py` — 19 pass, 4 fail (finding #3, a sizer defect surfaced here).

### Strategy Layer — `strategies/sma_crossover.py`
**Intended:** NaN during warm-up so `first_valid_index()` is meaningful; raise below `slow_period`.
**All pass.** Raises correctly at n = 0, 1, 2, 19, 49; at exactly 50 bars the warm-up rows are NaN (not 0) and only the last row is valid. `signal_runner.SMA_SLOW == 50` matches `generate_signals`' threshold exactly, so no ticker can slip past the fetch guard and raise inside `_process_stock`. A NaN close mid-series does not manufacture a false crossover.

### Risk & Execution Engine — `risk_manager.py`, `cooldown.py`, `position_sizer.py`, `order_manager.py`, `fill_resolution.py`
**Boundary values all correct:**
- Hard stop at **exactly** −20.00% fires (`<=`) ✅
- Time stop at **exactly** `bars_since_entry == 60` fires (`>=`) ✅
- L1 takes precedence when hard stop and time stop both qualify on one bar ✅
- ATR warm-up: chandelier is `None` and L1 still fires — matches the README's *"Fires even during ATR warm-up"* ✅
- Gap breaker at **exactly** 3.00% → `REQUEUE`, not `GAP_EXIT` (`>`), matching the documented *">3%"* ✅
- Circuit breaker at `>= 20.0` — the Jul 7 fix holds ✅

**Backtest/live parity re-verified.** I initially suspected the paper path incremented `bars_since_entry` one bar earlier than the backtester. It does not: with `use_next_day_fills=True`, `backtester.py` fills the pending BUY at step ⓪ then calls `check_exit()` on that **same** bar (line 254), exactly as `signal_runner` does. `on_position_open` seeds `highest_high` from that bar's high; `confirm_buy_fill` seeds `0.0` and the first `check_exit` sets it to `max(0, that bar's high)` — identical. **No divergence. Claim withdrawn before reporting.**

**Findings:** #3 (MEDIUM).

### Paper Trading Layer — `signal_runner.py`, `morning_fill_check.py`, `paper_portfolio.py`, `correlation_check.py`
Largest surface, most findings: **#1 (CRITICAL), #2 (HIGH), #4, #5, #6 (MEDIUM), #10, #11 (LOW)**.

**Verified correct:**
- Cold start with no state file → clean initial state ✅
- Empty / truncated `portfolio_state.json` → raises loudly, **never** silently resets to ₹100,000 ✅
- `total_trades` vs `trade_log` length mismatch caught ✅
- 50% floor catches a stale low-value file ✅ (but see #5)
- ETF tier exact at every boundary 0/1/2/3/4, clamps above 4 ✅
- `projected_tier_unwind_cash` capped at shares actually held ✅
- `close_position` on a flat position raises — duplicate fill cannot double-close ✅
- **`morning_fill_check` run twice with `--apply` does not double-deduct** ✅
- `queue_pending_sell` twice same day raises; `queue_pending_buy` on an open position raises ✅
- `pending_buy` counts toward `MAX_CONCURRENT_POSITIONS`, so candidate #5 in one run is correctly blocked ✅
- `load_news_flags()` fails open on empty / broken / schema-missing / wrong-type JSON, and on a missing file ✅
- `NEWS_FLAGS_FILE` is absolute — the Jul 17 fix holds under `chdir` ✅

**Universe mutation mid-run** (`test_removed_ticker_with_open_position_is_still_in_state_but_unprocessed`, PASS — documents exposure): a ticker removed from `universe.py` while a position is open survives `load()` (which backfills but never prunes — correct), and still counts toward `committed_open_count()` so the ETF tier reserves its capital. **But** `signal_runner`'s Phase 1 iterates `for ticker in STOCKS`, so the orphan is never passed to `_process_stock()`: no `check_exit`, no chandelier ratchet, no exit signal. It is also absent from `dfs`, so `get_portfolio_value()` marks it at its frozen `entry_price` forever.

Not rated as a finding because `CLAUDE_CONTEXT.md` shows the operating practice is consistently to verify zero open positions before removal ("Zero open position at removal — clean exit", "Both had 0 open positions at time of removal"). It is a documented-discipline dependency with no structural enforcement — worth an assertion in any future removal script, matching the guard `add_validated_stock.py` already provides on the addition side.

### Utilities — `costs.py`, `market_calendar.py`, `corporate_actions.py`, `news_monitor.py`
**Fail-open/fail-closed audited against `CLAUDE_CONTEXT.md` rather than assumed**, per instruction:

| Site | Documented intent | Actual | Match |
|---|---|---|---|
| `news_monitor` SURVEILLANCE | auto-block entry | `auto_block: True` → `BLOCKED` | ✅ |
| `news_monitor` EARNINGS_RISK | warn only, entry proceeds | `auto_block: False` → warn | ✅ |
| `news_monitor` network failure | fail-open | returns `{}` with explicit log | ✅ |
| `corporate_actions` API failure | *"skip=False so trading is not blocked"* | `skip: False` | ✅ |
| `market_calendar` API failure, no cache | fail-open | returns `True` + warning | ✅ |
| NIFTY regime fetch failure | `UNKNOWN` → allow entry | `resolve_buy_gate` skips on non-BEAR | ✅ |
| Live Hurst computation error | fail-open | `hurst=None` → check skipped | ✅ |
| Correlation check exception | fail-open | caught, BUY proceeds | ✅ |

**Every one matches its documented intent. No divergence found in this category.**

`resolve_buy_gate()` deserves specific credit: it passes `hurst=None` rather than a `0.5` sentinel, with a docstring explaining that `HURST_THRESHOLD` has been as high as 0.55 — above the neutral value — so a numeric sentinel would silently flip fail-open into fail-closed. That is the correct reasoning and it is correctly implemented.

`costs.py` verified against the README's stated figures: buy-side ₹11.91 / sell-side ₹25.75 / round-trip ₹37.66 per ₹10,000. `SEBI_FEE_RATE = 0.000001` correctly encodes 0.0001%. Existing `utils/test_costs.py` (7 tests) passes.

**Findings:** #7 (MEDIUM, scheduled).

### Validation — `walk_forward.py`, `system_health_check.py`
**The `"N/A"` type-handling class is properly contained.** The user flagged this specifically as a recurring pattern. I grepped every `"N/A"` site in the codebase and checked each for numeric comparison:
- `walk_forward.py:1481` — `_norm()` normalizes `"N/A" → None` before every gate comparison. The Jul 10 fix, correctly placed.
- Lines 1502–1563, 1997–2003, 2160–2195 — display-only, all guarded by `is not None` or `isinstance(..., float)`.
- Line 570 — `per_stock_scores` derives from `sum()` over lists of bools; `"N/A"` cannot reach it.
- `post_screener_pipeline.py`, `add_validated_stock.py`, `emailer.py`, `universe_scan.py`, `etf_overlay_backtest.py`, `backtester.py` — all display-only with `None` guards.

**No second instance of the extended-window crash pattern exists.** Existing `test_wf_gate.py::test_na_string_sentinels_normalize_to_none_not_typeerror` already pins it.

**Findings:** #8 (LOW).

### Auth — `auth/auto_login.py`, `auth/kite_login.py`
**Not stress-tested by design** — every meaningful test requires either live Zerodha credentials or a live TOTP exchange, both excluded by the no-live-calls guardrail. Reviewed by reading only:
- `_check_auth()` correctly calls `_attempt_auto_refresh()` before `sys.exit(1)` (Finding #6, Jun 19) ✅
- The empty-`access_token.txt` `IndexError` fix is present: `lines[0].strip() if lines else ""` then an explicit `ValueError` ✅
- `KITE_REQUEST_TIMEOUT_SECONDS = 15` is passed to the `KiteConnect` constructor ✅
- `_auto_refresh_token()` uses `cwd=Path(__file__).parent.parent` — cwd-independent ✅

**This is the one component I could not meaningfully stress-test.** Stated plainly rather than reported as clean.

---

# WHAT I DID NOT COVER

- **Auth** beyond static review (above) — blocked by the no-live-calls guardrail.
- **`engine/backtester.py`** end-to-end — read for RM call ordering (parity confirmed) but not stress-tested; it is offline research tooling, not live capital path.
- **`screener/`** (`auto_screener`, `universe_scan`, `regime_classifier`, `emailer`) — read for `compute_hurst` and `get_current_universe` only. A full screener audit needs a 500-ticker fetch; existing coverage is 3 + 9 + 4 tests.
- **`validation/etf_overlay_backtest.py`'s stale `A_current` default** — `CLAUDE_CONTEXT.md` (Jul 27) already flags this as known and deliberately deferred; I confirmed it is still present and still not wired into anything live. Nothing new to add.

---

# HOW TO RUN THIS AUDIT'S TESTS

```bash
cd ~/algo-trading && source venv/bin/activate

# All four new files (expect 9 failures — each asserts a confirmed defect)
python -m pytest paper_trading/test_morning_fill_check_audit.py \
                 paper_trading/test_paper_portfolio_audit.py \
                 paper_trading/test_concurrency_audit.py \
                 data/test_data_edge_audit.py -v

# Confirm no regression in the pre-existing suite
python -m pytest --ignore=paper_trading/test_morning_fill_check_audit.py \
                 --ignore=paper_trading/test_paper_portfolio_audit.py \
                 --ignore=paper_trading/test_concurrency_audit.py \
                 --ignore=data/test_data_edge_audit.py -q
# → 313 passed
```

Each failing test's assertion message states the defect in full. After applying a fix, that test flips to green and becomes its regression guard.


---

# APPENDIX — Verified fix for findings #1 and #1b

`fix_01_dry_run_contract.patch` at the repo root. **Not applied** — `git status` shows it as an untracked file alongside the report; `paper_trading/morning_fill_check.py` is unchanged at HEAD.

```
paper_trading/morning_fill_check.py | 468 ++++++++++++++++++-------------
1 file changed, 287 insertions(+), 181 deletions(-)
```

### What it does
Splits the fill loop into **plan → report → execute**:
- `plan_fill()` — read-only; returns a frozen `FillDecision` per order
- report — renders `decision.detail`; contains no writer
- `execute_decision()` — the only writer in the module, called from the only `if apply_fills:` guard

Dry-run safety stops being a discipline enforced at N call sites (the construct that produced #1) and becomes a property of the call graph.

### Verified, not asserted
Validated in a throwaway `git worktree` at HEAD; the working tree was never modified.

| Check | Result |
|---|---|
| Patch applies to working tree | `git apply --check` → **clean** |
| Property tests vs **patched** code | **15 passed** |
| Property tests vs **unpatched** code | **14 failed, 1 passed** — the tests genuinely catch the bug |
| Original `test_morning_fill_check_audit.py` (finding #1) vs patched | **3 passed** — flips green |
| Full pre-existing suite vs patched | **312 passed, 1 failed** |
| That 1 failure | `test_state_file_is_absolute_and_cwd_independent` — asserts `STATE_FILE.exists()`; `portfolio_state.json` is gitignored so absent from any worktree. **Fails identically on unpatched code in the same worktree** — environmental, not caused by the patch. |

### Deliberate behaviour changes (review these)
1. **Dry run now previews gap-exit/requeue decisions.** Previously that classification ran only under `apply_fills`, so a dry run reported a 3%+ gap-down SELL as a plain MISS and never showed the `GAP_EXIT` it would actually take. Cost: one extra read-only close-price fetch per requeued SELL in dry mode.
2. **Counters derive from a partition of `decisions`** rather than hand-maintained `+= 1` in each branch, so Audit2 Finding #4 (GAP_EXIT double-counted as MISSED) becomes impossible by construction.
3. `results` is rebuilt from decisions. The REJECTED/CANCELLED manual-attention section behaves identically — `CANCELLED_CA` and `UNKNOWN` are distinct strings and do not match `("REJECTED", "CANCELLED")`.

The Jul 18 portfolio-before-CSV ordering contract is preserved: the ledger write is last inside `execute_decision()`, so a crash still leaves the row at `DRY_RUN` and reprocessable.

### Not included, by design
Phase 2 (append-only event journal replacing the mutable `status` column) and Phase 3 (broker-as-source-of-truth reconciliation via Zerodha `tag` as idempotency key) are separate changes. Migrating an order ledger is much cheaper while still on paper, but it should not ride along with a CRITICAL hotfix.

### Separately — verify before live capital
The fill model assumes an AMO limit order is resolved at 9:20 AM: filled if the open beat the limit, otherwise dead (BUY cancelled, SELL requeued). My understanding is that a Zerodha AMO is released into the pre-open session and, if unexecuted at the open, **remains a live order in the book for the rest of the session**, cancelled at EOD rather than at 9:20. If so, the backtest and paper P&L systematically under-report fills, and the `[REQUEUED]` machinery duplicates something the exchange already does.

Flagged, not asserted — Zerodha's AMO handling has changed more than once and this repo's standard is to verify against the live system. Confirm against current Zerodha AMO documentation and one day of real `kite.orders()` output. Separate work from #1; do not bundle.

---

## Addendum — Finding #3: sizer could return negative shares (Aug 26 2026)

### What the finding actually is

`PositionSizer.calculate_shares()` derives its cash cap as:

```python
buy_cost_1share = transaction_costs(entry_price, 1, "buy", "delivery")
max_from_cash   = math.floor((cash_available - buy_cost_1share) / entry_price)
```

`buy_cost_1share` is the **fees on one share, not its price** — ₹0.12 on a ₹100
share, ₹5.95 on a ₹5,000 one. The numerator therefore goes negative only when
available cash is below those fees. That is a narrow window, and the original
audit text overstated it; the corrected condition is recorded here rather than
in the fix comment alone.

But `math.floor()` of a small negative fraction is `-1`, not `0`. The
`while max_from_cash > 0:` refinement loop cannot correct an already-negative
value, and the final `min()` propagates it. The sizer could return `-1` while
its own `binding` field simultaneously reported `all_constraints_zero` — the
function disagreeing with itself in the same return.

### The coupling, which is the real finding

Four call sites across three modules each independently decided what "no shares"
meant. All four chose `== 0`, against a producer whose own contract was `<= 0`:

| Module | Site | Guard | Consequence of `-1` |
|---|---|---|---|
| `paper_trading/signal_runner.py` | `_process_stock` | `if shares == 0:` | falls through to `queue_pending_buy()` with a negative quantity |
| `engine/backtester.py` | 3 sites feeding `portfolio.buy(shares=…)` | `if shares == 0:` | passes `-1` into the money seam |
| `engine/portfolio.py` | `buy()` | `if shares == 0:` | **does not no-op** — see below |

The sizer was the only component that knew the right answer, and it was the only
one not asked.

### The money seam

A negative quantity reaching `Portfolio.buy()` does not harmlessly do nothing.
`transaction_costs()` returns a **negative** cost, `total_spent` goes negative,
the `total_spent > self.cash` affordability guard passes trivially, and the
result is a short position in a long-only backtester with cash **increased**.

Measured against unpatched `HEAD`:

```
before: cash ₹100,000.00   shares 0
BUY  2026-08-26 | -1 shares @ ₹1000.50 (close ₹1000.00) | cost ₹-1.19 | cash left ₹101001.69
after : cash ₹101,001.69   shares -1
delta : ₹+1,001.69
```

This is the part that matters, and it is why the finding was worth chasing past
its low trigger probability. The backtester does not crash on this — it
**reports**. A backtest that hit this would have produced a better result than
the strategy earned: **a bug that produces a result that looks better than it
should**. `CLAUDE_CONTEXT.md` already records that exact failure mode twice —
Hurst computed on prices instead of returns and STT set to the intraday rate
(Jun 17), and trade P&L inflated by buy-side costs (Jun 20). **Same trap,
different module.**

### Severity

Split deliberately, because one rating would misrepresent both halves.

- **Severity today — LOW.** Requires portfolio cash below the fees on a single
  share (₹0.12–₹5.95 depending on price). Server-verified live state, read from
  `ubuntu@13.205.133.169` on Aug 26 at HEAD `5e26497`: **cash ₹243.49**, 359
  NIFTYBEES @ ₹272.44, `etf_tier` 1.0, **zero open equity positions**, last run
  2026-08-25 15:45. Margin to the worst-case trigger is roughly **41×**. Not
  currently firing, and no evidence it ever fired.

  Worth noting the direction of travel, though: the sizer's `while` loop
  deliberately drives leftover cash toward zero, so low-cash states are the
  system's normal steady state rather than an anomaly. What holds cash off the
  floor is the ETF gate topping it up below `MIN_CASH_TO_ATTEMPT_BUY` (₹1,000)
  — the exact path Finding #2 just repaired. Low probability, but not a corner
  the system is structurally kept away from.
- **Severity of the coupling — HIGH.** Four sites, three modules, one shared
  wrong assumption about a contract that was already stated correctly in the
  producer. The value was reachable only in a corner; the disagreement was
  everywhere.

### Fix

Every layer, deliberately redundantly — the coupling was the trap, so no layer
depends on another holding:

1. `position_sizer.py` — clamp `max_from_cash` with `max(0, …)` at the source
2. `position_sizer.py` — clamp the final `min()` with `max(0, …)` again
3. `signal_runner.py` — `== 0` → `<= 0`
4. `backtester.py` — three guards, `== 0` → `<= 0`
5. `portfolio.py` — `buy()` validates its own input, `== 0` → `<= 0`

### Two corrections made during this fix

1. **The patch was incomplete when first staged.** `engine/backtester.py` was
   not among the original four files. Its three `if shares == 0:` guards
   surfaced only when re-deriving this write-up against the actual code rather
   than trusting the earlier read. The module most exposed to the bug was the
   one the patch had missed.
2. **The first regression test was a false positive.** It asserted
   `re.search(r"^\s*if shares <= 0:", src)` against source text, and was
   demonstrated to pass when that line appears only in a **docstring** while the
   real guard stays `== 0` in a different function. Replaced with an AST walk
   that locates the actual `if` node and asserts its operator is `LtE`. Same
   false-positive class as the Aug 25 health-check scanner that matched its own
   docstring — also fixed by parsing rather than pattern matching. Twice now.

3. **The severity figure was read off the wrong file.** The first draft cited
   ₹29,106.13 as live cash. That is `paper_trading/portfolio_state.json` in the
   local checkout — mtime Jun 25, `last_run_date` 2026-06-24, and **no
   `etf_shares`/`etf_tier` keys at all** because it predates the ETF overlay.
   Its 9-key `positions` dict was also miscounted as 9 open positions when only
   one held shares. Real figure: **₹243.49**, server-verified. Both support LOW,
   but ₹29,106.13 implies a ~4,900× margin and the truth is ~41×. Filed
   separately as a finding — the trigger condition was "read a file that exists,
   at the path documented to be correct."

### Verification

| Check | Result |
|---|---|
| `engine/test_sizer_negative_shares.py` vs patched | **15 passed** |
| vs unpatched | **11 failed** — tests genuinely catch the bug |
| Backtester guard test vs unpatched | fails with `operators found: ['Eq', 'Eq', 'Eq']` |
| Full suite (fresh worktree) | **399 → 414 passed**, plus one pre-existing environmental failure in both runs — `test_state_file_is_absolute_and_cwd_independent` asserts `STATE_FILE.exists()` on a gitignored file absent from any worktree. Verified pre-existing by stashing to clean `main`. An earlier draft recorded "400 → 415", measured where a stray `portfolio_state.json` made that test pass |

---

## Finding #14 — A stale local state file reads as live (Aug 26 2026)

**Severity: MEDIUM.** **Class: process defect, not code defect.** Nothing in the
trading system is wrong. The defect is in how confidently the working tree
misleads whoever reads it.

### Trigger condition

**Read a file that exists, at the path documented to be correct.**

That is the entire exploit. It is the same failure shape this audit has been
hunting throughout — *a thing existing where it is expected is not the same claim
as it being the real one* — aimed at a reader instead of at code. Third instance
in two days, after the regex-vs-AST false positive twice.

### What happened

During the Finding #3 write-up, `paper_trading/portfolio_state.json` in the local
checkout was read and reported as live state.

| | Local file (reported as live) | Server (actual) |
|---|---|---|
| cash | ₹29,106.13 | **₹243.49** |
| ETF | *no `etf_shares` key at all* | 359 NIFTYBEES @ ₹272.44 |
| `etf_tier` | *absent — predates the overlay* | 1.0 |
| open positions | reported as 9 | **0** |
| `last_run_date` | 2026-06-24 | 2026-08-25 |

Three errors compounded: the file was two months stale, its 9-key `positions`
dict was miscounted as 9 open positions when only one held shares, and the gap
against a remembered server figure was narrated as days of unobserved trading
that never occurred.

The only signals that expose it are ones you must think to check — `mtime`, and
an **absent required key** (`etf_shares`/`etf_tier`, missing because the file
predates the ETF overlay). Nothing surfaces on its own. The file sits at the
documented path, parses cleanly, and returns plausible numbers.

**Cost:** Finding #3's severity margin was documented as ~4,900× when it is ~41×.
Both support LOW, so the conclusion held — but those are different claims to
anyone later deciding how urgently to care. Caught pre-push; corrected in
`c821900` (the Finding #3 commit).

### The real finding: two existing defences both missed it

**1. The integrity guard works — via a path nobody inspecting state takes.**
`validate_state_integrity()` ([paper_portfolio.py:102](paper_trading/paper_portfolio.py#L102))
was written for exactly this scenario. Verified against a temp copy of the actual
file:

```
STATE INTEGRITY FAIL: computed total value ₹39,392 is below 50% floor ₹50,000.
Possible stale portfolio_state.json loaded. Cash=₹29,106 | Stock=₹10,286 | ETF=₹0.
```

It fires correctly. But it runs from `load()`, and **every production consumer
goes through `load()`** — so the trading system was never at risk. The unguarded
consumer is ad-hoc inspection: `json.load(open(path))` bypasses it completely,
and that is what an auditor actually types.

**2. The written warning already existed — and already failed.**
`CLAUDE_CONTEXT.md:1195` warned about *that specific file*, naming the same Jun 24
sync date and the same BAJAJ-AUTO.NS position, closing with "this note exists so
the discrepancy isn't mistaken for a correction later." It was read in full at the
start of this audit and did not prevent the mistake.

That second point is load-bearing: **a note is the mitigation that had already
been tried and had already failed.** A passive warning must be recalled at the
moment of temptation; an instruction is found when you go looking for how to do
the thing. The note was also buried inside an incident write-up rather than in
Key Workflow Notes where operating rules live.

### Fix

1. **`paper_trading/show_live_state.py`** — a named read-only command. Reads
   server state over `ssh` with a single `cat`, and **fails loud**: any failure
   raises `LiveStateUnavailable` rather than falling back to a local read,
   returning a sentinel, or warning and continuing. A fallback would reintroduce
   the exact failure it exists to prevent. Same move as Finding #1's
   plan/report/execute split — stop relying on discipline, make the safe path the
   only path.
2. **Provenance rule moved into Key Workflow Notes**, reframed as **"never
   authoritative regardless of age"** rather than "stale." The local machine never
   runs the system, so a copy synced an hour ago is exactly as wrong as one synced
   in June; there is no age at which it becomes correct. The section also records
   that `len(positions)` is not the open-position count. The old note keeps its
   incident context and points up to the general rule.

**Deliberately not built:** a general staleness checker. Detection already exists
and already works — a second detector would not close the seam, which is the
bypassed read path.

### Verification

| Check | Result |
|---|---|
| `test_show_live_state_audit.py` | **15 passed**, no network (fake runner injected at the subprocess seam) |
| No-fallback test is behavioural | records every path passed to `open()`; not a source-text scan |
| Mutation test — add a local fallback | **fails**: `fell back to a local state read: ['paper_trading/portfolio_state.json']` |
| Module parses **and imports** | both checked (per the `import re` lesson) |
| **Live run — success path** | exit 0; printed ₹243.49, 359 NIFTYBEES @ ₹272.44, tier 1.0, `open positions  0  (positions dict has 17 keys)` — matches the manual server read exactly |
| **Live run — failure path** | pointed at 192.0.2.1 (TEST-NET-1, unroutable), real subprocess: exit 1, `LIVE STATE UNAVAILABLE: ssh ... exited 255`, plus the explicit "NOT falling back" line. No hang, no fallback, no partial output |
| Full suite | **414 passed, 1 failed** — the failure is `test_state_file_is_absolute_and_cwd_independent`, which asserts `STATE_FILE.exists()`; the state file is gitignored and therefore absent from any fresh worktree. Verified pre-existing: `git stash -u` → clean `main` → same single failure |

---

## Finding #4 — Relative-path default parameters resolve against the cwd (Aug 26 2026)

**Severity: LOW** for the live defect. **The more durable problem is the
detector gap it exposes** — see the last section.

### Intended vs actual

`check_entry_correlation()` is documented to check a candidate against current
open positions. Its `portfolio_state_path` parameter defaulted to the bare
relative string `"paper_trading/portfolio_state.json"`
([correlation_check.py:38](paper_trading/correlation_check.py#L38)) — six lines
below a perfectly good `_ROOT` at line 32.

At [correlation_check.py:85-94](paper_trading/correlation_check.py#L85) that
string is wrapped in `Path()` and tested with `.exists()`. Resolved against the
process cwd, so from any directory but the repo root it takes the missing-file
branch and returns:

```python
"open_positions":  [],
"safe":            True,
"reason":          "No portfolio state file — correlation check skipped",
```

Not an exception. A `safe=True` computed over zero positions.

### Root cause

Confirmed against current code, not the prior description. The repo's convention
is to resolve every path against the module's own location — `_ROOT / "dir" /
"file"`, or `Path(__file__).parent / "file"` — used at `morning_fill_check.py:50-52`,
`signal_runner.py:112`, `auto_screener.py:42`, `news_monitor.py:28`, and a dozen
other sites. These two defaults simply never adopted it.

### Reachability — answered with evidence, not the docstring's qualifier

**Production never touches the defective path.** `signal_runner.py:1469` — the
only automated caller — passes `portfolio_state_dict=corr_state` explicitly,
which takes the `if portfolio_state_dict is not None:` branch at line 82 and
never evaluates `portfolio_state_path` at all. Verified by reading the call site.

**And the CLI is not silent, contrary to the existing test's docstring.** That
docstring calls this "a fail-open wrong answer, not an error." True of the
*function*; overstated for the *CLI*, which is the only thing that reaches it.
Run from `/tmp` against unpatched code:

```
Correlation Check — PERSISTENT.NS
Open positions checked: []
Threshold: 0.6

  No portfolio state file — correlation check skipped
```

The CLI prints the skip and never reaches its `Verdict: ✅ SAFE TO ENTER` line —
that branch requires a non-empty `open_positions`. A human running the documented
command sees that nothing was checked. Patched, from the same wrong cwd:

```
Open positions checked: ['BAJAJ-AUTO.NS']
  vs BAJAJ-AUTO.NS: 0.164  ✅ safe
```

### A second site, not named by the existing test

An AST scan for the *shape* of the bug — a default parameter whose value is a
relative path string — found **two** instances, not one:

| Site | Reachable? | Failure mode |
|---|---|---|
| `correlation_check.py:38` `portfolio_state_path=` | documented CLI only | returns `safe=True` over zero positions; CLI displays the skip |
| **`paper_portfolio.py:44` `state_file=`** | **no caller uses the default** | **`load()` treats the missing file as first-run and fabricates a fresh `initial_capital` portfolio; a later `save()` writes real state to the wrong directory** |

The second is latent — `signal_runner.py:1183` and `morning_fill_check.py:859`
both pass `str(STATE_FILE)` explicitly — but it is the more dangerous of the two
if ever reached, and its own class docstring at line 38 *recommended the
buggy form* as the usage example. Fixed both, and the docstring.

Consistent with every prior finding this session that began as one instance:
Finding #3's four sites, the TOTP masking's five.

### Severity

**LOW.** Production is unaffected (the automated caller bypasses the parameter
entirely), the CLI surfaces the skip rather than printing a false verdict, and
the second site has no caller. There is no path by which this produced a wrong
trade or a wrong number in any report.

Rated on its own evidence rather than matched to a prior finding: this is
genuinely smaller than #3 (which reached a money seam) or #14 (which put a wrong
figure into this document). What keeps it worth fixing is the class, not the
instance.

### The detector gap — the part worth carrying forward

`system_health_check.py`'s Check 6 exists to catch exactly this bug class. It
does not cover it. Its `_REL_PATH_RE` is:

```python
_REL_PATH_RE = re.compile(r"=\s*Path\(['\"](?!/|__)")
```

That matches `FOO = Path("relative/path")` — an assignment wrapping `Path()`. A
default parameter is a bare string with no `Path()` around it, so both Finding #4
sites were invisible. Measured:

```
Check 6 today: PASS — 0 relative-path constants found in 50 scanned files

   miss   portfolio_state_path: str = "paper_trading/portfolio_state.json",
   miss   state_file: str = "paper_trading/portfolio_state.json",
   MATCH  _ROOT = Path("relative/path")
```

This is the **permanently-green** shape from the closing note, in the check
built to prevent it: a green PASS over a class it cannot see. The check was made
*trustworthy* in the Aug 25 fix (it stopped matching its own docstring); it was
never made *complete*.

The regression test added here closes the gap structurally rather than by
widening the regex — `test_no_bare_relative_path_defaults_remain_in_production_code`
walks the AST of every production file and fails on any relative-path default,
which is a stronger claim than any pattern could make. **Whether Check 6 itself
should also be extended is left as a separate decision**, deliberately not
bundled into this fix.

### Verification

| Check | Result |
|---|---|
| New tests vs patched | **5 passed** |
| vs unpatched | fails on both sites: `portfolio_state_path default is not absolute`, `state_file default is not absolute` |
| Pre-existing `test_correlation_cli_default_path_is_cwd_dependent` | **flips green** — patched **1 passed**, unpatched **1 failed**, same environment |
| CLI, wrong cwd, unpatched | prints `Open positions checked: []` + skip reason |
| CLI, wrong cwd, patched | prints `['BAJAJ-AUTO.NS']`, correlation 0.164 |
| Full suite | **435 passed, 0 failed** |

---

## Finding #15 — Check 6 was green over a class it could not see (Aug 26 2026)

**Severity: MEDIUM.** A detector that reports PASS over a defect class it cannot
detect is worse than no detector, because the PASS is read as evidence.

### The defect

Check 6 exists to catch cwd-dependent paths. It was a line regex matching an
**assignment wrapping `Path()`**. A default parameter is a bare string with no
`Path()` around it, so Finding #4's two sites were invisible. Measured before
the fix:

```
Check 6: PASS — 0 relative-path constants found in 50 scanned files

   miss   portfolio_state_path: str = "paper_trading/portfolio_state.json",
   miss   state_file: str = "paper_trading/portfolio_state.json",
   MATCH  _ROOT = Path("relative/path")
```

The Aug 25 trustworthiness fix made this check stop lying about what it saw — it
had been matching its own docstring. It never made it see everything.
**Trustworthy and complete are different properties**, and conflating them left
this gap open for a week.

### The fix — filter by context, not by content

The rewrite scans the AST for the **three** contexts where a relative path is
load-bearing:

1. a `Path()` / `open()` argument — what the regex covered
2. a **parameter default**, including keyword-only defaults (the first draft of
   the oracle missed those; one function in the repo uses them)
3. a **dict-literal value under a path-naming key** — the shape of
   `AMO_CONFIG["order_log_file"]`, which is the **original motivating case for
   Check 6 existing at all**, and the one it never covered: a dict value is
   neither an assignment wrapping `Path()` nor a parameter default

### The heuristic, stated explicitly

A string literal is treated as a relative path when it is non-empty, does not
start with `/`, contains no `://` (URL), no `%` or `{` (format template), no
newline (prose/HTML), and either contains `/` or ends in
`.json .csv .txt .log .pem .db`.

For **dict values only**, an additional gate: the key must contain one of
`file path dir csv log json`. That gate is load-bearing, not cosmetic. Measured
on the real repo:

```
BROAD  (any dict value that looks like a path): 25 hits — ALL noise
    auth/auto_login.py:48   'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) …'
    screener/emailer.py:372 'Content-Type': 'application/json'
    screener/emailer.py:367 'type': 'text/html'
    utils/market_calendar.py:114 'Accept': 'application/json'
KEY-GATED: 0 hits
```

Ungated, this shape is undetectable — HTTP headers and MIME types look exactly
like relative paths. `"order_log_file"` sits well inside the gate.

The inversion is the point. The regex filtered on **what the string looks like**,
which is why it needed `_masked_spans()` to stop matching prose. The AST filters
on **where the string appears** — and a docstring can never *be* a parameter
default. Measured: a "looks like a path" test over every string constant in the
repo matches **309** strings (docstrings, HTML, prose); applied to those two
structural contexts it matches **0**, while catching both Finding #4 sites in the
pre-fix tree.

So `_masked_spans()` is deleted, not ported. The machinery added in August to
suppress false positives becomes unnecessary once the filter is structural.

### One coverage regression, handled rather than hidden

An AST scan cannot read a file that does not parse; the regex would still have
scanned it line-by-line. The existing test
`test_relative_path_scan_degrades_gracefully_on_unparseable_file` asserts only
`status in ("WARN", "PASS")`, so a silent skip would have passed it — trading one
blind spot for another, inside the fix for a blind spot.

Unparseable files are therefore **counted and reported**, never dropped:

```
WARN — 1 relative-path constant(s) found in 2 files — review for cwd-dependency
       risk; 1 file(s) could not be parsed and were NOT scanned
       NOT SCANNED (unparseable): broken.py: SyntaxError
```

Same rule as `check_run_completion()`'s out-of-window logs.

### Backtest against real history

Every relative-path bug this audit and its predecessors found, replayed against
the **actual pre-fix source from git** — not synthetic fixtures — with the old
regex run alongside:

| Commit / file | Bug | OLD regex | NEW AST |
|---|---|---|---|
| `cb678a1:signal_runner.py` | `AMO_CONFIG["order_log_file"]` + 4 `Path()` constants | 4 | **5** |
| `3ed2966:signal_runner.py` | `NEWS_FLAGS_FILE` (Jul 17) + `order_log_file` (Jul 18) | 6 | **7** |
| `c6ec08a:signal_runner.py` | `Path()`s already fixed; `order_log_file` still relative | **0** | **1** |
| `90fd7fa:correlation_check.py` | Finding #4 `portfolio_state_path` default | **0** | **1** |
| `90fd7fa:paper_portfolio.py` | Finding #4 `state_file` default | **0** | **1** |

The third row is the sharpest. At `c6ec08a` the module's `Path()` constants had
already been fixed to `_ROOT / "…"` — so Check 6 reported **completely clean**
while `"order_log_file": "paper_trading/amo_orders.csv"` sat relative in the same
file. It outlived the constants it shipped alongside precisely because nothing
could see it. That is the entire finding in one commit.

The new scan is a strict superset on every case.

### Real-repo run (not a scratch copy)

```
STATUS : PASS
MESSAGE: 0 relative-path constants found in 50 scanned files
HITS   : []
UNPARSEABLE: []
```

No new hits, no noise. The tree is genuinely clean now that #4 is fixed.

### Verification

| Check | Result |
|---|---|
| **All 42 pre-existing Check 6 tests, unchanged** | **42 passed** — including the three docstring-masking tests, which now pass structurally rather than via `_masked_spans` |
| New tests | **28 passed** |
| Mutation — restore the old regex | **15 of 28 fail** |
| Backtest vs real pre-fix source | all 6 historical cases caught (table above) |
| False positives | 0 on absolute paths, `Path(__file__)`, URLs, `%`/`{}` templates, HTTP headers, MIME types |
| Check 6 on the real tree | `PASS — 0 relative-path constants found in 50 scanned files` |
| Full suite | **462 passed**, 1 pre-existing environmental failure |

### Severity — a tooling-completeness finding

**MEDIUM**, and the reasoning differs from a trading-logic finding. Nothing here
moved money or produced a wrong number; measured directly, the current tree is
clean, so there is no live defect behind the gap.

What it costs is *epistemic*. Check 6 runs post-deploy and its PASS is read as
evidence that the repo has no cwd-dependent paths. For a week it certified a
property it could not evaluate, and Finding #4 shipped underneath that PASS.
A detector that reports clean over a class it cannot see is worse than no
detector, because no detector prompts a manual look and a false PASS ends the
inquiry. That is why this rates above a cosmetic issue despite zero trading
impact — and below the findings that reached real state or real reports.

`import ast` was missing on the first attempt. The check caught it itself —
`FAIL — Exception: name 'ast' is not defined` — because it wraps its body in a
try/except that degrades to FAIL rather than crashing the run. Same class as the
`import re` slip earlier in this audit, caught faster because the surrounding
code was built to surface it.

---

## Closing note — what the fifteen findings had in common (Aug 26 2026)

Fifteen findings is the uninteresting number. The useful one: **five of the
fifteen were fixed not by correcting a value, but by removing the opportunity to
get it wrong.** That is a claim about what kind of engineering this was, and it
is worth more than the count.

Those five, named so the claim is checkable rather than asserted — an earlier
draft of this note gave the figure without listing them, which is exactly the
kind of unverifiable number this audit spent two days catching elsewhere:

| # | What was removed |
|---|---|
| **#1** | dry-run safety stopped being an `if apply_fills:` repeated at N call sites and became a plan/report/execute split in which only one function writes |
| **#3** | fixed at all five layers deliberately redundantly, so no layer depends on another holding |
| **#4** | regression test walks the AST of every production file, closing the class rather than the two instances |
| **#14** | a fail-loud command replaced a written warning — chosen *because* the warning had already been written, read, and failed |
| **#15** | detection filters on where a node appears in the syntax tree, not on what a string looks like |

The findings were not fifteen unrelated bugs. They were a handful of failure
shapes, recurring in unrelated files, several of them appearing more than once
*within this audit* — including three times in the audit's own instruments.

### 1. Text-matching where structure-matching was needed

*A string being present is not the same claim as it being the operative code.*

- `system_health_check.py`'s scanner matched **its own docstring** and reported a
  problem that did not exist (Aug 25). Fixed by tokenizing and skipping STRING
  and COMMENT spans.
- The first regression test for Finding #3 asserted
  `re.search(r"^\s*if shares <= 0:", src)` and was **demonstrated to pass on a
  docstring** while the real guard stayed `== 0` in another function. Fixed by an
  AST walk asserting the operative `if` node's operator.

Twice in two days, in unrelated files. Parsing structure is not a stylistic
preference over pattern-matching text; it is a different and stronger claim.

### 2. Plausible-but-wrong data at the expected path

*A thing existing where it is expected is not the same claim as it being the
real one.*

- Finding #14: the local `portfolio_state.json`, read as live state, reporting
  ₹29,106.13 against a true ₹243.49.
- The `nifty500_cache.json` divergence found during the Aug 24 deploy — the
  local copy read `fetched_at: 2026-07-12`, the server's `2026-08-23`.

Neither file announced anything. Both sat at the documented path and parsed
cleanly.

### 3. Permanently-green checks

*The dangerous failure is the one nothing complains about.* A permanently-red
check is annoying and gets fixed. A permanently-green one is trusted.

- The health check that scanned a window in which no logs existed, and passed.
- Counters maintained by hand in each branch, which could not disagree with
  themselves — Audit2 Finding #4's double-counted `GAP_EXIT` was invisible until
  the counters were rederived from the decision set.

### 4. Guards that disagree about the same contract

*Every duplicated decision is a future disagreement.*

- Finding #3: four call sites across three modules each independently deciding
  what "no shares" meant, all four choosing `== 0` against a producer whose own
  contract was `<= 0`.
- Finding #2: the ETF cash-gate estimator and the real unwind sharing the delta
  math but not the *decision*, so they disagreed at exactly one tier boundary.

### 5. Discipline where structure was available

This is the throughline, and it is where the five fixes above sit.

- Finding #1: dry-run safety enforced by `if apply_fills:` at N call sites,
  replaced by a plan/report/execute split in which only one function writes.
  Safety stopped being a discipline and became a property of the call graph.
- Finding #3: fixed at all five layers deliberately redundantly, so no layer
  depends on another holding.
- Finding #14: fixed with a fail-loud command rather than another warning —
  **specifically because the warning had already been written, had already been
  read, and had already failed.** That is not an abstract argument about
  documentation versus tooling; it is an experimental result.

### 6. A detector whose blind spot is shaped exactly like what it was built to catch

*Trustworthy and complete are different properties.*

This is the sharpest shape because it hides inside the fix for the others.

- **Finding #15.** `system_health_check.py`'s Check 6 exists to catch
  cwd-dependent paths. It matched an assignment wrapping `Path()`. The case that
  motivated building it — `AMO_CONFIG["order_log_file"]` — is a dict value, and
  was never covered. At commit `c6ec08a` the module's `Path()` constants had
  already been fixed to `_ROOT / "…"`, so the check reported **completely clean**
  while `"order_log_file": "paper_trading/amo_orders.csv"` sat relative in the
  same file. It outlived the constants it shipped alongside precisely because
  nothing could see it. Finding #4 then shipped underneath that same PASS.
- The Aug 25 fix had made that check *trustworthy* — it stopped matching its own
  docstring. Nobody asked whether it was *complete*. Those are different
  properties, and a check that has been repaired once reads as reliable.
- **The audit's own regression suite**, where three tests asserted the defect's
  continued existence and went green by construction — a suite that would have
  turned red the moment someone fixed duplicate handling.

A false negative from a detector is worse than having no detector, because no
detector prompts a manual look and a PASS ends the inquiry. Every instance here
was found by asking what a green check *cannot see*, never by the check itself.

### What follows from this

The recurring shapes are worth more than the individual fixes, because the fixes
are done and the shapes are not. When reviewing anything in this repo:

- If a check asserts something about **which code path is real**, parse it. Do
  not scan for text.
- If a file is being read as authoritative, establish **provenance** before
  content. Existence at the right path proves nothing.
- If a check has never failed, find out whether it *can*.
- Ask what a passing check **cannot see**, not just whether it passes. A check
  repaired once reads as reliable; trustworthy and complete are different
  properties, and Check 6 was the former for a week while being neither.
- If two places decide the same thing, they will eventually decide differently.
- Prefer the fix that removes the opportunity over the fix that corrects the
  value — and when a mitigation has already been tried and failed, that is
  evidence, not a reason to try it again more emphatically.

One process note, recorded because it is part of the result: several findings in
this audit were caught only when a write-up was **re-derived against the actual
code** rather than against the previous session's description of it. That step
found `engine/backtester.py`'s three missed guards in Finding #3, and corrected
two wrong claims in Finding #2. Descriptions drift; code does not.
