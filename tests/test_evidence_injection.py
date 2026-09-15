from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import RecoverySafety, Run
from continuum.recovery import RecoveryEngine, verify_contract
from continuum.storage import SQLiteStorage


def _seed_run(storage: SQLiteStorage) -> None:
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    storage.append_event(
        "run_1", EventType.DEPENDENCY_DECLARED, {"resource": "dataset", "version": "v3"}
    )
    storage.append_event(
        "run_1",
        EventType.EVIDENCE_ADDED,
        {"evidence_id": "e1", "summary": "s", "source": "dataset"},
    )
    CheckpointManager(storage).checkpoint(
        "run_1", environment=capture("run_1", StaticProvider(dataset="v3"))
    )


def _contract_for(storage: SQLiteStorage):
    engine = RecoveryEngine(storage)
    current = capture("run_1", StaticProvider(dataset="v4"))
    decision = engine.assess("run_1", current_environment=current)
    return decision.contract, decision.validation


def test_evidence_derived_from_validation_not_spoofed() -> None:
    storage = SQLiteStorage(":memory:")
    _seed_run(storage)
    contract, validation = _contract_for(storage)

    derived = sorted(
        f"{e.component.value}{f':{e.component_id}' if e.component_id else ''}: {e.detail}"
        for e in validation.report.statuses
        if e.detail
    )
    for item in derived:
        assert item in contract.evidence

    spoofed = ["fake: injected detail"]
    assert spoofed[0] not in contract.evidence


def test_empty_evidence_still_verifies_but_is_weaker() -> None:
    from continuum.models import RecoveryContract
    from continuum.recovery.contract import seal_contract

    storage = SQLiteStorage(":memory:")
    _seed_run(storage)
    rich, _ = _contract_for(storage)
    assert len(rich.evidence) > 0
    assert verify_contract(rich) is True

    empty = seal_contract(
        RecoveryContract(
            run_id="run_1",
            checkpoint_version=0,
            recovery_status=RecoverySafety.SAFE_TO_RESUME,
            evidence=[],
            reason="",
        )
    )

    assert empty.evidence == []
    assert verify_contract(empty) is True
    assert len(empty.evidence) < len(rich.evidence)
