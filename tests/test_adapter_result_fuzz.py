"""Adapter result schema fuzzing (issue #161).

Adapter outputs feed recovery; malformed or surprising outputs must not crash
it. These property tests push arbitrary JSON-safe shapes through the real
pipeline: run_action -> intercept_action -> ActionLedger -> RecoveryEngine,
and pin two invariants:

* the cached retry returns exactly what the first attempt returned, even when
  the output is a dict containing the internal envelope key;
* a recovery assessment after any of these outputs never crashes.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from continuum.adapters import AdapterAction, GenericAgentAdapter, run_action
from continuum.events import EventType
from continuum.models import Run
from continuum.recovery import RecoveryEngine
from continuum.storage import SQLiteStorage

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-(10**6), 10**6)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=8), children, max_size=3)
    ),
    max_leaves=6,
)


def _adapter() -> GenericAgentAdapter:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="fuzz"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "fuzz", "total": 1})
    return GenericAgentAdapter(storage)


@given(output=json_values)
@settings(max_examples=50)
def test_arbitrary_output_survives_cache_and_recovery(output) -> None:
    adapter = _adapter()
    action = AdapterAction(name="tool.call", params={"k": "v"})

    first = run_action(adapter, "run_1", action, lambda: output)
    assert first.status == "completed"

    def must_not_rerun() -> None:
        raise AssertionError("a completed action must not execute again")

    second = run_action(adapter, "run_1", action, must_not_rerun)
    assert second.status == "completed"
    assert second.output == output

    decision = RecoveryEngine(adapter.storage).assess("run_1")
    assert decision.contract is not None


@given(
    key=st.text(min_size=0, max_size=8),
    value=json_values,
    extra=st.dictionaries(st.text(max_size=6), json_values, max_size=2),
)
@settings(max_examples=40)
def test_envelope_key_collision_is_contained(key: str, value, extra: dict) -> None:
    # A caller dict that happens to contain the envelope key must come back
    # intact rather than being mistaken for the framework's own wrapper.
    adapter = _adapter()
    payload = dict(extra)
    if key != "__return_value__":
        payload[key] = value
    payload["__return_value__"] = value

    action = AdapterAction(name="tool.call", params={})
    first = run_action(adapter, "run_1", action, lambda: payload)
    assert first.status == "completed"
    second = run_action(adapter, "run_1", action, lambda: None)
    assert second.output == payload


@given(
    arguments=st.one_of(st.none(), st.dictionaries(st.text(max_size=6), json_values, max_size=3))
)
@settings(max_examples=30)
def test_weird_arguments_do_not_break_recovery(arguments) -> None:
    adapter = _adapter()
    outcome = run_action(
        adapter, "run_1", AdapterAction(name="tool.call", params=dict(arguments or {})), lambda: 1
    )
    assert outcome.status == "completed"
    decision = RecoveryEngine(adapter.storage).assess("run_1")
    assert decision.mode.value in {"resume", "repair_and_resume"}
