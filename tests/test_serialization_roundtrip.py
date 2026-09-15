"""Serialization round-trip hardening (issue #164).

Recovery depends on serializing and deserializing state exactly. These property
tests pin two guarantees:

1. Round-trips lose nothing: every state model, and a fully projected
   SemanticState, survive JSON and json-dict round-trips byte-for-byte in
   structure (model equality).
2. Old formats load on new code: fixtures stripped of the newest additive
   fields still validate, picking up backward-compatible defaults.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from continuum.events import EventType
from continuum.models import (
    Action,
    Approval,
    Decision,
    EnvironmentSnapshot,
    EnvResource,
    Evidence,
    Finding,
    Origin,
    PendingWork,
    Provenance,
    RecoveryContract,
    RecoverySafety,
    Run,
    StateCheckpoint,
)
from continuum.state.semantic import project
from continuum.storage import SQLiteStorage

JSON_TEXT = st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != "")
JSON_SCALAR = st.one_of(JSON_TEXT, st.integers(-1000, 1000), st.booleans())
JSON_MAPPING = st.dictionaries(JSON_TEXT, JSON_SCALAR, max_size=3)


def _assert_round_trip(model, cls) -> None:
    from_json = cls.model_validate_json(model.model_dump_json())
    assert from_json == model
    from_dict = cls.model_validate(model.model_dump(mode="json"))
    assert from_dict == model


@given(
    name=JSON_TEXT,
    kind=st.sampled_from(["resource", "dataset", "package"]),
    version=st.one_of(st.none(), JSON_TEXT),
    metadata=JSON_MAPPING,
)
@settings(max_examples=50)
def test_env_resource_round_trip(name: str, kind: str, version, metadata: dict) -> None:
    _assert_round_trip(
        EnvResource(name=name, kind=kind, version=version, metadata=metadata), EnvResource
    )


@given(summary=JSON_TEXT, source=st.one_of(st.none(), JSON_TEXT))
@settings(max_examples=50)
def test_evidence_round_trip(summary: str, source) -> None:
    _assert_round_trip(Evidence(summary=summary, source=source), Evidence)


@given(claim=JSON_TEXT, confidence=st.floats(0.0, 1.0, allow_nan=False))
@settings(max_examples=50)
def test_finding_round_trip(claim: str, confidence: float) -> None:
    _assert_round_trip(Finding(claim=claim, confidence=confidence), Finding)


@given(decision=JSON_TEXT, reason=JSON_TEXT)
@settings(max_examples=50)
def test_decision_round_trip(decision: str, reason: str) -> None:
    _assert_round_trip(Decision(decision=decision, reason=reason), Decision)


@given(
    action_type=JSON_TEXT,
    arguments=JSON_MAPPING,
    dep_scope=st.one_of(st.none(), JSON_TEXT),
)
@settings(max_examples=50)
def test_action_round_trip(action_type: str, arguments: dict, dep_scope) -> None:
    _assert_round_trip(
        Action(run_id="r", action_type=action_type, arguments=arguments, dep_scope=dep_scope),
        Action,
    )


@given(reason=JSON_TEXT, verified=st.lists(JSON_TEXT, max_size=3))
@settings(max_examples=50)
def test_recovery_contract_round_trip(reason: str, verified: list) -> None:
    contract = RecoveryContract(
        run_id="r", recovery_status=RecoverySafety.SAFE_TO_RESUME, verified=verified, reason=reason
    )
    _assert_round_trip(contract, RecoveryContract)


@given(origin=st.sampled_from(list(Origin)))
@settings(max_examples=20)
def test_provenance_round_trip(origin) -> None:
    _assert_round_trip(Provenance(origin=origin), Provenance)


def test_composite_models_round_trip() -> None:
    now = datetime.now(UTC)
    resource = EnvResource(name="dataset", version="v3")
    snapshot = EnvironmentSnapshot(run_id="r", resources={"dataset": resource})
    evidence = Evidence(evidence_id="e1", summary="s", source="dataset")
    finding = Finding(finding_id="f1", claim="c", evidence=["e1"])
    decision = Decision(decision_id="d1", decision="act", evidence=["f1"])
    work = PendingWork(task_id="t1", description="do", prerequisite=["t0"])
    approval = Approval(approval_id="a1", subject="send", granted_at=now - timedelta(days=1))

    for model, cls in (
        (snapshot, EnvironmentSnapshot),
        (evidence, Evidence),
        (finding, Finding),
        (decision, Decision),
        (work, PendingWork),
        (approval, Approval),
    ):
        _assert_round_trip(model, cls)

    # StateCheckpoint needs a real state; build one through projection.
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="r", goal="g"))
    storage.append_event("r", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    events = storage.read_events("r")
    state = project("r", events)
    real_checkpoint = StateCheckpoint(
        checkpoint_id="c1", run_id="r", state=state, environment=snapshot
    )
    _assert_round_trip(real_checkpoint, StateCheckpoint)


def test_projected_state_round_trip_is_lossless() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="r", goal="g"))
    storage.append_event("r", EventType.RUN_STARTED, {"goal": "g", "total": 2})
    storage.append_event(
        "r", EventType.DEPENDENCY_DECLARED, {"resource": "dataset", "version": "v3"}
    )
    storage.append_event(
        "r", EventType.EVIDENCE_ADDED, {"evidence_id": "e1", "summary": "s", "source": "dataset"}
    )
    storage.append_event(
        "r", EventType.FINDING_ADDED, {"finding_id": "f1", "claim": "c", "evidence": ["e1"]}
    )
    storage.append_event(
        "r", EventType.DECISION_CREATED, {"decision_id": "d1", "decision": "a", "evidence": ["f1"]}
    )
    storage.append_event("r", EventType.WORK_COMPLETED, {"doc": 1})

    state = project("r", storage.read_events("r"))
    restored = type(state).model_validate_json(state.model_dump_json())
    assert restored == state
    # A second hop through a json-mode dict must also be lossless.
    redumped = type(state).model_validate(restored.model_dump(mode="json"))
    assert redumped == state


# --- old formats load on new code ------------------------------------------- #


def test_old_contract_without_phase1_fields_loads() -> None:
    current = RecoveryContract(
        run_id="r",
        recovery_status=RecoverySafety.REQUIRES_REPAIR,
        invalidated=["evidence:e1"],
        next_allowed_action="revalidate_dependency:dataset",
        evidence=["validation:external_dependency:dataset"],
        reason="1 component(s) need repair",
    )
    old = current.model_dump(mode="json")
    del old["evidence"]
    del old["reason"]
    loaded = RecoveryContract.model_validate(old)
    assert loaded.evidence == []
    assert loaded.reason == ""
    assert loaded.invalidated == current.invalidated
    assert loaded.next_allowed_action == current.next_allowed_action


def test_old_action_without_dep_scope_loads() -> None:
    current = Action(run_id="r", action_type="pkg.install", dep_scope="numpy")
    old = current.model_dump(mode="json")
    del old["dep_scope"]
    loaded = Action.model_validate(old)
    assert loaded.dep_scope is None
    assert loaded.action_type == current.action_type


def test_old_checkpoint_without_reason_loads() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="r", goal="g"))
    storage.append_event("r", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    state = project("r", storage.read_events("r"))
    current = StateCheckpoint(run_id="r", state=state, trigger="milestone", reason="before risk")
    old = current.model_dump(mode="json")
    del old["reason"]
    loaded = StateCheckpoint.model_validate(old)
    assert loaded.reason == ""
    assert loaded.trigger == "milestone"
    assert loaded.state == state


def test_components_without_provenance_block_load() -> None:
    current = Evidence(evidence_id="e1", summary="s", source="dataset")
    old = current.model_dump(mode="json")
    del old["provenance"]
    loaded = Evidence.model_validate(old)
    assert loaded.provenance == Provenance()
    assert loaded.summary == current.summary


@pytest.mark.parametrize(
    "cls",
    [EnvResource, Evidence, Finding, Decision, PendingWork, Approval, Action, RecoveryContract],
)
def test_default_constructed_models_round_trip(cls) -> None:
    fields = {}
    if cls is Action:
        fields = {"run_id": "r", "action_type": "t"}
    if cls is RecoveryContract:
        fields = {"run_id": "r", "recovery_status": RecoverySafety.SAFE_TO_RESUME}
    if cls is Decision:
        fields = {"decision": "d"}
    if cls is Finding:
        fields = {"claim": "c"}
    if cls is PendingWork:
        fields = {"description": "w"}
    if cls is Approval:
        fields = {"subject": "s"}
    if cls is EnvResource:
        fields = {"name": "n"}
    _assert_round_trip(cls(**fields), cls)
