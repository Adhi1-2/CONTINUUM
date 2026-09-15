"""Tests for the lease coordinator (B2.2).

Covers the single-process and the SQLite implementations against the same
contract: a lease is exclusive while held, renewable only by its holder,
released on demand, and reclaimable once it expires. Two SQLite coordinators
sharing one file prove the cross-process contract, and a fuzz loop hammers
contention for many runs.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from continuum.concurrency.lease import (
    InMemoryLeaseCoordinator,
    LeaseCoordinator,
    SQLiteLeaseCoordinator,
)


def _controllable_clock(start: datetime) -> tuple[callable, callable]:
    """A clock that only moves when ``advance`` is called."""
    state = {"now": start}

    def now() -> datetime:
        return state["now"]

    def advance(delta: timedelta) -> None:
        state["now"] = state["now"] + delta

    return now, advance


# --------------------------------------------------------------------------- #
# Shared behavioral contract, exercised by both implementations
# --------------------------------------------------------------------------- #


def _run_contract(coordinator: LeaseCoordinator) -> None:
    # Free run is acquirable.
    assert coordinator.acquire("run_1", "agent_a") is True
    assert coordinator.holder("run_1") == "agent_a"
    assert coordinator.is_held("run_1", "agent_a") is True
    assert coordinator.is_available("run_1") is False

    # A second holder cannot take it while held.
    assert coordinator.acquire("run_1", "agent_b") is False
    assert coordinator.holder("run_1") == "agent_a"

    # Renewal by a non-holder fails; by the holder succeeds.
    assert coordinator.renew("run_1", "agent_b") is False
    assert coordinator.renew("run_1", "agent_a") is True

    # Release only works for the actual holder.
    coordinator.release("run_1", "agent_b")
    assert coordinator.holder("run_1") == "agent_a"
    coordinator.release("run_1", "agent_a")
    assert coordinator.holder("run_1") is None
    assert coordinator.is_available("run_1") is True


def test_inmemory_contract():
    _run_contract(InMemoryLeaseCoordinator())


def test_sqlite_contract(tmp_path: Path):
    _run_contract(SQLiteLeaseCoordinator(url=str(tmp_path / "contract.db")))


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #


def test_inmemory_expired_lease_is_reclaimable():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    now, advance = _controllable_clock(start)
    coord = InMemoryLeaseCoordinator(clock=now, default_ttl=timedelta(seconds=60))

    assert coord.acquire("run_1", "agent_a") is True
    # Jump past the 60s default TTL; the lease is now expired.
    advance(timedelta(seconds=120))
    assert coord.acquire("run_1", "agent_b") is True
    assert coord.holder("run_1") == "agent_b"


def test_inmemory_short_ttl_expires():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    now, advance = _controllable_clock(start)
    coord = InMemoryLeaseCoordinator(clock=now, default_ttl=timedelta(seconds=15))

    coord.acquire("run_1", "agent_a", ttl=timedelta(seconds=15))
    # Still within TTL at +10s.
    advance(timedelta(seconds=10))
    assert coord.is_held("run_1", "agent_a") is True
    # Past TTL at +20s.
    advance(timedelta(seconds=10))
    assert coord.holder("run_1") is None


def test_sqlite_expired_lease_is_reclaimable(tmp_path: Path):
    db = tmp_path / "leases.db"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    now, advance = _controllable_clock(start)

    a = SQLiteLeaseCoordinator(url=str(db), clock=now, default_ttl=timedelta(seconds=60))
    b = SQLiteLeaseCoordinator(url=str(db), clock=now, default_ttl=timedelta(seconds=60))

    assert a.acquire("run_1", "agent_a") is True
    advance(timedelta(seconds=120))  # past TTL on both coordinators
    assert b.acquire("run_1", "agent_b") is True
    assert b.holder("run_1") == "agent_b"
    a.close()
    b.close()


# --------------------------------------------------------------------------- #
# Cross-process contention + fuzz
# --------------------------------------------------------------------------- #


def test_two_sqlite_coordinators_contend(tmp_path: Path):
    db = tmp_path / "leases.db"
    a = SQLiteLeaseCoordinator(url=str(db))
    b = SQLiteLeaseCoordinator(url=str(db))

    assert a.acquire("run_1", "agent_a") is True
    # The other coordinator sees the lease as taken.
    assert b.acquire("run_1", "agent_b") is False
    assert b.holder("run_1") == "agent_a"

    a.release("run_1", "agent_a")
    # Now the competitor can take it.
    assert b.acquire("run_1", "agent_b") is True
    assert a.holder("run_1") == "agent_b"
    a.close()
    b.close()


def test_fuzz_contention_across_coordinators(tmp_path: Path):
    """Two coordinators racing for many runs leave each run with one holder."""
    db = tmp_path / "leases.db"
    a = SQLiteLeaseCoordinator(url=str(db))
    b = SQLiteLeaseCoordinator(url=str(db))

    random.seed(1234)
    runs = [f"run_{i}" for i in range(40)]
    for _ in range(200):
        coord = a if random.random() < 0.5 else b
        holder = f"agent_{random.randint(0, 1)}"
        run = random.choice(runs)
        coord.acquire(run, holder)

    # Invariant: no run is held by more than one agent.
    for run in runs:
        holders = {a.holder(run), b.holder(run)}
        holders.discard(None)
        assert len(holders) <= 1, f"{run} claimed by {holders}"
    a.close()
    b.close()


def test_sqlite_takes_over_expired_held_by_other(tmp_path: Path):
    db = tmp_path / "leases.db"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    now, advance = _controllable_clock(start)

    a = SQLiteLeaseCoordinator(url=str(db), clock=now, default_ttl=timedelta(seconds=60))
    b = SQLiteLeaseCoordinator(url=str(db), clock=now, default_ttl=timedelta(seconds=60))

    assert a.acquire("run_1", "agent_a") is True
    # agent_a's lease is now expired; agent_b should be able to take over.
    advance(timedelta(seconds=120))
    assert b.acquire("run_1", "agent_b") is True
    assert b.holder("run_1") == "agent_b"
    a.close()
    b.close()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
