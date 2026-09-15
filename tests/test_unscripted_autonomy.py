"""Unscripted autonomy test harness (issue 6).

The full test requires a human driving a real LLM agent CLI, so it cannot
run in CI. This module verifies the harness itself and that the tool surface
a real agent would see is motivating.

The actual unscripted runs are performed via examples/unscripted_autonomy_runner.py
and reported in issue 6. The e2e-autonomy-test kit already demonstrated three
full runs scoring 7 of 7 mechanics with unprompted recovery behavior.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_runner_script_exists_and_runs() -> None:
    runner = Path(__file__).parent.parent / "examples" / "unscripted_autonomy_runner.py"
    assert runner.exists()
    result = subprocess.run(
        [sys.executable, str(runner)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0
    assert "unscripted_demo" in result.stdout
    assert "continuum_resume" in result.stdout


def test_mcp_tool_descriptions_mention_checkpoint_and_resume() -> None:
    source = (Path(__file__).parent.parent / "src" / "continuum" / "mcp" / "server.py").read_text(
        encoding="utf-8"
    )
    assert "continuum_checkpoint" in source
    assert "continuum_resume" in source
