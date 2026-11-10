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
from typing import Any

import pytest

from continuum.actions import ActionLedger
from continuum.checkpoint import CheckpointManager
from continuum.cli import ExitCode, main
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import (
    Component,
    ComponentValidationEntry,
    Run,
    StateStatus,
    StateValidationResult,
)
from continuum.recovery import RecoveryEngine
from continuum.recovery.summary import (
    INFORMED_RETRY_CAP_BYTES,
    _avoid_rules,
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


# --- step naming, avoid-rule templates, eviction and rendering (#2054) ------------- #


def _retry_from_recovery_start(plan: list[dict[str, object]]) -> dict[str, Any]:
    """Build a block from one RECOVERY_STARTED carrying ``plan`` steps.

    ``_step_name`` reads the last attempt's recorded plan, which is where the
    block's ``last_attempt_steps`` come from -- the three naming shapes a
    stored plan step can carry are exercised through this single seam.
    """
    storage = SQLiteStorage(":memory:")
    seed(storage)
    storage.append_event("run_1", EventType.RECOVERY_STARTED, {"plan": plan})
    block = build_informed_retry(
        storage,
        "run_1",
        validation_report=StateValidationResult(run_id="run_1", statuses=[]),
    )
    assert block is not None
    return block


def test_the_last_plan_step_can_carry_an_explicit_action_name() -> None:
    block = _retry_from_recovery_start([{"action_name": "settle_invoice"}])

    assert block["last_attempt_steps"] == ["settle_invoice"]


def test_the_last_plan_step_falls_back_to_kind_colon_target() -> None:
    block = _retry_from_recovery_start([{"kind": "tool", "target": "write_file"}])

    assert block["last_attempt_steps"] == ["tool:write_file"]


def test_the_last_plan_step_with_only_a_kind_names_it_barely() -> None:
    block = _retry_from_recovery_start([{"kind": "step"}])

    assert block["last_attempt_steps"] == ["step"]


@pytest.mark.parametrize(
    ("component", "component_id", "expected"),
    [
        (
            Component.EVIDENCE,
            "paper_1",
            "evidence:paper_1: stale; re-derive from its source before citing it again",
        ),
        (
            Component.FINDING,
            "finding_1",
            "finding:finding_1: stale; re-derive from its source before citing it again",
        ),
        (
            Component.MODEL,
            "gpt-9",
            "model:gpt-9: model assumptions unverified; confirm which model resumes",
        ),
        (
            Component.APPROVAL,
            "approval_1",
            "approval:approval_1: approval does not carry over; obtain a fresh one",
        ),
    ],
)
def test_avoid_rules_template_per_component(
    component: Component, component_id: str, expected: str
) -> None:
    """Each template restates what the validator already concluded in the words
    a resuming agent can act on -- never a cause it cannot see."""
    entry = ComponentValidationEntry(
        component=component, component_id=component_id, status=StateStatus.INVALID
    )

    assert _avoid_rules([entry], []) == [expected]


def test_fit_strips_failure_detail_before_giving_up() -> None:
    """Deeper into the eviction ladder than the size-cap test reaches: with
    every list already as short as it can go, the loop strips ``detail`` from
    the remaining failure -- and when even that does not fit, it hard-stops
    keeping the block intact rather than emptying it."""
    fat = {
        "attempts": 1,
        "last_attempt_mode": "m" * (INFORMED_RETRY_CAP_BYTES * 4),
        "last_attempt_steps": ["only step"],
        "settled_effects": [],
        "current_failures": [
            {"component": "goal", "id": "g", "detail": "d" * INFORMED_RETRY_CAP_BYTES}
        ],
        "avoid": [],
    }

    fitted = _fit(fat)

    # First pass: the detail arm fires.
    assert fitted["current_failures"][0]["detail"] == ""
    # Second pass: nothing left to trim, so the loop breaks and keeps the
    # untrimmed remainder rather than discarding the block's shape.
    assert fitted["last_attempt_mode"] == fat["last_attempt_mode"]
    assert fitted["current_failures"][0]["id"] == "g"


def test_render_counts_every_recovery_outcome() -> None:
    block: dict[str, Any] = {
        "completed_recoveries": 3,
        "blocked_recoveries": 1,
        "compensations": 2,
    }

    lines = render_informed_retry(block)

    assert "recoveries completed: 3" in lines
    assert "recoveries blocked: 1" in lines
    assert "compensations applied: 2" in lines


def test_render_reports_attempts_without_a_last_plan_mode() -> None:
    """A prior attempt with no recorded mode still counts, without printing an
    empty mode line."""
    lines = render_informed_retry({"attempts": 2})

    assert "previous attempt(s): 2" in lines
    assert not any("last plan mode" in line for line in lines)


def test_render_names_failures_with_and_without_an_id_and_detail() -> None:
    block: dict[str, Any] = {
        "current_failures": [
            {"component": "goal", "id": "gone_stale", "detail": "no longer matches the work"},
            {"component": "pin", "detail": ""},
        ]
    }

    lines = render_informed_retry(block)

    assert "still failing: goal:gone_stale (no longer matches the work)" in lines
    assert "still failing: pin" in lines
