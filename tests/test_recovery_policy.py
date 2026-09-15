from __future__ import annotations

import contextlib
from pathlib import Path

from continuum.adapters import GenericAgentAdapter
from continuum.analysis import DependencyGraph
from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import RecoveryMode, Run
from continuum.recovery import RecoveryEngine
from continuum.storage import SQLiteStorage


def _two_dep_run() -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="policy task"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    for dep in ("dataset", "model"):
        storage.append_event(
            "run_1", EventType.DEPENDENCY_DECLARED, {"resource": dep, "version": "v1"}
        )
    storage.append_event(
        "run_1",
        EventType.EVIDENCE_ADDED,
        {"evidence_id": "e1", "summary": "s", "source": "dataset"},
    )
    storage.append_event(
        "run_1", EventType.EVIDENCE_ADDED, {"evidence_id": "e2", "summary": "s", "source": "model"}
    )
    storage.append_event(
        "run_1", EventType.FINDING_ADDED, {"finding_id": "f1", "claim": "c", "evidence": ["e1"]}
    )
    storage.append_event(
        "run_1", EventType.FINDING_ADDED, {"finding_id": "f2", "claim": "c", "evidence": ["e2"]}
    )
    storage.append_event(
        "run_1",
        EventType.DECISION_CREATED,
        {"decision_id": "d1", "decision": "a", "evidence": ["f1"]},
    )
    storage.append_event(
        "run_1",
        EventType.DECISION_CREATED,
        {"decision_id": "d2", "decision": "a", "evidence": ["f2"]},
    )
    clean = capture("run_1", StaticProvider(dataset="v1", model="v1"))
    CheckpointManager(storage).checkpoint("run_1", environment=clean)
    return storage


def test_scoped_single_dep_prefers_localized_repair() -> None:
    # Issue #104: a single corrupted dependency should localize repair rather
    # than escalate to a whole-state reset.
    storage = _two_dep_run()
    corrupt = capture("run_1", StaticProvider(dataset="v2", model="v1"))
    engine = RecoveryEngine(storage)
    decision = engine.assess_scoped("run_1", ["dataset"], current_environment=corrupt)

    assert decision.mode is RecoveryMode.REPAIR_AND_RESUME
    # Only the broken dependency's subtree is invalidated; the clean one stays.
    by_id = {e.evidence_id: e for e in decision.validation.state.evidence}
    assert by_id["e1"].status.value != "valid"
    assert by_id["e2"].status.value == "valid"
    # The chosen rationale is threaded into the contract reason (Phase 1).
    assert decision.contract.reason


def test_transient_uncertain_action_escalates() -> None:
    # A side effect with unknown outcome must not be silently resumed.
    storage = _two_dep_run()
    # An exception escaping the side effect leaves its outcome uncertain.
    with contextlib.suppress(RuntimeError):
        GenericAgentAdapter(storage).intercept_action(
            "run_1",
            "notify.slack",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            arguments={"x": 1},
        )

    engine = RecoveryEngine(storage)
    decision = engine.assess("run_1")
    assert decision.mode in (RecoveryMode.REQUEST_HUMAN, RecoveryMode.WAIT)


def test_source_graph_unavailable_is_graceful(tmp_path: Path) -> None:
    # Issue #109: a missing or corrupt manifest must not raise; recovery can
    # fall back to whole-state behavior.
    missing = DependencyGraph(tmp_path / "does_not_exist")
    assert missing.declared == set()

    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "pyproject.toml").write_text("this ::: is not valid toml @@@\n", encoding="utf-8")
    assert DependencyGraph(corrupt).declared == set()


def test_recovery_seals_without_source_graph() -> None:
    # Even with no dependency manifest available, assess still produces a
    # contract (whole-state behavior), so analysis failure never blocks recovery.
    storage = _two_dep_run()
    engine = RecoveryEngine(storage)
    decision = engine.assess_scoped("run_1", [])
    assert decision.contract is not None
