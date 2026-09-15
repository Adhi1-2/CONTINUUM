"""End-to-end recovery walkthrough (issue #174).

Runs one realistic failure against the real library: an agent dies mid side
effect while its dataset moves underneath it, then a fresh session assesses,
reconciles, and resumes. The output of this script is embedded verbatim in
docs/recovery_walkthrough.md, so the doc can never drift from reality.

Run it yourself:

    uv run python examples/recovery_walkthrough.py
"""

from __future__ import annotations

from continuum.actions import ActionLedger, ProbeReconciler, Resolution, reconcile_pending
from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import Run
from continuum.provenance_map import summarize
from continuum.recovery import RecoveryEngine
from continuum.storage import SQLiteStorage

RUN = "walkthrough"


def build_run(storage: SQLiteStorage) -> None:
    storage.create_run(Run(run_id=RUN, goal="Summarize quarterly reports"))
    storage.append_event(RUN, EventType.RUN_STARTED, {"goal": "Summarize quarterly reports"})
    storage.append_event(
        RUN, EventType.DEPENDENCY_DECLARED, {"resource": "dataset", "version": "v3"}
    )
    storage.append_event(
        RUN,
        EventType.EVIDENCE_ADDED,
        {"evidence_id": "q3_sales", "summary": "Q3 sales extract", "source": "dataset"},
    )
    storage.append_event(
        RUN,
        EventType.FINDING_ADDED,
        {"finding_id": "f_rev", "claim": "Revenue up 12%", "evidence": ["q3_sales"]},
    )
    storage.append_event(
        RUN,
        EventType.DECISION_CREATED,
        {"decision_id": "d_send", "decision": "send summary to #reports", "evidence": ["f_rev"]},
    )
    CheckpointManager(storage).checkpoint(
        RUN, environment=capture(RUN, StaticProvider(dataset="v3"))
    )


def main() -> None:
    storage = SQLiteStorage(":memory:")
    build_run(storage)

    print("== 1. the agent acts, then dies mid side effect ==")
    ledger = ActionLedger(storage, RUN)
    outcome = ledger.claim("slack.notify", {"channel": "#reports"})
    print(f"claimed action key: {outcome.key}")
    ledger.fail(outcome.key, "process killed mid-request", certain=False)
    print("recorded outcome: uncertain (the request may or may not have landed)")

    print()
    print("== 2. a fresh session assesses; the dataset moved v3 -> v4 ==")
    current = capture(RUN, StaticProvider(dataset="v4"))
    decision = RecoveryEngine(storage).assess(RUN, current_environment=current)
    print(decision.render())

    print()
    print("== 3. the sealed contract explains itself (Phase 1 fields) ==")
    contract = decision.contract
    print(f"recovery_status:     {contract.recovery_status.value}")
    print(f"invalidated:         {contract.invalidated}")
    print(f"next_allowed_action: {contract.next_allowed_action}")
    print(f"reason:              {contract.reason}")

    print()
    print("== 4. evidence provenance, canonical view ==")
    evidence = decision.state.evidence[0]
    view = summarize(evidence.provenance.origin, evidence.status)
    print(
        f"evidence q3_sales: origin={evidence.provenance.origin.value} "
        f"status={evidence.status.value} canonical={view.primary.value}"
    )

    print()
    print("== 5. reconcile the uncertain side effect with a probe ==")
    report = reconcile_pending(
        ledger,
        ProbeReconciler(
            lambda action: Resolution(occurred=False, note="slack API shows no message")
        ),
    )
    print(
        f"resolved_completed: {len(report.resolved_completed)}  "
        f"resolved_failed: {len(report.resolved_failed)}  "
        f"unresolved: {len(report.unresolved)}"
    )

    print()
    print("== 6. re-assess after reconciliation and repair ==")
    repaired = capture(RUN, StaticProvider(dataset="v4"))
    final = RecoveryEngine(storage).assess(RUN, current_environment=repaired)
    print(final.render())


if __name__ == "__main__":
    main()
