"""Consumed-grant tracking (issue #269): deny Authority Resurrection.

ACRFence (arXiv:2603.20625) validates the attack: a restore resurrects
single-use tokens from checkpointed state, and stateless validation lets
every reuse through. These tests pin the defence at the ledger chokepoint:
a spent grant refuses any later claim carrying it, regardless of argument
drift or a fresh key; mid-flight retries are untouched; malformed grants
are rejected outright; refusals are audited and keep the chain verifiable;
and absence of grants changes nothing.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from continuum.actions import ActionLedger
from continuum.actions.grants import GrantDenied, normalize_grant, scan_grants
from continuum.cli import ExitCode, main
from continuum.events import EventType
from continuum.models import Run
from continuum.storage import SQLiteStorage

GRANT = {"id": "tok_9", "scope": "refund:acme"}


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = str(tmp_path / "g.db")
    with SQLiteStorage(path) as store:
        store.create_run(Run(run_id="run_1", goal="Refunds"))
        store.append_event("run_1", EventType.RUN_STARTED, {"goal": "Refunds", "total": 2})
    yield path


def events(db: str) -> list:
    with SQLiteStorage(db) as store:
        return store.read_events("run_1")


# --- the ACRFence scenario ---------------------------------------------------- #


def test_reuse_after_restore_is_denied_regardless_of_key_drift(db: str) -> None:
    ledger = ActionLedger(SQLiteStorage(db), "run_1")
    first = ledger.claim(
        "issue_refund",
        {"order": "o-1", "amount": 42},
        key="refund:o-1",
        grant=GRANT,
    )
    assert first.fresh
    ledger.complete(first.key, external_id="rf-1", result={"ok": True})

    # The harness rewinds the agent to before the refund. The restored agent
    # remembers an unspent token and re-plans with drifted arguments and a
    # fresh key, exactly as a non-deterministic LLM would.
    new_ledger = ActionLedger(SQLiteStorage(db), "run_1")
    with pytest.raises(GrantDenied) as excinfo:
        new_ledger.claim(
            "issue_refund",
            {"orderId": "O-1", "cents": 4200},
            key="refund:attempt-2-uuid",
            grant={"id": "tok_9", "scope": "refund:acme"},
        )
    assert excinfo.value.grant_id == "tok_9"
    assert excinfo.value.prior.status == "completed"

    denial = [e for e in events(db) if e.type is EventType.GRANT_DENIED]
    assert len(denial) == 1
    assert denial[0].payload["prior_action_id"] == first.action.action_id
    # The drifted re-plan produced its own identity, distinct from the spent one.
    assert denial[0].payload["attempted_key"]
    assert denial[0].payload["attempted_key"] != first.key


def test_a_fresh_unrelated_action_is_unaffected(db: str) -> None:
    ledger = ActionLedger(SQLiteStorage(db), "run_1")
    first = ledger.claim("issue_refund", {"order": "o-1"}, key="refund:o-1", grant=GRANT)
    ledger.complete(first.key, external_id="rf-1", result={})

    second = ActionLedger(SQLiteStorage(db), "run_1").claim(
        "notify_customer", {"channel": "email"}, grant={"id": "tok_other", "scope": "notify"}
    )
    assert second.fresh


def test_midflight_retry_under_the_same_key_is_not_a_resurrection(db: str) -> None:
    ledger = ActionLedger(SQLiteStorage(db), "run_1")
    ledger.claim("issue_refund", {"order": "o-1"}, key="refund:o-1", grant=GRANT)

    from continuum.models import UnknownSideEffect

    # Same live attempt, same grant: the standard uncertainty protocol owns
    # this path (UnknownSideEffect without a resolver), never GrantDenied.
    with pytest.raises(UnknownSideEffect):
        ActionLedger(SQLiteStorage(db), "run_1").claim(
            "issue_refund",
            {"order": "o-1"},
            key="refund:o-1",
            grant=dict(GRANT),
        )


def test_failed_certain_still_spends_fail_closed(db: str) -> None:
    ledger = ActionLedger(SQLiteStorage(db), "run_1")
    outcome = ledger.claim("charge_card", {"amount": 10}, grant=GRANT)
    ledger.fail(outcome.key, "declined", certain=True)

    # Conservative by design: the authorisation was exercised downstream even
    # though the business effect did not land. Only the issuer can un-spend.
    with pytest.raises(GrantDenied):
        ActionLedger(SQLiteStorage(db), "run_1").claim(
            "charge_card", {"amount": 11}, key="charge:2", grant=GRANT
        )


def test_compensated_counts_as_spent_fail_closed(db: str) -> None:
    ledger = ActionLedger(SQLiteStorage(db), "run_1")
    outcome = ledger.claim("provision_vm", {"size": "s"}, grant=GRANT)
    ledger.complete(outcome.key, external_id="vm-1", result={})
    ledger.compensate(outcome.key, note="torn down")

    with pytest.raises(GrantDenied):
        ActionLedger(SQLiteStorage(db), "run_1").claim(
            "provision_vm", {"size": "m"}, key="vm:2", grant=GRANT
        )


# --- input validation ---------------------------------------------------------- #


def test_malformed_grants_are_rejected_before_anything_fires() -> None:
    for bad in (
        {"id": "x"},  # missing scope
        {"scope": "s"},  # missing id
        {"id": "", "scope": "s"},  # empty id
        {"id": "x", "scope": "s", "extra": 1},  # unknown key
        "tok_9",  # not a mapping
    ):
        with pytest.raises(ValueError):
            normalize_grant(bad)  # type: ignore[arg-type]
    assert normalize_grant(None) is None
    assert normalize_grant({"id": " tok ", "scope": " s "}) == {"id": "tok", "scope": "s"}


def test_oversized_grant_values_are_refused() -> None:
    with pytest.raises(ValueError, match="256"):
        normalize_grant({"id": "x" * 257, "scope": "s"})


# --- zero-change guarantee ------------------------------------------------------ #


def test_absence_of_grants_changes_nothing(db: str) -> None:
    ledger = ActionLedger(SQLiteStorage(db), "run_1")
    outcome = ledger.claim("send_email", {"to": "a@b.c"})
    assert outcome.fresh
    payload = [e for e in events(db) if e.type is EventType.ACTION_RECORDED][-1].payload
    assert "grant" not in payload
    spent, by_key = scan_grants(events(db))
    assert spent == {} and by_key == {}


# --- audit integrity ------------------------------------------------------------- #


def test_denial_keeps_the_chain_verifiable(db: str) -> None:
    ledger = ActionLedger(SQLiteStorage(db), "run_1")
    outcome = ledger.claim("issue_refund", {}, grant=GRANT)
    ledger.complete(outcome.key, external_id="r", result={})
    with pytest.raises(GrantDenied):
        ActionLedger(SQLiteStorage(db), "run_1").claim("issue_refund", {}, key="other", grant=GRANT)

    code, _, _ = run("--db", db, "verify", "run_1")
    assert code == ExitCode.OK


# --- transport surfaces ------------------------------------------------------------ #


def test_serve_surface_maps_denial_to_proceed_false(db: str) -> None:
    from continuum.serve.server import SidecarServer

    srv = SidecarServer(database=db)
    srv.dispatch("record_progress", {"run_id": "run_1", "completed": 0, "total": 5})
    first = srv.dispatch(
        "intercept_action",
        {"run_id": "run_1", "action_type": "issue_refund", "key": "refund:1", "grant": GRANT},
    )
    assert first["proceed"] is True
    srv.dispatch(
        "complete_action",
        {"run_id": "run_1", "action_key": first["action_key"], "external_id": "r"},
    )
    second = srv.dispatch(
        "intercept_action",
        {
            "run_id": "run_1",
            "action_type": "issue_refund",
            "key": "refund:2",
            "grant": GRANT,
        },
    )
    assert second["proceed"] is False
    assert second["reason_code"] == "grant_denied"
    assert second["grant_id"] == "tok_9"


# --- MCP surface ---------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mcp_surface_maps_denial_to_proceed_false(tmp_path: Path) -> None:
    from continuum.actions.ledger import ActionLedger as Ledger
    from continuum.mcp.authz import AuthorizationPolicy
    from continuum.mcp.server import build_server
    from tests.mcp_helpers import fake_context as _ctx

    path = str(tmp_path / "mcp.db")
    with SQLiteStorage(path) as store:
        store.create_run(Run(run_id="run_1", goal="Refunds"))
        store.append_event("run_1", EventType.RUN_STARTED, {"goal": "Refunds", "total": 2})
        ledger = Ledger(store, "run_1")
        outcome = ledger.claim("issue_refund", {}, key="refund:1", grant=GRANT)
        ledger.complete(outcome.key, external_id="r", result={})

    server, ctx = build_server(
        storage=SQLiteStorage(path), policy=AuthorizationPolicy(["pytest-client"])
    )
    try:
        payload = await server.call_tool(
            "continuum_intercept_action",
            {
                "run_id": "run_1",
                "action_type": "issue_refund",
                "key": "refund:2",
                "grant": GRANT,
            },
            context=_ctx("pytest-client"),
        )
        body = __import__("json").loads(payload.content[0].text)
        assert body["proceed"] is False
        assert body["reason_code"] == "grant_denied"
        assert body["grant_id"] == "tok_9"
    finally:
        ctx.close()
