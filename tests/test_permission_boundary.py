from continuum.checkpoint import CheckpointManager
from continuum.checkpoint.policy import CheckpointTrigger
from continuum.events import EventType
from continuum.models import RecoveryContract, RecoverySafety, Run
from continuum.recovery import MemoryLedgerBackend, RecoveryLedger
from continuum.storage import SQLiteStorage


def test_every_recovery_mutation_is_ledgered() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    mgr = CheckpointManager(storage)
    ledger = RecoveryLedger(MemoryLedgerBackend())

    checkpoint = mgr.checkpoint_on_recovery("run_1", reason="test anchor", environment=None)
    assert checkpoint.trigger == CheckpointTrigger.RECOVERY

    contract = RecoveryContract(
        run_id="run_1",
        checkpoint_version=checkpoint.version,
        recovery_status=RecoverySafety.REQUIRES_REPAIR,
        reason="needs repair",
    )
    ledger.append_decision("run_1", contract, anchor=True, gate="required")

    entries = ledger.entries("run_1")
    assert any(e.kind == "decision" and e.contract is not None for e in entries)
    assert any(e.gate == "required" for e in entries)

    assert len(ledger.entries("run_1")) >= 1


def test_unledgered_checkpoint_is_visible_as_drift() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    mgr = CheckpointManager(storage)
    ledger = RecoveryLedger(MemoryLedgerBackend())

    mgr.checkpoint("run_1", trigger=CheckpointTrigger.MANUAL, reason="ephemeral")

    assert ledger.entries("run_1") == []
