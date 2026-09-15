# Human Gate Minimization Study

Too many human gates undermine autonomy, too few risks bad recovery. This note analyzes the Phase 6 scenarios to propose which cases truly need a human.

## Method

Ran the 13 Phase 6 scenarios via `continuum.benchmark.phase6` and grouped them by recovery decision. The harness is deterministic and uses the real engine, validator, ledger, and adapters.

## Observed rates

| Scenario | Decision | Needs human |
| --- | --- | --- |
| single_dependency_corruption | REPAIR_AND_RESUME | No |
| multi_dependency_corruption | REPAIR_AND_RESUME | No |
| external_edit_drift | REPAIR_AND_RESUME | No |
| ledger_tamper_detected | REQUEST_HUMAN | Yes |
| recovery_lease_exhaustion | REQUEST_HUMAN | Yes |
| adapter_failure_across_environments | REPAIR_AND_RESUME | No |
| checkpoint_rollback_correctness | REPAIR_AND_RESUME | No |
| concurrent_recovery_safety | REQUEST_HUMAN | Yes |
| large_state_recovery_latency | REPAIR_AND_RESUME | No |
| missing_dependency_graph_fallback | RESUME | No |
| human_verdict_honored | REQUEST_HUMAN | Yes |
| transient_network_failure_on_install | REPAIR_AND_RESUME | No |
| out_of_scope_side_effect | REPAIR_AND_RESUME | No |

Overall `REQUEST_HUMAN` in 4 of 13 scenarios (31 percent). The other 69 percent auto resolve via `RESUME` or `REPAIR_AND_RESUME` with a single next action.

## Proposal

- Keep a human gate for tamper, lease exhaustion, concurrent safety, and explicit human verdict. These are trust or contention signals that cannot be auto resolved without risking duplicate effects or lost audit.
- Auto resolve dependency drift, adapter failures that are retryable, transient network failures, and out of scope side effects. The ledger and validation already localize these without human input. The out of scope side effect scenario in `src/continuum/benchmark/phase6/scenarios.py:1` demonstrates that a scoped assessment can safely exclude an uncertain action tagged to another dependency.
- For missing dependency graph, the fallback is `RESUME` with unscoped validation, which is correct when the graph is unavailable per `src/continuum/analysis/depends.py:1`.

No external claims are made. Reproduce with `uv run python benchmarks/run.py` and `uv run pytest tests/test_phase6.py -v`.

## Next steps

- Add a per dependency human gate budget to `RecoveryLedger` so a noisy dependency does not exhaust the global attempt count.
- Consider `ProbeReconciler` as the auto path for uncertain side effects before escalating to human.
