"""Unit tests for the LangChain adapter's SDK-free helpers (#1747).

Every test in ``tests/test_integration_langchain.py`` sits behind
``pytest.importorskip("langchain_core")``, so in an environment without
``langchain-core`` -- which is what a bare ``pip install -e .`` gives you --
none of them run and the pure helpers have no test at all. This file is the
SDK-free twin of the LangGraph one (``tests/test_adapters_langgraph.py``):
the helper tests run unconditionally, and the tests that need an adapter
instance (the constructor calls ``_ensure_langchain``) are skipped when the
SDK is absent, so the file passes either way.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

import continuum.adapters.langchain as langchain_module
from continuum.adapters.langchain import (
    _ensure_langchain,
    _extract_run_id,
    langchain_available,
)
from continuum.events import Event
from continuum.storage import SQLiteStorage
from continuum.storage.base import RunNotFound


class _StrictRunMissingStore(SQLiteStorage):
    """A storage whose ``read_events`` raises RunNotFound for every run.

    The Storage contract fixes nothing about how ``read_events`` reports an
    unknown run: the bundled SQLite backend returns an empty sequence for a
    missing id, so the ``except RunNotFound`` guard in ``checkpoint_node``
    never fires against it. This backend is the strict spelling (a run id
    with no row means the run is absent, not empty) that the guard exists to
    tolerate.
    """

    def read_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        upto: int | None = None,
        rehydrate: bool = True,
    ) -> Sequence[Event]:
        raise RunNotFound(run_id)


@pytest.fixture
def store() -> SQLiteStorage:
    return SQLiteStorage(":memory:")


# --- importability, and the fail-closed gate every SDK entry shares ----------


def test_the_adapter_module_imports_without_langchain_installed() -> None:
    """``continuum.adapters.langchain`` must import on a minimal environment.

    The adapter is optional machinery; a project that only needs the storage
    layer must never hit an import error because it lacks a vendor SDK.
    """
    assert callable(_extract_run_id)


def test__ensure_langchain_raises_when_the_sdk_is_absent(monkeypatch) -> None:
    """The refusal must tell a contributor exactly how to get un-stuck.

    ``_ensure_langchain`` is the gate ``__init__`` and every SDK-only entry
    share, so the message naming the extra is the only actionable signal on
    a bare install.
    """
    monkeypatch.setattr(langchain_module, "langchain_available", False)
    with pytest.raises(ImportError, match=r"pip install continuum-agent\[langchain\]"):
        _ensure_langchain()


def test__ensure_langchain_passes_when_the_sdk_is_present(monkeypatch) -> None:
    monkeypatch.setattr(langchain_module, "langchain_available", True)
    _ensure_langchain()  # must not raise


def test_the_module_fails_closed_when_langchain_core_is_absent(monkeypatch) -> None:
    """The import-time try/except must set the flag, not crash the import.

    Reloading the module with ``sys.modules["langchain_core"] = None`` replays
    the module body under an import that raises ImportError, exactly what a
    bare ``pip install -e .`` gives a contributor. The flag must be False after
    it, and every SDK-gated entry must refuse on that False.
    """
    import importlib
    import sys

    # Reload rebinds every module-level name it re-executes, and other modules
    # (the adapter registry in particular) compare the factory's output to
    # import-time bindings by identity. Snapshot the public surface and put it
    # back after the exercise.
    originals = {
        name: getattr(langchain_module, name)
        for name in ("LangChainAgentAdapter", "LangChainState", "_ensure_langchain")
    }
    monkeypatch.setitem(sys.modules, "langchain_core", None)
    reloaded = importlib.reload(langchain_module)
    assert reloaded.langchain_available is False
    with pytest.raises(ImportError, match=r"pip install continuum-agent\[langchain\]"):
        reloaded._ensure_langchain()

    # Restore before the teardown so every later test sees the honest flag for
    # this environment (True with the SDK, False without) plus the original
    # objects. The expected flag value is re-derived from the import system,
    # not from the flag captured at collection time: a harness that hid the
    # SDK before import would have recorded False there even though the SDK is
    # really installed.
    sys.modules.pop("langchain_core", None)
    reloaded = importlib.reload(langchain_module)
    assert reloaded.langchain_available == bool(importlib.util.find_spec("langchain_core"))
    for name, original in originals.items():
        setattr(langchain_module, name, original)


def test_the_adapter_refuses_to_construct_when_the_sdk_is_absent(
    store: SQLiteStorage, monkeypatch
) -> None:
    """Same gate as ``_ensure_langchain``, but reached through the constructor.

    A user's first contact with the requirement is ``LangChainAgentAdapter(...)``,
    not the private helper, so the message must arrive there too.
    """
    from continuum.adapters.langchain import LangChainAgentAdapter

    monkeypatch.setattr(langchain_module, "langchain_available", False)
    with pytest.raises(ImportError, match=r"pip install continuum-agent\[langchain\]"):
        LangChainAgentAdapter(store)


# --- _extract_run_id --------------------------------------------------------


def test__extract_run_id_prefers_the_explicit_continuum_kwarg() -> None:
    assert _extract_run_id((), {"continuum_run_id": "r1"}) == "r1"


def test__extract_run_id_reads_the_plain_run_id_kwarg() -> None:
    assert _extract_run_id((), {"run_id": "r2"}) == "r2"


def test__extract_run_id_treats_a_null_kwarg_as_absent() -> None:
    """A ``None`` id is absence, not a run named ``"None"``.

    Stringifying the null value would point a side effect at a run that does
    not exist, so the wrapped call would fail instead of running where it
    belongs. Both keyword spellings must obey the rule.
    """
    assert _extract_run_id((), {"continuum_run_id": None}) is None
    assert _extract_run_id((), {"run_id": None}) is None


def test__extract_run_id_falls_through_a_null_continuum_id_to_run_id() -> None:
    """An explicitly null ``continuum_run_id`` does not shadow a real one."""
    assert _extract_run_id((), {"continuum_run_id": None, "run_id": "r2"}) == "r2"


def test__extract_run_id_treats_an_empty_kwarg_as_absent() -> None:
    """An empty string id would ledger onto a run named ``""``."""
    assert _extract_run_id((), {"continuum_run_id": ""}) is None
    assert _extract_run_id((), {"run_id": ""}) is None


def test__extract_run_id_prefers_continuum_run_id_over_run_id() -> None:
    """Both keys present is a contradiction, and the explicit one wins."""
    assert _extract_run_id((), {"run_id": "plain", "continuum_run_id": "explicit"}) == "explicit"


def test__extract_run_id_reads_the_first_positional_dict() -> None:
    assert _extract_run_id(({"continuum_run_id": "r3"},), {}) == "r3"


def test__extract_run_id_ignores_a_positional_dict_without_the_key() -> None:
    """A dict with no ``continuum_run_id`` is a plain argument, not a claim."""
    assert _extract_run_id(({"order_id": "O-1"},), {}) is None


def test__extract_run_id_ignores_a_null_value_in_the_positional_dict() -> None:
    """``{"continuum_run_id": None}`` is absence, not the empty run.

    A falsy value would otherwise stringify to ``"None"`` and silently point a
    side effect at a run that does not exist.
    """
    assert _extract_run_id(({"continuum_run_id": None},), {}) is None


def test__extract_run_id_returns_none_with_no_arguments_at_all() -> None:
    assert _extract_run_id((), {}) is None


def test__extract_run_id_returns_none_for_plain_arguments() -> None:
    """A tool called with no run context is un-identified, so it runs un-intercepted."""
    assert _extract_run_id(("arg",), {"order_id": "O-1"}) is None


def test__extract_run_id_stringifies_a_non_string_id() -> None:
    """The ledger keys on ``str``; an int id must survive the round trip."""
    assert _extract_run_id((), {"continuum_run_id": 42}) == "42"


# --- adapter behaviour these helpers feed -----------------------------------


@pytest.mark.skipif(not langchain_available, reason="langchain-core not installed")
class TestWrapToolAndStartRun:
    """The raise branches the integration suite reaches only on the happy path.

    ``LangChainAgentAdapter.__init__`` calls ``_ensure_langchain``, so nothing
    here can run without the SDK; they are gathered in one place so the rest
    of the file is independent of it.
    """

    def test_wrap_tool_rejects_key_and_key_fn_together(self, store: SQLiteStorage) -> None:
        """``key`` and ``key_fn`` are two spellings of one identity, never both."""
        from continuum.adapters.langchain import LangChainAgentAdapter

        adapter = LangChainAgentAdapter(store)
        with pytest.raises(ValueError, match="not both"):

            @adapter.wrap_tool("notify.customer", key="fixed", key_fn=lambda *a, **k: "computed")
            def notify(order_id: str) -> str:
                return "sent"

    def test_wrap_tool_without_a_run_id_calls_the_tool_unchanged(
        self, store: SQLiteStorage
    ) -> None:
        """A call with no run context must reach the wrapped function untouched.

        ``run_id is None`` returns ``fn``, so a tool used outside a CONTINUUM
        run behaves exactly as it did before wrapping.
        """
        from continuum.adapters.langchain import LangChainAgentAdapter

        adapter = LangChainAgentAdapter(store)
        calls: list[str] = []

        @adapter.wrap_tool("notify.customer")
        def notify(order_id: str) -> str:
            calls.append(order_id)
            return f"sent {order_id}"

        assert notify(order_id="O-1") == "sent O-1"
        assert calls == ["O-1"]

    def test_wrap_tool_accepts_a_positional_run_id_in_the_first_dict(
        self, store: SQLiteStorage
    ) -> None:
        """The state-dict form is the shape a pipeline node passes, not just kwargs."""
        from continuum.adapters.langchain import LangChainAgentAdapter

        adapter = LangChainAgentAdapter(store)
        adapter.start_run(goal="notify", run_id="r_pos")
        calls: list[str] = []

        @adapter.wrap_tool("notify.customer")
        def notify(state: dict) -> str:
            calls.append(state["order_id"])
            return "sent"

        assert notify({"continuum_run_id": "r_pos", "order_id": "O-2"}) == "sent"
        assert calls == ["O-2"]

        # The return value and the call list pass whether or not the run id
        # was honoured, so the ledger is the evidence that it was: the action
        # is recorded under this run, and a repeat is served from the ledger
        # instead of executing the tool a second time.
        from continuum.actions.ledger import ActionLedger
        from continuum.models import ActionStatus

        ledger = ActionLedger(store, run_id="r_pos")
        recorded = ledger.all()
        assert [a.action_type for a in recorded] == ["notify.customer"]
        assert recorded[0].run_id == "r_pos"
        assert recorded[0].status is ActionStatus.COMPLETED

        assert notify({"continuum_run_id": "r_pos", "order_id": "O-2"}) == "sent"
        assert calls == ["O-2"], "a repeat must be served from the ledger, not re-executed"

    def test_start_run_refuses_a_run_that_does_not_begin_with_run_started(
        self, store: SQLiteStorage
    ) -> None:
        """A run already carrying history cannot be backfilled.

        The first event is the anchor of the whole hash-chained history; prepending
        a ``RUN_STARTED`` would re-order events that were already witnessed. The
        adapter refuses rather than invent a different past.
        """
        from continuum.adapters.langchain import LangChainAgentAdapter

        adapter = LangChainAgentAdapter(store)
        from continuum.events import EventType
        from continuum.models import Origin, Run

        store.create_run(Run(run_id="r_backfill", goal="original"))
        store.append_event(
            "r_backfill",
            EventType.TASK_UPDATED,
            {"completed": 1},
            source=Origin.DETERMINISTIC,
        )

        with pytest.raises(ValueError, match="does not begin with RUN_STARTED"):
            adapter.start_run(goal="late entry", run_id="r_backfill")


@pytest.mark.skipif(not langchain_available, reason="langchain-core not installed")
class TestCheckpointNodeAndStateExtraction:
    """The checkpoint node pipeline step and the semantic-state converters.

    Same SDK story as ``TestWrapToolAndStartRun``: the adapter constructor
    calls ``_ensure_langchain``, so these run only where the SDK is present.
    """

    @pytest.fixture
    def adapter(self, store: SQLiteStorage) -> Any:
        from continuum.adapters.langchain import LangChainAgentAdapter

        return LangChainAgentAdapter(store)

    def test_checkpoint_node_without_a_run_id_returns_the_state_unchanged(
        self, adapter: Any, monkeypatch
    ) -> None:
        """A pipeline state with no run id is outside CONTINUUM entirely.

        The node sits in an LCEL chain that may be invoked on states that
        never touched a run; those must pass through untouched and cost
        nothing, not raise and not write.
        """
        captured: list[str] = []
        monkeypatch.setattr(
            adapter, "capture_state", lambda run_id, state, **kw: captured.append(run_id)
        )

        state = {"other_field": 123}
        result = adapter.checkpoint_node(state)

        assert result is state
        assert captured == [], "no run id means no checkpoint"

    def test_checkpoint_node_extracts_semantic_state_for_a_run_without_events(
        self, adapter: Any, store: SQLiteStorage
    ) -> None:
        """A fresh run's dict is the only truth available (issue #46).

        The event log has nothing to project yet, so the checkpoint is built
        from the state dict fields, with the adapter's checkpoint reason.
        """
        from continuum.models import Run

        # The row exists (capture needs it) but no event was ever recorded.
        store.create_run(Run(run_id="unrecorded_run_id", goal=""))
        state = {
            "continuum_run_id": "unrecorded_run_id",
            "goal": "test",
            "completed_count": 2,
            "total_count": 5,
        }

        result = adapter.checkpoint_node(state)

        assert result == state
        latest = store.latest_checkpoint("unrecorded_run_id")
        assert latest is not None
        assert latest.reason == "langchain checkpoint node"
        assert latest.state.run_id == "unrecorded_run_id"
        assert latest.state.goal.description == "test"
        assert latest.state.progress.completed == 2
        assert latest.state.progress.total == 5

    def test_checkpoint_node_tolerates_a_backend_that_raises_run_not_found(self) -> None:
        """A strict backend's not-found answer counts as "fresh", not fatal.

        ``read_events`` runs before the run row is known good, and the Storage
        contract lets an implementation raise ``RunNotFound`` instead of
        returning an empty list. The guard must fall back to the dict-based
        extraction so the checkpoint still lands.
        """
        from continuum.adapters.langchain import LangChainAgentAdapter
        from continuum.models import Run

        strict = _StrictRunMissingStore()
        adapter = LangChainAgentAdapter(strict)
        adapter.storage.create_run(Run(run_id="strict_fresh", goal=""))
        state = {"continuum_run_id": "strict_fresh", "goal": "strict store", "completed_count": 7}

        result = adapter.checkpoint_node(state)

        assert result == state
        latest = strict.latest_checkpoint("strict_fresh")
        assert latest is not None
        assert latest.reason == "langchain checkpoint node"
        assert latest.state.goal.description == "strict store"
        assert latest.state.progress.completed == 7

    def test_checkpoint_node_projects_the_log_once_it_carries_work(
        self, adapter: Any, monkeypatch
    ) -> None:
        """With events on the log, the projection is authoritative (issue #46).

        Building the checkpoint from the dict fields would give
        ``source_sequence=0`` and contradict the log on restore, so the node
        must fold the event history instead of trusting the state dict's
        claims.
        """
        adapter.start_run(goal="recorded goal", run_id="r_proj")
        projected: list[str] = []
        original = adapter.manager.project_current

        def probe(run_id: str, **kwargs: Any) -> Any:
            projected.append(run_id)
            return original(run_id, **kwargs)

        monkeypatch.setattr(adapter.manager, "project_current", probe)

        state = {
            "continuum_run_id": "r_proj",
            "goal": "dict claims otherwise",
            "completed_count": 99,
        }
        result = adapter.checkpoint_node(state)

        assert result == state
        assert projected == ["r_proj"], "the log-backed fold, not the dict, must be captured"
        latest = adapter.storage.latest_checkpoint("r_proj")
        assert latest is not None
        assert latest.state.goal.description == "recorded goal"
        assert latest.state.progress.completed != 99

    def test_extract_semantic_state_honours_the_custom_converter(
        self, store: SQLiteStorage
    ) -> None:
        """A supplied ``state_to_semantic`` replaces the default extractor."""
        from continuum.adapters.langchain import LangChainAgentAdapter
        from continuum.models import Goal, Progress, SemanticState

        calls: list[dict[str, Any]] = []

        def custom_extract(state: dict[str, Any]) -> SemanticState:
            calls.append(state)
            return SemanticState(
                run_id=state["rid"],
                goal=Goal(description="custom"),
                progress=Progress(completed=99),
            )

        adapter = LangChainAgentAdapter(store, state_to_semantic=custom_extract)
        semantic = adapter.extract_semantic_state({"rid": "custom_1"})

        assert semantic.run_id == "custom_1"
        assert semantic.goal.description == "custom"
        assert semantic.progress.completed == 99
        assert calls == [{"rid": "custom_1"}]

    def test_extract_semantic_state_reads_the_default_dict_fields(self, adapter: Any) -> None:
        """Without a custom converter, the dict fields are parsed directly."""
        semantic = adapter.extract_semantic_state(
            {
                "continuum_run_id": "r_ext",
                "goal": "Process records",
                "completed_count": 42,
                "total_count": 100,
            }
        )

        assert semantic.run_id == "r_ext"
        assert semantic.goal.description == "Process records"
        assert semantic.progress.completed == 42
        assert semantic.progress.total == 100

    def test_extract_semantic_state_defaults_for_a_sparse_dict(self, adapter: Any) -> None:
        """A state with only a run id still yields a well-formed SemanticState.

        Missing fields fall back to the adapter's documented defaults rather
        than raising, so a pipeline can checkpoint from its very first node.
        """
        semantic = adapter.extract_semantic_state({})

        assert semantic.run_id == "unknown"
        assert semantic.goal.description == "LangChain agent task"
        assert semantic.progress.completed == 0
        assert semantic.progress.total is None

    def test_start_run_accepts_a_run_whose_anchor_lives_in_the_archive(
        self, adapter: Any, store: SQLiteStorage
    ) -> None:
        """After compaction the anchor is the archived first event, not a live one.

        ``start_run`` reads the archive before the live tail, so it must
        recognise the archived ``RUN_STARTED`` and let the run stand without
        appending a second anchor. Mirrors the LangGraph compaction test
        (#1453).
        """
        from continuum.events import EventType
        from continuum.models import Origin, Run

        run_id = "r_archived"
        store.create_run(Run(run_id=run_id, goal="ship release"))
        store.append_event(
            run_id,
            EventType.RUN_STARTED,
            {"goal": "ship release"},
            source=Origin.HUMAN,
        )
        store.append_event(
            run_id,
            EventType.WORK_ADDED,
            {"task_id": "w1", "description": "prepare artifacts"},
            source=Origin.HUMAN,
        )
        store.compact_run(run_id)
        assert store.read_archived_events(run_id), "the anchor must be archived to matter here"

        run = adapter.start_run(goal="ship release", run_id=run_id)

        assert run.run_id == run_id
        firsts = [e for e in store.read_all_events(run_id) if e.type is EventType.RUN_STARTED]
        assert len(firsts) == 1, "the archived anchor must not be duplicated"

    def test_assess_langchain_recovery_delegates_to_resume(self, adapter: Any) -> None:
        """The public helper must return a real decision, not a passthrough error.

        Same shape as the LangGraph counterpart: a run with a checkpoint and a
        matching environment is safe to resume.
        """
        from continuum.environment import StaticProvider, capture
        from continuum.models import Goal, RecoveryMode, SemanticState
        from continuum.recovery.engine import RecoveryDecision

        adapter.start_run(goal="Recovery test", run_id="r_recover")
        state = SemanticState(
            run_id="r_recover",
            goal=Goal(description="Recovery test"),
        )
        env = capture("r_recover", StaticProvider(service="v1"))
        adapter.capture_state("r_recover", state, environment=env)

        decision = adapter.assess_langchain_recovery("r_recover", current_environment=env)

        assert isinstance(decision, RecoveryDecision)
        assert decision.mode is RecoveryMode.RESUME
        assert decision.safe
