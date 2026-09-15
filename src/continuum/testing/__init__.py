"""Deterministic test and benchmark fixtures.

See :mod:`continuum.testing.fixtures` for the environment fixture factory used
by the Phase 6 scenarios and by tests that need a reproducible agent
environment with injectable failures.
"""

from continuum.testing.fixtures import (
    EnvironmentFixture,
    FixtureAdapter,
    InjectedFailures,
    environment_fixture,
)

__all__ = [
    "EnvironmentFixture",
    "FixtureAdapter",
    "InjectedFailures",
    "environment_fixture",
]
