# Implementation plan: remaining roadmap items (project.md §7)

Status check (verified against the tree on 2026-08-19):
- A1 done: MCP caller auth (`src/continuum/mcp/authz.py`, commit `6d3cd5f`).
- A2 done: observability + Phase 14 dashboard (PR #60) and CONTINUUM-Bench
  already exist (`src/continuum/benchmark/`, `test_benchmark.py`) and run
  real measurements (5 scenarios x 3 methods).
- B0 done: `continuum serve` sidecar exists (`src/continuum/serve/`, `test_serve.py`).
- B1 done: Registry + four seams (`src/continuum/plugins/registry.py`, `seams.py`).

Remaining open items: **B2, B3, B4, C1, D1**.

## Working conventions for all items
- Build in the isolated git worktree (the other agent is pushing `main`); one
  feature branch per slice, one PR per slice.
- Keep each slice small, focused, and green on `ruff check`, `ruff format
  --check`, `mypy src/continuum`, and `pytest` before opening a PR.
- Do not claim an item done without a direct, in-session verification (test
  output or a real repro). Infra that cannot run here stays behind an optional
  extra with tests that skip cleanly when the service is absent.

---

## B2 - Production durability
Scope: PostgreSQL storage, a centralized server mode, distributed run
locking/lease coordination, and a schema + projector migration framework.

### B2.1 Migration framework (SQLite-testable, do first)
- New `src/continuum/storage/migrations.py`: a versioned forward-migration
  runner. Track applied versions in a `schema_migrations` table; apply pending
  migrations on open, up to `SCHEMA_VERSION`.
- Decision needed: today `open_storage` is fail-closed and raises
  `SchemaVersionError` for older schemas (issue #17 fix). The migration runner
  must keep that safety for unsupported/downgrade cases and only forward-migrate
  known shapes. Confirm we are allowed to soften the old-schema rejection into
  "migrate forward when a path exists, else still refuse."
- Verification: unit tests build synthetic old-schema DBs in `:memory:` and
  assert they open and migrate forward; assert an unknown/downgrade still
  raises. No external service.

### B2.2 Lease / distributed-lock coordinator (SQLite + in-memory testable)
- New `src/continuum/concurrency/lease.py`: `LeaseCoordinator` ABC with
  `acquire/renew/release/holder`, an `InMemoryLeaseCoordinator` (tests) and a
  `SQLiteLeaseCoordinator` (a small dedicated sqlite sidecar DB, or a new table
  on the main store if the `Storage` protocol is extended). Guards "one agent
  resumes one run."
- Verification: acquire/renew/expire/conflict tests; a fuzz test where two
  coordinators contend for the same run.

### B2.3 PostgreSQL storage backend (needs a Postgres service)
- New `src/continuum/storage/postgres.py` implementing the `Storage` interface
  (see `src/continuum/storage/base.py`). Add optional extra `[postgres]` with
  `psycopg[binary]` (sync, matches the existing sync interface; `asyncpg` would
  mean rewriting the whole `Storage` surface, so prefer `psycopg` unless you
  want an async core). Route `postgres://`/`postgresql://` URLs in
  `open_storage` to it (replace the current `NotImplementedError`).
- Decision needed: driver (`psycopg` vs `asyncpg`), and whether to add a
  Postgres service container to CI so these tests actually run (otherwise they
  skip unless `CONTINUUM_TEST_POSTGRES_DSN` is set).
- Verification: the existing `Storage` contract tests (refactor
  `tests/test_storage.py` to run against both SQLite and Postgres) pass against
  a real Postgres in CI; skip locally when no DSN.

### B2.4 Centralized server mode
- Extend `continuum serve` with a `-central`/shared-storage mode backed by
  Postgres so multiple agents attach to one durable store. Reuses B0's wire
  protocol and B2.3 storage.
- Verification: integration test where two sidecar clients share one Postgres
  store and see each other's runs/leases.

Suggested PR order: B2.1 -> B2.2 -> B2.3 -> B2.4.

---

## B3 - Trust and ops (workload identity)
Scope: bind attestation to a workload identity (SPIFFE/SPIRE) instead of an
ad-hoc key file, emit an attestation propagation token, and enforce at `resume`
(refuse to continue if the attestation/chain no longer matches).

- New optional extra `[spiffe]`. Use the `spiffe` Python workload API client to
  fetch an SVID; `continuum attest` signs with the SVID; `resume` verifies the
  chain against the current identity.
- Decision needed: real verification requires a SPIRE agent. For CI/tests,
  define a `WorkloadIdentity` protocol with a fake implementation so the logic
  is tested without SPIRE; keep the real SPIFFE adapter behind the extra and
  skip when no workload API is reachable.
- Verification: signing/verification round-trip with the fake identity;
  `resume` refuses when the identity no longer matches the sealed chain.

---

## B4 - Portability (interchange schema)
Scope: a stable, versioned JSON interchange schema for `SemanticState`,
`RecoveryDecision`, and `RecoveryContract` so external tools can verify
CONTINUUM output and different versions interoperate.

- New `src/continuum/interchange/`: explicit JSON schema (versioned),
  `export_*`, `import_*` with validation, and a canonical example artifact an
  external verifier could check.
- No new runtime dependencies (stdlib only). Self-contained and testable.
- Verification: round-trip tests (export then import equals the original),
  schema-validation tests against the published schema, and a checked-in
  example file.

---

## C1 - Triage open correctness bugs (do now, no deps)
Scope: review #29, #30, #33, #34, #36, #42, #43, #45 (and note #49 is already
fixed by PR #54). For each: read the issue, inspect the code, reproduce or
confirm stale, then close as stale/duplicate or keep open with a confirmed-repro
comment. Do NOT implement fixes in this pass (triage only).

- Verification: each issue gets a triage comment with evidence and is either
  closed or labelled; nothing is merged to code.

---

## D1 - Unbuilt roadmap phases
Scope: Phase 13 (Cloud API), Phase 14 (Dashboard), Phase 23 (multi-agent
coordination). Phase 14's recovery report already exists (A2); the rest are
large and depend on B0/B1/B2.

- Recommendation: treat each phase as its own sub-project with a short plan
  before coding. Sequence after B0/B1/B2 land: Phase 14 polish -> Phase 13
  (Cloud API over the B0 sidecar) -> Phase 23 (multi-agent, builds on B2
  locking).

---

## Open decisions the maintainer must make before B2/B3 coding
1. Postgres driver: `psycopg` (sync, matches current core) vs `asyncpg`
   (cleaner for a server but requires an async `Storage` rewrite).
2. CI: add a Postgres service container (and optionally a SPIRE agent) so B2.3
   and B3 are actually verified, or keep them skip-by-default locally.
3. B2.1 may relax the issue #17 fail-closed old-schema behavior into
   forward-migration; confirm that is acceptable.
4. Work partitioning with the other agent (they built B0/B1): should I also
   take B2-B4, or focus on C1 + D1 to avoid collision?
