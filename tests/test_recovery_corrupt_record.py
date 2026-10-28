"""A damaged log still gets a verdict, not a traceback (#1907).

``RecoveryEngine.assess`` calls ``CheckpointManager.restore`` with
``on_unprojectable="degrade"`` on purpose: the engine's whole job is to
answer where a run stands, and a poisoned log is precisely the run that
needs an answer (issue #383). But the degrade contract only covered events
that could be *read* and then failed to fold. A row whose payload could not
be parsed at all raised ``CorruptedRecord`` out of ``storage.read_events``
before projection -- or out of the engine's auxiliary readers -- so ``assess``
crashed on the one run it was built for.

``CorruptedRecord`` now locates the refusing row, ``restore`` folds the clean
prefix preceding it and marks the state INVALID at the refused sequence, and
``assess`` degrades its auxiliary readers instead of letting the read error
escape.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from continuum.checkpoint import CheckpointManager
from continuum.environment import StaticProvider, capture
from continuum.events import EventType
from continuum.models import RecoveryMode, Run, StateStatus
from continuum.recovery import RecoveryEngine
from continuum.storage import SQLiteStorage
from continuum.storage.base import CorruptedRecord


def _build_log(tmp_path: Path, run_id: str = "damaged") -> Path:
    """A run whose first two events fold cleanly and whose third is corrupt.

    Corruption happens below the storage layer, the way bit rot or a crashed
    flush actually lands: the payload column holds invalid JSON afterwards.
    """
    db = tmp_path / "damaged.db"
    storage = SQLiteStorage(db)
    storage.create_run(Run(run_id=run_id, goal="g"))
    storage.append_event(run_id, EventType.RUN_STARTED, {"goal": "g", "total": 3})
    storage.append_event(run_id, EventType.WORK_COMPLETED, {"doc": 0})
    storage.append_event(run_id, EventType.WORK_COMPLETED, {"doc": 1})
    storage.close()
    _corrupt_event(db, run_id, sequence=3)
    return db


def _corrupt_event(db: Path, run_id: str, *, sequence: int) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE events SET payload = ? WHERE run_id = ? AND sequence = ?",
            ('{"broken": "trunc', run_id, sequence),
        )
        conn.commit()
    finally:
        conn.close()


def _engine(db: Path) -> RecoveryEngine:
    return RecoveryEngine(SQLiteStorage(db))


# --- the storage refusal locates the damage -------------------------------- #


def test_corrupted_record_locates_the_refusing_row(tmp_path: Path) -> None:
    db = _build_log(tmp_path)
    storage = SQLiteStorage(db)
    try:
        with pytest.raises(CorruptedRecord) as excinfo:
            storage.read_events("damaged")
    finally:
        storage.close()
    assert excinfo.value.run_id == "damaged"
    assert excinfo.value.sequence == 3


def test_corrupted_record_without_a_row_has_no_location(tmp_path: Path) -> None:
    """Refusals raised outside a per-row read carry no location."""
    exc = CorruptedRecord("checkpoint failed to load")
    assert exc.run_id is None
    assert exc.sequence is None


# --- restore ---------------------------------------------------------------- #


def test_restore_degrades_on_corrupted_tail(tmp_path: Path) -> None:
    db = _build_log(tmp_path)
    storage = SQLiteStorage(db)
    try:
        restored = CheckpointManager(storage).restore("damaged", on_unprojectable="degrade")
    finally:
        storage.close()
    state = restored.state
    assert state.status is StateStatus.INVALID
    assert state.unprojectable_at_sequence == 3
    reason = state.unprojectable_reason
    assert reason is not None
    assert "corrupted event record" in reason
    # The clean prefix folded: the refusal point is named, not the whole log.
    assert state.source_sequence == 2
    assert state.goal is not None


def test_restore_raises_by_default_on_corrupted_tail(tmp_path: Path) -> None:
    """Write paths keep the loud failure: they must never trust a partial fold."""
    db = _build_log(tmp_path)
    storage = SQLiteStorage(db)
    try:
        with pytest.raises(CorruptedRecord):
            CheckpointManager(storage).restore("damaged")
    finally:
        storage.close()


def test_restore_degrades_when_a_checkpoint_exists(tmp_path: Path) -> None:
    """The checkpointed path degrades the same way as the no-checkpoint one."""
    db = tmp_path / "checkpointed.db"
    storage = SQLiteStorage(db)
    storage.create_run(Run(run_id="r", goal="g"))
    storage.append_event("r", EventType.RUN_STARTED, {"goal": "g", "total": 3})
    storage.append_event("r", EventType.WORK_COMPLETED, {"doc": 0})
    env = capture("r", StaticProvider(dataset="v1"))
    CheckpointManager(storage).checkpoint("r", environment=env)
    storage.append_event("r", EventType.WORK_COMPLETED, {"doc": 1})
    storage.close()
    _corrupt_event(db, "r", sequence=4)

    storage = SQLiteStorage(db)
    try:
        restored = CheckpointManager(storage).restore("r", on_unprojectable="degrade")
    finally:
        storage.close()
    assert restored.state.status is StateStatus.INVALID
    assert restored.state.unprojectable_at_sequence == 4


def test_restore_raises_when_nothing_is_readable(tmp_path: Path) -> None:
    """A corrupted first event leaves no prefix to report (build_degrade's guard)."""
    db = tmp_path / "dead.db"
    storage = SQLiteStorage(db)
    storage.create_run(Run(run_id="r", goal="g"))
    storage.append_event("r", EventType.RUN_STARTED, {"goal": "g"})
    storage.close()
    _corrupt_event(db, "r", sequence=1)

    storage = SQLiteStorage(db)
    try:
        with pytest.raises(CorruptedRecord):
            CheckpointManager(storage).restore("r", on_unprojectable="degrade")
    finally:
        storage.close()


# --- assess ----------------------------------------------------------------- #


def test_assess_returns_advisory_verdict_on_corrupted_event(tmp_path: Path) -> None:
    """The issue's reproduction: assess answers instead of crashing."""
    db = _build_log(tmp_path)
    decision = _engine(db).assess("damaged")
    assert decision.mode is RecoveryMode.REQUEST_HUMAN
    assert decision.restored.state.status is StateStatus.INVALID
    assert decision.restored.state.unprojectable_at_sequence == 3
    rationale = "; ".join(decision.rationale)
    assert "sequence 3" in rationale
    assert "action ledger unreadable" in rationale


def test_assess_includes_a_repair_step_for_the_damaged_log(tmp_path: Path) -> None:
    """The degraded fold must reach the plan (#383), also for an unreadable row."""
    db = _build_log(tmp_path)
    decision = _engine(db).assess("damaged")
    assert decision.plan.steps
    assert any(step.requires_human for step in decision.plan.steps)


def test_assess_survives_a_corrupted_action_ledger(tmp_path: Path) -> None:
    """An in-flight side effect before the damage cannot be enumerated, so the
    verdict names the unreadable ledger instead of guessing it away."""
    db = tmp_path / "inflight.db"
    storage = SQLiteStorage(db)
    storage.create_run(Run(run_id="r", goal="g"))
    storage.append_event("r", EventType.RUN_STARTED, {"goal": "g", "total": 2})
    from continuum.actions import ActionLedger

    claim = ActionLedger(storage, "r").claim("stripe.charge", {"amount": 10})
    assert claim.fresh is True
    storage.append_event("r", EventType.WORK_COMPLETED, {"doc": 0})
    storage.close()
    _corrupt_event(db, "r", sequence=3)

    decision = _engine(db).assess("r")
    assert decision.mode is RecoveryMode.REQUEST_HUMAN
    assert any("action ledger unreadable" in line for line in decision.rationale)


def test_assess_on_a_clean_log_is_unchanged(tmp_path: Path) -> None:
    """The fix must not change the verdict a healthy run gets."""
    db = tmp_path / "clean.db"
    storage = SQLiteStorage(db)
    storage.create_run(Run(run_id="r", goal="g"))
    storage.append_event("r", EventType.RUN_STARTED, {"goal": "g", "total": 2})
    storage.append_event("r", EventType.WORK_COMPLETED, {"doc": 0})
    storage.close()

    decision = _engine(db).assess("r")
    assert decision.mode is RecoveryMode.RESUME
    assert decision.restored.state.status is StateStatus.VALID
