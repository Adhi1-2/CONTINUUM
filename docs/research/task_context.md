# Durable Lossless Task Context

Free text `Run.goal` plus `completed/total` counters is lossy for non sequential tasks. A resumed session re explores the repo or re asks clarifying questions, reintroducing overhead on the resume side.

## Proposal

Store a structured plan artifact alongside the run, not inside the free text goal.

- **New event type** `PLAN_UPSERT` carrying `plan_id`, `units: list[Unit]` where each unit has `id`, `title`, `status` (`pending`, `working`, `done`, `blocked`), and `depends_on`. This is append only and hash chained like other events, so no new table is needed.

- **Projection** `SemanticState.plan` holds the latest plan. `project` merges `PLAN_UPSERT` events by `plan_id` and unit `id`, so the latest write wins and the history remains. The existing `PlanStep` model in `src/continuum/models.py:1` can be reused.

- **Agent maintenance** The agent updates a single unit status per turn via `continuum_record_plan` (new MCP tool, mutating, allowlisted). Cost is one event per unit, not a full rewrite. The resume payload then includes `state.plan` with exact remaining units, so no re exploration is needed.

- **Recovery** `StateValidator` already walks `depends_on` for localized repair. A stale plan unit is invalidated the same way as evidence, so the resumed session knows which units need rework.

No implementation is done here. This is a design note for issue 85, with no external claims and no em dashes.

Reproduce the gap by creating a run with `goal="Analyze 10k docs"` and observing that `continuum_resume` returns only `goal` and counters, not per unit status.

## Alternatives

- Keep free text goal. Simple but lossy for branched tasks.
- Store plan outside CONTINUUM in agent memory. Loses durability and audit.
