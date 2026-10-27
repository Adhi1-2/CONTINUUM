"""Structured attempt memory with falsification lessons (issue #313)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from continuum.events import EventType
from continuum.interchange import export_semantic_state, import_semantic_state
from continuum.models import AttemptLesson, Origin, Run
from continuum.recovery.summary import build_attempt_lesson
from continuum.state.semantic import project
from continuum.storage import SQLiteStorage


def _make_storage() -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="g"))
    storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})
    return storage


def test_derivation_from_synthetic_repair_decision_yields_one_lesson() -> None:
    """Falsified from rationale, scars from uncertain actions."""
    from continuum.checkpoint.manager import RestoredRun
    from continuum.models import (
        Component,
        ComponentValidationEntry,
        RecoveryContract,
        RecoverySafety,
        StateStatus,
        StateValidationResult,
    )
    from continuum.recovery.engine import RecoveryDecision
    from continuum.recovery.planner import RepairPlan

    storage = _make_storage()
    try:
        from continuum.actions import ActionLedger

        ledger = ActionLedger(storage, "run_1")
        ledger.claim("test.action", {"x": 1}, key="k1")
        uncertain = ledger.pending()
        assert len(uncertain) == 1

        state = project("run_1", storage.read_events("run_1"))
        report = StateValidationResult(
            run_id="run_1",
            statuses=[
                ComponentValidationEntry(
                    component=Component.EXTERNAL_DEPENDENCY,
                    component_id="db",
                    status=StateStatus.STALE,
                    detail="db v1 -> v2",
                ),
            ],
            safe_to_resume=False,
            reason="db stale",
        )
        from continuum.environment.diff import EnvironmentDiff
        from continuum.state.validator import ValidationOutcome

        validation = ValidationOutcome(
            state=state, report=report, environment_diff=EnvironmentDiff()
        )
        contract = RecoveryContract(
            run_id="run_1",
            checkpoint_version=0,
            recovery_status=RecoverySafety.REQUIRES_REPAIR,
            verified=[],
            invalidated=[],
            required_actions=[],
        )
        restored = RestoredRun(
            run_id="run_1", state=state, checkpoint=None, pending_events=0, replayed=False
        )
        decision = RecoveryDecision(
            run_id="run_1",
            mode="repair_and_resume",  # type: ignore[arg-type]
            contract=contract,
            plan=RepairPlan(steps=[]),
            validation=validation,
            restored=restored,
            uncertain_actions=tuple(uncertain),
            rationale=("db v1 -> v2 is stale", "1 external side effect(s) have unknown outcomes"),
        )
        lesson = build_attempt_lesson(decision, uncertain_actions=uncertain)
        assert lesson.falsified == "db v1 -> v2 is stale"
        assert lesson.scar_action_ids == [uncertain[0].action_id]
        assert "db v1 -> v2" in lesson.env_delta or "db" in lesson.env_delta
        assert lesson.source_evidence
        assert lesson.next_avoid
        assert len(lesson.falsified) <= 512
        assert lesson.attempt_id
    finally:
        storage.close()


def test_lesson_is_hash_chained_and_verify_detects_tampering() -> None:
    storage = _make_storage()
    try:
        from continuum.recovery.engine import RecoveryEngine
        from continuum.recovery.summary import record_attempt_lesson

        engine = RecoveryEngine(storage)
        from continuum.environment import StaticProvider, capture

        provider = StaticProvider(db="v2")
        snap = capture("run_1", provider)
        decision = engine.assess("run_1", current_environment=snap)
        lesson = record_attempt_lesson(
            storage, "run_1", decision, uncertain_actions=decision.uncertain_actions
        )
        assert lesson.falsified

        report = storage.verify_events("run_1")
        assert report.ok
        assert report.trusted_through["run_1"] == storage.last_sequence("run_1")

        events = list(storage.read_events("run_1"))
        tampered = None
        for ev in events:
            if ev.type == EventType.ATTEMPT_LESSON:
                tampered = ev
                break
        assert tampered is not None
        bad = tampered.model_copy(
            update={
                "payload": {
                    "falsified": "hacked",
                    "attempt_id": "x",
                    "created_at": tampered.payload.get("created_at", ""),
                }
            }
        )
        from continuum.events import AppendOnlyViolation, EventLog

        log = EventLog()
        try:
            for e in events:
                if e.event_id == tampered.event_id:
                    log.extend([bad])
                else:
                    log.extend([e])
            rep = log.verify("run_1")
            assert not rep.ok
            assert any(v.kind == "TAMPERED_CONTENT" for v in rep.violations)
        except AppendOnlyViolation as exc:
            assert "hash does not match content" in str(exc).lower()
    finally:
        storage.close()


def test_project_with_no_lesson_events_yields_empty() -> None:
    storage = _make_storage()
    try:
        state = project("run_1", storage.read_events("run_1"))
        assert state.attempt_lessons == []
        assert hasattr(state, "attempt_lessons")
    finally:
        storage.close()


def test_interchange_round_trip_preserves_lessons() -> None:
    storage = _make_storage()
    try:
        from continuum.checkpoint.manager import RestoredRun
        from continuum.environment.diff import EnvironmentDiff
        from continuum.models import (
            Component,
            ComponentValidationEntry,
            RecoveryContract,
            RecoverySafety,
            StateStatus,
            StateValidationResult,
        )
        from continuum.recovery.engine import RecoveryDecision
        from continuum.recovery.planner import RepairPlan
        from continuum.recovery.summary import record_attempt_lesson
        from continuum.state.validator import ValidationOutcome

        state_before = project("run_1", storage.read_events("run_1"))
        report = StateValidationResult(
            run_id="run_1",
            statuses=[
                ComponentValidationEntry(
                    component=Component.GOAL, status=StateStatus.STALE, detail="goal stale"
                )
            ],
            safe_to_resume=False,
            reason="goal stale",
        )
        validation = ValidationOutcome(
            state=state_before, report=report, environment_diff=EnvironmentDiff()
        )
        contract = RecoveryContract(
            run_id="run_1",
            checkpoint_version=0,
            recovery_status=RecoverySafety.REQUIRES_REPAIR,
            verified=[],
            invalidated=[],
            required_actions=[],
        )
        restored = RestoredRun(
            run_id="run_1", state=state_before, checkpoint=None, pending_events=0, replayed=False
        )
        decision = RecoveryDecision(
            run_id="run_1",
            mode="repair_and_resume",  # type: ignore[arg-type]
            contract=contract,
            plan=RepairPlan(steps=[]),
            validation=validation,
            restored=restored,
            uncertain_actions=(),
            rationale=("goal stale",),
        )
        lesson = record_attempt_lesson(storage, "run_1", decision)
        state_after = project("run_1", storage.read_events("run_1"))
        assert len(state_after.attempt_lessons) == 1
        payload = export_semantic_state(state_after)
        imported = import_semantic_state(payload)
        assert imported.attempt_lessons == state_after.attempt_lessons
        assert imported.attempt_lessons[0].falsified == lesson.falsified
    finally:
        storage.close()


def test_lesson_survives_compaction() -> None:
    storage = SQLiteStorage(":memory:")
    try:
        storage.create_run(Run(run_id="run_1", goal="g"))
        storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})
        from continuum.checkpoint.manager import CheckpointManager

        manager = CheckpointManager(storage)
        manager.checkpoint("run_1")
        from continuum.checkpoint.manager import RestoredRun
        from continuum.environment.diff import EnvironmentDiff
        from continuum.models import (
            Component,
            ComponentValidationEntry,
            RecoveryContract,
            RecoverySafety,
            StateStatus,
            StateValidationResult,
        )
        from continuum.recovery.engine import RecoveryDecision
        from continuum.recovery.planner import RepairPlan
        from continuum.recovery.summary import record_attempt_lesson
        from continuum.state.validator import ValidationOutcome

        state = project("run_1", storage.read_events("run_1"))
        report = StateValidationResult(
            run_id="run_1",
            statuses=[
                ComponentValidationEntry(
                    component=Component.EXTERNAL_DEPENDENCY,
                    status=StateStatus.STALE,
                    detail="dep stale",
                )
            ],
            safe_to_resume=False,
            reason="dep stale",
        )
        validation = ValidationOutcome(
            state=state, report=report, environment_diff=EnvironmentDiff()
        )
        contract = RecoveryContract(
            run_id="run_1",
            checkpoint_version=0,
            recovery_status=RecoverySafety.REQUIRES_REPAIR,
            verified=[],
            invalidated=[],
            required_actions=[],
        )
        restored = RestoredRun(
            run_id="run_1", state=state, checkpoint=None, pending_events=0, replayed=False
        )
        decision = RecoveryDecision(
            run_id="run_1",
            mode="repair_and_resume",  # type: ignore[arg-type]
            contract=contract,
            plan=RepairPlan(steps=[]),
            validation=validation,
            restored=restored,
            uncertain_actions=(),
            rationale=("dep stale",),
        )
        lesson = record_attempt_lesson(storage, "run_1", decision)
        assert storage.supports_compaction
        report2 = storage.compact_run("run_1")
        assert report2["archived"] >= 0
        all_events = list(storage.read_archived_events("run_1")) + list(
            storage.read_events("run_1")
        )
        state2 = project("run_1", all_events)
        assert any(
            lesson.attempt_id == lesson_item.attempt_id for lesson_item in state2.attempt_lessons
        )
        restored2 = manager.restore("run_1")
        assert any(
            lesson.attempt_id == lesson_item.attempt_id
            for lesson_item in restored2.state.attempt_lessons
        )
    finally:
        storage.close()


def test_lesson_size_bounded() -> None:
    from continuum.checkpoint.manager import RestoredRun
    from continuum.environment.diff import EnvironmentDiff
    from continuum.models import (
        Component,
        ComponentValidationEntry,
        RecoveryContract,
        RecoverySafety,
        StateStatus,
        StateValidationResult,
    )
    from continuum.recovery.engine import RecoveryDecision
    from continuum.recovery.planner import RepairPlan
    from continuum.recovery.summary import build_attempt_lesson
    from continuum.state.validator import ValidationOutcome

    storage = _make_storage()
    try:
        state = project("run_1", storage.read_events("run_1"))
        oversize = "x" * 1000
        report = StateValidationResult(
            run_id="run_1",
            statuses=[
                ComponentValidationEntry(
                    component=Component.GOAL, status=StateStatus.STALE, detail=oversize
                )
            ],
            safe_to_resume=False,
            reason=oversize,
        )
        validation = ValidationOutcome(
            state=state, report=report, environment_diff=EnvironmentDiff()
        )
        contract = RecoveryContract(
            run_id="run_1",
            checkpoint_version=0,
            recovery_status=RecoverySafety.REQUIRES_REPAIR,
            verified=[],
            invalidated=[],
            required_actions=[],
        )
        restored = RestoredRun(
            run_id="run_1", state=state, checkpoint=None, pending_events=0, replayed=False
        )
        decision = RecoveryDecision(
            run_id="run_1",
            mode="repair_and_resume",  # type: ignore[arg-type]
            contract=contract,
            plan=RepairPlan(steps=[]),
            validation=validation,
            restored=restored,
            uncertain_actions=(),
            rationale=(oversize, oversize),
        )
        lesson = build_attempt_lesson(decision)
        assert len(lesson.falsified) <= 512
        assert len(lesson.env_delta) <= 512
        assert len(lesson.next_avoid) <= 512
        for ev in lesson.source_evidence:
            assert len(ev) <= 512
        total = len(json.dumps(lesson.model_dump(mode="json"), sort_keys=True).encode())
        assert total <= 2048
    finally:
        storage.close()


def _synthetic_decision_with(oversize: str, status_count: int) -> Any:
    """A RecoveryDecision whose validation statuses carry ``oversize`` details."""
    from continuum.checkpoint.manager import RestoredRun
    from continuum.environment.diff import EnvironmentDiff
    from continuum.models import (
        Component,
        ComponentValidationEntry,
        RecoveryContract,
        RecoverySafety,
        StateStatus,
        StateValidationResult,
    )
    from continuum.recovery.engine import RecoveryDecision
    from continuum.recovery.planner import RepairPlan
    from continuum.state.validator import ValidationOutcome

    storage = _make_storage()
    try:
        state = project("run_1", storage.read_events("run_1"))
    finally:
        storage.close()
    report = StateValidationResult(
        run_id="run_1",
        statuses=[
            ComponentValidationEntry(
                component=Component.GOAL,
                component_id=f"g{i}",
                status=StateStatus.STALE,
                detail=oversize,
            )
            for i in range(status_count)
        ],
        safe_to_resume=False,
        reason=oversize,
    )
    validation = ValidationOutcome(state=state, report=report, environment_diff=EnvironmentDiff())
    decision = RecoveryDecision(
        run_id="run_1",
        mode="repair_and_resume",  # type: ignore[arg-type]
        contract=RecoveryContract(
            run_id="run_1",
            checkpoint_version=0,
            recovery_status=RecoverySafety.REQUIRES_REPAIR,
            verified=[],
            invalidated=[],
            required_actions=[],
        ),
        plan=RepairPlan(steps=[]),
        validation=validation,
        restored=RestoredRun(
            run_id="run_1", state=state, checkpoint=None, pending_events=0, replayed=False
        ),
        uncertain_actions=(),
        rationale=(oversize, oversize),
    )
    return decision


def test_build_attempt_lesson_trims_an_oversized_lesson_adaptively() -> None:
    """The adaptive 2048-byte loop runs and converges (#1867).

    Per-field capping to 512 does not by itself keep a lesson within budget:
    several large statuses fill ``source_evidence`` past 2048 bytes, so the
    loop has to halve the largest fields and then trim evidence. The lesson
    it returns must fit, and at least one field must have been *halved*
    rather than merely capped -- a capped field is exactly 512 long, and a
    field the loop touched is strictly shorter.
    """
    from continuum.recovery.summary import build_attempt_lesson

    oversize = "x" * 512
    lesson = build_attempt_lesson(_synthetic_decision_with(oversize, status_count=8))

    assert len(lesson.falsified) <= 512
    assert len(lesson.env_delta) <= 512
    assert len(lesson.next_avoid) <= 512
    total = len(json.dumps(lesson.model_dump(mode="json"), sort_keys=True).encode())
    assert total <= 2048
    # The loop ran: eight 512-char evidence entries (~4 KiB) could not fit
    # otherwise, and it halves fields past their 512 cap-down before it
    # touches evidence.
    assert len(lesson.source_evidence) < 8 or any(
        len(f) < 512 for f in (lesson.falsified, lesson.env_delta, lesson.next_avoid)
    )


def test_build_attempt_lesson_halves_the_largest_field_first() -> None:
    """A skirting-the-budget lesson halves its largest field, once, and keeps evidence (#1867)."""
    from continuum.recovery.summary import build_attempt_lesson

    oversize = "x" * 512
    decision = _synthetic_decision_with(oversize, status_count=2)
    lesson = build_attempt_lesson(decision)

    # Without the loop the two 512-char evidence entries alone would sit the
    # total above 2048; the halving is what brings it under.
    total = len(json.dumps(lesson.model_dump(mode="json"), sort_keys=True).encode())
    assert total <= 2048
    assert len(lesson.source_evidence) == 2
    assert any(len(f) < 512 for f in (lesson.falsified, lesson.env_delta, lesson.next_avoid))


def test_render_attempt_lesson_accepts_a_raw_dictionary() -> None:
    """A dict routes through the same formatter as a model instance."""
    from continuum.recovery.summary import render_attempt_lesson

    lines = render_attempt_lesson(
        {
            "attempt_id": "attempt_7",
            "falsified": "db v1 -> v2 stale",
            "env_delta": "db v1 -> v2",
            "scar_action_ids": ["act_1", "act_2"],
            "next_avoid": "re-pin --env before trusting cache",
            "source_evidence": ["ledger shows the flip", "env probe disagrees"],
        }
    )
    assert lines == [
        "attempt attempt_7: db v1 -> v2 stale",
        "env delta: db v1 -> v2",
        "scar actions: act_1, act_2",
        "next avoid: re-pin --env before trusting cache",
        "evidence: ledger shows the flip",
        "evidence: env probe disagrees",
    ]


def test_render_attempt_lesson_without_an_attempt_id_leads_with_falsified() -> None:
    """An empty attempt id falls back to the falsified premise as the lead line."""
    from continuum.recovery.summary import render_attempt_lesson

    lines = render_attempt_lesson(
        {
            "attempt_id": "",
            "falsified": "db v1 -> v2 stale",
            "env_delta": "",
            "scar_action_ids": [],
            "next_avoid": "",
            "source_evidence": [],
        }
    )
    assert lines == ["falsified: db v1 -> v2 stale"]


def test_briefing_includes_lessons_and_excludes_raw_tail(tmp_path=None) -> None:
    from continuum.state.semantic import project

    storage = SQLiteStorage(":memory:")
    try:
        storage.create_run(Run(run_id="run_1", goal="g"))
        storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})
        from continuum.checkpoint.manager import RestoredRun
        from continuum.environment.diff import EnvironmentDiff
        from continuum.models import (
            Component,
            ComponentValidationEntry,
            RecoveryContract,
            RecoverySafety,
            StateStatus,
            StateValidationResult,
        )
        from continuum.recovery.engine import RecoveryDecision
        from continuum.recovery.planner import RepairPlan
        from continuum.recovery.summary import record_attempt_lesson
        from continuum.state.validator import ValidationOutcome

        state = project("run_1", storage.read_events("run_1"))
        report = StateValidationResult(
            run_id="run_1",
            statuses=[
                ComponentValidationEntry(
                    component=Component.GOAL, status=StateStatus.STALE, detail="goal stale"
                )
            ],
            safe_to_resume=False,
            reason="goal stale",
        )
        validation = ValidationOutcome(
            state=state, report=report, environment_diff=EnvironmentDiff()
        )
        contract = RecoveryContract(
            run_id="run_1",
            checkpoint_version=0,
            recovery_status=RecoverySafety.REQUIRES_REPAIR,
            verified=[],
            invalidated=[],
            required_actions=[],
        )
        restored = RestoredRun(
            run_id="run_1", state=state, checkpoint=None, pending_events=0, replayed=False
        )
        decision = RecoveryDecision(
            run_id="run_1",
            mode="repair_and_resume",  # type: ignore[arg-type]
            contract=contract,
            plan=RepairPlan(steps=[]),
            validation=validation,
            restored=restored,
            uncertain_actions=(),
            rationale=("goal stale",),
        )
        lesson = record_attempt_lesson(storage, "run_1", decision)
        storage.append_event(
            "run_1",
            EventType.EVIDENCE_ADDED,
            {
                "evidence_id": "tail_1",
                "summary": "Traceback: raw error tail that should be excluded",
                "status": "valid",
            },
        )
        state2 = project("run_1", storage.read_events("run_1"))
        assert any(
            lesson_item.attempt_id == lesson.attempt_id for lesson_item in state2.attempt_lessons
        )
        from continuum.recovery.engine import RecoveryEngine

        decision2 = RecoveryEngine(storage).assess("run_1")
        assert decision2.state.attempt_lessons
        from continuum.recovery.summary import render_attempt_lesson

        lines = render_attempt_lesson(decision2.state.attempt_lessons[0])
        assert any("falsified" in line or lesson.falsified[:20] in line for line in lines)
        for line in lines:
            assert "Traceback" not in line
    finally:
        storage.close()


def test_property_ordering_deterministic_by_created_at() -> None:

    storage = SQLiteStorage(":memory:")
    try:
        storage.create_run(Run(run_id="run_1", goal="g"))
        storage.append_event("run_1", EventType.RUN_STARTED, {"goal": "g"})
        base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        lessons = [
            AttemptLesson(
                attempt_id="a2", falsified="second", created_at=base_time + timedelta(seconds=10)
            ),
            AttemptLesson(attempt_id="a1", falsified="first", created_at=base_time),
            AttemptLesson(
                attempt_id="a3", falsified="third", created_at=base_time + timedelta(seconds=20)
            ),
        ]
        for lesson in lessons:
            storage.append_event(
                "run_1",
                EventType.ATTEMPT_LESSON,
                lesson.model_dump(mode="json"),
                source=Origin.DETERMINISTIC,
            )
        state = project("run_1", storage.read_events("run_1"))
        ids = [lesson.attempt_id for lesson in state.attempt_lessons]
        assert ids == ["a1", "a2", "a3"]
        state2 = project("run_1", storage.read_events("run_1"))
        assert [lesson_item.attempt_id for lesson_item in state2.attempt_lessons] == ids
        dup = AttemptLesson(
            attempt_id="a1", falsified="duplicate", created_at=base_time + timedelta(seconds=30)
        )
        storage.append_event(
            "run_1",
            EventType.ATTEMPT_LESSON,
            dup.model_dump(mode="json"),
            source=Origin.DETERMINISTIC,
        )
        state3 = project("run_1", storage.read_events("run_1"))
        a1 = next(
            lesson_item for lesson_item in state3.attempt_lessons if lesson_item.attempt_id == "a1"
        )
        assert a1.falsified == "first"
    finally:
        storage.close()
