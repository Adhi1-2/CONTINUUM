"""Benchmark baseline drivers (issue 11).

The drivers are the comparison's control group: everything a baseline returns
is derived from the event log it is handed, so a driver that started
fabricating numbers would cost the whole benchmark. These tests pin each
driver's exact output, the failure arm that keeps a broken run from crashing
the harness, and the name lookup the harness dispatches through.
"""

from __future__ import annotations

import pytest

from continuum.benchmark.baselines import (
    BASELINES,
    ContinuumSemanticCheckpoint,
    FullTranscriptReplay,
    NaiveCheckpointing,
    SimpleConversationSummarization,
    StructuredTaskSummary,
    baseline_by_name,
)
from continuum.events import EventType
from continuum.models import Run
from continuum.storage import SQLiteStorage


@pytest.fixture
def storage() -> SQLiteStorage:
    store = SQLiteStorage(":memory:")
    store.create_run(Run(run_id="run_1", goal="g"))
    store.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    yield store  # type: ignore[misc]
    store.close()


def test_each_baseline_runs() -> None:
    for baseline in BASELINES:
        storage = SQLiteStorage(":memory:")
        storage.create_run(Run(run_id="run_1", goal="g"))
        storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
        result = baseline.run(storage, "run_1")
        assert "mode" in result


def test_baseline_lookup() -> None:
    assert baseline_by_name("full_transcript_replay").name == "full_transcript_replay"


def test_every_registered_baseline_is_reachable_by_name() -> None:
    """``BASELINES`` and the name lookup are two views of one registry: a driver
    added without wiring its name in is a driver the harness cannot dispatch to."""
    registered = {baseline.name for baseline in BASELINES}
    assert registered == {
        "full_transcript_replay",
        "simple_conversation_summarization",
        "naive_checkpointing",
        "structured_task_summary",
        "continuum_semantic_checkpoint",
    }
    for name in registered:
        assert baseline_by_name(name).name == name


def test_an_unknown_baseline_name_raises_key_error() -> None:
    with pytest.raises(KeyError, match="unknown baseline 'nope'"):
        baseline_by_name("nope")


def test_full_transcript_replay_reports_the_event_count() -> None:
    store = SQLiteStorage(":memory:")
    store.create_run(Run(run_id="run_1", goal="g"))
    store.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})
    store.append_event("run_1", EventType.WORK_COMPLETED, {"doc": 1})

    assert FullTranscriptReplay().run(store, "run_1") == {"replayed": "2", "mode": "replay_all"}


def test_simple_conversation_summarization_reports_the_summary() -> None:
    store = SQLiteStorage(":memory:")
    store.create_run(Run(run_id="run_1", goal="g"))
    store.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})

    result = SimpleConversationSummarization().run(store, "run_1")
    assert result == {"summary": "summarized 1 events", "mode": "summarize"}


def test_structured_task_summary_counts_only_work_completed() -> None:
    """The count is the baseline's whole claim, so a non-work event swelling it
    would inflate the baseline the comparison is measured against."""
    store = SQLiteStorage(":memory:")
    store.create_run(Run(run_id="run_1", goal="g"))
    store.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})
    store.append_event("run_1", EventType.TOOL_COMPLETED, {"tool": "t", "status": 200})
    store.append_event("run_1", EventType.WORK_COMPLETED, {"doc": 1})
    store.append_event("run_1", EventType.WORK_COMPLETED, {"doc": 2})

    assert StructuredTaskSummary().run(store, "run_1") == {
        "tasks": "2",
        "mode": "structured_summary",
    }


def test_naive_checkpointing_records_a_checkpoint() -> None:
    store = SQLiteStorage(":memory:")
    store.create_run(Run(run_id="run_1", goal="g"))
    store.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})

    result = NaiveCheckpointing().run(store, "run_1")
    assert result["mode"] == "naive"
    assert result["checkpoint_id"]


def test_naive_checkpointing_reports_a_failed_checkpoint_rather_than_raising() -> None:
    """The naive baseline has no recovery logic to lean on, so its job is to
    fail loudly into its result dict: a crash here would take the whole
    benchmark run down with one bad run."""
    store = SQLiteStorage(":memory:")

    result = NaiveCheckpointing().run(store, "run_never_created")

    assert result["mode"] == "naive_failed"
    assert result["error"]


def test_continuum_semantic_checkpoint_records_a_checkpoint() -> None:
    store = SQLiteStorage(":memory:")
    store.create_run(Run(run_id="run_1", goal="g"))
    store.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})

    result = ContinuumSemanticCheckpoint().run(store, "run_1")
    assert result["mode"] == "continuum"
    assert result["checkpoint_id"]
