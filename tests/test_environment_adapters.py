import pytest

from continuum.actions.ledger import ActionLedger
from continuum.adapters import (
    BrowserAdapter,
    ContainerAdapter,
    KubernetesAdapter,
    PythonInProcAdapter,
)
from continuum.models import Run
from continuum.storage import SQLiteStorage


def _storage() -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    storage.create_run(Run(run_id="run_1", goal="env"))
    return storage


def test_python_inproc_runs_and_records(tmp_path) -> None:
    adapter = PythonInProcAdapter(_storage(), str(tmp_path / "work"))
    result = adapter.run_python("run_1", "print('hi')")
    assert result.status == "completed"
    assert "hi" in result.output
    recorded = ActionLedger(adapter.storage, "run_1").all()
    assert recorded[0].action_type == "python"


def test_python_inproc_dep_scope(tmp_path) -> None:
    adapter = PythonInProcAdapter(_storage(), str(tmp_path / "work"))
    adapter.run_python("run_1", "pass", dep_scope="numpy")
    assert ActionLedger(adapter.storage, "run_1").all()[0].dep_scope == "numpy"


@pytest.mark.skipif(ContainerAdapter.available(), reason="docker present; run manually")
def test_container_adapter_imports_without_docker() -> None:
    adapter = ContainerAdapter(_storage(), "alpine:latest")
    assert adapter.available() is False
    with pytest.raises(RuntimeError):
        adapter.run_in_container("run_1", "echo hi")


@pytest.mark.skipif(BrowserAdapter.available(), reason="playwright present; run manually")
def test_browser_adapter_imports_without_playwright() -> None:
    adapter = BrowserAdapter(_storage())
    assert adapter.available() is False
    with pytest.raises(RuntimeError):
        adapter.navigate("run_1", "https://example.com")


@pytest.mark.skipif(KubernetesAdapter.available(), reason="kubectl/k8s present; run manually")
def test_k8s_adapter_imports_without_cluster() -> None:
    adapter = KubernetesAdapter(_storage())
    assert adapter.available() is False
    with pytest.raises(RuntimeError):
        adapter.run_job("run_1", "alpine", "echo hi")
