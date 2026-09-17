"""Sleep-time trajectory reports (issue #393)."""

from __future__ import annotations

import json
import os

import pytest

from continuum.analysis.trajectory_report import (
    build_trajectory_report,
    health_maybe_generate_trajectory_report,
    is_quiet_window,
    maybe_generate_trajectory_report,
    record_trajectory_report,
    render_trajectory_report,
)
from continuum.checkpoint import CheckpointManager
from continuum.events import Event, EventType
from continuum.models import Origin, Run, TrajectoryReport
from continuum.state.semantic import project
from continuum.storage import SQLiteStorage


def _make_storage(run_id: str = "run_1") -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id=run_id, goal="g"))
    storage.append_event(run_id, EventType.RUN_STARTED, {"goal": "g"})
    return storage


def _add_failed_action(storage: SQLiteStorage, run_id: str, action_type: str, key: str) -> None:
    from continuum.actions import ActionLedger

    ledger = ActionLedger(storage, run_id)
    outcome = ledger.claim(action_type, {"x": 1}, key=key)
    ledger.fail(outcome.key, error="failed", certain=True)


def _add_quiet_window_events(storage: SQLiteStorage, run_id: str, count: int = 3) -> None:
    for i in range(count):
        _add_failed_action(storage, run_id, "test.stall", f"k{i}")


def test_after_10_idle_compactions_newest_report_lists_top_stall_and_scar_rate() -> None:
    storage = _make_storage()
    try:
        run_id = "run_1"
        # Simulate 10 idle compaction windows by directly building reports for successive windows
        # Each window is synthetic: we add quiet events and then build a report for that window
        # without relying on CheckpointManager.checkpoint after compaction which would fail due to archived RUN_STARTED
        for window in range(10):
            start = storage.last_sequence(run_id)
            for i in range(3):
                _add_failed_action(storage, run_id, "test.stall", f"w{window}_k{i}")
            from continuum.actions import ActionLedger

            ledger = ActionLedger(storage, run_id)
            ledger.claim("test.scar", {"y": window}, key=f"scar_{window}")
            end = storage.last_sequence(run_id)
            # Simulate a compaction anchor for this window
            storage.append_event(
                run_id,
                EventType.EVENT_LOG_ANCHORED,
                {"anchor_sequence": end},
                source=Origin.DETERMINISTIC,
            )
            report = maybe_generate_trajectory_report(
                storage, run_id, window_start=start, window_end=end
            )
            assert report is not None

        from heapq import merge

        all_events = list(
            merge(
                storage.read_archived_events(run_id),
                storage.read_events(run_id),
                key=lambda e: e.sequence,
            )
        )
        state2 = project(run_id, all_events)
        assert len(state2.trajectory_reports) >= 1
        newest = state2.trajectory_reports[-1]
        assert newest.scar_rate >= 0.0
        assert newest.scar_rate <= 1.0
        assert "test.stall" in newest.stall_sites or "test.stall" in newest.top_failure_action_types
        assert newest.top_failure_action_types
        assert newest.top_failure_action_types[0] == "test.stall"
        verify = storage.verify_events(run_id)
        assert verify.ok, f"verify failed: {verify.violations}"
        events = list(storage.read_events(run_id))
        report_events = [e for e in events if e.type is EventType.TRAJECTORY_REPORT]
        assert report_events
        for ev in report_events:
            payload = ev.payload
            assert "report_id" in payload
            assert "scar_rate" in payload
            dumped = json.dumps(payload, sort_keys=True).encode()
            assert len(dumped) < 2048
    finally:
        storage.close()


def test_reports_obey_min_authority_non_amplification() -> None:
    storage = _make_storage()
    try:
        run_id = "run_1"
        storage.append_event(
            run_id,
            EventType.TOOL_COMPLETED,
            {"path": "/tmp/x", "sha256": "abc"},
            source=Origin.EXTERNAL_AGENT,
        )
        _add_quiet_window_events(storage, run_id, count=2)
        CheckpointManager(storage).checkpoint(run_id, trigger="test")
        storage.compact_run(run_id)
        report = maybe_generate_trajectory_report(storage, run_id)
        assert report is not None
        assert report.derived_origin == Origin.EXTERNAL_AGENT.value
        from heapq import merge

        all_events = list(
            merge(
                storage.read_archived_events(run_id),
                storage.read_events(run_id),
                key=lambda e: e.sequence,
            )
        )
        state = project(run_id, all_events)
        assert state.trajectory_reports
        proj_report = state.trajectory_reports[0]
        assert proj_report.derived_origin == Origin.EXTERNAL_AGENT.value

        storage2 = _make_storage(run_id="run_2")
        try:
            storage2.append_event(
                "run_2",
                EventType.TOOL_COMPLETED,
                {"path": "/tmp/y", "sha256": "def"},
                source=Origin.DETERMINISTIC,
            )
            _add_quiet_window_events(storage2, "run_2", count=2)
            CheckpointManager(storage2).checkpoint("run_2", trigger="test")
            storage2.compact_run("run_2")
            report2 = maybe_generate_trajectory_report(storage2, "run_2")
            assert report2 is not None
            assert report2.derived_origin in (Origin.DETERMINISTIC.value, Origin.HUMAN.value)
        finally:
            storage2.close()
    finally:
        storage.close()


def test_zero_overhead_when_quiet_never_occurs() -> None:
    storage = _make_storage()
    try:
        run_id = "run_1"
        for window in range(3):
            start = storage.last_sequence(run_id)
            storage.append_event(
                run_id,
                EventType.WORK_COMPLETED,
                {"count": 1, "task_id": f"t{window}"},
                source=Origin.DETERMINISTIC,
            )
            end = storage.last_sequence(run_id)
            storage.append_event(
                run_id,
                EventType.EVENT_LOG_ANCHORED,
                {"anchor_sequence": end},
                source=Origin.DETERMINISTIC,
            )
            report = maybe_generate_trajectory_report(
                storage, run_id, window_start=start, window_end=end
            )
            assert report is None

        from heapq import merge

        all_events = list(
            merge(
                storage.read_archived_events(run_id),
                storage.read_events(run_id),
                key=lambda e: e.sequence,
            )
        )
        state = project(run_id, all_events)
        assert state.trajectory_reports == []
        events = list(storage.read_events(run_id))
        report_events = [e for e in events if e.type is EventType.TRAJECTORY_REPORT]
        assert not report_events
    finally:
        storage.close()


def test_one_report_per_compaction_window_idempotent() -> None:
    storage = _make_storage()
    try:
        run_id = "run_1"
        _add_quiet_window_events(storage, run_id, count=2)
        CheckpointManager(storage).checkpoint(run_id, trigger="test")
        storage.compact_run(run_id)
        report1 = maybe_generate_trajectory_report(storage, run_id)
        assert report1 is not None
        window_end = report1.window_end
        report2 = maybe_generate_trajectory_report(storage, run_id)
        assert report2 is not None
        assert report2.report_id == report1.report_id
        assert report2.window_end == window_end
        events = list(storage.read_events(run_id))
        reports = [
            e
            for e in events
            if e.type is EventType.TRAJECTORY_REPORT and e.payload.get("window_end") == window_end
        ]
        assert len(reports) == 1

        report3 = maybe_generate_trajectory_report(
            storage, run_id, window_start=0, window_end=window_end
        )
        assert report3 is not None
        assert report3.report_id == report1.report_id
    finally:
        storage.close()


def test_bounded_size_per_report() -> None:
    storage = _make_storage()
    try:
        run_id = "run_1"
        for i in range(20):
            _add_failed_action(storage, run_id, f"test.type_{i}", f"key_{i}")
        CheckpointManager(storage).checkpoint(run_id, trigger="test")
        storage.compact_run(run_id)
        report = build_trajectory_report(storage, run_id, 0, storage.last_sequence(run_id))
        assert len(report.stall_sites) <= 5
        assert len(report.top_failure_action_types) <= 3
        dumped = json.dumps(report.model_dump(mode="json"), sort_keys=True).encode()
        assert len(dumped) < 2048
        recorded = record_trajectory_report(storage, run_id, report)
        assert recorded.report_id == report.report_id
    finally:
        storage.close()


def test_digest_auditable_and_briefing_consumption() -> None:
    storage = _make_storage()
    try:
        run_id = "run_1"
        _add_quiet_window_events(storage, run_id, count=2)
        CheckpointManager(storage).checkpoint(run_id, trigger="test")
        storage.compact_run(run_id)
        report = maybe_generate_trajectory_report(storage, run_id)
        assert report is not None
        verify = storage.verify_events(run_id)
        assert verify.ok
        from heapq import merge

        all_events = list(
            merge(
                storage.read_archived_events(run_id),
                storage.read_events(run_id),
                key=lambda e: e.sequence,
            )
        )
        state = project(run_id, all_events)
        assert state.trajectory_reports
        import io
        import pathlib
        import tempfile

        from continuum.cli.main import main as cli_main

        fd, tmp = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        file_storage = SQLiteStorage(tmp)
        try:
            file_storage.create_run(Run(run_id=run_id, goal="g"))
            for ev in all_events:
                file_storage.append_event(run_id, ev.type, ev.payload, source=ev.source)
            verify2 = file_storage.verify_events(run_id)
            assert verify2.ok
            out = io.StringIO()
            err = io.StringIO()
            code = cli_main(["--db", tmp, "briefing", "--run-id", run_id], out=out, err=err)
            assert code == 0
            text = out.getvalue()
            assert "trajectory reports" in text.lower() or "trajectory report" in text.lower()
            assert report.report_id in text or str(report.window_end) in text
        finally:
            file_storage.close()
            pathlib.Path(tmp).unlink(missing_ok=True)
    finally:
        storage.close()


def test_synthetic_archive_determinism() -> None:
    def _build_once() -> TrajectoryReport:
        storage = _make_storage()
        try:
            run_id = "run_1"
            for i in range(3):
                _add_failed_action(storage, run_id, "test.stall", f"k{i}")
            CheckpointManager(storage).checkpoint(run_id, trigger="test")
            storage.compact_run(run_id)
            report = build_trajectory_report(storage, run_id, 0, storage.last_sequence(run_id))
            return report
        finally:
            storage.close()

    r1 = _build_once()
    r2 = _build_once()
    assert r1.report_id == r2.report_id
    assert r1.scar_rate == r2.scar_rate
    assert r1.stall_sites == r2.stall_sites
    assert r1.top_failure_action_types == r2.top_failure_action_types


def test_health_idle_trigger_generates_for_quiet_and_not_for_busy() -> None:
    storage = _make_storage()
    try:
        run_id = "run_1"
        prev_end = 0
        for window in range(10):
            for i in range(3):
                _add_failed_action(storage, run_id, "test.stall", f"w{window}_k{i}")
            end = storage.last_sequence(run_id)
            storage.append_event(
                run_id,
                EventType.EVENT_LOG_ANCHORED,
                {"anchor_sequence": end},
                source=Origin.DETERMINISTIC,
            )
            report = health_maybe_generate_trajectory_report(storage, run_id)
            assert report is not None, f"window {window} should be quiet and generate"
            assert report.window_end == end
            assert report.window_start == prev_end
            prev_end = end
        events = list(storage.read_events(run_id))
        reports = [e for e in events if e.type is EventType.TRAJECTORY_REPORT]
        assert len(reports) == 10
        storage.append_event(
            run_id,
            EventType.WORK_COMPLETED,
            {"count": 1, "task_id": "busy"},
            source=Origin.DETERMINISTIC,
        )
        end = storage.last_sequence(run_id)
        storage.append_event(
            run_id,
            EventType.EVENT_LOG_ANCHORED,
            {"anchor_sequence": end},
            source=Origin.DETERMINISTIC,
        )
        before = len(
            [e for e in storage.read_events(run_id) if e.type is EventType.TRAJECTORY_REPORT]
        )
        report_busy = health_maybe_generate_trajectory_report(storage, run_id)
        assert report_busy is None
        after = len(
            [e for e in storage.read_events(run_id) if e.type is EventType.TRAJECTORY_REPORT]
        )
        assert after == before
        via_health = health_maybe_generate_trajectory_report(storage, run_id)
        assert via_health is None
        direct = maybe_generate_trajectory_report(storage, run_id)
        assert direct is None
    finally:
        storage.close()


# --- is_quiet_window: the trigger that decides a report is built at all (#1235) ---


def _evt(
    event_type: EventType,
    payload: dict[str, object] | None = None,
    sequence: int = 1,
) -> Event:
    """Build a bare event for the pure window checks, no storage needed."""
    return Event(run_id="run_1", sequence=sequence, type=event_type, payload=payload or {})


def test_is_quiet_window_empty_window_is_quiet() -> None:
    """The 'quiet never occurs' case: an empty window makes no progress claim."""
    assert is_quiet_window([]) is True


@pytest.mark.parametrize(
    "event_type",
    [
        EventType.RUN_STARTED,
        EventType.TOOL_FAILED,
        EventType.STATE_CHECKPOINTED,
        EventType.LIVENESS_SILENCE_DETECTED,
        EventType.ACTION_RECORDED,
        EventType.TRAJECTORY_REPORT,
    ],
)
def test_is_quiet_window_without_progress_events_is_quiet(event_type: EventType) -> None:
    """A window holding only non-progress events still counts as quiet."""
    assert is_quiet_window([_evt(event_type, {"any": "payload"})]) is True


def test_is_quiet_window_checks_every_event_not_just_the_last() -> None:
    """A progress event anywhere in the window breaks quiet, not only the tail."""
    window = [
        _evt(EventType.TOOL_FAILED, {}, sequence=1),
        _evt(EventType.WORK_COMPLETED, {"count": 1}, sequence=2),
        _evt(EventType.RUN_STARTED, {}, sequence=3),
    ]
    assert is_quiet_window(window) is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"count": 1}, False),
        ({"count": 5}, False),
        # Zero completed work makes no progress claim.
        ({"count": 0}, True),
        # Failed work is not progress.
        ({"count": 1, "failed": True}, True),
        # An unreadable count is treated as progress rather than silently ignored.
        ({"count": "not-a-number"}, False),
        # A missing count defaults to one unit of completed work.
        ({}, False),
    ],
)
def test_is_quiet_window_work_completed_edge_cases(
    payload: dict[str, object], expected: bool
) -> None:
    assert is_quiet_window([_evt(EventType.WORK_COMPLETED, payload)]) is expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"completed": 3}, False),
        ({"completed": 0}, True),
        # No completed field means the event makes no progress claim.
        ({}, True),
        # An unreadable completed count is treated as progress.
        ({"completed": "not-a-number"}, False),
    ],
)
def test_is_quiet_window_task_updated_edge_cases(
    payload: dict[str, object], expected: bool
) -> None:
    assert is_quiet_window([_evt(EventType.TASK_UPDATED, payload)]) is expected


def test_is_quiet_window_decision_created_breaks_quiet_regardless_of_payload() -> None:
    assert is_quiet_window([_evt(EventType.DECISION_CREATED, {})]) is False
    assert is_quiet_window([_evt(EventType.DECISION_CREATED, {"deferred": True})]) is False


def test_quiet_window_bounds_exclude_start_and_include_end() -> None:
    """The window is half-open, (start, end]: the boundary decides what a report sees."""
    storage = _make_storage()
    try:
        run_id = "run_1"
        storage.append_event(
            run_id, EventType.WORK_COMPLETED, {"count": 1}, source=Origin.DETERMINISTIC
        )
        progress_seq = storage.last_sequence(run_id)
        _add_quiet_window_events(storage, run_id, count=2)
        end = storage.last_sequence(run_id)

        # Starting the window *at* the progress sequence excludes it, so the
        # window holds only the stalled actions and a report is built.
        report = maybe_generate_trajectory_report(
            storage, run_id, window_start=progress_seq, window_end=end
        )
        assert report is not None
        assert (report.window_start, report.window_end) == (progress_seq, end)

        # Ending the window at the progress sequence includes it, so quiet fails
        # and nothing is recorded.
        assert (
            maybe_generate_trajectory_report(
                storage, run_id, window_start=1, window_end=progress_seq
            )
            is None
        )
    finally:
        storage.close()


# --- render_trajectory_report: the human-facing end of the feature (#1235) ---


def _example_report(**overrides: object) -> TrajectoryReport:
    base: dict[str, object] = {
        "report_id": "rep_abc123",
        "window_start": 2,
        "window_end": 9,
        "compaction_seq": 9,
        "attempts": 3,
        "scar_rate": 0.25,
        "stall_sites": ["ingest.api", "db.migrate"],
        "top_failure_action_types": ["ingest.api"],
        "derived_origin": Origin.EXTERNAL_AGENT.value,
    }
    base.update(overrides)
    return TrajectoryReport(**base)  # type: ignore[arg-type]


def test_render_names_the_report_window_and_recorded_metrics() -> None:
    report = _example_report()
    lines = render_trajectory_report(report)
    text = "\n".join(lines)

    assert lines, "a report must render something"
    assert report.report_id in text
    assert "2->9" in text
    # scar_rate renders to two decimals, whatever the report recorded.
    assert f"scar_rate {report.scar_rate:.2f}" in text
    assert f"attempts {report.attempts}" in text
    # every recorded stall site and failure type is named
    for site in report.stall_sites:
        assert site in text
    for action_type in report.top_failure_action_types:
        assert action_type in text


def test_render_without_lessons_still_reports_honestly() -> None:
    """No stalls and no failures render the header and metrics, not an empty list."""
    report = _example_report(stall_sites=[], top_failure_action_types=[])
    lines = render_trajectory_report(report)
    text = "\n".join(lines)

    assert lines
    assert report.report_id in lines[0]
    assert f"scar_rate {report.scar_rate:.2f}" in text
    assert "stall_sites:" not in text
    assert "top failures:" not in text


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (Origin.EXTERNAL_AGENT.value, "unverified (derived)"),
        (Origin.LLM.value, "unverified (derived)"),
        (Origin.DETERMINISTIC.value, "derived from deterministic"),
        (Origin.HUMAN.value, "derived from human"),
    ],
)
def test_render_labels_the_derived_origin(origin: str, expected: str) -> None:
    report = _example_report(derived_origin=origin)
    text = "\n".join(render_trajectory_report(report))
    assert expected in text


def test_render_of_a_report_built_from_storage_names_what_it_recorded() -> None:
    """The renderer echoes the metrics build_trajectory_report actually computed."""
    storage = _make_storage()
    try:
        run_id = "run_1"
        for i in range(3):
            _add_failed_action(storage, run_id, "test.stall", f"k{i}")
        # A claimed-but-never-settled action is the scar the scar_rate counts.
        from continuum.actions import ActionLedger

        ActionLedger(storage, run_id).claim("test.scar", {"y": 1}, key="scar_0")
        end = storage.last_sequence(run_id)

        report = build_trajectory_report(storage, run_id, 0, end)
        text = "\n".join(render_trajectory_report(report))

        assert report.report_id in text
        assert "test.stall" in text
        assert f"scar_rate {report.scar_rate:.2f}" in text
    finally:
        storage.close()
