from pathlib import Path

from continuum.checkpoint import CheckpointManager
from continuum.events import EventType
from continuum.hooks import (
    count_sections,
    get_tail_section,
    make_async_auto_checkpoint_hook,
    make_auto_checkpoint_hook,
    make_file_derived_progress_hook,
)
from continuum.models import Run
from continuum.storage import SQLiteStorage


def test_auto_checkpoint_hook_respects_policy() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    manager = CheckpointManager(storage)
    hook = make_auto_checkpoint_hook(manager, "run_1")

    first = hook()
    assert first is True
    second = hook()
    assert second is False


def test_async_hook_does_not_block_and_still_checkpoints() -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
    manager = CheckpointManager(storage)
    hook = make_async_auto_checkpoint_hook(manager, "run_1")

    import time

    start = time.perf_counter()
    result = hook()
    elapsed = time.perf_counter() - start

    assert result is True
    assert elapsed < 0.1

    time.sleep(0.2)
    assert storage.latest_checkpoint("run_1") is not None


def test_count_sections_counts_headings(tmp_path: Path) -> None:
    file = tmp_path / "guide.md"
    file.write_text("# Title\n\n## A\n\n## B\n\nText\n", encoding="utf-8")
    assert count_sections(file) == 2


def test_file_derived_hook_records_from_file(tmp_path: Path) -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 8})
    manager = CheckpointManager(storage)
    file = tmp_path / "guide.md"
    file.write_text("## 1\n## 2\n## 3\n", encoding="utf-8")
    hook = make_file_derived_progress_hook(manager, "run_1", file, total=8)
    hook()
    from continuum.state.semantic import project

    state = project("run_1", storage.read_events("run_1"))
    assert state.progress.completed == 3


def test_get_tail_section_returns_last_section(tmp_path: Path) -> None:
    file = tmp_path / "guide.md"
    file.write_text("# Title\n\n## A\ncontent a\n\n## B\ncontent b\n", encoding="utf-8")
    tail = get_tail_section(file)
    assert tail.startswith("## B")
    assert "content b" in tail


def test_file_derived_hook_captures_tail_evidence(tmp_path: Path) -> None:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 8})
    manager = CheckpointManager(storage)
    file = tmp_path / "guide.md"
    file.write_text("## 1\ncontent 1\n\n## 2\ncontent 2\n", encoding="utf-8")
    hook = make_file_derived_progress_hook(manager, "run_1", file, total=8)
    hook()
    events = storage.read_events("run_1")
    evidence_events = [e for e in events if e.type == EventType.EVIDENCE_ADDED]
    assert any("content 2" in str(e.payload.get("summary", "")) for e in evidence_events)
