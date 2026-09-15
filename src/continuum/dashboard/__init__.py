"""Presentation layer for CONTINUUM runs (issue 13).

This is a thin view over the same data the CLI already renders. It does not
introduce new storage or recovery machinery.
"""

from continuum.dashboard.app import (
    render_dashboard_html,
    render_run_detail_html,
    serve_dashboard,
)

__all__ = ["render_dashboard_html", "render_run_detail_html", "serve_dashboard"]
