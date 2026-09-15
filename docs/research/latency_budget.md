# Recovery Latency Budget

Recovery must be fast enough to not dominate the agent loop but thorough enough to be correct. This note derives a budget from measured support points.

## Measured points

- **Source graph build** (`benchmarks/graph_build_overhead.py`): 50 files in 2.2 ms, 100 files in 3.8 ms, 200 files in 9.2 ms. Linear in file count, well below a scheduler quantum.

- **Assessment of 100 dependencies** (`src/continuum/benchmark/phase6/scenarios.py: large_state_recovery_latency`): `engine.assess` completes well under a second and the test asserts under 1s. The validator and planner walk the state once.

- **Full suite** (`uv run pytest`): 1020 tests in about 32 seconds on CI, including hypothesis fuzzing. Per test median is well under 100 ms.

## Budget formula

Propose:

```
budget_ms = 50 + 0.2 * files + 5 * decision_count
```

- `50 ms` base covers transaction open and ledger verify.
- `0.2 ms` per file covers import scanning.
- `5 ms` per decision covers provenance and validation propagation.

For a typical run with 100 files and 10 decisions the budget is `50 + 20 + 50 = 120 ms`. For a large run with 200 files and 100 dependencies the budget is `50 + 40 + 500 = 590 ms`, still under the 1 second assertion in the large state scenario.

## Guidance

- Keep `engine.assess` read only so it can be called often without cost of a write.
- Cache the source graph when it is passed as `source_graph` to `assess_scoped`. The graph is immutable for a given root, so the 0.2 ms per file cost is paid once.
- Budget is a soft SLO for the harness, not a hard timeout. The opt in `run_with_limits` in `src/continuum/recovery/limits.py:1` can enforce a hard cap when a caller needs it.

Reproduce support points with `uv run python benchmarks/graph_build_overhead.py` and `uv run pytest tests/test_phase6.py::test_large_state_recovery_latency -v`.

No external claims are made. The curve is from the repo's own fixtures on the CI runner at this writing.
