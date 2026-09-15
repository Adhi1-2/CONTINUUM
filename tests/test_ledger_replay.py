from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import RecoveryContract, RecoverySafety, Run
from continuum.recovery import MemoryLedgerBackend, RecoveryLedger
from continuum.storage import SQLiteStorage


def test_stale_replay_is_detected() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    mgr = CheckpointManager(storage)
    mgr.checkpoint("run_1", environment=capture("run_1", StaticProvider(dataset="v1")))

    storage.append_event(
        "run_1",
        EventType.EVIDENCE_ADDED,
        {"evidence_id": "e1", "summary": "s", "source": "dataset"},
    )
    mgr.checkpoint("run_1", environment=capture("run_1", StaticProvider(dataset="v1")))

    versions = list(storage.list_versions("run_1"))
    assert len(versions) >= 2

    ledger = RecoveryLedger(MemoryLedgerBackend())
    high_watermark = max(versions)
    contract = RecoveryContract(
        run_id="run_1",
        checkpoint_version=high_watermark,
        recovery_status=RecoverySafety.REQUIRES_REPAIR,
        reason="watermark",
    )
    ledger.append_decision("run_1", contract)

    stale_state = storage.get_version("run_1", versions[0])
    report = ledger.reconcile("run_1", stale_state)

    assert report.drift is True
    assert any("behind the contract checkpoint" in d for d in report.details)


def test_current_state_does_not_drift() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    mgr = CheckpointManager(storage)
    mgr.checkpoint("run_1", environment=capture("run_1", StaticProvider(dataset="v1")))

    ledger = RecoveryLedger(MemoryLedgerBackend())
    version = list(storage.list_versions("run_1"))[-1]
    contract = RecoveryContract(
        run_id="run_1",
        checkpoint_version=version,
        recovery_status=RecoverySafety.SAFE_TO_RESUME,
        reason="ok",
    )
    ledger.append_decision("run_1", contract)

    current_state = storage.get_version("run_1", version)
    report = ledger.reconcile("run_1", current_state)

    assert report.drift is False
