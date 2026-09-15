from __future__ import annotations

from continuum.actions.ledger import ActionLedger
from continuum.adapters import GenericAgentAdapter
from continuum.models import Action, Run
from continuum.storage import SQLiteStorage


def test_action_round_trip_without_dep_scope() -> None:
    # An Action serialized before dep_scope existed must still load.
    old = {"action_id": "a1", "run_id": "r", "action_type": "t"}
    loaded = Action.model_validate(old)
    assert loaded.dep_scope is None
    assert loaded.action_type == "t"


def test_action_round_trip_with_dep_scope() -> None:
    a = Action(run_id="r", action_type="t", dep_scope="numpy")
    restored = Action.model_validate(a.model_dump(mode="json"))
    assert restored.dep_scope == "numpy"


def test_intercept_action_sets_dep_scope() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="dep_scope_test"))
    adapter = GenericAgentAdapter(storage)
    adapter.intercept_action(
        "run_1", "pkg.install", lambda: 42, arguments={"x": 1}, dep_scope="numpy"
    )
    recorded = ActionLedger(storage, "run_1").all()
    assert len(recorded) == 1
    assert recorded[0].dep_scope == "numpy"
