"""Guard the documented pytest counts against silent drift (#630).

README.md, docs/CONTRIBUTING_ONBOARDING.md, and CHANGELOG.md each state the
collected total. The guard asserts the three files agree with each other and
with a live ``pytest --collect-only`` within tolerance. Skips vary by
environment, so only collected totals are compared, never passed/skipped
splits. Regenerate the figures with ``pytest --collect-only -q; pytest -q``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNTED_FILES = (
    ROOT / "README.md",
    ROOT / "docs" / "CONTRIBUTING_ONBOARDING.md",
    ROOT / "CHANGELOG.md",
)
# Small PRs move the total by a handful of tests; doc rot moves it by the
# hundreds (#316: exact, #630: 135). Tolerance 30 splits the difference.
TOLERANCE = 30

_COLLECTED_RE = re.compile(r"~([\d,]+)`?\s+collected")


def documented_total(path: Path) -> int:
    matches = _COLLECTED_RE.findall(path.read_text(encoding="utf-8"))
    assert matches, f"{path.name} states no ~N collected figure"
    totals = {int(m.replace(",", "")) for m in matches}
    assert len(totals) == 1, f"{path.name} states inconsistent figures: {sorted(totals)}"
    return totals.pop()


def live_total() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"collect-only failed:\n{proc.stderr[-2000:]}"
    match = re.search(r"(\d[\d,]*)\s+tests? collected", proc.stdout)
    assert match, f"unparsable collect-only output:\n{proc.stdout[-500:]}"
    return int(match.group(1).replace(",", ""))


def test_documented_counts_agree() -> None:
    totals = {f.name: documented_total(f) for f in COUNTED_FILES}
    assert len(set(totals.values())) == 1, f"documented counts disagree: {totals}"


def test_documented_count_matches_suite() -> None:
    documented = documented_total(COUNTED_FILES[0])
    live = live_total()
    assert abs(live - documented) <= TOLERANCE, (
        f"suite collects {live} tests but docs say ~{documented}: "
        "re-sync README.md, docs/CONTRIBUTING_ONBOARDING.md, and CHANGELOG.md "
        "(pytest --collect-only -q; pytest -q)"
    )
