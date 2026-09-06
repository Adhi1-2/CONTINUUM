"""Thin adapters for CrewAI, AutoGen and Pydantic AI (seam 1, #213 follow-up).

Each framework exposes a different interception surface, verified against
its real API:

- CrewAI: global ``before_tool_call`` / ``after_tool_call`` hook registry in
  ``crewai.hooks``; the before hook returns False to block.
- AutoGen (core): tools expose ``run_json(arguments, cancellation_token)``;
  wrapping that method intercepts every execution of an existing tool.
- Pydantic AI: a Hooks capability object with async ``before_tool_call(ctx,
  tool_name, args)`` / ``after_tool_call(ctx, tool_name, args, result,
  error=None)`` registered via ``Agent(capabilities=[...])``.

All three route through one shared engine: claim before, evidence after,
exactly-once under argument drift via optional stable keys. Provenance is
EXTERNAL_AGENT because the framework asserts the facts.

Framework imports stay lazy: importing this module costs nothing when a
framework is absent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from continuum.models import Origin

__all__ = [
    "crewai_available",
    "pydantic_ai_available",
    "autogen_available",
    "ContinuumToolGuard",
    "install_crewai_hooks",
    "wrap_autogen_tool",
    "wrap_pydantic_ai_hooks",
]


def _flag(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def crewai_available() -> bool:
    return _flag("crewai")


def pydantic_ai_available() -> bool:
    return _flag("pydantic_ai")


def autogen_available() -> bool:
    return _flag("autogen_core.tools")


class ContinuumToolGuard:
    """Shared claim/complete plumbing behind the thin adapters.

    Constructed per run over a Storage. ``claim`` opens a ledger slot before a
    side-effecting tool executes; ``complete``/``fail`` settle it. Keys default
    to the MCP-style derivation (tool type + argument hash + run scope); pass
    ``key_fn(tool_name, args) -> str`` for resource-identity keys.
    """

    def __init__(
        self,
        storage: Any,
        run_id: str,
        *,
        key_fn: Callable[[str, dict[str, Any]], str] | None = None,
        external_id_fn: Callable[[Any], str | None] | None = None,
        source: Origin = Origin.EXTERNAL_AGENT,
    ) -> None:
        """Bind the guard to a run.

        ``source`` stamps the ledger writes (issue #612): a framework
        executing tools asserts facts about its own work, so the
        default is ``EXTERNAL_AGENT`` and the run is held for review
        like any agent-reported work.
        """
        self._storage = storage
        self._run_id = run_id
        self._key_fn = key_fn
        self._external_id_fn = external_id_fn
        self._source = source
        self._ledger: Any | None = None
        self._seq = 0
        self._inflight: dict[int, str] = {}

    @property
    def ledger(self) -> Any:
        if self._ledger is None:
            from continuum.actions import ActionLedger

            self._ledger = ActionLedger(self._storage, self._run_id, source=self._source)
        return self._ledger

    def _args_dict(self, args: Any) -> dict[str, Any]:
        if isinstance(args, dict):
            return args
        if hasattr(args, "model_dump"):
            return dict(args.model_dump())
        if isinstance(args, str):
            return {"input": args}
        return {"value": args}

    def claim(self, tool_name: str, args: Any) -> int:
        args_dict = self._args_dict(args)
        key = str(self._key_fn(tool_name, args_dict)) if self._key_fn is not None else None
        outcome = self.ledger.claim(tool_name, args_dict, key=key)
        self._seq += 1
        token = self._seq
        self._inflight[token] = outcome.key
        return token

    def complete(self, token: int, result: Any = None) -> None:
        key = self._inflight.pop(token, None)
        if key is None:
            return
        external_id = self._external_id_fn(result) if self._external_id_fn else None
        self.ledger.complete(key, external_id=external_id)

    def fail(self, token: int, error: str, *, certain: bool = True) -> None:
        key = self._inflight.pop(token, None)
        if key is None:
            return
        self.ledger.fail(key, error, certain=certain)


def install_crewai_hooks(
    storage: Any,
    run_id: str,
    *,
    key_fn: Callable[[str, dict[str, Any]], str] | None = None,
    action_types: set[str] | None = None,
) -> Callable[[], None]:
    """Register global CrewAI before/after tool-call hooks. Returns uninstaller.

    Every registered tool call is claimed then completed through CONTINUUM.
    Calls whose tool name is not in ``action_types`` pass through untouched
    (pass ``None`` to track everything). Requires the ``crewai`` package;
    raises ImportError otherwise.
    """
    if not crewai_available():
        raise ImportError(
            "crewai is required for install_crewai_hooks. Install it (and "
            "CONTINUUM stays dependency-free)."
        )
    from crewai import hooks as crewai_hooks

    guard = ContinuumToolGuard(storage, run_id, key_fn=key_fn)
    tokens: dict[int, int] = {}

    def before(context: Any) -> bool | None:
        tool_name = getattr(context, "tool_name", "") or ""
        if action_types is not None and tool_name not in action_types:
            return None
        raw_args = getattr(context, "tool_input", {}) or {}
        token = guard.claim(tool_name, raw_args)
        tokens[id(context)] = token
        return None  # allow

    def after(context: Any) -> str | None:
        token = tokens.pop(id(context), None)
        if token is None:
            return None
        error = getattr(context, "error", None)
        result = getattr(context, "result", None)
        if error:
            guard.fail(token, str(error))
        else:
            guard.complete(token, result)
        return None  # keep original result

    crewai_hooks.register_before_tool_call_hook(before)
    crewai_hooks.register_after_tool_call_hook(after)

    def uninstall() -> None:
        try:
            crewai_hooks.unregister_before_tool_call_hook(before)
            crewai_hooks.unregister_after_tool_call_hook(after)
        except AttributeError:
            # Older crews: registry exposes clear-all only.
            crewai_hooks.clear_all_tool_call_hooks()

    return uninstall


def wrap_autogen_tool(
    tool: Any,
    storage: Any,
    run_id: str,
    *,
    key_fn: Callable[[str, dict[str, Any]], str] | None = None,
) -> Any:
    """Wrap an AutoGen FunctionTool's ``run_json`` with claim/complete.

    The returned object is the same tool instance: its execution entry point
    is replaced in place, so agent construction code does not change.
    """
    guard = ContinuumToolGuard(storage, run_id, key_fn=key_fn)
    original = tool.run_json

    def run_json_wrapped(args: Any, *rest: Any, **kwargs: Any) -> Any:
        args_dict = args if isinstance(args, dict) else {"value": args}
        token = guard.claim(getattr(tool, "name", "autogen_tool"), args_dict)
        try:
            result = original(args, *rest, **kwargs)
        except Exception as exc:
            guard.fail(token, str(exc))
            raise
        guard.complete(token, result)
        return result

    tool.run_json = run_json_wrapped
    return tool


def wrap_pydantic_ai_hooks(
    storage: Any,
    run_id: str,
    *,
    key_fn: Callable[[str, dict[str, Any]], str] | None = None,
) -> Any:
    """Return a Hooks-capability-shaped object for pydantic-ai Agents.

    Register it via ``Agent(model, capabilities=[wrap_pydantic_ai_hooks(ad)])``
    (or the hooks constructor argument of your installed version). The async
    signatures match the documented capability protocol.
    """
    guard = ContinuumToolGuard(storage, run_id, key_fn=key_fn)
    tokens: dict[int, int] = {}

    class ContinuumPydanticHooks:
        async def before_tool_call(self, ctx: Any, tool_name: str, args: Any) -> Any:
            token = guard.claim(tool_name, args)
            tokens[id(ctx)] = token
            return args

        async def after_tool_call(
            self, ctx: Any, tool_name: str, args: Any, result: Any, error: Any = None
        ) -> Any:
            token = tokens.pop(id(ctx), None)
            if token is None:
                return result
            if error:
                guard.fail(token, str(error))
            else:
                guard.complete(token, result)
            return result

    return ContinuumPydanticHooks()
