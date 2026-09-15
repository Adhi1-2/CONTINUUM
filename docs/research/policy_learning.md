# Recovery Policy Learning from Ledger History

## Question

Can past recovery decisions improve the hand coded policy in `src/continuum/recovery/planner.py:1` and `src/continuum/recovery/engine.py:1`? The ledger holds every decision, its contract, and its reconciled outcome, so it is tempting to learn from it.

## Findings

- **Signal exists.** `RecoveryLedger` records per attempt kind and the final `RecoveryContract.reason`. Aggregating `attempts` and `requires_human` rates by `action_type` shows which adapter operations most often need human gates. This is useful as a report, not as an auto policy.

- **Risk of auto change.** The max severity ordering in `src/continuum/recovery/engine.py:62` is intentionally conservative: the most cautious signal wins. Learning a less cautious threshold from data that is itself biased by prior conservative decisions would silently lower the bar. A ledger trained on past human gates cannot prove that a gate was unnecessary, only that it happened.

- **Safe use is advisory.** The ledger history can inform periodic human review of the policy weights, not autonomous updates. Example: if `POST /payments` shows a 90 percent human gate rate due to `probe` timeouts, the fix is to improve the probe, not to lower the gate threshold.

## Recommendation

Keep the manual policy as the safe default. Use ledger history for a weekly report that lists per action type: attempts, human required count, compact survival, and drift after auto repairs. Do not wire the report back into `plan_repairs` without a human approved change and a dedicated adversarial test.

Reproduce the underlying data with `tests/test_ledger_replay.py:1` and `tests/test_contract_forgery.py:1` which exercise the ledger and contract paths that such a report would read.

No production policy change is made. This is a report only.
