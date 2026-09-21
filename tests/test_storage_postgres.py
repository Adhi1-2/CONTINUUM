"""Contract tests for the PostgreSQL storage backend.

Runs the core SQLite-suite behaviours against a real Postgres so the second
engine is a verified surface, not a typed stub. Skips cleanly when
``CONTINUUM_TEST_POSTGRES_DSN`` or ``psycopg`` is absent; CI exercises it for
real via a Postgres 16 service container.
"""

from __future__ import annotations

import os

import pytest

from continuum.actions import ActionLedger
from continuum.actions.idempotency import idempotency_key
from continuum.checkpoint import CheckpointManager
from continuum.events import EventType
from continuum.models import ActionStatus, Origin, Run, RunStatus
from continuum.storage.base import ConcurrentWriteError, RunNotFound
from continuum.storage.postgres import PostgresStorage


class _Abort(Exception):
    """Sentinel raised inside a ``transaction()`` block to roll it back.

    psycopg3 commits a ``transaction()`` block on a clean exit and rolls it
    back on a propagating one, so raising this is how the tamper tests undo
    their corruption. An ``assert`` that fails inside the block propagates
    too, so the row is restored even when the test itself fails -- the
    snapshot-and-restore this replaced only repaired the happy path.
    """


DSN = os.environ.get("CONTINUUM_TEST_POSTGRES_DSN")


def _psycopg_available() -> bool:
    try:
        import psycopg  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    DSN is None or not _psycopg_available(),
    reason="set CONTINUUM_TEST_POSTGRES_DSN and install continuum[postgres] to run",
)


@pytest.fixture
def storage() -> PostgresStorage:
    store = PostgresStorage(DSN)
    yield store
    store.close()


def make_run(store: PostgresStorage, run_id: str, goal: str = "g") -> None:
    store.create_run_started(Run(run_id=run_id, goal=goal))


# --- run lifecycle ------------------------------------------------------------ #


def test_run_lifecycle_round_trip(storage: PostgresStorage) -> None:
    make_run(storage, "pg_life", "Ship it")
    run = storage.get_run("pg_life")
    assert run.status.value == "started"
    assert storage.last_sequence("pg_life") == 1

    updated = storage.update_run(storage.get_run("pg_life").touch(status=RunStatus.COMPLETED))
    assert updated.status.value == "completed"


def test_duplicate_start_is_refused_atomically(storage: PostgresStorage) -> None:
    from continuum.models import Origin

    make_run(storage, "pg_dup")
    with pytest.raises(ConcurrentWriteError):
        storage.create_run_started(Run(run_id="pg_dup", goal="again"), source=Origin.HUMAN)


def test_unknown_run_maps_to_not_found(storage: PostgresStorage) -> None:
    with pytest.raises(RunNotFound):
        storage.get_run("ghost")


def test_active_run_resolution_skips_terminal(storage: PostgresStorage) -> None:
    make_run(storage, "pg_done", "done deal")
    storage.update_run(storage.get_run("pg_done").touch(status=RunStatus.COMPLETED))
    make_run(storage, "pg_live", "still going")
    active = storage.get_active_run()
    assert active is not None
    assert active.run_id == "pg_live"


# --- events --------------------------------------------------------------------- #


def test_event_ordering_reads_and_windowing(storage: PostgresStorage) -> None:
    make_run(storage, "pg_ev", "events")
    for i in range(1, 5):
        storage.append_event("pg_ev", EventType.TASK_UPDATED, {"i": i})
    events = storage.read_events("pg_ev")
    # RUN_STARTED + four TASK_UPDATED appends.
    assert [e.sequence for e in events] == [1, 2, 3, 4, 5]

    window = storage.read_events("pg_ev", after_sequence=1, upto=3)
    assert [e.sequence for e in window] == [2, 3]
    assert all(e.type is EventType.TASK_UPDATED for e in window)


def test_event_chain_verification_and_tamper_detection(
    storage: PostgresStorage,
) -> None:
    make_run(storage, "pg_chain", "chain")
    storage.append_event("pg_chain", EventType.TASK_UPDATED, {"n": 1})
    report = storage.verify_events("pg_chain")
    assert report.ok is True
    assert report.trusted_through["pg_chain"] == 2


def test_concurrent_sequence_is_refused(storage: PostgresStorage) -> None:
    make_run(storage, "pg_c", "c")
    storage.append_event("pg_c", EventType.TASK_UPDATED, {"n": 1})
    with pytest.raises(ConcurrentWriteError):
        storage.append_event("pg_c", EventType.TASK_UPDATED, {"n": 2}, expected_sequence=0)


def test_provenance_survives_the_round_trip(storage: PostgresStorage) -> None:
    make_run(storage, "pg_prov", "p")
    storage.append_event(
        "pg_prov",
        EventType.TOOL_COMPLETED,
        {"tool": "write_file"},
        source=Origin.EXTERNAL_AGENT,
    )
    with PostgresStorage(DSN) as fresh:
        events = fresh.read_events("pg_prov")
    assert events[-1].source is Origin.EXTERNAL_AGENT


# --- versions / checkpoints ------------------------------------------------------ #


def test_checkpoint_manager_round_trip(storage: PostgresStorage) -> None:
    make_run(storage, "pg_ck", "checkpoint me")
    manager = CheckpointManager(storage)
    checkpoint = manager.checkpoint("pg_ck")
    assert checkpoint.version >= 0
    restored = CheckpointManager(storage).restore("pg_ck")
    assert restored.state.run_id == "pg_ck"
    manager.checkpoint("pg_ck")  # second checkpoint: new version or same id
    assert storage.list_versions("pg_ck"), "versions must persist"


def test_list_versions_and_latest(storage: PostgresStorage) -> None:
    make_run(storage, "pg_v", "versions")
    CheckpointManager(storage).checkpoint("pg_v")
    versions = storage.list_versions("pg_v")
    assert versions, "expected at least one stored version"
    assert storage.latest_version("pg_v") is not None


# --- action index (issue #216 projection over Postgres) -------------------------- #


def test_unscoped_claim_deduplicates_through_the_index(
    storage: PostgresStorage,
) -> None:
    a = ActionLedger(storage, "pg_a")
    make_run(storage, "pg_a", "a")
    b = ActionLedger(storage, "pg_b")
    make_run(storage, "pg_b", "b")

    first = a.claim("send_invoice", {}, key="invoice:I-1", scoped_to_run=False)
    a.complete(first.key, external_id="INV-1")
    second = b.claim("send_invoice", {}, key="invoice:I-1", scoped_to_run=False)
    assert second.fresh is False
    assert second.action.external_id == "INV-1"


def test_uncertain_elsewhere_blocks_through_the_index(
    storage: PostgresStorage,
) -> None:
    from continuum.models import UnknownSideEffect

    a = ActionLedger(storage, "pg_c1")
    b = ActionLedger(storage, "pg_c2")
    make_run(storage, "pg_c1", "a")
    make_run(storage, "pg_c2", "b")
    a.claim("send_invoice", {}, key="invoice:X", scoped_to_run=False)
    with pytest.raises(UnknownSideEffect):
        b.claim("send_invoice", {}, key="invoice:X", scoped_to_run=False)


def test_action_status_enum_round_trip(storage: PostgresStorage) -> None:
    ledger = ActionLedger(storage, "pg_s")
    make_run(storage, "pg_s", "s")
    outcome = ledger.claim("deploy", {}, key="dep:1")
    ledger.fail(outcome.key, "boom", certain=True)
    statuses = {a.action_type: a.status for a in ledger.all()}
    assert statuses["deploy"] is ActionStatus.FAILED


# --- langgraph tables exist (schema v4 baseline) ---------------------------------- #


def test_langgraph_tables_present(storage: PostgresStorage) -> None:
    rows = storage._connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name IN"
        " ('lg_checkpoints', 'lg_writes')"
    ).fetchall()
    names = {r["table_name"] for r in rows}
    assert {"lg_checkpoints", "lg_writes"} <= names


# --- compaction (issue #239 parity with the SQLite engine) -------------------------- #


def test_compact_archives_prefix_and_verify_stays_ok(storage: PostgresStorage) -> None:
    make_run(storage, "pg_k", "long task")
    for i in range(3):
        storage.append_event("pg_k", EventType.TASK_UPDATED, {"i": i})
    CheckpointManager(storage).checkpoint("pg_k")

    report = storage.compact_run("pg_k")
    assert report["archived"] > 0

    live = storage.read_events("pg_k")
    assert [e.type for e in live][-1] is EventType.EVENT_LOG_ANCHORED
    archived = storage.read_archived_events("pg_k")
    assert archived[0].sequence == 1
    # Archived prefix and live tail agree on history: no gaps, hashes line up.
    assert storage.verify_events("pg_k").ok is True


def test_pg_compact_rejects_through_sequence_that_would_eat_the_anchor(
    storage: PostgresStorage,
) -> None:
    """Issue #1078: the Postgres backend kept every other safety check the
    SQLite compaction makes but dropped this one. A through_sequence at or
    above the anchor marker's sequence would archive and delete the marker and
    every live row after it, so the next append mints a fresh genesis and forks
    the hash chain away from the archive."""
    make_run(storage, "pg_kg", "anchor guard")
    for i in range(3):
        storage.append_event("pg_kg", EventType.TASK_UPDATED, {"i": i})
    pre_live = len(storage.read_events("pg_kg"))

    with pytest.raises(ValueError, match="anchor"):
        storage.compact_run("pg_kg", through_sequence=10_000)

    # The rejected call leaves a healthy, verifiable log behind: nothing was
    # archived, only the forced checkpoint marker was appended.
    assert storage.verify_events("pg_kg").ok is True
    live = storage.read_events("pg_kg")
    assert len(live) == pre_live + 1
    assert live[0].sequence == 1, "live rows must not have moved"
    assert list(storage.read_archived_events("pg_kg")) == []

    # A bounded value below the anchor still compacts normally.
    result = storage.compact_run("pg_kg", through_sequence=1)
    assert result["archived"] >= 1
    assert [e.type for e in storage.read_events("pg_kg")][-1] is EventType.EVENT_LOG_ANCHORED
    assert storage.verify_events("pg_kg").ok is True


def test_pg_a_run_can_be_compacted_repeatedly(storage: PostgresStorage) -> None:
    """Compact, work, compact, on Postgres too (issue #648, PR #715 review).

    The second compact takes a fresh anchor checkpoint whose projection used
    to fold the live tail only. After the first compaction that tail begins
    at the anchor markers with no RUN_STARTED, so the anchor raised
    "could not be anchored ... has no goal" on this backend exactly as it did
    on SQLite before the fix. The SQLite regression test lives in
    tests/test_compaction.py; this is its Postgres twin, so the second engine
    is a verified surface for the same property, not a typed stub.
    """
    make_run(storage, "pg_kr", "long-lived task")
    for i in range(3):
        storage.append_event("pg_kr", EventType.TASK_UPDATED, {"i": i})
    first = storage.compact_run("pg_kr")
    assert first["archived"] > 0
    assert storage.verify_events("pg_kr").ok is True

    for i in range(3, 6):
        storage.append_event("pg_kr", EventType.TASK_UPDATED, {"i": i})
    archived_before = len(storage.read_archived_events("pg_kr"))
    second = storage.compact_run("pg_kr")
    assert second["archived"] > 0, "the second compact must archive the new prefix"
    assert storage.verify_events("pg_kr").ok is True
    # Only the new prefix moved: the archive grew by exactly what this compact
    # reported, and the live tail still carries its anchor marker.
    archived_after = len(storage.read_archived_events("pg_kr"))
    assert archived_after - archived_before == second["archived"]
    assert [e.type for e in storage.read_events("pg_kr")][-1] is EventType.EVENT_LOG_ANCHORED

    # Restore still works on the twice-compacted run.
    restored = CheckpointManager(storage).restore("pg_kr")
    assert restored.state.run_id == "pg_kr"


def test_pg_archive_tampering_fails_verify(storage: PostgresStorage) -> None:
    make_run(storage, "pg_kt", "tamper target")
    CheckpointManager(storage).checkpoint("pg_kt")
    storage.compact_run("pg_kt")

    # The suite shares one database, so the tamper is undone afterwards: a
    # permanently corrupted archive makes every later store-wide check read
    # dirty for a reason this test owns (issue #1321). psycopg commits a
    # transaction() block on a clean exit and rolls it back on a propagating
    # one, so the test raises to undo its own corruption -- which also restores
    # the row when an assertion inside the block fails, where the
    # snapshot-and-restore this replaced only repaired the happy path, and
    # needs no knowledge of the row's current contents at all.
    try:
        with storage._connection.transaction():
            storage._connection.execute(
                "UPDATE events_archive SET payload = '{\"tampered\": true}' WHERE run_id = 'pg_kt'"
            )
            report = storage.verify_events("pg_kt")
            assert report.ok is False
            assert any(v.kind == "TAMPERED_CONTENT" for v in report.violations)
            raise _Abort
    except _Abort:
        pass


def test_pg_deleted_boundary_event_fails_verify(storage: PostgresStorage) -> None:
    make_run(storage, "pg_kb", "boundary target")
    CheckpointManager(storage).checkpoint("pg_kb")
    storage.compact_run("pg_kb")

    # The DELETE must stay run-scoped: an unscoped
    # "sequence = (SELECT MIN(sequence) ... WHERE run_id = 'pg_kb')" takes that
    # sequence number out of every other run too, and the action index rows
    # those rows wrote survive as orphans the projection cannot explain.
    # Rolled back rather than re-inserted, for the same reason as the tamper
    # above: an INSERT that has to enumerate the column list stops restoring
    # the row the day a migration adds a NOT NULL column to events.
    try:
        with storage._connection.transaction():
            storage._connection.execute(
                "DELETE FROM events WHERE run_id = 'pg_kb' AND sequence ="
                " (SELECT MIN(sequence) FROM events WHERE run_id = 'pg_kb')"
            )
            report = storage.verify_events("pg_kb")
            assert report.ok is False
            kinds = {v.kind for v in report.violations}
            assert {"SEQUENCE_GAP", "BROKEN_CHAIN"} & kinds
            raise _Abort
    except _Abort:
        pass


def test_pg_action_index_covers_the_archive_after_compaction(
    storage: PostgresStorage,
) -> None:
    make_run(storage, "pg_ki", "index target")
    ledger = ActionLedger(storage, "pg_ki")
    outcome = ledger.claim("process_doc", {}, key="doc:1")
    ledger.complete(outcome.key, external_id="doc:1")
    storage.compact_run("pg_ki")

    # Compaction alone is not drift: the archived claim is still served, and
    # the renumbering the fold performs changes no answer the projection gives
    # (issue #1321).
    assert storage.action_index_drift() == 0
    key = str(idempotency_key("process_doc", None, scope="pg_ki", key="doc:1"))
    foreign = storage.foreign_action(key, exclude_run="some_other_run")
    assert foreign is not None
    assert foreign.status is ActionStatus.COMPLETED

    # The guard used to reach its "> 0" step through that renumbering; now it
    # can only be reached by a row the log does not support, which is what
    # makes the check worth running at all.
    storage._connection.execute("UPDATE action_index SET status = 'failed' WHERE key = %s", (key,))
    assert storage.action_index_drift() >= 1
    storage.rebuild_action_index()
    assert storage.action_index_drift() == 0


def test_pg_drift_stays_zero_when_a_non_action_event_precedes_an_action(
    storage: PostgresStorage,
) -> None:
    """The case issue #1321 reports: the fold counts every row while the
    incremental writer counts only actions, so the two number a row on
    different scales and the store read dirty the moment it had logged
    anything but actions. ``verify`` must stay quiet on a store like this.
    """
    make_run(storage, "pg_drift_a", "drift")
    storage.append_event("pg_drift_a", EventType.TASK_UPDATED, {"n": 1})
    ledger = ActionLedger(storage, "pg_drift_a")
    ledger.claim("process_doc", {}, key="doc:1")
    assert storage.action_index_drift() == 0
    ledger.claim("process_doc", {}, key="doc:2")
    assert storage.action_index_drift() == 0


def test_pg_drift_stays_zero_after_a_rebuild_and_further_actions(
    storage: PostgresStorage,
) -> None:
    """A rebuilt store must stay converged as new actions arrive.

    Rebuild writes the fold's own position back for every row, which is a
    different scale from the sequence the incremental writer hands out. That
    used to matter only because ``foreign_action`` ranked rows by that column;
    the ranking is gone, the row a lookup reads is chosen by the primary key,
    and the two scales meeting in one table changes no answer. What still has
    to hold is convergence: a rebuilt store that then logs more actions must
    not read dirty, and must not need rebuilding again.
    """
    make_run(storage, "pg_drift_b", "drift")
    ledger = ActionLedger(storage, "pg_drift_b")
    ledger.claim("process_doc", {}, key="doc:1")
    assert storage.action_index_drift() == 0
    assert storage.rebuild_action_index() == 0, "an intact store repairs nothing"
    assert storage.action_index_drift() == 0
    ledger.claim("process_doc", {}, key="doc:2")
    ledger.claim("process_doc", {}, key="doc:3")
    assert storage.action_index_drift() == 0
    # Every claim the store made is still reachable through the projection.
    for n in (1, 2, 3):
        key = idempotency_key("process_doc", None, scope="pg_drift_b", key=f"doc:{n}")
        assert storage.foreign_action(str(key), exclude_run="nobody") is not None


def test_pg_a_status_the_log_does_not_support_is_still_drift(
    storage: PostgresStorage,
) -> None:
    """The order column is not compared, so the check needs a real foothold.

    A row whose status the log no longer produces is corruption an operator
    must hear about. This is the guarantee the relaxed comparison keeps now
    that renumbering no longer counts.
    """
    make_run(storage, "pg_drift_c", "drift")
    ActionLedger(storage, "pg_drift_c").claim("process_doc", {}, key="doc:1")
    key = str(idempotency_key("process_doc", None, scope="pg_drift_c", key="doc:1"))
    storage._connection.execute("UPDATE action_index SET updated_seq = 0 WHERE key = %s", (key,))
    assert storage.action_index_drift() == 0
    storage._connection.execute(
        "UPDATE action_index SET status = 'completed' WHERE key = %s", (key,)
    )
    assert storage.action_index_drift() == 1
    # The count the CLI prints is the drift that called for the repair, and it
    # matches the number the SQLite engine prints for the same corruption
    # (#1267): this used to return 0 while the repair ran.
    assert storage.rebuild_action_index() == 1
    assert storage.action_index_drift() == 0


def test_pg_a_lost_index_row_counts_once_and_is_repaired_once(
    storage: PostgresStorage,
) -> None:
    """A row the projection lost is one row of drift on this engine too, not
    two (review 1324). The drift helper's fallback counted a missing key in
    both the ``missing`` and the ``changed`` term, so ``verify --index`` and
    the rebuild's "N row(s) corrected" both doubled it."""
    make_run(storage, "pg_drift_d", "drift")
    ledger = ActionLedger(storage, "pg_drift_d")
    outcomes = [ledger.claim("process_doc", {}, key=f"doc:{n}") for n in (1, 2)]
    assert storage.action_index_drift() == 0

    storage._connection.execute("DELETE FROM action_index WHERE key = %s", (str(outcomes[0].key),))
    assert storage.action_index_drift() == 1
    assert storage.rebuild_action_index() == 1
    assert storage.action_index_drift() == 0
    assert storage.foreign_action(str(outcomes[0].key), exclude_run="nobody") is not None
