# Long-horizon runs: what weeks-long autonomy needs

A dive into what the research says an AI system must have to run tasks for
weeks, mapped against what CONTINUUM already ships and what it still lacks.
Written 2026-08-24. Every claim below links its source; where something is a
plan rather than a built feature, it says so.

## The capability math is not the bottleneck; reliability is

METR measures the task length frontier agents complete with 50% reliability
(the "time horizon") at roughly two to five hours as of early 2026, doubling
every seven months over the long run and every three to four months since
2024 (metr.org/time-horizons; arXiv:2503.14499; TH1.1 update,
metr.org/blog/2026-1-29-time-horizon-1-1). Extrapolation puts month-long
tasks somewhere between 2027 and 2031 if the trend holds.

The reason today's systems cannot simply run longer is arithmetic, not
intelligence. Task success behaves like the product of per-step reliabilities
(the compositionality gap), so a 99.9% reliable step over a hundred thousand
steps fails essentially always. Worse, the per-step rate is not constant:

- Models self-condition on their own errors. When the context contains prior
  mistakes, per-step accuracy drops further, and scaling model size does not
  fix it; thinking models and context hygiene do (arXiv:2509.09677).
- Errors concentrate on a few hard steps that become irreversible without an
  explicit recovery path (the no-recovery bottleneck; LEAD, arXiv:2603.06870).
- Off-plan drift is self-reinforcing: each off-canonical step raises the odds
  the next one drifts too (+22.7 points per step), and simply monitoring
  mid-trajectory adherence and restarting bad runs lifts success rates
  (arXiv:2602.19008).
- As horizons grow, planning failures and catastrophic forgetting dominate
  the failure mix, including repeating failed actions across steps (HORIZON,
  arXiv:2604.11978).

Infrastructure that detects, survives, and learns from errors is therefore
the multiplier on time horizon. That is exactly CONTINUUM's territory.

## What CONTINUUM already covers (with receipts)

| Long-horizon need | Shipped |
|:--|:--|
| Survive crashes without corrupting state | Hash-chained event log, checkpoints, recovery engine |
| Never repeat a paid side effect after replay | Action ledger, exactly-once claims, gate enforcement (#217) |
| Never trust the agent's own report of itself | Provenance-gated state, self-certification fix (`9738b9e`) |
| Resume cognition, not just state | Reasoning rehydration (#235), session briefing |
| Month-scale logs without linear cost | Event-log compaction (#239), action index (#216) |
| Retry storms | Run-level retry budgets (#240) |
| Unknown-outcome effects settle from reality | Reconciler probes (#218), gateway settlement |
| Replay correctness under prompt/model drift | Version pinning (#241), replay-or-fork (#291) |
| Many agents, one truth | Parent/child runs, aggregated contracts (#243) |

## The gaps this exercise surfaced, and what we did about them

### 1. Nothing watches the trajectory between crashes (built)

Durability answers "safe to resume?". It never asks "worth resuming?" A run
can be perfectly durable while circling a wall for days: the same action
failing repeatedly (HORIZON's history error accumulation), progress counters
moving backwards, crash scars accumulating because the harness keeps dying
mid-effect, or long stretches of activity with no verifiable forward motion.

New module `continuum.health` folds raw events into a `HealthReport` with
five detectors: stalled actions (trailing consecutive failures of one action
identity), progress regression, unresolved claims plus accumulated
interrupted-effect scars, error-dense recent history (the self-conditioning
risk signal from arXiv:2509.09677), and quiet windows with no progress-bearing
events. Surfaced read-only via `continuum health <run>`. Advisory only, by
house rule: findings never move mode or safety.

Not claimed: these detectors are statistical signals over the event log, not
proof the agent is stuck. A legitimately exploratory phase can trip the
quiet-window note.

### 2. Resume context is curated by nobody (open)

The briefing serves the newest self-authored summary verbatim. Research says
an error-laden history actively degrades the next session (self-conditioning),
so resume context should be assembled from verified state and distilled
lessons rather than raw transcript tails. Partially addressed upstream:
`build_informed_retry` already keeps failed-attempt lessons out of the way of
verified state. The open piece is a policy for what the next session should
not see again.

### 3. Failed attempts leave no structured memory behind (open)

AgentRewind (arXiv:2608.14380) shows recovery works best when the restored
session inherits a summary of what the abandoned attempt falsified, aligned
with an environment restore. CONTINUUM records attempts (ledger, recovery
ledger, informed-retry block) but nothing distills cross-attempt lessons into
a first-class artifact a fresh session can consume. Fork events (#291,
RUN_FORKED) carry a reason string; they could carry structured lessons too.

### 4. Progress is a flat counter, not a plan (open)

HORIZON finds subplanning failures dominate at long horizons. CONTINUUM's
Goal/Progress model cannot represent "milestone 3 of 5, verified". A milestone
layer with verification gates per milestone would give the validator and the
health monitor structural anchors instead of counts. Design needed before
code; touching Goal semantics ripples through projections, so this is the
largest of the open items.

### 5. Environment rewind is not aligned with state restore (tracked)

Checkpoints restore the projection, not the workspace. AgentRewind's ablation
puts environment rewind as the single most valuable component of recovery.
Issue #292 (atomic dual-state rewind) already tracks this; nothing to add
here beyond endorsing the priority.

### 6. Idle cycles consolidate nothing (open)

Sleep-time compute (arXiv:2504.13171) and offline memory consolidation
(Auto-Dreamer, arXiv:2605.20616; RecMem's recurrence-triggered consolidation)
turn idle time into cheaper, better future sessions. For CONTINUUM the honest
scope is narrower: distill the compacted archive into periodic trajectory
reports (attempts, scar rate, stall sites) that feed the weekly-report idea
already sketched in docs/research/policy_learning.md. CONTINUUM stays out of
the semantic-memory business; that belongs to memory systems, and our
neutrality across harnesses is worth more than owning one more subsystem.

## What would actually make weeks possible

Combining METR's trend with the reliability literature: a week-long run needs
roughly four orders of magnitude more reliable steps than today's models
deliver unaided. The only known path is architectural: decompose (planning),
verify each step against ground truth (validator, reconcilers), make every
side effect exactly-once (ledger, gate), recover alignment on restart
(checkpoints plus rehydration), watch for degradation between crashes
(health), and never pay twice for the same mistake (idempotency, budgets,
pinning). CONTINUUM now covers every layer except the two marked open above
that matter most for month-scale autonomy: structured attempt memory and
milestone-anchored progress.
