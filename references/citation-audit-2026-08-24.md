# Citation audit, 2026-08-24 (issue #261)

An external Gemini-generated report pitching a "CONTINUUM paradigm" cited several
works absent from this repository's related-work list, and made claims that
contradict sources already verified here. This file records a per-citation
verdict so the project has a durable position when the report is cross-referenced
against our docs.

## Method

Every candidate arXiv ID was resolved against the arXiv API
(`https://export.arxiv.org/api/query?id_list=...`) on 2026-08-24. A verdict of
**verified** means: the ID resolves, the title matches the claimed domain, and
the headline numbers quoted by the report appear in the paper's abstract as
retrieved that day. Abstract-level verification only; body-level claims were not
checked beyond what the abstract states. DAPH was additionally searched by title
(`ti:"DAPH"`), by the phrase "Distributed AI Pipeline Harness", and by
`abs:"pipeline harness" AND abs:"Raft"`; all three searches returned zero results.

A second pass the same day cross-checked every verdict against the open web:
independent mirrors (HuggingFace Papers, NASA ADS, alphaXiv, Semantic Scholar,
papers.cool), the arXiv HTML full texts where available, and targeted web
searches for DAPH's claimed venue. Findings from that pass are folded into the
verdicts below and marked where they go beyond the abstract.

## Verdicts

| Report citation | ID | Verdict | Notes |
|:--|:--|:--|:--|
| Belayer | [arXiv:2608.14635](https://arxiv.org/abs/2608.14635) | **Verified real**, out of scope | Zhou, Hu, Sun, Zhang, Zhang, *Belayer: Efficient Fault Tolerance for LLM Agentic RL Training* (submitted 2026-07-28, v2). Abstract confirms shadow workers, selective GPU-state reuse retaining weights and raw KV-arena allocations, worker recovery up to 42 times faster than cold start, 1.5 to 3.5 times faster environment-failure recovery. All report figures match. Web pass: corroborated by the arXiv HTML full text and independent mirrors (papers.cool, academ.us). Domain is agentic RL *training loops*, not agent runtime recovery, so it is not added to our Related work list. |
| Transactional sandboxing | [arXiv:2512.12806](https://arxiv.org/abs/2512.12806) | **Verified real**, one fabricated figure | Yan (University of Virginia), *Fault-Tolerant Sandboxing for AI Coding Agents: A Transactional Approach to Safe Autonomous Execution* (submitted 2025-12-14). Full-text check confirms: policy-based interception, transactional filesystem snapshots on a Proxmox/EVPN/VXLAN testbed, Minimind-MoE (about 26M parameters) via nano-vllm, 100 percent interception of blacklisted commands, 100 percent rollback success, and 14.5 percent overhead (about 1.82s on a 4.69s baseline). The report's table claim of "10.6% improvement in task completion" appears **nowhere in the paper**: the evaluation is a 10-scenario safety matrix plus a latency benchmark; no task-completion metric exists. The "Atomix" name also appears nowhere. Added to Related work with abstract-backed description only. Notable for us: section 7.5 concedes the framework cannot handle external stateful APIs and names compensating transactions as future work, which independently supports this project's ledger-over-sagas position (LogAct reasoning in our related work). |
| ReliabilityBench | [arXiv:2601.06112](https://arxiv.org/abs/2601.06112) | **Verified real**, accurately described | Gupta (GoHighLevel), *ReliabilityBench: Evaluating LLM Agent Reliability Under Production-Like Stress Conditions* (submitted 2026-01-03). Abstract and web pass confirm every figure: the R(k, epsilon, lambda) surface, pass^k consistency, action metamorphic relations over end-state equivalence, fault injection (timeouts, rate limits, partial responses, schema drift), 1,280 episodes across two models and two architectures in four domains, perturbation degrading success from 96.9 to 88.1 percent at epsilon=0.2 (8.8 points), rate limiting the most damaging fault in ablations, ReAct more robust than Reflexion under combined stress. Corroborated by HuggingFace Papers, NASA ADS, alphaXiv and Semantic Scholar. Evaluation-only: it provides no runtime mitigation, exactly as the report's own limitation row states. Relevant to issue #258 (stress-surface benchmark), which should build on this surface concept and cite the verified paper rather than restating report numbers. |
| DAPH | none given | **Exists only as a blog post; venue attribution unsupported** | No arXiv ID was provided and all arXiv searches returned zero results. A targeted web search found exactly one source: a self-published Medium post, "Fault-Tolerant Distributed AI Agent Harness: Architecture, Implementation and Evaluation" (gwrx2005, June 2026), which does present "the Distributed AI Pipeline Harness (DAPH)". However, no OpenReview page, no ICLR workshop listing, and no arXiv record corroborate the report's "(ICLR Workshop)" attribution. The Planner/Generator/Evaluator role split it describes traces to Anthropic's harness-engineering posts, not to peer-reviewed literature. Treat as an engineering blog at most; do not cite as academic work anywhere. |

## Claims about already-verified work

| Report claim | Verdict | Correction |
|:--|:--|:--|
| ACRFence "eliminates 100% of Action Replay and Authority Resurrection attacks in 10/10 trials" | **Wrong, and the inversion is now proven from the paper's own text** | The full text states: "In our experiments, all 10 checkpoint-restore trials produced duplicate commits (100%), while a no-checkpoint baseline produced none (0/10)" (the attack succeeded every time) and, in the conclusion: "Our evaluation validates the attacks but does not yet include an implementation of ACRFence itself." The 10/10 figure is the attack success rate against an unprotected agent; the mitigation is proposed and explicitly unevaluated. Our README related-work entry already states this correctly and needs no change. |
| "Pydantic v3 primitives configured with extra=forbid" | **Wrong for this repo** | This repository pins `pydantic>=2.7` (v2). Any proposal text referencing v3 describes something we do not use. |
| "Over 60% of production agent incidents stem directly from flawed state persistence..." | **Unsourced** | No citation given in the report; nothing resembling this statistic appears in any abstract checked above. Treat as rhetoric. |
| Latency and recovery targets (<35 ms proxy overhead, <1.2 s recovery, 42x via shadow workers) | **Aspirational, not measurements** | The 42x figure is Belayer's, measured for RL training workers, not for anything in this repository. CONTINUUM publishes measured numbers only via `continuum benchmark` (see also #258 for extending what is measured). |

## Repository impact

- Grep on 2026-08-24 confirmed none of these IDs, "Pydantic v3", or the wrong
  ACRFence characterisation appeared anywhere under `*.md` before this audit.
  Nothing to correct; this file plus the two Related work additions are the
  complete change.
- ReliabilityBench and the transactional sandboxing paper were added to the
  README related-work list with abstract-backed descriptions.
- Belayer stays recorded here only (verified but out of scope).
- DAPH stays uncited everywhere: the only public artefact is a Medium post,
  and the report's ICLR workshop attribution has no supporting record.

## Bonus findings from the web pass

Two results worth keeping next to this audit because they touch active issues:

- **ACRFence's framework-issue survey** (Section 1 of the paper) documents
  tool-refire bugs across 12 frameworks (LangGraph 8+, CrewAI 5, Google ADK 4,
  AutoGen 3, OpenAI Agents 3, Claude Code 5, and others), plus a HashiCorp
  Vault issue where single-use tokens reappear after snapshot restore. This is
  independent, citable evidence for the replay problem #258 measures and the
  fork semantics #259 proposes.
- **The sandboxing paper's section 7.5** explicitly rules out its own scope for
  external stateful APIs ("an HTTP request cannot be un-sent via a filesystem
  snapshot") and names compensating transactions as future work. The external
  boundary is exactly the layer CONTINUUM's ledger already covers.
