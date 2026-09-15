from __future__ import annotations

import pytest

from continuum.adapters import (
    AdapterRegistry,
    GenericAgentAdapter,
    get_adapter,
    list_adapters,
    recover,
)
from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import RecoveryMode, Run
from continuum.storage import SQLiteStorage


def test_builtin_adapters_are_registered() -> None:
    names = list_adapters()
    for name in ("generic", "langchain", "langgraph", "openai"):
        assert name in names


def test_get_adapter_returns_the_class() -> None:
    assert get_adapter("generic") is GenericAgentAdapter


def test_unknown_adapter_raises() -> None:
    with pytest.raises(ValueError):
        get_adapter("does-not-exist")


def test_registry_register_and_get_is_local() -> None:
    reg = AdapterRegistry()
    reg.register("demo", lambda: GenericAgentAdapter)
    assert reg.get("demo") is GenericAgentAdapter
    assert "demo" in reg.names()


def _seed(storage: SQLiteStorage) -> None:
    storage.create_run(Run(run_id="r1", goal="g"))
    storage.append_event("r1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    storage.append_event(
        "r1", EventType.DEPENDENCY_DECLARED, {"resource": "dataset", "version": "v3"}
    )
    storage.append_event(
        "r1", EventType.EVIDENCE_ADDED, {"evidence_id": "e1", "summary": "s", "source": "dataset"}
    )
    CheckpointManager(storage).checkpoint(
        "r1", environment=capture("r1", StaticProvider(dataset="v3"))
    )


def test_recover_dispatches_through_the_generic_adapter() -> None:
    storage = SQLiteStorage(":memory:")
    _seed(storage)
    decision = recover(
        "generic",
        "r1",
        storage,
        current_environment=capture("r1", StaticProvider(dataset="v3")),
    )
    assert decision.mode is RecoveryMode.RESUME
    assert decision.safe


def test_recover_unknown_adapter_errors() -> None:
    storage = SQLiteStorage(":memory:")
    with pytest.raises(ValueError):
        recover("nope", "r1", storage)
