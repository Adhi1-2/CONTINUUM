# BUILD: CONTINUUM

## Verifiable Semantic Recovery Layer for Long-Running AI Agents

You are a senior distributed-systems engineer, AI-agent infrastructure engineer, Python library maintainer, and research engineer.

Build a serious open-source project called:

# CONTINUUM

### Tagline

**Agents that can lose their context without losing their work.**

The project is a lightweight, framework-agnostic infrastructure layer for long-running AI agents.

---

# 1. THE PROBLEM

Modern AI agents perform long-running tasks involving:

* many LLM calls
* tool calls
* external APIs
* files
* databases
* browser sessions
* plans
* intermediate decisions
* evidence
* approvals
* human intervention
* changing environments

A long-running agent can fail because:

* the process crashes
* the machine restarts
* the deployment is replaced
* the model context is compacted
* the context window becomes too large
* a tool fails
* an API session expires
* the environment changes
* the agent changes models
* an external resource changes
* a previously valid decision becomes stale

A naïve agent often responds by replaying the entire task.

That causes:

* duplicated work
* duplicated API calls
* duplicated side effects
* higher cost
* lost decisions
* inconsistent state
* stale assumptions
* unreliable recovery

Existing durable execution and checkpointing systems address parts of this problem.

CONTINUUM should investigate a narrower problem:

> **Can an agent resume safely from a compact semantic representation of its task state, while independently verifying whether that state is still valid in the current environment?**

Do NOT build a generic agent framework.

Do NOT build another memory/RAG system.

Do NOT build another workflow orchestration framework.

Do NOT train a model.

The project must be CPU-first and lightweight.

---

# 2. CORE IDEA

CONTINUUM separates:

```text
LLM CONTEXT
from
DURABLE TASK STATE
```

The LLM context is temporary.

The semantic task state is durable.

Architecture:

```text
                   AI AGENT
                      │
                      ▼
              ┌───────────────┐
              │ CONTINUUM SDK │
              └───────┬───────┘
                      │
          ┌───────────┼────────────┐
          ▼           ▼            ▼
       State        Action       Evidence
       Engine       Ledger       Registry
          │           │            │
          └───────────┼────────────┘
                      ▼
              Semantic Checkpoint
                      │
                      ▼
               Durable Storage
                      │
            ┌─────────┴─────────┐
            ▼                   ▼
       Environment          Recovery
       Validation              │
            │                  ▼
            └────────────> Resume
```

---

# 3. THE KEY DIFFERENTIATOR

Do NOT simply save:

```text
conversation history
```

Do NOT simply save:

```text
full agent state
```

Instead construct a compact:

# SEMANTIC CHECKPOINT

A checkpoint should represent the minimum verified information required to continue the task.

Example:

```json
{
  "run_id": "run_4821",
  "goal": {
    "description": "Analyze 10,000 documents for evidence supporting hypothesis X",
    "version": 3
  },

  "progress": {
    "completed": 3421,
    "pending": 6579,
    "failed": 3
  },

  "decisions": [
    {
      "decision": "Only include peer-reviewed studies",
      "reason": "User requirement",
      "evidence": ["user_instruction_001"],
      "status": "valid"
    }
  ],

  "findings": [
    {
      "id": "finding_17",
      "claim": "...",
      "evidence": ["paper_128"],
      "confidence": 0.91
    }
  ],

  "pending_work": [
    "Search 2019-2022 literature",
    "Resolve contradictory evidence"
  ],

  "external_dependencies": [
    {
      "resource": "dataset",
      "version": "v3"
    }
  ],

  "last_verified_environment": {
    "timestamp": "...",
    "hash": "..."
  }
}
```

The checkpoint must be:

* compact
* inspectable
* versioned
* deterministic where possible
* independently verifiable
* diffable
* recoverable
* serializable

---

# 4. MOST IMPORTANT CONCEPT:

# STATE VALIDATION

The system MUST NOT blindly trust an old checkpoint.

Before recovery:

```text
CHECKPOINT
     │
     ▼
STATE VALIDATOR
     │
     ├── environment unchanged?
     ├── dependencies unchanged?
     ├── permissions unchanged?
     ├── external actions still valid?
     ├── evidence still available?
     ├── goals still valid?
     └── previous decisions still valid?
             │
             ▼
       VALID / STALE / CONFLICT
```

Example:

Checkpoint says:

```text
Dataset = v3
```

Current environment:

```text
Dataset = v4
```

CONTINUUM should produce:

```text
STATE VALIDATION FAILED

Dependency:
dataset

Checkpoint:
v3

Current:
v4

Affected state:
17 experiments

Recovery:
REQUIRES_REVALIDATION
```

The agent must NOT silently continue.

---

# 5. STATE STATUS

Every semantic state component should have a status:

```text
VALID
STALE
CONFLICTED
UNKNOWN
INVALID
REQUIRES_REVIEW
```

Example:

```text
Goal             VALID
Progress         VALID
Decision #12     STALE
Evidence #81     VALID
Dataset          CONFLICTED
Approval         EXPIRED
```

This should be exposed through the API.

---

# 6. ACTION LEDGER

Create an idempotent action ledger.

The system must remember external side effects.

Example:

```json
{
  "action_id": "action_812",
  "type": "github.create_issue",
  "arguments_hash": "...",
  "status": "COMPLETED",
  "external_id": "481",
  "timestamp": "...",
  "result_hash": "..."
}
```

If the agent crashes after creating issue #481:

On recovery:

```text
Agent:
Create GitHub issue.

CONTINUUM:
Action already completed.

External ID:
#481

Returning previous result.
```

This prevents duplicate side effects.

Support action states:

```text
PLANNED
STARTED
COMPLETED
FAILED
UNKNOWN
COMPENSATED
REQUIRES_REVIEW
```

Important:

If the system cannot determine whether an external side effect occurred, it must NOT blindly retry.

It should return:

```text
UNKNOWN_SIDE_EFFECT
```

and require a reconciliation strategy.

---

# 7. CHECKPOINT STRATEGY

Do NOT checkpoint every turn by default.

Implement configurable checkpoint policies.

Examples:

```yaml
checkpoint:
  mode: semantic

  triggers:
    - important_state_change
    - external_side_effect
    - milestone
    - context_pressure
    - explicit_request

  max_interval_seconds: 300
```

The system should support:

```text
manual
interval
event-driven
semantic
hybrid
```

---

# 8. SEMANTIC STATE EXTRACTION

Implement a pluggable state extractor.

Architecture:

```python
class StateExtractor:
    def extract(self, trajectory, environment): ...
```

Provide:

### Deterministic extractor

Uses:

* explicit task metadata
* tool calls
* action results
* state transitions
* structured outputs

### Optional LLM extractor

Uses an external LLM only when enabled.

The system MUST work without an LLM.

Do not require OpenAI, Anthropic, Gemini, or any proprietary provider.

---

# 9. CONTEXT RECONSTRUCTION

When the agent resumes, CONTINUUM constructs a bounded recovery context:

```text
CURRENT GOAL

VERIFIED PROGRESS

VALID DECISIONS

UNRESOLVED QUESTIONS

RECENT ACTIONS

RELEVANT EVIDENCE

PENDING TASKS

STALE STATE

ENVIRONMENT CHANGES

NEXT SAFE ACTION
```

Do NOT simply dump the complete previous transcript.

The goal is:

> **minimum sufficient recovery context.**

---

# 10. RECOVERY MODES

Support:

```text
RESUME
REPAIR_AND_RESUME
ROLLBACK
WAIT
REQUEST_HUMAN
ABORT
```

Example:

```text
Checkpoint valid
→ RESUME

Checkpoint partially stale
→ REPAIR_AND_RESUME

External side effect uncertain
→ REQUEST_HUMAN

Critical dependency changed
→ ABORT or REPLAN
```

---

# 11. RECOVERY CONTRACT

Before allowing the agent to resume, CONTINUUM should generate a recovery contract.

Example:

```json
{
  "run_id": "run_4821",

  "recovery_status": "SAFE_TO_RESUME",

  "verified": [
    "goal",
    "completed_documents",
    "evidence"
  ],

  "invalidated": [
    "dataset_v3"
  ],

  "required_actions": [
    "revalidate experiment results"
  ],

  "next_allowed_action": "revalidate_dataset"
}
```

This contract should be deterministic and machine-readable.

---

# 12. ENVIRONMENT SNAPSHOT

Create an environment abstraction.

The environment may contain:

```text
files
database records
API resources
dataset versions
Git commits
configuration
permissions
tool availability
external IDs
```

Create:

```python
EnvironmentSnapshot
```

with:

```python
capture()
compare()
diff()
validate()
```

Example:

```text
ENVIRONMENT DIFF

dataset:
v3 → v4

git:
abc123 → def456

permissions:
write_access → revoked

API:
session_valid → expired
```

---

# 13. STATE DIFF

Provide:

```bash
continuum diff checkpoint_a checkpoint_b
```

Output:

```text
+ New finding: finding_81
~ Dataset version: v3 → v4
- Decision #7 invalidated
+ Pending task: re-run experiment
```

Also provide:

```python
state.diff(other_state)
```

---

# 14. STATE VERSIONING

Every state mutation must produce a version.

Example:

```text
v0
 ↓
v1
 ↓
v2
 ↓
v3
```

Use immutable event records where practical.

Support:

```bash
continuum history run_4821
```

and:

```bash
continuum inspect run_4821 --version 17
```

---

# 15. EVENT LOG

Create an append-only event model.

Events include:

```text
RUN_STARTED
TASK_UPDATED
TOOL_CALLED
TOOL_COMPLETED
TOOL_FAILED
DECISION_CREATED
DECISION_INVALIDATED
EVIDENCE_ADDED
STATE_CHECKPOINTED
STATE_VALIDATED
ENVIRONMENT_CHANGED
RECOVERY_STARTED
RECOVERY_COMPLETED
RECOVERY_BLOCKED
ACTION_RECONCILED
```

The event log should be the source of truth where practical.

---

# 16. CRASH RECOVERY DEMO

Create a complete example.

Example:

```text
examples/
    crash_recovery_agent.py
```

The example should:

1. Start a long-running task.
2. Process several items.
3. Save semantic state.
4. Simulate process termination.
5. Modify the environment.
6. Restart the process.
7. Load the checkpoint.
8. Validate the state.
9. Detect the environmental change.
10. Repair the affected state.
11. Resume execution.
12. Finish without repeating completed work.

The README MUST show this demo.

---

# 17. CONTEXT COMPACTION DEMO

Create:

```text
examples/context_compaction.py
```

Simulate:

```text
large context
→ compaction
→ loss of transcript
→ semantic checkpoint survives
→ recovery context reconstructed
→ task continues
```

Measure:

```text
original context tokens
recovery context tokens
task completion
recovery correctness
```

---

# 18. MODEL-SWITCH DEMO

Create:

```text
examples/model_switch.py
```

Simulate:

```text
Model A
 ↓
checkpoint
 ↓
Model unavailable
 ↓
Model B
 ↓
recover
```

The agent should retain:

* task goal
* verified progress
* decisions
* evidence
* pending work

without requiring the original model's full conversation.

The system must NOT assume that switching models is automatically safe.

If a model-specific assumption exists, mark it:

```text
MODEL_SPECIFIC_STATE
```

and require validation.

---

# 19. FRAMEWORK ADAPTERS

Build the core framework-agnostic.

Create adapters for:

```text
generic Python agent
OpenAI Agents SDK
LangGraph
```

Only implement adapters when they can be done cleanly.

Do not tightly couple the core to these frameworks.

Adapter interface:

```python
class AgentAdapter:
    def capture_state(...)
    def restore_state(...)
    def intercept_action(...)
    def resume(...)
```

---

# 20. CLI

Implement:

```bash
continuum init
continuum run
continuum checkpoint
continuum resume
continuum inspect
continuum diff
continuum validate
continuum history
continuum replay
continuum benchmark
```

Example:

```bash
continuum resume run_4821
```

Output:

```text
CONTINUUM RECOVERY

Run: run_4821

Checkpoint:
v17

State validation:
✓ Goal
✓ Progress
✓ Evidence
⚠ Dataset version changed
✓ Action ledger

Recovery decision:
REPAIR_AND_RESUME

Repair:
Revalidate experiments 14–17

Next permitted action:
dataset_revalidation
```

---

# 21. PYTHON API

Keep the API extremely simple.

Example:

```python
from continuum import Continuum

runtime = Continuum(storage="sqlite:///agent.db")

run = runtime.start(goal="Analyze these documents")

run.checkpoint()

run.record_action(...)

run.complete()
```

Recovery:

```python
run = runtime.resume("run_4821")

status = run.validate()

if status.safe:
    run.continue_execution()
```

---

# 22. STORAGE

MVP:

```text
SQLite
```

Optional:

```text
PostgreSQL
```

Optional object storage:

```text
S3-compatible
```

Do NOT require a cloud database.

A developer must be able to run the entire system locally.

---

# 23. CLOUD ARCHITECTURE

Create optional cloud deployment:

```text
                  AGENT
                    │
                    ▼
              CONTINUUM SDK
                    │
                    ▼
               API Gateway
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
     State Service       Event Service
          │                   │
          └─────────┬─────────┘
                    ▼
               PostgreSQL
                    │
                    ▼
              Object Storage
```

Use:

* FastAPI
* PostgreSQL
* Docker
* optional Redis
* optional S3-compatible object storage

No GPU.

The cloud service should primarily provide:

* durable storage
* centralized run management
* multi-agent coordination
* recovery history
* state inspection

---

# 24. SECURITY

This is critical.

Never treat the checkpoint as trusted merely because it is persisted.

Validate:

* integrity
* version
* authorization
* provenance
* environment compatibility

Support cryptographic hashes for:

* checkpoints
* evidence
* action records
* environment snapshots

Do not store secrets inside semantic checkpoints.

Credentials must be referenced, never serialized into state.

---

# 25. RESEARCH BENCHMARK

Create:

# CONTINUUM-Bench

Evaluate long-running agents under controlled failures.

Scenarios:

```text
process crash
context compaction
tool failure
API timeout
dataset change
file modification
permission change
model switch
external side effect
stale decision
partial completion
```

Each scenario should have ground-truth recovery behavior.

Example:

```json
{
  "scenario": "dataset_change",
  "checkpoint_version": "v3",
  "environment_version": "v4",
  "expected": "REPAIR_AND_RESUME"
}
```

Measure:

### Recovery correctness

Did the agent resume correctly?

### Duplicate work

How much previously completed work was repeated?

### Duplicate side effects

How many external actions were accidentally repeated?

### Recovery latency

How long until safe continuation?

### Recovery context size

How many tokens were required?

### State validation accuracy

Were stale states detected?

### Cost

How much LLM/API cost was saved?

---

# 26. BASELINES

Compare CONTINUUM against:

```text
1. Full transcript replay
2. Simple conversation summarization
3. Naive checkpointing
4. Structured task summary
5. CONTINUUM semantic checkpoint
```

Do NOT fabricate results.

Implement the benchmark harness first.

Run experiments only after the system is complete.

---

# 27. KEY RESEARCH METRIC

Introduce:

# Recovery Fidelity

Define a measurable metric for whether the recovered agent behaves consistently with a fault-free execution.

For example:

```text
Recovery Fidelity =
correct recovered decisions
/
required recovered decisions
```

Also measure:

```text
State Fidelity
Action Fidelity
Goal Fidelity
Evidence Fidelity
```

Clearly document the definitions.

---

# 28. SECOND RESEARCH METRIC

Introduce:

# Recovery Compression Ratio

Measure:

```text
Full historical context size
/
Semantic recovery state size
```

Example:

```text
Full transcript:
182,000 tokens

Recovery state:
4,200 tokens

Compression ratio:
43.3×
```

Do NOT claim this number until experimentally measured.

---

# 29. IMPORTANT RESEARCH QUESTION

The project should investigate:

> **How much agent execution history can be removed while preserving safe task continuation?**

Secondary questions:

1. Does semantic state outperform naïve summarization?
2. Can stale state be detected automatically?
3. How much duplicate work can durable semantic state eliminate?
4. How does recovery behave across different models?
5. How does recovery behave after environment changes?
6. Can external side effects be safely reconciled?

---

# 30. README POSITIONING

The README should NOT claim:

> "The first durable agent framework."

That is not defensible.

Instead:

> **CONTINUUM is a lightweight semantic recovery layer for long-running AI agents. It externalizes the minimum verified task state needed to survive crashes, context loss, and environmental changes.**

Then immediately show:

```text
Agent crashes.
Context disappears.
CONTINUUM remembers what actually matters.
```

---

# 31. README DEMO

The first screen of the README should show:

```bash
pip install continuum-agent
```

Then:

```python
from continuum import Continuum

runtime = Continuum("agent.db")

run = runtime.start(goal="Analyze 10,000 documents")

...
```

Then a crash:

```text
💥 PROCESS TERMINATED
```

Then:

```bash
continuum resume run_4821
```

Output:

```text
✓ 3,421 documents already processed
✓ 127 findings preserved
✓ 14 decisions preserved
⚠ dataset changed
✓ affected state identified
→ revalidation required
→ safe recovery
```

This should be the project's 30-second pitch.

---

# 32. REPOSITORY STRUCTURE

Use:

```text
continuum/
│
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── SECURITY.md
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
│
├── src/
│   └── continuum/
│       ├── __init__.py
│       ├── runtime.py
│       ├── models.py
│       ├── events.py
│       ├── state/
│       │   ├── semantic.py
│       │   ├── extractor.py
│       │   ├── validator.py
│       │   ├── versioning.py
│       │   └── diff.py
│       │
│       ├── recovery/
│       │   ├── engine.py
│       │   ├── planner.py
│       │   └── contract.py
│       │
│       ├── actions/
│       │   ├── ledger.py
│       │   ├── idempotency.py
│       │   └── reconciliation.py
│       │
│       ├── environment/
│       │   ├── snapshot.py
│       │   ├── validator.py
│       │   └── diff.py
│       │
│       ├── storage/
│       │   ├── base.py
│       │   ├── sqlite.py
│       │   └── postgres.py
│       │
│       ├── adapters/
│       │   ├── base.py
│       │   ├── generic.py
│       │   ├── openai.py
│       │   └── langgraph.py
│       │
│       ├── cli/
│       │   └── main.py
│       │
│       └── security/
│           ├── hashing.py
│           └── validation.py
│
├── tests/
│
├── examples/
│
├── benchmarks/
│
├── docs/
│
└── cloud/
    ├── api/
    └── deployment/
```

---

# 33. TESTING

Use:

* pytest
* hypothesis/property-based testing where useful
* mypy or pyright
* Ruff
* integration tests
* crash simulation tests

Test especially:

```text
crash during action
crash after action but before ledger commit
crash after ledger commit
duplicate recovery
stale checkpoint
environment change
model switch
partial state
corrupted checkpoint
unknown external side effect
concurrent state update
```

The action ledger must be tested heavily.

---

# 34. DISTRIBUTED-SYSTEM CORRECTNESS

Treat recovery as a distributed systems problem.

Explicitly reason about:

```text
exactly-once vs at-least-once
idempotency
atomicity
race conditions
partial failure
event ordering
eventual consistency
stale reads
concurrent updates
```

Do not claim exactly-once semantics unless actually implemented.

When guarantees are weaker, document them honestly.

---

# 35. PERFORMANCE TARGETS

MVP should run:

* CPU only
* locally
* without downloading models
* with SQLite
* with minimal memory
* with minimal latency

Semantic extraction through an LLM must be optional.

The deterministic core should remain fully functional offline.

---

# 36. DEVELOPMENT PHASES

Implement incrementally.

## Phase 1

Data models + event system.

## Phase 2

Semantic state representation.

## Phase 3

SQLite persistence.

## Phase 4

Checkpoint creation.

## Phase 5

State validation.

## Phase 6

Action ledger + idempotency.

## Phase 7

Recovery engine.

## Phase 8

CLI.

## Phase 9

Crash recovery examples.

## Phase 10

Environment snapshots and diffs.

## Phase 11

Framework adapters.

## Phase 12

Benchmark suite.

## Phase 13

Cloud API.

## Phase 14

Dashboard.

DO NOT build the dashboard first.

---

# 37. GITHUB QUALITY

The project must feel like a real open-source infrastructure project.

Include:

* clean README
* architecture diagram
* quickstart
* API docs
* examples
* benchmark
* tests
* contribution guide
* issue templates
* PR template
* GitHub Actions
* releases
* changelog

Add labels:

```text
good first issue
help wanted
detector
adapter
benchmark
documentation
research
```

Make extension points obvious.

---

# 38. EXTENSIBILITY

Developers should eventually be able to implement:

```python
class StatePlugin: ...
```

and:

```python
class EnvironmentProvider: ...
```

and:

```python
class ActionReconciler: ...
```

and:

```python
class RecoveryStrategy: ...
```

The core should remain small.

---

# 39. PRODUCT PHILOSOPHY

CONTINUUM is NOT:

* an LLM
* a chatbot
* an agent framework
* a vector database
* a RAG framework
* a workflow engine
* a generic memory system

CONTINUUM IS:

> **A reliability layer that makes long-running agent work durable, inspectable, and recoverable.**

The core abstraction is:

```text
semantic state
+
environment validation
+
action reconciliation
=
safe recovery
```

---

# 40. CRITICAL RULE

Before implementing a feature, check whether the feature already exists in established agent runtimes.

Do not duplicate functionality merely to increase feature count.

When existing functionality is similar, explicitly document:

```text
Existing approach:
...

CONTINUUM difference:
...
```

The project's differentiation should be based on measurable behavior and architecture, not marketing language.

---

# 41. FINAL ACCEPTANCE TEST

The finished MVP must successfully demonstrate this scenario:

```text
1. Agent starts long-running task.

2. Agent processes 100 items.

3. CONTINUUM creates semantic checkpoint.

4. Agent performs external side effect.

5. CONTINUUM records the action.

6. Process crashes.

7. Environment changes.

8. Process restarts.

9. CONTINUUM loads checkpoint.

10. CONTINUUM detects environment change.

11. CONTINUUM marks affected state stale.

12. CONTINUUM prevents unsafe replay.

13. Agent reconciles the stale state.

14. Agent resumes from verified progress.

15. Previously completed work is not repeated.

16. Previously completed external side effects are not duplicated.

17. Task completes.

18. CONTINUUM produces a complete recovery report.
```

The demo should be reproducible with one command.

---

# 42. FINAL INSTRUCTION TO YOU

Do not generate the entire codebase in one response.

Work incrementally.

Start with:

**Phase 1, Data models + event system + tests.**

After implementing Phase 1:

1. run the tests
2. inspect failures
3. fix them
4. show the resulting file tree
5. explain the design briefly
6. wait for confirmation before moving to Phase 2

Do not skip tests.

Do not invent APIs.

Do not fabricate benchmark results.

Do not claim novelty that has not been established.

Prioritize correctness, minimalism, reproducibility, and real developer usefulness over flashy features.

The final goal is a project that can become a serious open-source repository and a credible AI-systems research artifact.


# CONTINUUM: Publishing & Premium Roadmap

## 0. Where things actually stand

I don't have your repo checked out in this session (no network in my sandbox,
no GitHub connector wired up here), so everything below is built from the
three documents you attached, CHANGELOG, the original build spec, and the
README, plus fresh web research on the competitive landscape as of August
2026. I have not touched your code. Section 6 is a prompt written for your
coding agent (with actual repo access) to execute, using the same
verify-before-file rigor you set earlier.

Two things worth saying plainly:

- Phases 1–8 and 10 are, per your own docs, complete with real tests and real
  crash-kill demonstrations (`os._exit(9)` mid-run, subprocess-level
  verification). That is unusually honest for a project at this stage,
  most READMEs at this size claim more than they've built. Keep that
  discipline; it is itself a differentiator (see 2.3).
- The unresolved items from our prior conversation (stale Roadmap rows,
  untracked `demo_report.md`, missing labels, unconfirmed community files,
  the `docs/` marketing-site question, `pricing.html`) are still open. They
  belong in Part A of the agent prompt below, unchanged from before.

## 1. Competitive landscape (August 2026)

Durable execution for AI agents became a crowded, well-funded category this
year:

- **Temporal-style replay.** Event-history replay to reconstruct in-memory
  state after a crash is now table stakes; LangGraph, Temporal, and Dagster
  all ship it, with PostgresSaver recommended for LangGraph production and
  MemorySaver for dev only.
- **Framework-native durability.** Microsoft's Durable Task extension and
  Azure's Durable Task Scheduler bring automatic per-step checkpointing to
  agent frameworks without code changes. OpenAI's Agents SDK added
  externalized state, snapshotting, and rehydration into fresh containers.
  CrewAI Flow persists flow state via SQLite by default. AutoGen saves and
  loads full message-thread and group-chat state.
- **The new frontier is verification, not just recovery.** Dapr 1.18
  (June 2026) shipped "Verifiable Execution": cryptographically signed
  workflow history (SPIFFE-backed identities), lineage propagation across
  service boundaries, and workflow attestation so downstream systems can
  make trust decisions on *proven* provenance rather than assumed state.
  Diagrid's Catalyst 2.0 layers this onto LangGraph, MSFT Agent Framework,
  Google ADK, AWS Strands, OpenAI Agents SDK, CrewAI, Pydantic AI, and Dapr
  Agents, explicitly calling out that LangGraph's own checkpointing leaves
  "detection and recovery logic to developers."
- **The gap they're all pointing at, and mostly not filling:** none of the
  above independently *revalidate* checkpointed state against the current
  environment before resuming, and none propagate staleness through a
  dependency graph. They checkpoint and replay faithfully, which is a
  different problem from asking "is this still true?" Most of them also
  require a scheduler, a cluster, or a managed control plane.

**Where CONTINUUM already sits differently, per your own README:**

1. It is the only one in this set that treats "the checkpoint is valid" as
   a claim to be *disproven*, not assumed, staleness propagates
   `dependency -> evidence -> finding -> decision`, and `UNKNOWN` degrades
   toward unsafe rather than resolving to safe.
2. `UnknownSideEffect` plus mandatory reconciliation is a sharper, more
   honestly-documented answer to the exactly-once problem than most of the
   category, which quietly assumes idempotent tools.
3. It is CPU-only, dependency-free at the core (`argparse`, stdlib), and
   runs against a single SQLite file, no cluster, no scheduler, no managed
   control plane. That is a real wedge against Temporal/Dapr/Diagrid, whose
   value proposition assumes you already run their infrastructure.

That combination, independent revalidation with staleness propagation,
zero infrastructure, and an honestly-scoped guarantee set, is a legitimate,
defensible positioning line. Don't chase Dapr's cryptographic-attestation
category head-on; borrow the one piece of it that composes naturally with
what you've already built (see 2.3).

## 2. What "premium" should mean here

Not a paywall, this is infra-trust software; open-core done badly kills
adoption in this category. "Premium" should mean: the kind of project a
staff engineer green-lights for a production agent without a follow-up
meeting. Concretely:

### 2.1 Close the credibility gaps first (higher leverage than any new feature)
A reviewer's first move is to check if the README lies. Right now the
Roadmap table, the label set, the community files, and `docs/` are all
either stale or unverified per your own audit notes. Fixing those is the
single highest-value "premium" action available, because it's the
difference between "looks maintained" and "looks abandoned by phase 10."

### 2.2 Ship the two demos the README promises but Phase 9 hasn't delivered
`examples/crash_recovery_agent.py`, `context_compaction.py`, and
`model_switch.py` are described in loving, specific detail in the README as
if they exist and run. If Phase 9 is actually "Planned" per the Roadmap
table, that's the single biggest trust risk in the whole repo, a technical
reader who clones and runs the quick-start commands and gets `FileNotFoundError`
will not come back. This is Part A, item 1, below: verify first, then either
fix the docs or ship the examples, don't leave the gap.

### 2.3 A cryptographic verification layer, natural, not bolted-on
You already have hash-chained events, sealed checkpoints with integrity
hashes, and canonical deterministic hashing. The entire cryptographic
substrate Dapr just launched a feature category around, you already built
for a different reason. The premium move is a thin, optional layer:

- `continuum verify --sign`, sign the event chain's `trusted_through` hash
  with a local Ed25519 key (stdlib `hashlib` + a small, audited dependency
  like `pynacl`, kept behind an extra so the core stays dependency-free).
- `continuum attest <run_id>`, emit a portable, independently-checkable
  attestation document: run id, trusted_through, chain hash, signer public
  key, timestamp. No SPIFFE, no cluster, no managed identity, just "here is
  cryptographic proof this run's history has not been altered since I
  signed it," which is exactly the trust question a compliance reviewer
  asks and exactly what your hash chain already computes.
- This is genuinely differentiated: it gives you the outcome Dapr 1.18
  markets, without asking anyone to adopt Dapr.

### 2.4 Turn the recovery report into something a human wants to look at
The CLI's plaintext recovery report is good engineering; it is not
something a founder screenshots for a launch post. A `--format rich` flag
(using `rich`, optional dependency) that renders the same
`RecoveryContract` as a readable panel, plus a static HTML export
(`continuum inspect <run> --html report.html`) using only stdlib templating,
turns your best existing artifact into your best marketing asset. This is
cheap relative to its payoff.

### 2.5 Framework adapters, ordered by real leverage, not the original list
Phase 11 currently lists generic Python, OpenAI Agents SDK, LangGraph. Given
where the ecosystem is now:
- **LangGraph first**, biggest install base, and its checkpointer story is
  explicitly the thing Diagrid criticized as "basic." An adapter that lets a
  `SqliteSaver`/`PostgresSaver` user opt into CONTINUUM's environment
  revalidation on top of LangGraph's own checkpointing is a clean,
  non-competing pitch: "keep your checkpointer, add the validator."
- **Claude Code / Claude Agent SDK**, you're already building this with
  Claude Code; an adapter that lets a Claude Code session itself checkpoint
  through CONTINUUM (relevant to the concurrent-write hang you mentioned)
  doubles as your own dogfood story and a natural launch demo.
- OpenAI Agents SDK and CrewAI Flow next; both already have their own
  persistence, so pitch the same "add the validator" angle.

### 2.6 Benchmark: ship the smallest defensible version, not the full CONTINUUM-Bench
The README is admirably honest that no benchmark harness exists yet. Don't
build the full 11-scenario suite before launch, build 3 scenarios (process
crash, dataset change, unknown side effect) with the two baselines you can
implement honestly (full transcript replay, naive checkpointing), and
publish real numbers for just those. A small, real number beats a large,
promised one, and it directly substantiates the compression-ratio and
recovery-fidelity claims the README currently declines to make.

### 2.7 Packaging and distribution polish (fast, mechanical, high perceived-quality)
- Migrate to `uv` for the build/publish path; use PyPI Trusted Publishing
  (OIDC) instead of API tokens in the release workflow, this is the 2026
  default and its absence now reads as dated.
- A generated docs site beats a README once the API surface is this large.
  `mkdocs-material` (mature, in wide use) or a tool like `great-docs`
  (newer, purpose-built for turning a README into a landing page plus
  auto-generated API reference from docstrings) are both reasonable; either
  closes the "no separate API-reference docs" gap flagged in the earlier
  audit far better than hand-written pages.
- `SECURITY.md` with a real disclosure process matters more than usual here
  given the project's subject matter is literally "can this state be
  trusted." Don't skip it even though it's boilerplate.

## 3. Suggested sequencing

1. **Part A verification + doc fixes** (below), a few hours, removes every
   credibility risk currently sitting in the repo.
2. **Part B fresh audit** (below), finds real bugs before anyone else does.
3. **Phase 9 examples**, since the README already promises them.
4. **Packaging/publishing polish** (2.7), cheap, mechanical, unblocks a
   real PyPI release.
5. **Attestation layer** (2.3), your strongest differentiated feature,
   built on infrastructure you already have.
6. **LangGraph adapter + minimal benchmark** (2.5, 2.6), this is what
   turns "interesting repo" into "thing people cite and adopt."
7. Rich CLI output / HTML export (2.4) last, nice-to-have, not load-bearing.

## 4. New GitHub issues to file (in addition to whatever Part A/B produce)

Only file these once labels are confirmed to exist (Part A, item 3):

- **"Add cryptographic attestation for event chains (`continuum attest`)"**
 , labels: `research`, `help wanted`. Not `good first issue`; touches
  security-sensitive code.
- **"LangGraph adapter: environment revalidation on top of existing
  checkpointer"**, labels: `adapter`, `help wanted`.
- **"Minimal CONTINUUM-Bench: 3 scenarios, 2 baselines, real numbers"**,
  labels: `benchmark`, `research`.
- **"Migrate release workflow to uv + PyPI Trusted Publishing"**, labels:
  `good first issue` if scoped to just the workflow file.
- **"Generate API reference docs site from docstrings"**, labels:
  `documentation`, `good first issue`.

Don't create these until Part A confirms the label set actually exists;
otherwise they'll land unlabeled or on the wrong labels.

## 5. Things I will not help with, and why

`docs/` allegedly being a marketing site and `pricing.html` sitting in a
repo for an Apache-2.0 CPU-only local library is worth flagging as a
positioning inconsistency, not a security issue, if the audit confirms
it's genuinely stale template content, delete it or replace it with real
API docs rather than polish it, since a pricing page on an unpriced OSS
infra library undercuts the "no cloud account required" pitch the README
leads with.

---

## 6. Ready-to-run prompt for your coding agent

Paste this to your coding agent (with real repo access) as-is. It preserves
the standing rules from before: verify before filing, real repo evidence,
existing labels only, no em dashes, no AI attribution, small scoped items
marked `good first issue`.

```
Two parts, in order, same rigor as before: reproduce every candidate before
treating it as real, and list every confirmed candidate (title + one-line
summary + evidence) before creating anything on GitHub.

PART A, verify these specific gaps before filing anything:

1. Confirm the Roadmap table's Phase 9 and Phase 11 status against actual
   repo contents. Specifically: do examples/crash_recovery_agent.py,
   examples/context_compaction.py, and examples/model_switch.py exist and
   run to completion? Does adapters/ contain anything beyond base.py? If
   Phase 9 examples don't exist but the README describes their output as
   if they do, this is a launch-blocking credibility gap, not a routine
   docs fix: fix the Roadmap table directly, AND either build the missing
   examples or clearly mark the README sections as target/illustrative
   (matching how the Continuum() API section is already marked "not
   implemented yet"). Do not leave the README implying a working demo that
   doesn't exist.

2. Find out exactly why MCP server tests are excluded from the suite, what
   MCP SDK version is installed vs required in pyproject.toml, and whether
   it's a quick pin fix or a real incompatibility. Show the actual error
   output, not a paraphrase.

3. Check whether .github/labels config or repo label settings already
   include detector, adapter, benchmark, research. If genuinely missing,
   create them via `gh label create` (small, low-risk, no need to ask
   first). Also create these two if missing, since they'll be needed for
   the new candidate issues below: none beyond the four listed unless the
   audit surfaces a real need.

4. Confirm whether CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md exist
   at repo root. If missing, list as a candidate issue rather than creating
   them yourself. For SECURITY.md specifically, note in the issue that this
   project's subject matter (state trust and recovery) makes a real
   vulnerability-disclosure process more than boilerplate.

5. Check docs/ contents directly. Confirm or refute that it's a marketing
   site rather than API reference docs. Confirm whether any separate
   API-reference documentation exists anywhere else in the repo (docstrings
   don't count unless something renders them). If docs/ is confirmed to be
   marketing content for an unpriced local-only OSS library, flag that as
   a positioning inconsistency in the candidate list, not just a docs gap.

6. Check pricing.html's actual content verbatim. Flag whether it reads as
   leftover template copy (placeholder tiers, lorem-ipsum-adjacent text,
   inconsistent with Apache-2.0 + local-only positioning) versus
   intentional content someone meant to ship.

PART B, general fresh audit: walk src/continuum/ module by module for real
bugs, weak coverage, docstring/code mismatches, and TODO/FIXME comments.
Reproduce every candidate with a failing test or a direct repro before
treating it as real. Cross-check against issues #1, #6-#13 to avoid
duplicates.

PART C, new work, only after A and B are filed and confirmed:

1. Draft (don't merge) a `continuum attest` command: sign the event chain's
   trusted_through hash with a local Ed25519 key, and a `continuum verify
   --sign` flag. Keep this behind a new optional extra (e.g. `.[attest]`)
   so the core stays dependency-free. Write it as a design doc + a stub
   module with tests for the signing/verification round trip before wiring
   it into the CLI. Stop and show me the design before implementing the
   full CLI surface.

2. Audit the release workflow (release.yml) against 2026 best practice:
   is it using uv and PyPI Trusted Publishing (OIDC), or manual API
   tokens? If tokens, file a candidate issue (good first issue if scoped
   to just the workflow file) rather than migrating it yourself.

List every confirmed candidate first, title + one-line summary + evidence,
before creating anything. Same standing rules: real issue templates,
correct existing labels only (plus the four just created if you added
them), no em dashes, no AI attribution.
```

---

## 7. Next updates to work through (one by one)

Prioritized from the integration-architecture deep dive
(`references/integration-architecture.md`) and the standing bug list in
STATUS.md. Work top to bottom, complete and verify each before the next. Mark
an item done only with evidence (a test or a direct repro).

### A. Close the credibility and trust gaps (these invalidate the core claim)

- [x] **A1. Fix the remaining trust bug `#1` (MCP caller authentication).**
  - `#17` (older-schema DB accepted silently) and `#19` (`resume --repair`
    no-op) were **already resolved** (commits `82b9f1c`, `f145818`, with
    regression tests in `tests/test_storage.py` and `tests/test_cli.py`).
  - `#1`: implemented. When `CONTINUUM_MCP_TOKEN` is set, the server refuses
    every mutating tool unless the caller presents that shared secret in the
    handshake's `_meta.authToken`. Fail-closed (the PR #3 failed-open mistake is
    explicitly tested against). `AuthPolicy`/`load_auth` in
    `src/continuum/mcp/authz.py`, wired into the tool `guard` in
    `src/continuum/mcp/server.py`, tests in `tests/test_mcp_authz.py`. Default
    local behavior unchanged when the variable is unset.
- [x] **A2. Observability and proof.** Add metrics plus the Phase 14 dashboard,
  and run the minimal CONTINUUM-Bench (`#6`): LangGraph adapter revalidation on
  top of the existing checkpointer, with 3 scenarios, 2 baselines, and real
  numbers. This is the adoption proof point.
  - Observability (metrics collector + Phase 14 dashboard + `--dashboard` flag)
    merged via PR #60. The `#6` proof is now a real, runnable benchmark suite in
    `continuum benchmark`: it drives the actual `ActionLedger` (the same path the
    LangGraph/OpenAI/MCP adapters call) under argument drift, with 4 methods and
    real numbers, 0 duplicate side effects for CONTINUUM vs N for the baselines.
    Regression test in `tests/test_benchmark.py`.

### B. Make CONTINUUM attachable to any system (from `integration-architecture.md`)

- [x] **B0 (Tier 0, boundary).** `continuum serve` sidecar exposing the current
  CLI surface over a stable wire protocol (reuse MCP or add HTTP/gRPC), plus
  thin multi-language SDKs and a generic auto-instrumentation shim (HTTP client
  / DB driver wrappers) so non-Python systems attach with minimal code.
  - Implemented as a language-agnostic newline-delimited JSON protocol over
    stdio in `src/continuum/serve/` (`server.py` handlers, `__init__.py` client
    and `cmd_serve` wired into `src/continuum/cli/main.py`). The surface mirrors
    the MCP tools (`record_progress`, `checkpoint`, `validate`, `resume`,
    `confirm`, `intercept_action`, `complete_action`, `fail_action`,
    `reconcile_action`, `list_actions`) with fail-closed `CONTINUUM_SERVE_TOKEN`
    auth. `serve_subprocess()` launches a real `continuum serve` child. Tests in
    `tests/test_serve.py`. Multi-language SDKs and HTTP/gRPC transport are still
    open (a follow-up, not required for Tier 0).
- [x] **B1 (Tier 1, teachability).** Add a `Registry` (dependency-injected
  context) and the four capability seams as `Protocol` interfaces with
  conformance tests: `EnvironmentProvider` (discover, not declare),
  `StateExtractor` (map arbitrary internal state), `ActionReconciler`
  (common idempotency / read-back), `ValidationRule` (domain staleness). Ship
  at least one real `EnvironmentProvider` (git HEAD or Postgres schema version).
  - Implemented in `src/continuum/plugins/` (`Registry`, seams, `Reconciliation`)
    and `GitProvider` in `src/continuum/environment/snapshot.py`; conformance
    tests in `tests/test_plugins.py`. Note `EnvironmentProvider` already existed
    as an ABC with `StaticProvider`/`ValueProvider`/`FileProvider`/`CallableProvider`.
- [ ] **B2 (Tier 2, production durability).** PostgreSQL storage, a centralized
  server, distributed locking / lease coordination for runs, and the
  schema plus projector migration framework from A1/`#17`.
- [ ] **B3 (Tier 3, trust and ops).** Bind attestation to a workload identity
  (optional SPIFFE/SPIRE) instead of an ad-hoc key file, emit an attestation
  propagation token, and add real-time enforcement in `resume` (refuse to
  continue if the attestation or chain no longer matches).
- [ ] **B4 (Tier 4, portability).** Define a portable "Recovery State"
  interchange schema so different systems and versions interoperate and
  external tools can verify CONTINUUM output.

### C. Triage the remaining open correctness bugs

- [ ] **C1.** Review `#29, #30, #33, #34, #36, #42, #43, #45, #49`. Decide which
  are real versus stale, file or close accordingly. Do not implement before
  triage.

### D. Unbuilt roadmap phases

- [ ] **D1.** Phase 13 (Cloud API), Phase 14 (Dashboard), Phase 23
  (multi-agent coordination). Schedule after B0/B1 land.

### Where to start

Done: **A1/`#1`** (MCP caller authentication, Session 13), **B1** (the `Registry`
and the four seam protocols with conformance tests, Session 14), and **B0**
(`continuum serve` sidecar over a newline-delimited JSON stdio protocol, Session 15).
`#17` and `#19` are already resolved; verify their regression tests pass and move on.

Next in order: **B2** (PostgreSQL, centralized server, distributed locking, schema
migration), **B3** (attestation bound to workload identity), **B4** (portable
Recovery State schema), **C1** (triage `#29/#30/#33/#34/#36/#42/#43/#45/#49`),
**D1** (Phases 13/14/23).

