from __future__ import annotations

import contextlib

from continuum.actions.ledger import ActionLedger
from continuum.models import RecoveryMode
from continuum.testing import environment_fixture


def test_layout_is_deterministic() -> None:
    with (
        environment_fixture(dependencies=("dataset", "model")) as a,
        environment_fixture(dependencies=("dataset", "model")) as b,
    ):
        files_a = sorted(p.name for p in a.root.iterdir())
        files_b = sorted(p.name for p in b.root.iterdir())
        assert files_a == files_b
        verdicts_a = [
            (e.component_id, e.status) for e in a.engine.assess(a.run_id).validation.report.statuses
        ]
        verdicts_b = [
            (e.component_id, e.status) for e in b.engine.assess(b.run_id).validation.report.statuses
        ]
        assert verdicts_a == verdicts_b


def test_corruption_scopes_to_broken_dependency() -> None:
    with environment_fixture(dependencies=("dataset", "other")) as fx:
        decision = fx.engine.assess_scoped(
            fx.run_id, ["dataset"], current_environment=fx.capture(dataset="v2")
        )
        assert decision.mode is RecoveryMode.REPAIR_AND_RESUME
        other = decision.state.dependency("other")
        assert other is not None and other.status.value == "valid"


def test_injected_failure_records_uncertain_side_effect() -> None:
    from continuum.testing import InjectedFailures

    with environment_fixture(
        dependencies=("dataset",),
        failures=InjectedFailures(action_types=frozenset({"net.call"})),
    ) as fx:
        with contextlib.suppress(RuntimeError):
            fx.adapter.intercept_action(fx.run_id, "net.call", lambda: 1)
        pending = ActionLedger(fx.storage, fx.run_id).pending()
        assert len(pending) == 1
        decision = fx.engine.assess(fx.run_id, current_environment=fx.capture())
        assert decision.mode is not RecoveryMode.RESUME


def test_source_graph_sees_fixture_files() -> None:
    with environment_fixture(dependencies=("dataset", "pandas")) as fx:
        graph = fx.source_graph()
        owners = graph.files_using("dataset")
        assert len(owners) == 1 and owners.pop().name == "dataset_impl.py"
        assert graph.declared == {"dataset", "pandas"}
