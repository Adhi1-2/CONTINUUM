"""Shared maintenance logic for the action index (issue #216).

The action index is a derived projection: one row per ledger key, rebuilt
from ``ACTION_*`` events, maintained incrementally inside the same
transaction that appends the event. The event log remains the source of
truth; the index exists so cross-run idempotency lookups are an indexed
read instead of folding every run's full log.

Both storage engines share :data:`ACTION_EVENT_TYPES` and
:func:`index_entry_from_payload` so incremental writes, backfills and
rebuilds cannot drift apart in what they accept.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from continuum.events import EventType

__all__ = [
    "ACTION_EVENT_TYPES",
    "INDEX_DDL_SQLITE",
    "INDEX_DDL_POSTGRES",
    "index_drift_count",
    "index_entry_from_payload",
]

#: The only event types that carry a ledger entry.
ACTION_EVENT_TYPES = (
    EventType.ACTION_RECORDED,
    EventType.ACTION_RECONCILED,
    EventType.ACTION_COMPENSATED,
)

INDEX_DDL_POSTGRES = """
CREATE TABLE IF NOT EXISTS action_index (
    key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_seq INTEGER NOT NULL,
    action_json TEXT NOT NULL
)
"""

INDEX_DDL_SQLITE = INDEX_DDL_POSTGRES.replace("TEXT PRIMARY KEY", "TEXT PRIMARY KEY") + (
    "\nCREATE INDEX IF NOT EXISTS action_index_run ON action_index(run_id)\n"
)


def index_entry_from_payload(
    event_type: EventType, payload: Mapping[str, Any]
) -> tuple[str, str, str, str, str] | None:
    """Extract ``(key, run_id_in_action, action_id, status, action_json)``.

    Returns None for anything the index does not track. The embedded Action
    record is the authority for identity; a malformed payload yields None
    rather than a half-row, because the index must never disagree with the
    log silently.
    """
    if event_type not in ACTION_EVENT_TYPES:
        return None
    key = payload.get("key")
    action_payload = payload.get("action")
    if not isinstance(key, str) or not key or not isinstance(action_payload, Mapping):
        return None
    try:
        return (
            key,
            str(action_payload.get("run_id", "")),
            str(action_payload.get("action_id", "")),
            str(action_payload.get("status", "")),
            json.dumps(dict(action_payload), sort_keys=True),
        )
    except (TypeError, ValueError):
        return None


def index_drift_count(
    expected: Mapping[str, tuple[int, str, str]],
    stored: Mapping[str, tuple[int, str, str]],
) -> int:
    """Count index rows that disagree with the log fold (issue #1321).

    ``expected`` is the fold of the log and ``stored`` is the projection as it
    stands; both map a ledger key to ``(updated_seq, status, action_json)``. A
    row disagrees when the log no longer produces it, when the log produces a
    key the projection lacks, or when the row's own contents are not what the
    fold produced.

    A key the projection lost is counted once, by the ``missing`` term. The
    ``changed`` term ranges only over keys present in both mappings: the
    ``stored.get(key, ...)`` default it used to fall back to never equals a
    real status, so a lost key was counted a second time there as well, and
    ``verify --index`` reported two rows of drift for one missing row.

    ``updated_seq`` is deliberately *not* compared. Two healthy stores make it
    disagree with the fold no matter how the fold numbers its rows:

    - The incremental writer consumes one sequence value per action event;
      the fold counts every row it walks, so a non-action event between two
      actions shifts the fold off the writer's scale by one.
    - The fold ranks every archived row below every live one, which holds
      within a run but not across runs: once a run's actions are archived, the
      fold puts them before another run's live actions that were written
      earlier, while the sequence values they were assigned say the opposite.
    - The value an archived row carries was assigned while it was still live
      and nothing in the log carries it across compaction, so the fold cannot
      reproduce it at all without a schema change.

    Comparing it anyway made ``action_index_drift`` report a dirty index on
    every Postgres store whose first action was not also its first event, and
    on every store with an archived action -- which is what made ``continuum
    verify`` unusable as a signal on that backend.

    The omission costs nothing, because the column cannot affect an answer
    today: ``key`` is the projection's primary key, so at most one row per key
    exists and the ordering that consumes the column is scoped to a single
    row and ranks nothing. Should the schema later allow more than one row per
    key, that ranking needs a numbering both sides can reconstruct, and this
    comparison is where to put it.

    ``action_json`` *is* compared, and carries the comparison the column
    above cannot: it is the record ``foreign_action`` hands back to the
    idempotency guard, so a row whose embedded Action was rewritten is a
    silent wrong answer, not a cosmetic mismatch. It embeds ``run_id`` and
    ``action_id``, so comparing it covers a tamper of either column too. Both
    sides are built by :func:`index_entry_from_payload`, so a healthy store
    holds the same string on both; the contested-key case -- two runs writing
    one key -- already disagrees on ``status``, because the fold and the
    writer upsert agree on the last writer, so widening the comparison adds
    detection without adding false positives.
    """
    missing = sum(1 for key in expected if key not in stored)
    extra = sum(1 for key in stored if key not in expected)
    changed = sum(
        1
        for key, (_, status, action_json) in expected.items()
        # Only keys the projection still holds: a lost key is already counted
        # by ``missing``, and the fallback it used to take here never matched
        # a real row, so it counted that key a second time.
        if key in stored and stored[key][1:] != (status, action_json)
    )
    return missing + extra + changed
