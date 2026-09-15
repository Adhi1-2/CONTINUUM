"""Phase 6 recovery-correctness scenario suite (additive to CONTINUUM-Bench)."""

from continuum.benchmark.phase6 import scenarios
from continuum.benchmark.phase6.harness import (
    ScenarioContext,
    run_benchmark,
    run_scenario,
    write_report,
)
from continuum.benchmark.phase6.metrics import (
    BenchmarkReport,
    RecoveryOutcome,
    ScenarioResult,
)

__all__ = [
    "BenchmarkReport",
    "RecoveryOutcome",
    "ScenarioContext",
    "ScenarioResult",
    "run_benchmark",
    "run_scenario",
    "scenarios",
    "write_report",
]
