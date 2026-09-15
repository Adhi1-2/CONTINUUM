"""Guards examples/recovery_walkthrough.py against rot (issue #174).

The walkthrough doc embeds this script's output verbatim, so the script must
keep running and keep hitting the same decision points.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).parent.parent / "examples" / "recovery_walkthrough.py"


def test_walkthrough_example_runs_end_to_end() -> None:
    result = subprocess.run(
        [sys.executable, str(EXAMPLE)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Recovery decision: REQUEST_HUMAN" in out
    assert "reason:" in out
    assert "resolved_failed: 1  unresolved: 0" in out
    assert "Recovery decision: REPAIR_AND_RESUME" in out
    assert "Next permitted action: revalidate_dependency:dataset" in out
