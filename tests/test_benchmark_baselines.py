from continuum.benchmark.baselines import BASELINES, baseline_by_name
from continuum.events import EventType
from continuum.models import Run
from continuum.storage import SQLiteStorage


def test_each_baseline_runs() -> None:
    for baseline in BASELINES:
        storage = SQLiteStorage(":memory:")
        storage.create_run(Run(run_id="run_1", goal="g"))
        storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g", "total": 1})
        result = baseline.run(storage, "run_1")
        assert "mode" in result


def test_baseline_lookup() -> None:
    assert baseline_by_name("full_transcript_replay").name == "full_transcript_replay"
