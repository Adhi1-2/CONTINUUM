"""The ``continuum tui`` driver: a full-screen terminal dashboard (issue #782).

Two layers, deliberately separated:

- :class:`TuiApp` is a pure state machine. Keys arrive as plain names
  (``"up"``, ``"enter"``, ``"y"``) and rendering is a list of strings, so
  every flow is testable without a terminal.
- :func:`run_tui` is a thin curses driver that maps real key codes onto those
  names and draws the lines. It contains no decisions of its own.

House style: read-only until an action is confirmed. Every mutating verb
lands in ``pending`` first, the footer shows exactly what will happen, and
only a further ``y`` performs the write. Anything else cancels.
"""

from __future__ import annotations

import contextlib
import os
import sys
import textwrap
from collections.abc import Callable
from typing import Any

from continuum import __version__
from continuum.cli.exitcodes import ExitCode
from continuum.storage.base import Storage
from continuum.tui import animate, model
from continuum.tui.model import RunRow

__all__ = ["TuiApp", "run_tui"]

#: The splash logo, drawn in the mono9 figlet style: solid block and half-block
#: glyphs at 63 columns, so it centres in a standard 80-column terminal with a
#: real margin on each side and no drop-shadow row to blur the letterforms.
_LOGO_LINES = (
    "   ▄▄▄   ▄▄▄▄  ▄▄   ▄▄▄▄▄▄▄▄ ▄▄▄▄▄  ▄▄   ▄ ▄    ▄ ▄    ▄ ▄    ▄",
    " ▄▀   ▀ ▄▀  ▀▄ █▀▄  █   █      █    █▀▄  █ █    █ █    █ ██  ██",
    " █      █    █ █ █▄ █   █      █    █ █▄ █ █    █ █    █ █ ██ █",
    " █      █    █ █  █ █   █      █    █  █ █ █    █ █    █ █ ▀▀ █",
    "  ▀▄▄▄▀  █▄▄█  █   ██   █    ▄▄█▄▄  █   ██ ▀▄▄▄▄▀ ▀▄▄▄▄▀ █    █",
)
_LOGO_WIDTH = max(len(line) for line in _LOGO_LINES)
_LANDING_TAGLINE = "durable recovery for long-running agents"
_LANDING_PROMPT = "press any key to open the dashboard"


def _logo_spans(frame: int, *, pad: int, art_width: int) -> list[tuple[int, int, int]]:
    """Emphasis for one line of the splash art: the logo's accent colour with
    the sheen band swept across it.

    The spans are returned non-overlapping and in order, so the driver writes
    each exactly once. The band is never wider than the art, so it can only
    recolour glyphs that are already there.
    """
    band = animate.shimmer_span(frame, art_width=art_width)
    if band is None:  # settled, or the art is too narrow to sweep
        return [(pad, pad + art_width, animate.ACCENT)]
    start, end = pad + band[0], pad + band[1]
    return [
        span
        for span in (
            (pad, start, animate.ACCENT),
            (start, end, animate.BRIGHT),
            (end, pad + art_width, animate.ACCENT),
        )
        if span[0] < span[1]
    ]


class TuiApp:
    """State machine for the terminal dashboard.

    The view is three-level: a landing splash (logo, version, run count),
    then a runs index, then one run's detail with tabs (overview, recovery,
    checkpoints, actions, events, family, budget). Table tabs carry a
    selectable cursor; text tabs only scroll.
    """

    TABS = ("overview", "recovery", "checkpoints", "actions", "events", "family", "budget")
    _TABLE_TABS = frozenset({"checkpoints", "actions", "events", "budget"})

    def __init__(self, storage: Storage | None, *, database_error: str | None = None) -> None:
        self.storage = storage
        self.database_error = database_error
        self.view = "landing"
        self.width = 80  # the driver restamps this from the real screen each draw
        self.tab = 0
        self.index = 0  # selection in the runs index
        self.cursor = -1  # selected body line in a table tab, -1 when none
        self.scroll = 0
        self.rows: list[RunRow] = []
        self._run_count: int | None = None
        self._read_error = ""  # set when a store that opened cannot be read
        self.lines: list[str] = []
        # the actions tab as last drawn, indexed like the lines below the
        # header; _selected_action reads this rather than the store, so a row
        # inserted after the render cannot move the key a keypress settles
        self._action_rows: list[model.ActionRow] = []
        self.pending: tuple[str, Callable[[], str]] | None = None
        self.message = ""
        self.show_help = False
        # The animation clock. SETTLED means the splash holds still: either
        # CONTINUUM_NO_ANIMATION asked for that, or the frame simply is not
        # moving yet. Only the landing view reads it (see _landing_render).
        self.frame = animate.SETTLED if not animate.animation_enabled() else 0
        self.refresh()

    # ------------------------------------------------------------------ #
    # data
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        """Reload the current view's data from storage. Read-only.

        Every read is guarded: a store that opens but cannot be read (deleted
        out of band, corrupted) must degrade to a message, never escape the
        app as a traceback. A broken store is exactly when the operator
        reaches for the dashboard.
        """
        if self.storage is None:
            self.rows = []
            self.lines = [
                "Database unavailable.",
                self.database_error or "No compatible database was opened.",
                "Run `continuum --db <compatible-database> tui` to open run data.",
            ]
            return
        if self.view == "landing":
            # the splash only needs a count; assessing recovery for every run
            # on every tick would price the idle screen at a full store scan
            self.rows = []
            self.cursor = -1
            try:
                self._run_count = self._count_runs()
                self._read_error = ""  # a recovered count must retire the old error
            except Exception as exc:
                self._run_count = None
                self._read_error = f"cannot count runs: {exc}"
        elif self.view == "runs":
            try:
                self.rows = model.run_rows(self.storage)
                self._read_error = ""
            except Exception as exc:
                self.rows = []
                self._read_error = f"cannot list runs: {exc}"
            self.cursor = -1  # the runs list marks its selection itself
            if self.rows:
                self.index = min(self.index, len(self.rows) - 1)
        else:
            self._refresh_detail()

    def _count_runs(self) -> int:
        """Count runs without assessing recovery for each: a bare list read."""
        assert self.storage is not None
        return len(self.storage.list_runs())

    def tick(self) -> None:
        """Advance the animation frame.

        Touches nothing else, on purpose. The landing screen redraws roughly
        eleven times a second, so a tick that also read storage would price the
        idle splash at eleven store scans a second; the driver re-reads the run
        count on a separate throttle. A settled clock never starts: that is how
        ``CONTINUUM_NO_ANIMATION`` keeps the splash static.
        """
        if self.frame >= 0:
            self.frame += 1

    def _run_id(self) -> str | None:
        if 0 <= self.index < len(self.rows):
            return self.rows[self.index].run_id
        return None

    def _refresh_detail(self) -> None:
        storage = self.storage
        if storage is None:
            self.lines = [
                "Database unavailable; run data cannot be opened.",
                self.database_error or "No compatible database was opened.",
            ]
            self.cursor = -1
            return
        run_id = self._run_id()
        if run_id is None:
            self.lines = ["No run selected. Press esc to go back to the runs list."]
            self.cursor = -1
            return
        tab = self.TABS[self.tab]
        # Preserve the selected row across a refresh: an auto-refresh tick
        # must not silently move the cursor while the operator reads the
        # footer, or a keypress settles a different action than parked on.
        # The actions tab is sorted by key, so a row inserted since the last
        # render shifts the list and a numeric restore would park the
        # highlight on a different key; that tab is restored by key. Table
        # tabs only otherwise: a text tab owns the scroll, not a selection, so
        # restoring a table tab's cursor onto it would light a phantom
        # highlight and flip navigation into cursor mode.
        preserved = self.cursor
        preserved_key = (
            self._action_rows[preserved - 1].key
            if tab == "actions" and 1 <= preserved <= len(self._action_rows)
            else None
        )
        self.cursor = -1
        self._action_rows = []
        try:
            self._render_detail_tab(storage, run_id, tab)
        except Exception as exc:
            # One unreadable run must fail this view alone, never the process:
            # the dashboard is the operator's window into a broken store, so
            # crashing here would hide the very thing being investigated.
            self.lines = [
                f"Cannot read run {run_id} ({tab} tab): {exc}",
                "",
                "The rest of the dashboard is unaffected. Press esc for the runs list,",
                "r to retry, or run `continuum verify` outside the dashboard for detail.",
            ]
        else:
            if tab == "actions" and preserved_key is not None:
                # the parked key, wherever it has moved to; -1 if it has gone,
                # which is how a settled action retires its own highlight
                self.cursor = next(
                    (
                        index
                        for index, row in enumerate(self._action_rows, start=1)
                        if row.key == preserved_key
                    ),
                    -1,
                )
            elif tab in self._TABLE_TABS and 1 <= preserved < len(self.lines):
                self.cursor = preserved
        if self.scroll > max(0, len(self.lines) - 1):
            self.scroll = 0

    def _render_detail_tab(self, storage: Storage, run_id: str, tab: str) -> None:
        """Fill self.lines for one tab. Raises on an unreadable run; the
        caller renders the failure instead of letting it escape the app."""
        rows: list[Any]  # one of the model's table row types, per tab
        if tab == "overview":
            self.lines = model.overview_lines(storage, run_id)
        elif tab == "recovery":
            self.lines = model.recovery_lines(storage, run_id)
        elif tab == "checkpoints":
            rows = model.checkpoint_rows(storage, run_id)
            self.lines = [f"{'CHECKPOINT':<12} {'VERSION':<9} {'TRIGGER':<10} COMPLETED"]
            self.lines += [
                f"{r.checkpoint_id[:10]:<12} v{r.version:<8} {r.trigger:<10} {r.completed}"
                for r in rows
            ] or ["No checkpoints recorded. Press c to force one."]
            self.cursor = 1 if len(self.lines) > 1 else -1
        elif tab == "actions":
            self._action_rows = model.action_rows(storage, run_id)
            self.lines = [f"{'STATUS':<16} {'TYPE':<24} {'EXTERNAL ID':<20} KEY"]
            self.lines += [
                (
                    f"{'(!)' if r.uncertain else '   '} {r.status:<12} {r.action_type:<24} "
                    f"{r.external_id[:18]:<20} {r.key[:24]}"
                )
                for r in self._action_rows
            ] or ["No actions recorded."]
            self.cursor = 1 if len(self.lines) > 1 else -1
        elif tab == "events":
            rows = model.event_rows(storage, run_id)
            self.lines = [f"{'SEQ':>5}  {'TYPE':<26} PAYLOAD"]
            self.lines += [f"{r.sequence:>5}  {r.type:<26} {r.summary}" for r in rows] or [
                "No events."
            ]
            self.cursor = 1 if len(self.lines) > 1 else -1
        elif tab == "family":
            self.lines = model.family_lines(storage, run_id)
        elif tab == "budget":
            rows = model.budget_rows(storage, run_id)
            self.lines = [f"{'ACTION TYPE':<28} {'ATTEMPTS':>8} {'MAX':>4} {'REMAINING':>10}"]
            self.lines += [
                f"{r.action_type:<28} {r.attempts:>8} {r.max_attempts:>4} {r.remaining:>10}"
                for r in rows
            ] or ["No action attempts recorded."]
            self.cursor = 1 if len(self.lines) > 1 else -1

    def _selected_action(self) -> model.ActionRow | None:
        """The action row under the cursor, on the actions tab only.

        Read from the snapshot the tab drew, never from the store: the actions
        tab is sorted by key, so an action arriving between the render and the
        keypress would shift the list and make `y` settle the row one line
        away from the one the highlight marks. The snapshot is what is on
        screen, so the key reconciled is the key parked on.
        """
        if (
            self.view != "detail"
            or self.TABS[self.tab] != "actions"
            or not 1 <= self.cursor <= len(self._action_rows)
        ):
            return None
        return self._action_rows[self.cursor - 1]

    # ------------------------------------------------------------------ #
    # rendering
    # ------------------------------------------------------------------ #

    def header(self) -> str:
        """The top line: where we are and what view is active."""
        if self.view == "landing":
            return ""
        if self.view == "runs":
            return "CONTINUUM  runs  (enter: open, r: refresh, ?: help, q: quit)"
        run_id = self._run_id() or "-"
        tab = self.TABS[self.tab]
        return (
            f"CONTINUUM  run {run_id}  "
            f"[{self.tab + 1}/{len(self.TABS)} {tab}]  "
            "(left/right or 1-7: tabs, esc: runs, r: refresh, q: quit)"
        )

    def _landing_render(self) -> tuple[list[str], list[list[tuple[int, int, int]]]]:
        """The splash page and its emphasis, built together so the two cannot
        drift apart.

        Line for line this is the old static layout; the only textual change is
        the tagline typing itself in. Everything else is emphasis (the logo's
        accent colour, a sheen sweeping it, a prompt that breathes) layered over
        text that is already complete, so any single frame of the splash,
        including the first, already holds the whole logo. The animation never
        hides a glyph, which also keeps a snapshot of the screen readable.
        """

        def center(text: str) -> tuple[str, int]:
            pad = max(0, (self.width - len(text)) // 2)
            return " " * pad + text, pad

        if self.width >= _LOGO_WIDTH + 2:
            art: list[str] = list(_LOGO_LINES)
        else:  # too narrow for the logo: a banner that fits, not one that clips
            art = ["C O N T I N U U M"]
        art_width = len(art[0])

        typed = animate.typewriter(_LANDING_TAGLINE, self.frame)
        # the cursor blinks only while there is still something to type, and
        # only when the line has room for it. A splash wider than the terminal
        # is clipped by the driver, but the line itself must not overflow
        typing = len(typed) < len(_LANDING_TAGLINE)
        show_cursor = typing and animate.cursor_visible(self.frame)
        tagline = typed + ("▋" if show_cursor else "")
        if len(tagline) > self.width:
            tagline = typed

        count = self._run_count or 0
        runs_line = f"{count} run(s) recorded" if count else "no runs recorded yet"
        if self._read_error:  # the store opened but could not even be counted
            runs_line = self._read_error

        lines: list[str] = [""]
        attrs: list[list[tuple[int, int, int]]] = [[]]
        for raw in art:
            line, pad = center(raw)
            lines.append(line)
            attrs.append(_logo_spans(self.frame, pad=pad, art_width=art_width))
        lines += ["", ""]
        attrs += [[], []]

        tag_line, tag_pad = center(tagline)
        lines.append(tag_line)
        tag_attrs: list[tuple[int, int, int]] = []
        if typed:
            tag_attrs.append((tag_pad, tag_pad + len(typed), animate.ACCENT))
        if show_cursor:
            # the cursor is the last glyph of the line and stands alone
            tag_attrs.append((tag_pad + len(typed), tag_pad + len(tagline), animate.BRIGHT))
        attrs.append(tag_attrs)

        version_line, _ = center(f"v{__version__}   {runs_line}")
        lines.append(version_line)
        attrs.append([])

        if self.database_error:
            for wrapped in textwrap.wrap(
                f"database unavailable: {self.database_error}",
                width=max(1, self.width),
            ):
                centered, _ = center(wrapped)
                lines.append(centered)
                attrs.append([])

        prompt_line, prompt_pad = center(_LANDING_PROMPT)
        lines += ["", "", prompt_line]
        attrs += [
            [],
            [],
            [(prompt_pad, prompt_pad + len(_LANDING_PROMPT), animate.pulse(self.frame))],
        ]
        return lines, attrs

    def _landing_lines(self) -> list[str]:
        """The splash page: logo, version, how many runs the store holds."""
        return self._landing_render()[0]
        status: list[str] = []
        if self.database_error:
            status = [
                center(line)
                for line in textwrap.wrap(
                    f"database unavailable: {self.database_error}",
                    width=max(1, self.width),
                )
            ]
        return (
            [""]
            + [center(line) for line in art]
            + ["", ""]
            + [center(_LANDING_TAGLINE), center(f"v{__version__}   {runs_line}")]
            + status
            + ["", "", center("press any key to open the dashboard")]
        )

    def body_lines(self) -> list[str]:
        """The body: the help overlay when asked for, the view otherwise."""
        if self.show_help:
            return list(_HELP_LINES)
        if self.view == "landing":
            return self._landing_lines()
        if self.view == "runs":
            if self.storage is None:
                return [
                    "Database unavailable; no runs can be displayed.",
                    self.database_error or "No compatible database was opened.",
                    "Use --db with a compatible database, then run `continuum tui`.",
                ]
            if self._read_error:
                return [
                    f"Cannot read runs from this store: {self._read_error}",
                    "",
                    "Press r to retry, or q to quit and investigate outside the dashboard.",
                ]
            if not self.rows:
                return ['No runs recorded. Start one with: continuum start <id> --goal "..."']
            lines = [f"{'RUN':<20} {'STATUS':<10} {'EVT':>5}  {'MODE':<14} {'SAFE':<7} GOAL"]
            for i, row in enumerate(self.rows):
                marker = ">" if i == self.index else " "
                lines.append(
                    f"{marker} {row.run_id:<19} {row.status:<10} {row.events:>5}  "
                    f"{row.mode:<14} {row.safe:<7} {row.goal}"
                )
            return lines
        marked = []
        for i, line in enumerate(self.lines):
            if self.cursor >= 0:
                marked.append(("> " if i == self.cursor else "  ") + line)
            else:
                marked.append(line)
        return marked

    def body_attrs(self) -> list[list[tuple[int, int, int]]]:
        """Per-body-line emphasis: one list of ``(start, end, flags)`` spans,
        sorted and non-overlapping, indexed like :meth:`body_lines`.

        Empty on every view but the landing splash. The dashboard is an
        instrument: its lines hold still, and an empty list is the signal the
        driver writes each line exactly as it did before animation existed.
        """
        if self.view != "landing":
            return []
        return self._landing_render()[1]

    def footer_attr(self) -> int:
        """Emphasis for the footer. The splash's prompt breathes; every other
        view is plain, so the footer's wording stays exactly as tested."""
        if self.view == "landing":
            return animate.pulse(self.frame)
        return animate.PLAIN

    def footer(self) -> str:
        """The bottom line: a pending confirmation outranks everything, and a
        result message outranks the key hints. The hints are the longest text
        here, so on an 80-column terminal they would otherwise clip the one
        line the operator most needs to read."""
        if self.pending is not None:
            return f"{self.pending[0]}  [y = do it, anything else = cancel]"
        if self.message:
            return self.message
        if self.show_help:
            return "? hides help"
        if self.view == "landing":
            return "press any key to open the dashboard   q quits"
        if self.view == "runs":
            return "runs: enter open | r refresh | c checkpoint | x complete | y confirm"
        return "1-7 tabs | esc back | y/n reconcile (actions tab) | c checkpoint | x complete"

    # ------------------------------------------------------------------ #
    # keys
    # ------------------------------------------------------------------ #

    def handle_key(self, key: str) -> bool:
        """Apply one key. Returns False when the app should quit."""
        if self.pending is not None:
            self._resolve_pending(key)
            return True
        if key == "q":
            return False
        if self.view == "landing":
            if key == "resize":  # a resize is not a keystroke: keep the splash
                return True
            # the splash promises "press any key", so any key (except q above)
            # opens the dashboard; there is nothing else to do on this screen
            self.view = "runs"
            self.message = ""
            self.scroll = 0
            self.refresh()
            return True
        if key == "?":
            self.show_help = not self.show_help
            return True
        if key == "r":
            self.refresh()
            return True
        if self.show_help:
            return True  # any other key just leaves help on screen

        if self.view == "runs":
            return self._handle_runs_key(key)
        return self._handle_detail_key(key)

    def _resolve_pending(self, key: str) -> None:
        assert self.pending is not None
        prompt, action = self.pending
        if key == "y":
            try:
                self.message = action()
            except Exception as exc:
                self.message = f"error: {exc}"
        else:
            self.message = f"cancelled: {prompt.split('?')[0].strip()}"
        self.pending = None
        self.refresh()

    def _handle_runs_key(self, key: str) -> bool:
        if key in ("up", "k") and self.rows:
            self.index = (self.index - 1) % len(self.rows)
        elif key in ("down", "j") and self.rows:
            self.index = (self.index + 1) % len(self.rows)
        elif key in ("enter", "o", "right") and self.rows:
            self.view = "detail"
            self.tab = 0
            self.scroll = 0
            self.message = ""
            self._refresh_detail()
        elif key == "c":
            self._queue_checkpoint()
        elif key == "x":
            self._queue_complete()
        elif key == "y":
            self._queue_confirm()
        return True

    def _handle_detail_key(self, key: str) -> bool:
        tab = self.TABS[self.tab]
        if key == "esc" or (key == "left" and tab == "overview"):
            self.view = "runs"
            self.scroll = 0
            self.message = ""
            self.refresh()
        elif key in ("left", "h"):
            self.tab = (self.tab - 1) % len(self.TABS)
            self.scroll = 0
            self._refresh_detail()
        elif key in ("right", "l"):
            self.tab = (self.tab + 1) % len(self.TABS)
            self.scroll = 0
            self._refresh_detail()
        elif key.isdigit() and 1 <= int(key) <= len(self.TABS):
            self.tab = int(key) - 1
            self.scroll = 0
            self._refresh_detail()
        elif key in ("up", "k"):
            self._move_selection(-1)
        elif key in ("down", "j"):
            self._move_selection(1)
        elif key == "c":
            self._queue_checkpoint()
        elif key == "x":
            self._queue_complete()
        elif key == "y":
            if tab == "actions":
                self._queue_reconcile(occurred=True)
            else:
                self._queue_confirm()
        elif key == "n":
            if tab == "actions":
                self._queue_reconcile(occurred=False)
        return True

    def _move_selection(self, delta: int) -> None:
        """Move the cursor in table tabs, the scroll in text tabs."""
        if self.cursor >= 0:
            self.cursor = max(1, min(len(self.lines) - 1, self.cursor + delta))
        else:
            self.scroll = max(0, min(max(0, len(self.lines) - 1), self.scroll + delta))

    def _queue_checkpoint(self) -> None:
        run_id = self._run_id()
        storage = self.storage
        if run_id is None or storage is None:
            self.message = "no run selected"
            return
        self.pending = (
            f"force a checkpoint on run {run_id} now?",
            lambda: model.force_checkpoint(storage, run_id),
        )

    def _queue_complete(self) -> None:
        run_id = self._run_id()
        storage = self.storage
        if run_id is None or storage is None:
            self.message = "no run selected"
            return
        self.pending = (
            f"close run {run_id} as completed? (REVIEW_CONFIRMED + RUN_COMPLETED)",
            lambda: model.complete_run(storage, run_id),
        )

    def _queue_confirm(self) -> None:
        run_id = self._run_id()
        storage = self.storage
        if run_id is None or storage is None:
            self.message = "no run selected"
            return
        self.pending = (
            f"confirm the self-reported goal and progress of run {run_id}? (REVIEW_CONFIRMED)",
            lambda: model.confirm_state(storage, run_id),
        )

    def _queue_reconcile(self, *, occurred: bool) -> None:
        run_id = self._run_id()
        row = self._selected_action()
        storage = self.storage
        if run_id is None or storage is None or row is None:
            self.message = "select an action row first (cursor is on the actions list)"
            return
        if not row.uncertain:
            self.message = f"{row.key[:24]} is {row.status}; only uncertain actions can be settled"
            return
        self.pending = (
            f"settle {row.action_type} on run {run_id} as "
            f"{'OCCURRED' if occurred else 'NOT OCCURRED'}? (ACTION_RECONCILED)",
            lambda: model.reconcile_action(storage, run_id, row.key, occurred=occurred),
        )


_HELP_LINES = [
    "CONTINUUM tui keys",
    "",
    "  q            quit                     r        refresh the view",
    "  ?            toggle this help",
    "",
    "  runs list:   up/down or j/k move      enter/o  open the run",
    "               y confirm state          c        force a checkpoint",
    "               x complete the run",
    "",
    "  run detail:  left/right or 1-7 tabs   esc      back to the runs list",
    "               up/down move or scroll   y        confirm state",
    "               c force a checkpoint     x        complete the run",
    "  actions tab: y settle as occurred     n        settle as not occurred",
    "",
    "  Every mutating key asks first: the footer shows the exact write and",
    "  only a further y performs it. Anything else cancels. Reads never",
    "  write: refreshing and browsing are always safe on a live run.",
]


# --------------------------------------------------------------------------- #
# curses driver
# --------------------------------------------------------------------------- #


def _key_name(curses: Any, ch: int) -> str | None:
    """Map one curses key code to the plain name TuiApp understands."""
    special = {
        curses.KEY_UP: "up",
        curses.KEY_DOWN: "down",
        curses.KEY_LEFT: "left",
        curses.KEY_RIGHT: "right",
        curses.KEY_ENTER: "enter",
        curses.KEY_RESIZE: "resize",
        curses.KEY_BACKSPACE: "esc",
        10: "enter",
        13: "enter",
        27: "esc",
    }
    if ch in special:
        return special[ch]
    if 0 < ch < 256:
        return chr(ch)
    return None


def _addline(screen: Any, y: int, x: int, text: str, attr: int = 0) -> None:
    """Write one line, clipping to the screen. The bottom-right cell raises
    on a full write, so failures are swallowed: a clipped dashboard beats a
    dead one."""
    with contextlib.suppress(Exception):
        screen.addnstr(y, x, text, screen.getmaxyx()[1] - x - 1, attr)


#: Colour pair ids the TUI owns. The cursor highlight is a plain attribute
#: rather than a pair, so these are the only two in use.
_ACCENT_PAIR = 1  # the logo
_BRIGHT_PAIR = 2  # the sheen band and the breathing prompt


def _colour_pairs(curses: Any) -> dict[int, int]:
    """Map emphasis flags to colour pairs, or ``{}`` when colour is off.

    Colour is opt-in exactly the way the CLI's ``Palette`` makes it opt-in: off
    for ``NO_COLOR``, off for ``TERM=dumb``, off on a terminal that reports no
    support, and off if any colour call raises. Empty means the driver falls
    back to bold and dim, never to a traceback, and never to different text.
    """
    if os.environ.get("NO_COLOR") is not None:
        return {}
    if os.environ.get("TERM", "").lower() == "dumb":
        return {}
    probes = (
        getattr(curses, name, None)
        for name in ("has_colors", "start_color", "init_pair", "color_pair")
    )
    if not all(callable(probe) for probe in probes):
        return {}  # a curses stub, as in the tests: monochrome, not a crash
    try:
        if not curses.has_colors():
            return {}
        curses.start_color()
        curses.init_pair(_ACCENT_PAIR, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(_BRIGHT_PAIR, curses.COLOR_WHITE, curses.COLOR_BLACK)
        bold = getattr(curses, "A_BOLD", 0) or 0
        dim = getattr(curses, "A_DIM", 0) or 0
        return {
            animate.ACCENT: curses.color_pair(_ACCENT_PAIR),
            animate.BRIGHT: curses.color_pair(_BRIGHT_PAIR) | bold,
            animate.DIM: curses.color_pair(_BRIGHT_PAIR) | dim,
        }
    except Exception:
        return {}


def _emphasis_table(curses: Any) -> Callable[[int], int]:
    """Resolve emphasis flags to a single curses attribute.

    Colour pairs are used where the terminal offers them; otherwise bold and
    dim carry the same emphasis monochrome. Overlapping flags OR together, so
    a band sweeping an accent-coloured logo can brighten it further.
    """
    colours = _colour_pairs(curses)
    dim = getattr(curses, "A_DIM", 0) or 0
    bold = getattr(curses, "A_BOLD", 0) or 0

    def resolve(flags: int) -> int:
        attr = 0
        if flags & animate.BRIGHT:
            attr |= colours.get(animate.BRIGHT, bold)
        if flags & animate.DIM:
            attr |= colours.get(animate.DIM, dim)
        if flags & animate.ACCENT:
            attr |= colours.get(animate.ACCENT, bold)
        return attr

    return resolve


def _runs(
    line: str, spans: list[tuple[int, int, int]], resolve: Callable[[int], int]
) -> list[tuple[int, int, int]]:
    """Partition a line into ``(start, end, attr)`` runs covering every column.

    Neighbouring runs that resolve to the same attribute are merged, so a line
    whose spans all carry one emphasis, or a terminal that cannot tell them
    apart, is written as a single call. The text on screen is then identical
    to the pre-animation path, which is what keeps the drawn output readable
    by anything that records lines rather than attributes.
    """
    cell = [0] * len(line)
    for start, end, flags in spans:
        for i in range(max(0, start), min(len(line), end)):
            cell[i] |= flags
    runs: list[list[int]] = []
    for i, flags in enumerate(cell):
        attr = resolve(flags)
        if runs and runs[-1][2] == attr:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, attr])
    return [(start, end, attr) for start, end, attr in runs]


def _write_line(
    screen: Any,
    y: int,
    line: str,
    spans: list[tuple[int, int, int]],
    base: int,
    resolve: Callable[[int], int],
) -> None:
    """Write one line with its emphasis, or plain when there is none.

    A line held by the selection cursor keeps the reverse-video highlight it
    always had; emphasis never competes with the thing that marks the row an
    operator is about to act on.
    """
    if base or not spans:
        _addline(screen, y, 0, line, base)
        return
    for start, end, attr in _runs(line, spans, resolve):
        _addline(screen, y, start, line[start:end], attr)


#: How often the idle splash re-reads its run count when --refresh is unset.
_LANDING_DATA_PERIOD = 2.0


def _driver(curses: Any, screen: Any, app: TuiApp, refresh_seconds: float) -> int:
    """Draw, wait for one key, repeat.

    Two clocks. On the landing screen the timeout is the animation interval, so
    the splash moves even with ``--refresh 0`` (the default, where the rest of
    the dashboard blocks until a key arrives). Each tick advances the frame,
    and the run count is re-read on a throttle so the idle screen stays cheap.
    Everywhere else the timeout is exactly what it was: a refresh tick, or a
    blocking wait.
    """
    screen.keypad(True)
    # terminals without a cursor control still render fine
    with contextlib.suppress(Exception):
        curses.curs_set(0)
    resolve = _emphasis_table(curses)
    dashboard_timeout = int(refresh_seconds * 1000) if refresh_seconds > 0 else -1
    landing_timeout = int(animate.TICK_SECONDS * 1000)
    # ticks between run-count reads; with --refresh set, this keeps the same
    # wall-clock cadence the dashboard always had
    data_every = max(1, round((refresh_seconds or _LANDING_DATA_PERIOD) / animate.TICK_SECONDS))
    ticks = 0

    while True:
        screen.erase()
        height, width = screen.getmaxyx()
        app.width = width  # the landing screen centres the logo on this
        on_landing = app.view == "landing"
        screen.timeout(landing_timeout if on_landing else dashboard_timeout)
        _addline(screen, 0, 0, app.header(), curses.A_BOLD)
        body = app.body_lines()
        attrs = app.body_attrs() if on_landing else []
        available = max(1, height - 3)
        if app.cursor >= 0:  # keep the selected line inside the window
            if app.cursor < app.scroll:
                app.scroll = app.cursor
            if app.cursor >= app.scroll + available:
                app.scroll = app.cursor - available + 1
        start = min(app.scroll, max(0, len(body) - available))
        for row, line in enumerate(body[start : start + available], start=start):
            attr = curses.A_REVERSE if row == app.cursor else 0
            spans = attrs[row] if 0 <= row < len(attrs) else []
            _write_line(screen, row - start + 2, line, spans, attr, resolve)
        _addline(screen, height - 1, 0, app.footer(), resolve(app.footer_attr()))
        screen.refresh()

        ch = screen.getch()
        if ch == -1:  # an animation or refresh tick, not a keystroke
            if on_landing:
                app.tick()
                ticks += 1
                if ticks % data_every == 0:
                    app.refresh()
            else:
                app.refresh()
            continue
        key = _key_name(curses, ch)
        if key is None:
            continue
        if not app.handle_key(key):
            return ExitCode.OK


def run_tui(
    storage: Storage | None,
    *,
    refresh_seconds: float = 0.0,
    database_error: str | None = None,
    err: Any = None,
) -> int:
    """Open the full-screen dashboard; returns a process exit status.

    Refuses rather than half-rendering when curses is unavailable or stdout
    is not a terminal: the recovery data on screen deserves a whole screen,
    and a mangled scrape of one is worse than a clear refusal pointing at
    ``continuum dashboard``.
    """
    err = err if err is not None else sys.stderr
    try:
        import curses
    except ImportError:
        print(
            "error: the curses module is not available on this platform; "
            "use `continuum dashboard` for the browser dashboard",
            file=err,
        )
        return ExitCode.ERROR
    if not sys.stdout.isatty():
        print(
            "error: continuum tui needs an interactive terminal; stdout is not a TTY",
            file=err,
        )
        return ExitCode.ERROR
    try:
        return _run(curses, storage, refresh_seconds, database_error)
    except curses.error as exc:  # a terminal too small or too alien for curses
        print("error: this terminal cannot run the tui:", exc, file=err)
        print("use `continuum dashboard` for the browser dashboard", file=err)
        return ExitCode.ERROR


def _run(
    curses: Any,
    storage: Storage | None,
    refresh_seconds: float,
    database_error: str | None = None,
) -> int:
    """Enter curses mode; the wrapper restores the terminal on the way out."""

    def inner(screen: Any) -> int:
        return _driver(
            curses,
            screen,
            TuiApp(storage, database_error=database_error),
            refresh_seconds,
        )

    result: int = curses.wrapper(inner)
    return result
