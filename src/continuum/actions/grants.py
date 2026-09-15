"""Single-use grant consumption tracking (issue #269).

ACRFence (arXiv:2603.20625) validates a second attack class beyond duplicate
execution: **Authority Resurrection**. Restoring checkpointed state resurrects
single-use tokens the world has already consumed; under stateless validation
every reuse attempt succeeded, under stateful server-side validation every
attempt was rejected. CONTINUUM checkpoints harness state verbatim, so a token
living there comes back believing itself unspent.

This module gives the ledger portable consumption semantics for callers whose
downstream cannot be changed:

- :func:`normalize_grant` validates caller-supplied grant references (closed
  key set, size caps, same discipline as #241's pinning dict).
- :func:`scan_grants` folds ACTION_RECORDED/ACTION_RECONCILED payloads into a
  per-key view of which grant each attempt carried and whether that attempt
  reached a terminal status. A grant on a terminal attempt counts as spent:
  fail-closed, including COMPENSATED, because the downstream authorisation
  was exercised even when the business effect was later undone.

Enforcement lives in ``ActionLedger.claim``: a claim carrying a spent grant
is refused with :class:`continuum.actions.grants.GrantDenied` and an audited
``GRANT_DENIED`` event before anything fires. A live mid-flight retry under
the same key and grant is untouched, so the two-phase protocol keeps working.

Out of scope, stated plainly: issuing credentials, integrating identity
providers, replacing server-side validation. Stateful validation downstream
remains the real wall; this makes consumption portable across restores.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from continuum.events import Event, EventType

__all__ = ["GrantDenied", "normalize_grant", "SpentGrant", "scan_grants"]

_GRANT_KEYS = {"id", "scope"}
_GRANT_VALUE_CAP = 256

_TERMINAL_STATUSES = {"completed", "failed", "compensated"}


class LedgerDenied(RuntimeError):
    """Base for claims refused on authority rather than uncertainty."""


class GrantDenied(LedgerDenied):
    """Raised when a claim attempts to reuse a consumed single-use grant."""

    def __init__(self, grant_id: str, prior: SpentGrant, attempted_key: str) -> None:
        super().__init__(
            f"grant {grant_id!r} was already consumed by action "
            f"{prior.action_id[:14]} ({prior.status}); single-use authority does "
            "not survive a restore. Obtain a fresh grant from the issuer."
        )
        self.grant_id = grant_id
        self.prior = prior
        self.attempted_key = attempted_key


def normalize_grant(grant: Mapping[str, Any] | None) -> dict[str, str] | None:
    """Validate a caller-supplied grant reference. None passes through."""
    if grant is None:
        return None
    if not isinstance(grant, Mapping):
        raise ValueError("grant must be a mapping with 'id' and 'scope'")
    unknown = sorted(set(grant) - _GRANT_KEYS)
    if unknown:
        raise ValueError(f"grant has unknown key(s) {unknown}; expected {sorted(_GRANT_KEYS)}")
    missing = [
        k for k in ("id", "scope") if not isinstance(grant.get(k), str) or not grant[k].strip()
    ]
    if missing:
        raise ValueError(f"grant needs non-empty string value(s) for {missing}")
    cleaned = {k: grant[k].strip() for k in _GRANT_KEYS}
    oversized = [k for k, v in cleaned.items() if len(v) > _GRANT_VALUE_CAP]
    if oversized:
        raise ValueError(
            f"grant {oversized} exceeds {_GRANT_VALUE_CAP} characters; store a reference, not the artefact"
        )
    return cleaned


@dataclass(frozen=True)
class SpentGrant:
    """A grant attached to an attempt that reached a terminal status."""

    grant_id: str
    scope: str
    key: str
    action_id: str
    action_type: str
    status: str


def scan_grants(events: Iterable[Event]) -> tuple[dict[str, SpentGrant], dict[str, dict[str, str]]]:
    """Fold grant attachments out of the log.

    Returns ``(spent, by_key)`` where ``spent`` maps grant id to its terminal
    SpentGrant and ``by_key`` maps ledger key to the grant dict it carried, so
    a live mid-flight retry can be recognised and allowed.
    """
    spent: dict[str, SpentGrant] = {}
    by_key: dict[str, dict[str, str]] = {}
    open_grants: dict[str, dict[str, str]] = {}

    for event in events:
        if event.type not in (
            EventType.ACTION_RECORDED,
            EventType.ACTION_RECONCILED,
            EventType.ACTION_COMPENSATED,
        ):
            continue
        key = event.payload.get("key")
        if not isinstance(key, str):
            continue
        grant = event.payload.get("grant")
        if isinstance(grant, Mapping) and isinstance(grant.get("id"), str):
            open_grants[key] = {"id": grant["id"], "scope": str(grant.get("scope", ""))}
            by_key.setdefault(key, open_grants[key])
        status = event.payload.get("status")
        if not isinstance(status, str) or status not in _TERMINAL_STATUSES:
            continue
        attached = open_grants.get(key)
        if attached is None:
            continue
        current = spent.get(attached["id"])
        if current is not None:
            continue  # first terminal verdict wins; the log is append-only
        spent[attached["id"]] = SpentGrant(
            grant_id=attached["id"],
            scope=attached.get("scope", ""),
            key=key,
            action_id=str(event.payload.get("action_id", "")),
            action_type=str(event.payload.get("action_type", "")),
            status=status,
        )
    return spent, by_key
