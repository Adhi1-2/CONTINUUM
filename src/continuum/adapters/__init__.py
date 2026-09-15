"""Framework adapters for CONTINUUM.

Import-cost discipline (issue #214): this package is on the critical path of
every entry point, including processes that never touch a framework adapter
(the MCP server, and each ``continuum observe`` hook subprocess). The optional
SDK adapters pull in heavyweight dependencies (openai ~1.3s, langgraph ~0.8s
cumulative, measured with ``-X importtime``), so their names resolve lazily
through module ``__getattr__`` (PEP 562) and are only imported when actually
requested. The dependency-free adapters stay eager.

The public surface is unchanged: ``from continuum.adapters import X`` works
for every name in ``__all__``, because ``from`` imports fall back to
``__getattr__`` when module attribute lookup misses.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from continuum.adapters.actions import (
    AdapterAction,
    AdapterResult,
    run_action,
)
from continuum.adapters.base import AgentAdapter
from continuum.adapters.filesystem import FilesystemSandboxAdapter
from continuum.adapters.generic import GenericAgentAdapter
from continuum.adapters.python_inproc import PythonInProcAdapter
from continuum.adapters.registry import (
    AdapterRegistry,
    get_adapter,
    list_adapters,
    recover,
    register_adapter,
)

if TYPE_CHECKING:
    # Type-checkers see the real definitions; runtime resolves them lazily
    # through __getattr__ below.
    from continuum.adapters.browser import BrowserAdapter
    from continuum.adapters.container import ContainerAdapter
    from continuum.adapters.kubernetes import KubernetesAdapter
    from continuum.adapters.langchain import LangChainAgentAdapter, langchain_available
    from continuum.adapters.langgraph import LangGraphAgentAdapter, langgraph_available
    from continuum.adapters.openai import (
        ContinuumContext,
        OpenAIAgentAdapter,
        openai_agents_available,
    )

#: Lazy name -> submodule under :mod:`continuum.adapters`. Every name here is
#: provided by a module whose import pulls an optional third-party SDK.
_LAZY_EXPORTS: dict[str, str] = {
    "BrowserAdapter": "browser",
    "ContainerAdapter": "container",
    "KubernetesAdapter": "kubernetes",
    "LangChainAgentAdapter": "langchain",
    "langchain_available": "langchain",
    "LangGraphAgentAdapter": "langgraph",
    "langgraph_available": "langgraph",
    "ContinuumContext": "openai",
    "OpenAIAgentAdapter": "openai",
    "openai_agents_available": "openai",
}

__all__ = [
    "AdapterAction",
    "AdapterRegistry",
    "AdapterResult",
    "AgentAdapter",
    "BrowserAdapter",
    "ContainerAdapter",
    "ContinuumContext",
    "FilesystemSandboxAdapter",
    "GenericAgentAdapter",
    "KubernetesAdapter",
    "LangChainAgentAdapter",
    "LangGraphAgentAdapter",
    "OpenAIAgentAdapter",
    "PythonInProcAdapter",
    "get_adapter",
    "langchain_available",
    "langgraph_available",
    "list_adapters",
    "openai_agents_available",
    "recover",
    "register_adapter",
    "run_action",
]


def __getattr__(name: str) -> Any:
    """Resolve a lazy export on first access and cache it as a module attr."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f"{__name__}.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
