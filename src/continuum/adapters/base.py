"""Abstract AgentAdapter interface.

Spec Section 19: AgentAdapter interface:
- capture_state
- restore_state
- intercept_action
- resume
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from continuum.models import (
    EnvironmentSnapshot,
    SemanticState,
    StateCheckpoint,
)
from continuum.recovery.engine import RecoveryDecision

__all__ = ["AgentAdapter"]


class AgentAdapter(ABC):
    """Framework-agnostic adapter interface for AI agent loops.

    Adapters provide a high-level facade over CONTINUUM's storage, state,
    checkpointing, action ledger, and recovery engine.
    """

    @abstractmethod
    def capture_state(
        self,
        run_id: str,
        state: SemanticState,
        *,
        environment: EnvironmentSnapshot | None = None,
        reason: str = "",
    ) -> StateCheckpoint:
        """Create and store a semantic state checkpoint for a run."""

    @abstractmethod
    def restore_state(
        self,
        run_id: str,
        *,
        replay: bool = True,
    ) -> SemanticState:
        """Restore the latest semantic state for a run."""

    @abstractmethod
    def intercept_action(
        self,
        run_id: str,
        action_type: str,
        action_fn: Callable[[], Any],
        arguments: Mapping[str, Any] | None = None,
        *,
        volatile: Sequence[str] = (),
        scoped_to_run: bool = True,
        dep_scope: str | None = None,
    ) -> Any:
        """Intercept and safely execute an external action via the ActionLedger.

        Prevents duplicate side effects by checking the ledger prior to invoking
        action_fn. Returns the cached result on duplicate attempts without re-executing.

        ``dep_scope`` optionally tags the recorded ``Action`` with the dependency
        it belongs to, for later localized-repair scoping.
        """

    @abstractmethod
    def resume(
        self,
        run_id: str,
        *,
        current_environment: EnvironmentSnapshot | None = None,
        expected_model: str | None = None,
        replay: bool = True,
    ) -> RecoveryDecision:
        """Assess recovery safety and return a recovery decision for a run."""
