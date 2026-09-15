from __future__ import annotations

from pathlib import Path

from continuum.actions.ledger import ActionLedger
from continuum.analysis import DependencyGraph
from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import RecoveryMode, Run
from continuum.recovery import RecoveryEngine
from continuum.storage import SQLiteStorage


def _run_with_two_deps() -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="scoping"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    for dep in ("dataset", "model"):
        storage.append_event(
            "run_1", EventType.DEPENDENCY_DECLARED, {"resource": dep, "version": "v1"}
        )
    clean = capture("run_1", StaticProvider(dataset="v1", model="v1"))
    CheckpointManager(storage).checkpoint("run_1", environment=clean)
    return storage


def _claim_uncertain(storage: SQLiteStorage, action_type: str, dep_scope: str | None) -> str:
    # A claim left STARTED counts as an uncertain side effect to the engine.
    outcome = ActionLedger(storage, "run_1").claim(action_type, {"x": 1}, dep_scope=dep_scope)
    return outcome.key


def test_out_of_scope_side_effect_does_not_block_scoped_decision() -> None:
    storage = _run_with_two_deps()
    _claim_uncertain(storage, "model.push", dep_scope="model")

    engine = RecoveryEngine(storage)
    decision = engine.assess_scoped(
        "run_1", ["dataset"], current_environment=capture("run_1", StaticProvider(dataset="v1"))
    )

    assert decision.mode is RecoveryMode.RESUME
    assert decision.uncertain_actions == ()


def test_in_scope_side_effect_still_blocks() -> None:
    storage = _run_with_two_deps()
    _claim_uncertain(storage, "dataset.publish", dep_scope="dataset")

    engine = RecoveryEngine(storage)
    decision = engine.assess_scoped(
        "run_1", ["dataset"], current_environment=capture("run_1", StaticProvider(dataset="v1"))
    )

    assert decision.mode is not RecoveryMode.RESUME
    assert len(decision.uncertain_actions) == 1
    assert decision.uncertain_actions[0].dep_scope == "dataset"


def test_untagged_side_effect_blocks_even_when_scoped() -> None:
    storage = _run_with_two_deps()
    _claim_uncertain(storage, "notify.slack", dep_scope=None)

    engine = RecoveryEngine(storage)
    decision = engine.assess_scoped(
        "run_1", ["dataset"], current_environment=capture("run_1", StaticProvider(dataset="v1"))
    )

    assert decision.mode is not RecoveryMode.RESUME
    assert len(decision.uncertain_actions) == 1
    assert decision.uncertain_actions[0].dep_scope is None


def test_whole_state_assess_sees_every_uncertain_action() -> None:
    storage = _run_with_two_deps()
    _claim_uncertain(storage, "model.push", dep_scope="model")
    _claim_uncertain(storage, "dataset.publish", dep_scope="dataset")

    engine = RecoveryEngine(storage)
    decision = engine.assess("run_1")

    assert len(decision.uncertain_actions) == 2


def test_source_graph_surfaces_impacted_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["numpy", "pandas"]\n', encoding="utf-8"
    )
    f_numpy = repo / "numeric.py"
    f_numpy.write_text("import numpy\n", encoding="utf-8")
    f_pandas = repo / "tabular.py"
    f_pandas.write_text("import pandas\n", encoding="utf-8")
    graph = DependencyGraph(repo)

    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="files"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    for dep in ("numpy", "pandas"):
        storage.append_event(
            "run_1", EventType.DEPENDENCY_DECLARED, {"resource": dep, "version": "v1"}
        )
    clean = capture("run_1", StaticProvider(numpy="v1", pandas="v1"))
    CheckpointManager(storage).checkpoint("run_1", environment=clean)

    corrupt = capture("run_1", StaticProvider(numpy="v2", pandas="v1"))
    engine = RecoveryEngine(storage)
    decision = engine.assess_scoped(
        "run_1", ["numpy"], current_environment=corrupt, source_graph=graph
    )

    assert decision.mode is RecoveryMode.REPAIR_AND_RESUME
    assert decision.impacted_files == {str(f_numpy)}
