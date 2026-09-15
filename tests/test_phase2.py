from __future__ import annotations

from collections.abc import Iterator

import pytest

from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import Decision, Run, StateStatus
from continuum.recovery import DependencyGraph, RecoveryEngine, RepairKind
from continuum.state.validator import validate_state
from continuum.storage import SQLiteStorage


@pytest.fixture
def store() -> Iterator[SQLiteStorage]:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="Two dependency recovery"))
    storage.append_event(
        "run_1", EventType.RUN_STARTED, {"goal": "Two dependency recovery", "total": 10}
    )
    yield storage
    storage.close()


def env_multi(**versions: str):
    return capture("run_1", StaticProvider(**versions))


def seed_two(store: SQLiteStorage) -> None:
    """Two dependencies, each with its own evidence and finding."""
    store.append_event(
        "run_1", EventType.DEPENDENCY_DECLARED, {"resource": "dataset", "version": "v3"}
    )
    store.append_event(
        "run_1", EventType.DEPENDENCY_DECLARED, {"resource": "other", "version": "v3"}
    )
    store.append_event(
        "run_1",
        EventType.EVIDENCE_ADDED,
        {"evidence_id": "ev_a", "summary": "a", "source": "dataset"},
    )
    store.append_event(
        "run_1",
        EventType.EVIDENCE_ADDED,
        {"evidence_id": "ev_b", "summary": "b", "source": "other"},
    )
    store.append_event(
        "run_1", EventType.FINDING_ADDED, {"finding_id": "f_a", "claim": "A", "evidence": ["ev_a"]}
    )
    store.append_event(
        "run_1", EventType.FINDING_ADDED, {"finding_id": "f_b", "claim": "B", "evidence": ["ev_b"]}
    )
    CheckpointManager(store).checkpoint("run_1", environment=env_multi(dataset="v3", other="v3"))


def _state(store: SQLiteStorage):
    return CheckpointManager(store).restore("run_1").state


# --- the impact graph ------------------------------------------------------ #


def test_dependency_graph_finds_the_impacted_subgraph(store: SQLiteStorage) -> None:
    seed_two(store)
    graph = DependencyGraph(_state(store))
    impacted = graph.impacted_by({"dataset"})

    assert impacted.evidence == {"ev_a"}
    assert impacted.findings == {"f_a"}
    assert impacted.decisions == frozenset()
    # The clean dependency's subtree is not implicated.
    assert "ev_b" not in impacted.all_items
    assert "f_b" not in impacted.all_items


def test_impact_graph_is_transitive(store: SQLiteStorage) -> None:
    seed_two(store)
    state = _state(store)
    dec = Decision(decision="use A", evidence=["f_a"], provenance=state.findings[0].provenance)
    state.decisions.append(dec)
    impacted = DependencyGraph(state).impacted_by({"dataset"})
    assert dec.decision_id in impacted.decisions


# --- scoped validation ----------------------------------------------------- #


def test_scoped_validation_only_taints_the_subtree(store: SQLiteStorage) -> None:
    seed_two(store)
    outcome = validate_state(
        _state(store),
        current_environment=env_multi(dataset="v4", other="v4"),
        checkpoint_environment=env_multi(dataset="v3", other="v3"),
        scope={"dataset"},
    )

    ids = {e.component_id for e in outcome.report.statuses}
    assert ids == {"dataset", "ev_a", "f_a"}
    # The clean dependency is preserved, not re-tainted.
    other = outcome.state.dependency("other")
    assert other is not None and other.status is StateStatus.VALID
    assert outcome.safe is False


def test_scoped_validation_matches_full_when_only_one_breaks(store: SQLiteStorage) -> None:
    seed_two(store)
    scoped = validate_state(
        _state(store),
        current_environment=env_multi(dataset="v4", other="v3"),
        checkpoint_environment=env_multi(dataset="v3", other="v3"),
        scope={"dataset"},
    )
    full = validate_state(
        _state(store),
        current_environment=env_multi(dataset="v4", other="v3"),
        checkpoint_environment=env_multi(dataset="v3", other="v3"),
    )
    scoped_ids = {e.component_id for e in scoped.report.statuses}
    full_ids = {e.component_id for e in full.report.statuses}
    assert scoped_ids == full_ids & {"dataset", "ev_a", "f_a"}


# --- scoped recovery preserves clean deps ----------------------------------- #


def test_scoped_assess_preserves_the_clean_dependency(store: SQLiteStorage) -> None:
    seed_two(store)
    current = env_multi(dataset="v4", other="v4")
    full = RecoveryEngine(store).assess("run_1", current_environment=current)
    scoped = RecoveryEngine(store).assess("run_1", current_environment=current, scope={"dataset"})

    # Full recovery would revalidate both dependencies.
    assert any(
        s.kind is RepairKind.REVALIDATE_DEPENDENCY and s.target == "other" for s in full.plan.steps
    )
    # Scoped recovery leaves "other" alone entirely.
    assert "other" not in str(scoped.contract.invalidated)
    assert not any(s.target == "other" for s in scoped.plan.steps)
    # ...but still repairs the broken dependency's subtree.
    assert any(
        s.kind is RepairKind.REVALIDATE_DEPENDENCY and s.target == "dataset"
        for s in scoped.plan.steps
    )


def test_assess_scoped_alias_equals_scope_kwarg(store: SQLiteStorage) -> None:
    seed_two(store)
    current = env_multi(dataset="v4", other="v4")
    via_kwarg = RecoveryEngine(store).assess(
        "run_1", current_environment=current, scope={"dataset"}
    )
    via_alias = RecoveryEngine(store).assess_scoped(
        "run_1", {"dataset"}, current_environment=current
    )

    assert str(via_kwarg.contract.invalidated) == str(via_alias.contract.invalidated)
    assert [s.action_name for s in via_kwarg.plan.steps] == [
        s.action_name for s in via_alias.plan.steps
    ]


def test_scoped_contract_records_its_scope(store: SQLiteStorage) -> None:
    seed_two(store)
    decision = RecoveryEngine(store).assess(
        "run_1", current_environment=env_multi(dataset="v4", other="v4"), scope={"dataset"}
    )
    assert any(
        "localized recovery scoped to: dataset" in line for line in decision.contract.evidence
    )
