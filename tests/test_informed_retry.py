"""Informed retry (#265): the engine's account of prior attempts.

The block is a pure projection of recovery-path events already in the hash
chain plus current failure signals. These tests pin: absence on clean runs,
component naming on failures, repair-history summarising, settlement verdicts,
determinism, the size cap, and every surface that carries it (CLI resume,
briefing, serve payload). They also pin the single-Next-steps regression in
``continuum resume`` output.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from continuum.actions import ActionLedger
from continuum.checkpoint import CheckpointManager
from continuum.cli import ExitCode, main
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import Run
from continuum.recovery import RecoveryEngine
from continuum.recovery.summary import (
    INFORMED_RETRY_CAP_BYTES,
    _fit,
    build_informed_retry,
    render_informed_retry,
)
from continuum.storage import SQLiteStorage


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def make_storage() -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    seed(storage)
    return storage


def seed(storage: SQLiteStorage) -> None:
    storage.create_run(Run(run_id="run_1", goal="Do things"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "Do things", "total": 2})


def checkpoint_clean(storage: SQLiteStorage) -> None:
    clean = capture("run_1", StaticProvider(dataset="v1"))
    CheckpointManager(storage).checkpoint("run_1", environment=clean)


# --- pure derivation ---------------------------------------------------------- #


def test_a_run_with_no_recovery_history_yields_none() -> None:
    storage = make_storage()
    checkpoint_clean(storage)
    engine = RecoveryEngine(storage)
    decision = engine.assess("run_1")
    assert decision.informed_retry is None


def test_stale_dependency_names_the_component_and_rule() -> None:
    storage = make_storage()
    storage.append_event(
        "run_1", EventType.DEPENDENCY_DECLARED, {"resource": "dataset", "version": "v1"}
    )
    checkpoint_clean(storage)

    drifted = capture("run_1", StaticProvider(dataset="v2"))
    decision = RecoveryEngine(storage).assess("run_1", current_environment=drifted)

    block = decision.informed_retry
    assert block is not None
    components = {f["component"] for f in block["current_failures"]}
    assert "external_dependency" in components
    assert any("dataset" in rule and "--env" in rule for rule in block["avoid"])


def test_repair_history_is_summarised_on_the_next_resume(tmp_path: Path) -> None:
    db = str(tmp_path / "r.db")
    with SQLiteStorage(db) as storage:
        seed(storage)
        outcome = ActionLedger(storage, "run_1").claim("send_invoice", {}, key="invoice:1")
        assert outcome.fresh

    code, _, _ = run("--db", db, "resume", "run_1", "--repair")
    assert code != 0  # request_human: uncertain side effect blocks resume

    code, out, _ = run("--db", db, "--json", "resume", "run_1")
    assert code != 0
    payload = json.loads(out)
    block = payload["informed_retry"]
    assert block is not None
    assert block["attempts"] == 1
    assert block["last_attempt_mode"] == "request_human"
    assert block["last_attempt_steps"]
    assert any("outcome unknown" in rule for rule in block["avoid"])


def test_settlement_verdicts_come_from_recorded_status(tmp_path: Path) -> None:
    db = str(tmp_path / "s.db")
    with SQLiteStorage(db) as storage:
        seed(storage)
        ledger = ActionLedger(storage, "run_1")
        first = ledger.claim("send_invoice", {}, key="invoice:1")
        second = ledger.claim("write_file", {}, key="file:a")
        ledger.reconcile(first.key, occurred=True, external_id="msg-9")
        ledger.reconcile(second.key, occurred=False)

        decision = RecoveryEngine(storage).assess("run_1")

    block = decision.informed_retry
    assert block is not None
    verdicts = {s["action_type"]: s["occurred"] for s in block["settled_effects"]}
    assert verdicts == {"send_invoice": True, "write_file": False}
    rendered = render_informed_retry(block)
    assert any("effect confirmed present" in line for line in rendered)
    assert any("absence confirmed" in line for line in rendered)


def test_identical_logs_produce_identical_blocks() -> None:
    storage = make_storage()
    ActionLedger(storage, "run_1").claim("send_invoice", {}, key="invoice:1")
    first = build_informed_retry(
        storage,
        "run_1",
        validation_report=RecoveryEngine(storage).assess("run_1").validation.report,
    )
    second = build_informed_retry(
        storage,
        "run_1",
        validation_report=RecoveryEngine(storage).assess("run_1").validation.report,
    )
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_cap_is_enforced_with_deterministic_eviction() -> None:
    fat = {
        "attempts": 99,
        "completed_recoveries": 0,
        "blocked_recoveries": 0,
        "compensations": 0,
        "last_attempt_mode": "x" * 64,
        "last_attempt_steps": [f"step_{i}_" + "x" * 128 for i in range(50)],
        "settled_effects": [
            {"action_id": f"a{i}", "action_type": f"type_{i}", "occurred": True} for i in range(50)
        ],
        "current_failures": [
            {
                "component": "external_dependency",
                "id": f"dep{i}",
                "status": "stale",
                "detail": "d" * 200,
            }
            for i in range(50)
        ],
        "avoid": ["rule " + "y" * 200 for _ in range(50)],
    }
    fitted = _fit(fat)
    assert len(json.dumps(fitted, sort_keys=True).encode()) <= INFORMED_RETRY_CAP_BYTES


# --- surfaces ------------------------------------------------------------------ #


@pytest.fixture
def history_db(tmp_path: Path) -> str:
    db = str(tmp_path / "h.db")
    with SQLiteStorage(db) as storage:
        seed(storage)
        ActionLedger(storage, "run_1").claim("send_invoice", {}, key="invoice:1")
    run("--db", db, "resume", "run_1", "--repair")
    return db


def test_resume_text_carries_the_section(history_db: str) -> None:
    code, out, _ = run("--db", history_db, "resume", "run_1")
    assert code != 0
    assert "What previous attempts changed (informed retry):" in out
    assert "previous attempt(s): 1" in out


def test_briefing_carries_the_section(history_db: str) -> None:
    code, out, _ = run("--db", history_db, "briefing")
    assert code == ExitCode.OK or code == ExitCode.ERROR  # briefing exits by mode
    assert "what previous attempts changed" in out


def test_serve_payload_carries_the_block(history_db: str) -> None:
    from continuum.serve.server import SidecarServer

    srv = SidecarServer(database=history_db)
    out = srv.dispatch("resume", {"run_id": "run_1"})
    assert out["mode"] != "resume"
    assert out["informed_retry"] is not None
    assert out["informed_retry"]["attempts"] == 1


def test_clean_run_output_has_no_section(tmp_path: Path) -> None:
    db = str(tmp_path / "c.db")
    with SQLiteStorage(db) as storage:
        seed(storage)
        checkpoint_clean(storage)
    code, out, _ = run("--db", db, "--json", "resume", "run_1")
    assert code == ExitCode.OK
    payload = json.loads(out)
    assert payload["informed_retry"] is None
    assert "informed retry" not in out


# --- regression: duplicated Next steps section ---------------------------------- #


def test_resume_prints_next_steps_exactly_once(history_db: str) -> None:
    _, out, _ = run("--db", history_db, "resume", "run_1")
    assert out.count("\n\nNext steps:\n") == 1
