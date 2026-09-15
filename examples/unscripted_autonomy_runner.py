"""Runner for the unscripted autonomy test (issue 6).

This is not a scripted test. It sets up a concrete long running task and
prints the exact command a human should run with an independent LLM agent CLI
(such as claude) that is already configured to use continuum-mcp. The agent is
told to use CONTINUUM for safe resumption but is not given a step by step
script. The human then records whether the agent autonomously called
continuum_checkpoint and continuum_resume and whether it respected the response.

Usage:
  uv run python examples/unscripted_autonomy_runner.py
  # Follow the printed instructions with your agent CLI
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from continuum.events import EventType
from continuum.models import Run
from continuum.storage import SQLiteStorage


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "unscripted.db"
        storage = SQLiteStorage(str(db))
        run_id = "unscripted_demo"
        storage.create_run(
            Run(
                run_id=run_id,
                goal="Write a five part guide to Git, about 150 words each, with one command example per part.",
            )
        )
        storage.append_event(
            run_id, EventType.RUN_STARTED, {"goal": "Write a five part guide to Git", "total": 5}
        )
        storage.close()

        print("=== Unscripted autonomy test ===")
        print(f"Database: {db}")
        print(f"Run: {run_id}")
        print()
        print("Instructions for the human observer:")
        print(
            "1. Ensure continuum-mcp is registered in .mcp.json with your client name allowlisted."
        )
        print("2. Launch your agent CLI in this project directory, for example: claude")
        print("3. Give the agent this task verbatim, with no extra steps:")
        print(
            "   Write a five part guide to Git. Sections: (1) What Git is, (2) The basic workflow, (3) Branches, (4) Merging and conflicts, (5) Remotes. About 150 words each, with one command example per part. Use CONTINUUM for safe resumption if you are interrupted."
        )
        print("4. After two sections, kill the agent terminal hard (kill -9).")
        print(
            "5. Launch a fresh agent session in the same directory and send any message, for example: hi"
        )
        print(
            "6. Observe whether the fresh agent calls continuum_resume without being told the run_id, and whether it calls continuum_checkpoint after each section."
        )
        print()
        print(
            "Record the sequence of tool calls and whether resume was respected. Then file the observation in issue 6."
        )
        print()
        print("To verify the stored run without an agent:")
        print(f"  continuum --db {db} inspect {run_id}")
        print(f"  continuum --db {db} resume {run_id}")
        _ = os.environ.get("CONTINUUM_MCP_MUTATING_CLIENTS", "")


if __name__ == "__main__":
    main()
