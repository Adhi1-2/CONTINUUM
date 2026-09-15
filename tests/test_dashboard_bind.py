"""Dashboard bind hardening (issue #270).

The dashboard renders recovery contracts with goals and side-effect details;
it must default to loopback like every other CONTINUUM server. Operators opt
into exposure explicitly via --host.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from continuum.dashboard.app import make_dashboard_server
from continuum.storage import SQLiteStorage


@pytest.fixture
def db(tmp_path: Path) -> str:
    return str(tmp_path / "bind.db")


def test_default_bind_is_loopback(db: str) -> None:
    server = make_dashboard_server(SQLiteStorage(db))
    host, port = server.server_address[:2]
    assert host == "127.0.0.1"
    assert isinstance(port, int) and port > 0
    server.server_close()


def test_explicit_zero_conf_host_is_honoured(db: str) -> None:
    server = make_dashboard_server(SQLiteStorage(db), host="0.0.0.0")
    assert server.server_address[0] in ("0.0.0.0", "")
    server.server_close()


def test_handler_serves_the_dashboard_over_a_real_socket(db: str) -> None:
    """End-to-end smoke through the constructed server: GET / renders."""
    import threading

    server = make_dashboard_server(SQLiteStorage(db), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, port = server.server_address[:2]
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
        body = resp.read().decode()
        assert resp.status == 200
        assert "CONTINUUM Dashboard" in body
    finally:
        server.shutdown()
        server.server_close()


def test_an_unknown_run_answers_404_not_200(db: str) -> None:
    """A run nobody wrote to must not answer 200 over HTTP.

    The body already said "Run not found", but the status line said OK, and the
    status is what anything other than a human reads. The CLI holds the same
    line: `test_no_command_reports_success_for_a_run_that_does_not_exist` exists
    so a typo'd run name never looks like a clean bill of health, and the
    dashboard is that same claim over a different transport.
    """
    import threading
    import urllib.error

    from continuum.events import EventType
    from continuum.models import Run

    storage = SQLiteStorage(db)
    storage.create_run(Run(run_id="real_run", goal="g"))
    storage.append_event("real_run", EventType.RUN_STARTED, {"goal": "g"})

    server = make_dashboard_server(storage, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, port = server.server_address[:2]
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/runs/real_run", timeout=5)
        assert resp.status == 200

        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/runs/definitely-not-a-run", timeout=5)
        assert caught.value.code == 404
        assert "Run not found" in caught.value.read().decode()
    finally:
        server.shutdown()
        server.server_close()
