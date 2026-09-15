from continuum.checkpoint import CheckpointManager
from continuum.checkpoint.policy import CheckpointTrigger
from continuum.events import EventType
from continuum.models import RecoveryContract, RecoverySafety, Run
from continuum.recovery import MemoryLedgerBackend, RecoveryLedger, cleanup_ephemeral_artifacts
from continuum.storage import SQLiteStorage


def test_referenced_anchors_survive_cleanup() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    mgr = CheckpointManager(storage)

    a = mgr.checkpoint("run_1", trigger=CheckpointTrigger.MANUAL, reason="ephemeral 1")
    storage.append_event(
        "run_1", EventType.EVIDENCE_ADDED, {"evidence_id": "ev1", "summary": "s1", "source": "d"}
    )
    b = mgr.checkpoint("run_1", trigger=CheckpointTrigger.RECOVERY, reason="anchor")
    storage.append_event(
        "run_1", EventType.EVIDENCE_ADDED, {"evidence_id": "ev2", "summary": "s2", "source": "d"}
    )
    c = mgr.checkpoint("run_1", trigger=CheckpointTrigger.MANUAL, reason="ephemeral 2")

    ledger = RecoveryLedger(MemoryLedgerBackend())
    contract = RecoveryContract(
        run_id="run_1",
        checkpoint_version=b.version,
        recovery_status=RecoverySafety.REQUIRES_REPAIR,
        reason="referenced",
    )
    ledger.append_decision("run_1", contract, anchor=True)

    deleted = cleanup_ephemeral_artifacts(storage, ledger, "run_1", keep_anchors=True)

    assert a.checkpoint_id in deleted
    assert c.checkpoint_id in deleted
    remaining = {cp.checkpoint_id for cp in storage.list_checkpoints("run_1")}
    assert b.checkpoint_id in remaining
    assert len(remaining) == 1


def test_unreferenced_non_anchor_is_removed() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    mgr = CheckpointManager(storage)
    only = mgr.checkpoint("run_1", trigger=CheckpointTrigger.MANUAL, reason="temp")
    ledger = RecoveryLedger(MemoryLedgerBackend())

    deleted = cleanup_ephemeral_artifacts(storage, ledger, "run_1")

    assert deleted == [only.checkpoint_id]
    assert list(storage.list_checkpoints("run_1")) == []
