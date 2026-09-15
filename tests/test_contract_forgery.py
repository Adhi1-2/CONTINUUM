from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import Run
from continuum.recovery import RecoveryEngine, verify_contract
from continuum.storage import SQLiteStorage


def _contract(storage: SQLiteStorage, run_id: str = "run_1"):
    engine = RecoveryEngine(storage)
    current = capture(run_id, StaticProvider(dataset="v4"))
    decision = engine.assess(run_id, current_environment=current)
    return decision.contract


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


def test_forged_digest_is_rejected() -> None:
    storage = SQLiteStorage(":memory:")
    _seed_run(storage)
    contract = _contract(storage)
    assert verify_contract(contract) is True
    forged = contract.model_copy(update={"integrity_hash": "bad" * 16})
    assert verify_contract(forged) is False


def test_forged_reason_and_evidence_are_rejected() -> None:
    storage = SQLiteStorage(":memory:")
    _seed_run(storage)
    contract = _contract(storage)
    assert verify_contract(contract) is True

    tampered_reason = contract.model_copy(update={"reason": "trust me, it is fine"})
    assert verify_contract(tampered_reason) is False

    tampered_evidence = contract.model_copy(update={"evidence": ["fake: injected detail"]})
    assert verify_contract(tampered_evidence) is False


def test_missing_hash_never_verifies() -> None:
    storage = SQLiteStorage(":memory:")
    _seed_run(storage)
    contract = _contract(storage)
    stripped = contract.model_copy(update={"integrity_hash": None})
    assert verify_contract(stripped) is False
