"""Tests for OpenAPI 3.1 specification and wire conformance suite.

Verifies:
- OpenAPI 3.1 schema definition and export
- Live HTTP serving of /openapi.json and /healthz
- Continuum-Version: 1.0 header on responses
- Automated run_wire_conformance suite passing against authenticated and unauthenticated servers
- CLI entry point for continuum conformance --url <endpoint>
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from continuum.serve import (
    SidecarAuth,
    SidecarServer,
    get_openapi_spec,
)
from continuum.serve.conformance import (
    WireConformanceReport,
    WireTestResult,
    _http_call,
    cmd_conformance,
    run_wire_conformance,
)
from continuum.serve.server import SidecarHTTP
from continuum.storage import SQLiteStorage

_PORT_COUNTER = {"port": 9600}


def _next_port() -> int:
    _PORT_COUNTER["port"] += 1
    return _PORT_COUNTER["port"]


def test_openapi_spec_structure() -> None:
    spec = get_openapi_spec()
    assert spec["openapi"] == "3.1.0"
    assert "info" in spec
    assert spec["info"]["version"] == "1.0.0"

    paths = spec["paths"]
    assert "/intercept_action" in paths
    assert "/complete_action" in paths
    assert "/fail_action" in paths
    assert "/record_progress" in paths
    assert "/checkpoint" in paths
    assert "/validate" in paths
    assert "/resume" in paths
    assert "/confirm" in paths
    assert "/list_actions" in paths
    assert "/healthz" in paths
    assert "/openapi.json" in paths

    components = spec["components"]
    assert "BearerAuth" in components["securitySchemes"]
    assert "ConfirmAuth" in components["securitySchemes"]
    assert "InterceptActionRequest" in components["schemas"]
    assert "InterceptActionResponse" in components["schemas"]
    assert "ErrorResponse" in components["schemas"]


def test_sidecar_serves_openapi_and_healthz(tmp_path: Path) -> None:
    db = str(tmp_path / "openapi_test.db")
    storage = SQLiteStorage(db)
    sidecar = SidecarServer(storage=storage)
    port = _next_port()
    http = SidecarHTTP(sidecar, port=port, bind="127.0.0.1")
    http.start_background()

    try:
        import json
        import urllib.request

        base = f"http://127.0.0.1:{http.port}"

        # GET /healthz
        with urllib.request.urlopen(f"{base}/healthz", timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Continuum-Version") == "1.0"
            body = json.loads(resp.read().decode("utf-8"))
            assert body["status"] == "ok"

        # GET /openapi.json
        with urllib.request.urlopen(f"{base}/openapi.json", timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Continuum-Version") == "1.0"
            spec = json.loads(resp.read().decode("utf-8"))
            assert spec["openapi"] == "3.1.0"
            assert "/intercept_action" in spec["paths"]
    finally:
        http.shutdown()


def test_wire_conformance_unauthenticated_server(tmp_path: Path) -> None:
    db = str(tmp_path / "conf_unauth.db")
    storage = SQLiteStorage(db)
    sidecar = SidecarServer(storage=storage)
    port = _next_port()
    http = SidecarHTTP(sidecar, port=port, bind="127.0.0.1")
    http.start_background()

    try:
        report = run_wire_conformance(f"http://127.0.0.1:{http.port}")
        rendered = report.render()
        assert "All" in rendered
        assert "conformance tests passed" in rendered
        assert report.passed is True
        assert report.failed_count == 0
    finally:
        http.shutdown()


def test_wire_conformance_authenticated_server(tmp_path: Path) -> None:
    db = str(tmp_path / "conf_auth.db")
    storage = SQLiteStorage(db)
    auth = SidecarAuth(
        client_tokens=["test-client-token"],
        confirm_token="test-confirm-token",
    )
    sidecar = SidecarServer(storage=storage, auth=auth)
    port = _next_port()
    http = SidecarHTTP(sidecar, port=port, bind="127.0.0.1")
    http.start_background()

    try:
        report = run_wire_conformance(
            f"http://127.0.0.1:{http.port}",
            client_token="test-client-token",
            confirm_token="test-confirm-token",
        )
        assert "All" in report.render()
        assert report.passed is True
        assert report.failed_count == 0
        assert report.passed_count >= 10
    finally:
        http.shutdown()


def test_cli_conformance_command(tmp_path: Path) -> None:
    db = str(tmp_path / "conf_cli.db")
    storage = SQLiteStorage(db)
    auth = SidecarAuth(
        client_tokens=["client-tok"],
        confirm_token="confirm-tok",
    )
    sidecar = SidecarServer(storage=storage, auth=auth)
    port = _next_port()
    http = SidecarHTTP(sidecar, port=port, bind="127.0.0.1")
    http.start_background()

    try:
        from continuum.cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "conformance",
                "--url",
                f"http://127.0.0.1:{http.port}",
                "--token",
                "client-tok",
                "--confirm-token",
                "confirm-tok",
            ]
        )

        out = io.StringIO()
        err = io.StringIO()
        code = cmd_conformance(args, out=out, err=err)
        assert code == 0
        output = out.getvalue()
        assert "All" in output
        assert "conformance tests passed" in output
    finally:
        http.shutdown()


# --- report rendering and error handling, without a server (#1840) --------------- #


def test_render_names_every_failure_and_the_totals() -> None:
    """The failing branch prints per-test FAIL lines and the aggregate sentence."""
    report = WireConformanceReport(
        target_url="http://127.0.0.1:8765",
        results=[
            WireTestResult("health_check", True),
            WireTestResult("wire_version_header", False, "missing Continuum-Version header"),
            WireTestResult("openapi_spec", False, ""),
        ],
    )

    rendered = report.render()
    lines = rendered.splitlines()

    assert "CONTINUUM Wire Conformance Suite" in lines
    assert "Target: http://127.0.0.1:8765" in lines
    assert "  [PASS] health_check" in lines
    # A detail renders after a colon; an absent one renders no trailing colon.
    assert "  [FAIL] wire_version_header: missing Continuum-Version header" in lines
    assert "  [FAIL] openapi_spec" in lines
    assert "FAILED: 2 of 3 conformance tests failed." in lines
    assert report.passed is False
    assert (report.passed_count, report.failed_count) == (1, 2)


def test_render_of_a_passing_report_names_the_passing_total() -> None:
    report = WireConformanceReport(
        target_url="http://127.0.0.1:8765",
        results=[WireTestResult("health_check", True), WireTestResult("openapi_spec", True)],
    )

    assert report.passed is True
    assert "All 2 conformance tests passed." in report.render().splitlines()


def _http_error(code: int, reason: str, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:8765/healthz",
        code,
        reason,
        {},
        BytesIO(body),
    )


def test_http_call_falls_back_to_the_raw_body_when_an_error_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-JSON error body becomes ``{"raw": ...}`` rather than a JSON traceback."""

    def _refuse(req: urllib.request.Request, timeout: float | None = None) -> object:
        raise _http_error(503, "Service Unavailable", b"<html>upstream is down</html>")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)

    status, parsed, headers = _http_call("http://127.0.0.1:8765/healthz")

    assert status == 503
    assert parsed == {"raw": "<html>upstream is down</html>"}
    assert headers == {}


def test_http_call_parses_a_json_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def _json_error(req: urllib.request.Request, timeout: float | None = None) -> object:
        raise _http_error(
            429,
            "Too Many Requests",
            b'{"error": "rate limited", "code": 429}',
        )

    monkeypatch.setattr(urllib.request, "urlopen", _json_error)

    status, parsed, _ = _http_call("http://127.0.0.1:8765/healthz")

    assert status == 429
    assert parsed == {"error": "rate limited", "code": 429}


def test_http_call_reads_an_empty_error_body_as_no_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty body is a valid empty JSON document, not a decode failure."""

    def _empty(req: urllib.request.Request, timeout: float | None = None) -> object:
        raise _http_error(404, "Not Found", b"")

    monkeypatch.setattr(urllib.request, "urlopen", _empty)

    status, parsed, _ = _http_call("http://127.0.0.1:8765/healthz")

    assert status == 404
    assert parsed == {}


def test_cmd_conformance_returns_one_when_the_suite_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing report is an error exit, and the summary reaches the operator."""
    import continuum.serve.conformance as conformance

    failing = WireConformanceReport(
        target_url="http://127.0.0.1:8765",
        results=[WireTestResult("health_check", False, "connection refused")],
    )
    monkeypatch.setattr(conformance, "run_wire_conformance", lambda *a, **k: failing)

    out, err = io.StringIO(), io.StringIO()
    code = cmd_conformance(
        SimpleNamespace(url="http://127.0.0.1:8765", token=None, confirm_token=None),
        out=out,
        err=err,
    )

    assert code == 1
    assert "FAILED: 1 of 1 conformance tests failed." in out.getvalue()


def test_run_wire_conformance_reports_a_missing_version_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that omits Continuum-Version fails that check by name (#1840)."""
    import continuum.serve.conformance as conformance

    def _no_version_header(
        url: str,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict, dict[str, str]]:
        return 200, {"status": "ok", "openapi": "3.1.0"}, {}

    monkeypatch.setattr(conformance, "_http_call", _no_version_header)

    report = run_wire_conformance("http://127.0.0.1:8765")

    by_name = {r.name: r for r in report.results}
    assert by_name["health_check"].passed is True
    assert by_name["wire_version_header"].passed is False
    assert by_name["wire_version_header"].detail == "missing Continuum-Version header"


def test_run_wire_conformance_reports_an_unreachable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that cannot be reached fails each check it touches, by reason (#1840)."""
    import continuum.serve.conformance as conformance

    calls = {"n": 0}

    def _refuse_then_recover(
        url: str,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict, dict[str, str]]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection refused")
        return 200, {"status": "ok", "openapi": "3.1.0"}, {"Continuum-Version": "1.0"}

    monkeypatch.setattr(conformance, "_http_call", _refuse_then_recover)

    report = run_wire_conformance("http://127.0.0.1:8765")

    by_name = {r.name: r for r in report.results}
    assert by_name["health_check"].passed is False
    assert "connection refused" in by_name["health_check"].detail
