from __future__ import annotations

from pathlib import Path

import pytest

from continuum.benchmark.phase6 import (
    RecoveryOutcome,
    run_benchmark,
    run_scenario,
    scenarios,
    write_report,
)
from continuum.benchmark.phase6.harness import ScenarioContext


@pytest.mark.parametrize(
    "name,fn", scenarios.ALL_SCENARIOS, ids=lambda v: v if isinstance(v, str) else ""
)
def test_phase6_scenario(name: str, fn) -> None:
    result = run_scenario(name, fn)
    assert result.passed is True, f"{name} failed: {result.notes}"
    assert result.outcome is RecoveryOutcome.PASS


def test_harness_records_failure() -> None:
    def boom(ctx: ScenarioContext) -> None:
        ctx.fail("invariant broken")

    result = run_scenario("boom", boom)
    assert result.passed is False
    assert result.outcome is RecoveryOutcome.FAIL


def test_harness_records_exception() -> None:
    def raises(ctx: ScenarioContext) -> None:
        raise ValueError("kaboom")

    result = run_scenario("raises", raises)
    assert result.passed is False
    assert any("ValueError" in n for n in result.notes)


def test_benchmark_report_and_summary(tmp_path: Path) -> None:
    report = run_benchmark([("ok", lambda ctx: None), ("ok2", lambda ctx: None)])
    assert report.summary() == {
        "total": 2,
        "passed": 2,
        "failed": 0,
        "by_outcome": {"pass": 2},
    }

    json_path, md_path = write_report(report, tmp_path / "report")
    assert json_path.exists() and md_path.exists()
    assert "Recovery benchmark report" in md_path.read_text(encoding="utf-8")
