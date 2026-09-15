# Audit: Provenance through compaction (Issue #294)

Date: 2026-08-30
Scope: `src/continuum/checkpoint/*`, `src/continuum/state/*`, `src/continuum/storage/*`, `src/continuum/mcp/server.py`, `src/continuum/recovery/summary.py`, `src/continuum/analysis/trajectory_report.py`

## Context

TMA-NM (arXiv:2606.24322) proves lineage and content-based defenses are unsound under laundering channels: self-summarization, trusted-tool echo, manufactured corroboration. MPBench (arXiv:2606.04329) identifies compaction-driven writes as an unguarded channel. CONTINUUM's hash-chained provenance is the right foundation, but the compaction path must not collapse per-fact Origin into a single summary trust level.

## Drop points found

| Location | How Origin could be dropped or flattened | Risk |
|---|---|---|
| `recovery/summary.py:build_informed_retry` | Reads only `storage.read_events(run_id)` (live log). After `compact_run` the archived prefix (which may contain `EXTERNAL_AGENT` facts) is invisible, so `derived_provenance_for_events(live_only)` returns `DETERMINISTIC` even though full history contains `EXTERNAL_AGENT`. Next derived block is incorrectly stamped `deterministic`, laundering prior untrusted history into a trusted advisory. | High: compaction laundering via derived block. Fixed by reading `archived + live` sorted. |
| `state/semantic.py:_provenance` | Trusts `payload["derived_origin"]` verbatim. A malicious `REASONING_SUMMARY` or compromised writer can claim `derived_origin=deterministic` while `event.source=external_agent`, upgrading weak history to trusted without projector check. No recomputed min over prefix. | High: summary can upgrade. Fixed by clamping claimed origin to `min(claimed, event.source, weakest_seen)`. |
| `state/semantic.py:_Accumulator` | No tracking of weakest origin seen so far. Even if `_provenance` clamped to `event.source`, a `DETERMINISTIC` summary written by deterministic code after an `EXTERNAL_AGENT` history would still claim `deterministic` (writer is trusted, history is not). Need history-aware min. | High: deterministic summarizer laundering. Fixed by tracking global weakest and enforcing `summary trust = min(sources)`. |
| `checkpoint/context.py` | Sections `VALID DECISIONS`, `RELEVANT FINDINGS`, `PENDING TASKS` render `decision_id: decision` and `finding_id: claim` without origin. Two findings with identical claims but different origins render identically; the briefing (the compaction output consumed by the next session) collapses per-fact trust to a single text block. | Medium: briefing laundering. Fixed by appending `[origin]` tag per line and preserving `provenance_map` in context. |
| `mcp/server.py:continuum_record_summary` | Stores `{"summary": {...}}` with `source=EXTERNAL_AGENT` but no `derived_origin` and no per-fact origins inside `plan_stack`/`decisions`. A summary that repeats an untrusted finding as if observed has no machine-checkable per-fact tag; the next session inherits it verbatim via `cli/main.py` briefing path. | Medium: self-summarization laundering. Fixed by stamping `derived_origin` as min over full history and keeping per-fact origin in payload when present. |
| `checkpoint/manager.py:checkpoint` / `storage/sqlite.py:compact_run` | `compact_run` creates a fresh checkpoint (`source=DETERMINISTIC`) whose `STATE_CHECKPOINTED` marker and `EVENT_LOG_ANCHORED` marker are `DETERMINISTIC`. The checkpoint's `SemanticState` correctly keeps per-fact provenance, but any code that treats the checkpoint version as a single trust level (e.g., `resume.json` progress numbers without provenance) flattens origin. | Low: already per-fact in state, but `resume.json` and `build_recovery_context` flatten. Fixed by context preservation above and by documenting that checkpoint trust is per-fact. |
| `storage/verify_events` | Already walks archived prefix and checks hash chain across boundary. No flattening. Provenance in archived rows is re-digested. Kept. | None |

## Invariants enforced after fix

1. **Per-fact origin preserved:** `SemanticState` components (`Goal`, `Progress`, `Decision`, `Finding`, `Evidence`, `PlanStep`, `ConstraintPin`) each carry `Provenance(origin, source_sequence, source_event_id)`. Checkpoint `body` serializes them; hash covers them.
2. **Trust monotonicity in projector:** For any event carrying `derived_origin`, `projected_origin = min(claimed, event.source, weakest_seen_in_prefix)`. Missing or invalid `derived_origin` degrades to `EXTERNAL_AGENT` (unverified). Deterministic rule, checked in pure fold, not by LLM.
3. **Compacted references resolvable:** `Provenance.source_sequence` and `source_event_id` point to archived `sequence` when pre-compaction; `verify_events` deep-audits archive and requires live chain to continue at `archive_edge + 1`. Full history helper `archived + live` keeps derived calculations honest.
4. **Summaries cannot upgrade:** `derived_origin` for `build_informed_retry`, `maybe_generate_trajectory_report`, and `continuum_record_summary` is computed over full sorted history (`archived + live`), so an `EXTERNAL_AGENT` fact archived before compaction still taints post-compaction summaries as `external_agent`.
5. **Hash-chained provenance survives:** Every `Event.content()` includes `source`; `Event.hash` signs it. Compaction moves rows verbatim between `events` and `events_archive` preserving `hash`/`prev_hash`/`source`; `verify_events` and `read_archived_events` keep the chain auditable.

## Verification

Property test injects an `EXTERNAL_AGENT` finding, forces `checkpoint` + `compact`, asserts resumed `SemanticState` still marks `f1` as `EXTERNAL_AGENT`, validator still yields `REQUIRES_REVIEW`, `derived_origin` of post-compaction informed-retry block remains `external_agent`, and `provenance.source_sequence` resolves to archived row.
