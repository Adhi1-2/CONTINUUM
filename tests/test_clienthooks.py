"""Tests for the observe hook's payload extraction (``continuum.clienthooks``).

Covers the durable facts a PostToolUse hook extracts from the host's report:
tool name, file path, size and digest, and the optional correlation
identifier that links the observation to a ledger action attempt (#785).

The correlation branches exist on a trust boundary. The hook process runs
inside the host (Claude Code, Cursor, ...), so every value it forwards is
agent-controlled. The hook's own contract forbids it from failing the host
tool call, so a malformed identifier is dropped while the observation is
still recorded, and the strict check stays on the explicit CLI flag (review
of #1680, and the coverage request on #1875).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from continuum.clienthooks import observe_event_payload


def test_valid_correlation_id_is_carried_into_the_observation() -> None:
    """A valid top-level correlation ID is forwarded to the event payload."""
    payload = observe_event_payload(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "make"},
            "correlation_id": "attempt-1",
        }
    )
    assert payload["correlation_id"] == "attempt-1"
    assert payload["tool"] == "Bash"


def test_correlation_id_nested_in_tool_input_is_carried() -> None:
    """A correlation ID nested in ``tool_input`` is read when the top level has none.

    Hosts pass the agent's tool arguments as ``tool_input`` and do not reserve
    a top-level slot for a correlation ID, so the nested read is the path the
    feature actually takes in production.
    """
    payload = observe_event_payload(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "x.py",
                "correlation_id": "attempt-2",
            },
        }
    )
    assert payload["correlation_id"] == "attempt-2"


def test_top_level_correlation_id_wins_over_a_nested_one() -> None:
    """A top-level ID is preferred; the nested one is only a fallback."""
    payload = observe_event_payload(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "make", "correlation_id": "nested"},
            "correlation_id": "top-level",
        }
    )
    assert payload["correlation_id"] == "top-level"


@pytest.mark.parametrize("bad", [42, "", "   ", "x" * 129])
def test_malformed_correlation_id_is_dropped_not_fatal(bad: object) -> None:
    """A malformed agent-supplied ID is dropped while the observation survives.

    The hook must never fail the host tool call (its own contract), and the
    observation is durable evidence regardless of linkage quality.
    """
    payload = observe_event_payload(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "x.py", "correlation_id": bad},
        }
    )
    assert "correlation_id" not in payload
    assert payload["tool"] == "Write"


def test_observation_without_any_correlation_id_is_unchanged() -> None:
    """An observation with no identifier anywhere is recorded uncorrelated."""
    payload = observe_event_payload({"tool_name": "Write", "tool_input": {"file_path": "x.py"}})
    assert "correlation_id" not in payload
    assert payload["tool"] == "Write"


def test_non_mapping_tool_input_is_not_searched_for_a_correlation_id() -> None:
    """A ``tool_input`` that is not a mapping is skipped, not crashed on."""
    payload = observe_event_payload({"tool_name": "Bash", "tool_input": "make"})
    assert "correlation_id" not in payload
    assert payload["tool"] == "Bash"


def test_file_facts_are_still_recorded_alongside_a_correlation_id(
    tmp_path: Path,
) -> None:
    """Linking an observation does not displace the durable file facts.

    Size and digest describe what is on disk and are what recovery reads; the
    correlation ID is additive metadata on the same event.
    """
    written = tmp_path / "built.log"
    written.write_bytes(b"correlated build output" * 4)
    payload = observe_event_payload(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(written),
                "correlation_id": "attempt-3",
            },
        }
    )
    assert payload["correlation_id"] == "attempt-3"
    assert payload["path"] == str(written)
    assert payload["bytes"] == len(b"correlated build output" * 4)
    assert payload["sha256"] == hashlib.sha256(b"correlated build output" * 4).hexdigest()


# --- installers, settings validation, and removal guards (#2050) ----------------- #
#
# The settings file is hand-edited configuration, so every malformed-shape path
# must fail loudly (install) or decline quietly (remove) -- never edit a file the
# module could not parse, and never lose the entries someone else wrote.


def test_a_snapshot_failure_never_fails_the_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Snapshotting may fail; the hook must still record the file facts.

    The hook runs inside the host tool call, so it is forbidden from failing it:
    a broken snapshot is swallowed, leaving size and digest intact."""
    written = tmp_path / "built.log"
    written.write_bytes(b"snapshot me")

    def _boom(path: object, sha256: object) -> None:
        raise RuntimeError("snapshot store unavailable")

    monkeypatch.setattr("continuum.environment.file_snapshot.snapshot_file", _boom, raising=False)
    payload = observe_event_payload(
        {"tool_name": "Write", "tool_input": {"file_path": str(written)}}
    )

    assert payload["bytes"] == len(b"snapshot me")
    assert payload["sha256"] == hashlib.sha256(b"snapshot me").hexdigest()


def test_install_claude_code_hook_maps_each_kind_to_its_event_and_matcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wrapper is the one place a kind name becomes the client's own event
    and matcher vocabulary, so every kind is pinned against the profile."""
    import json as json_module

    from continuum.clienthooks import CLIENT_PROFILES, install_claude_code_hook

    profile = CLIENT_PROFILES["claude-code"]
    monkeypatch.setenv("PATH", str(tmp_path))
    # A command whose kind word lets _installed_kind recognise each install.
    observed: dict[str, object] = {}
    for kind in ("observe", "gate", "briefing", "precompact"):
        settings = tmp_path / f"settings-{kind}.json"
        command = f"/opt/continuum/bin/continuum {kind}"
        status = install_claude_code_hook(settings, command, kind=kind)
        assert status == "installed"
        observed[kind] = json_module.loads(settings.read_text())["hooks"]

    assert next(iter(observed["observe"])) == profile["post_event"]
    assert observed["observe"][profile["post_event"]][0]["matcher"] == profile["write_matcher"]
    assert next(iter(observed["gate"])) == profile["pre_event"]
    assert observed["gate"][profile["pre_event"]][0]["matcher"] == profile["any_matcher"]
    assert next(iter(observed["briefing"])) == profile["start_event"]
    assert observed["briefing"][profile["start_event"]][0]["matcher"] == ""
    assert next(iter(observed["precompact"])) == profile["compact_event"]


def test_install_claude_code_hook_keeps_a_callers_custom_matcher(tmp_path: Path) -> None:
    import json as json_module

    from continuum.clienthooks import install_claude_code_hook

    settings = tmp_path / "settings.json"
    install_claude_code_hook(
        settings, "/opt/continuum/bin/continuum gate", kind="gate", matcher="Bash"
    )

    hooks = json_module.loads(settings.read_text())["hooks"]
    assert hooks["PreToolUse"][0]["matcher"] == "Bash"


def test_install_claude_code_hook_with_an_unknown_kind_defaults_to_the_pre_event(
    tmp_path: Path,
) -> None:
    import json as json_module

    from continuum.clienthooks import install_claude_code_hook

    settings = tmp_path / "settings.json"
    install_claude_code_hook(settings, "/opt/continuum/bin/continuum gate", kind="unsettled")

    assert list(json_module.loads(settings.read_text())["hooks"]) == ["PreToolUse"]


def test_install_refuses_an_unreadable_settings_file(tmp_path: Path) -> None:
    from continuum.clienthooks import install_client_hook

    settings = tmp_path / "settings.json"
    settings.write_text("{not valid json")

    with pytest.raises(ValueError, match="is not valid JSON"):
        install_client_hook(settings, "continuum observe", event_name="PostToolUse", matcher="*")


def test_install_refuses_a_settings_file_without_a_json_object(tmp_path: Path) -> None:
    from continuum.clienthooks import install_client_hook

    settings = tmp_path / "settings.json"
    settings.write_text('["not", "an", "object"]')

    with pytest.raises(ValueError, match="does not contain a JSON object"):
        install_client_hook(settings, "continuum observe", event_name="PostToolUse", matcher="*")


def test_install_refuses_a_settings_file_whose_hooks_key_is_not_an_object(
    tmp_path: Path,
) -> None:
    from continuum.clienthooks import install_client_hook

    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": "not-an-object"}')

    with pytest.raises(ValueError, match="'hooks' is not an object"):
        install_client_hook(settings, "continuum observe", event_name="PostToolUse", matcher="*")


def test_install_refuses_a_settings_file_whose_event_list_is_not_a_list(
    tmp_path: Path,
) -> None:
    from continuum.clienthooks import install_client_hook

    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {"PostToolUse": {"matcher": "*"}}}')

    with pytest.raises(ValueError, match="'hooks.PostToolUse' is not a list"):
        install_client_hook(settings, "continuum observe", event_name="PostToolUse", matcher="*")


def test_install_ignores_malformed_groups_and_entries_rather_than_failing(
    tmp_path: Path,
) -> None:
    """A hand-edited hook list can hold shapes the installer never writes; the
    scan must step over them and still install alongside."""
    import json as json_module

    from continuum.clienthooks import install_client_hook

    settings = tmp_path / "settings.json"
    settings.write_text(
        json_module.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        "a bare string is not a group",
                        {"matcher": "*", "hooks": "entries-are-not-a-list"},
                        {"matcher": "Other", "hooks": [{"type": "command", "command": "true"}]},
                    ]
                }
            }
        )
    )

    status = install_client_hook(
        settings, "continuum observe", event_name="PostToolUse", matcher="*"
    )

    assert status == "installed"
    groups = json_module.loads(settings.read_text())["hooks"]["PostToolUse"]
    dict_groups = [g for g in groups if isinstance(g, dict)]
    # The malformed "*" group is not repaired in place; a new well-formed one
    # is appended alongside it.
    ours = next(g for g in dict_groups if g["matcher"] == "*" and isinstance(g["hooks"], list))
    assert ours["hooks"] == [{"type": "command", "command": "continuum observe"}]
    assert {"matcher": "*", "hooks": "entries-are-not-a-list"} in groups
    # The stranger's group on another matcher survived untouched.
    other = next(g for g in dict_groups if g["matcher"] == "Other")
    assert other["hooks"] == [{"type": "command", "command": "true"}]
    # And the malformed entries were left in place, not repaired or dropped.
    assert "a bare string is not a group" in groups


def test_remove_refuses_an_unreadable_settings_file(tmp_path: Path) -> None:
    from continuum.clienthooks import remove_claude_code_hook

    settings = tmp_path / "settings.json"
    settings.write_text("{not valid json")

    with pytest.raises(ValueError, match="is not valid JSON"):
        remove_claude_code_hook(settings)


def test_remove_returns_false_when_the_settings_are_not_an_object(tmp_path: Path) -> None:
    from continuum.clienthooks import remove_claude_code_hook

    settings = tmp_path / "settings.json"
    settings.write_text('["not", "an", "object"]')
    assert remove_claude_code_hook(settings) is False


def test_remove_returns_false_when_hooks_is_not_an_object(tmp_path: Path) -> None:
    from continuum.clienthooks import remove_claude_code_hook

    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": "not-an-object"}')
    assert remove_claude_code_hook(settings) is False


def test_remove_returns_false_when_nothing_was_ours(tmp_path: Path) -> None:
    import json as json_module

    from continuum.clienthooks import remove_claude_code_hook

    settings = tmp_path / "settings.json"
    body = {"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [{"command": "true"}]}]}}
    settings.write_text(json_module.dumps(body))

    assert remove_claude_code_hook(settings) is False
    assert settings.read_text() == json_module.dumps(body)


def test_remove_skips_malformed_event_lists_and_preserves_non_group_entries(
    tmp_path: Path,
) -> None:
    """The scan cannot assume the file was written by this module: non-list
    event arrays are skipped and groups that hold nothing recognisable are
    preserved exactly."""
    import json as json_module

    from continuum.clienthooks import remove_claude_code_hook

    settings = tmp_path / "settings.json"
    body: dict[str, object] = {
        "hooks": {
            "PostToolUse": ["a bare string", {"hooks": {"not": "a list"}}, 42],
            "PreToolUse": [{"matcher": "*", "hooks": [{"command": "true"}]}],
        }
    }
    settings.write_text(json_module.dumps(body))

    assert remove_claude_code_hook(settings) is False
    # Nothing was ours, so the file is byte-identical, malformed entries and all.
    assert json_module.loads(settings.read_text()) == body


def test_removing_every_hook_from_an_event_drops_the_event_key(tmp_path: Path) -> None:
    import json as json_module

    from continuum.clienthooks import install_claude_code_hook, remove_claude_code_hook

    settings = tmp_path / "settings.json"
    command = "/opt/continuum/bin/continuum observe"
    install_claude_code_hook(settings, command, kind="observe")

    assert remove_claude_code_hook(settings) is True
    assert "hooks" not in json_module.loads(settings.read_text())


# --- command recognisers: the shapes removal trusts -------------------------------- #


def test_a_non_string_command_is_never_managed() -> None:
    from continuum.clienthooks import _is_managed_hook

    assert _is_managed_hook({"command": 7}, "observe") is False
    assert _is_managed_hook({}, "observe") is False


def test_an_unbalanced_command_is_never_managed() -> None:
    """A command shlex cannot split is unrecognisable, so it is left alone."""
    from continuum.clienthooks import _is_managed_hook

    assert _is_managed_hook({"command": "continuum observe 'unbalanced"}, "observe") is False


def test_a_non_string_command_is_never_a_legacy_precompact_hook() -> None:
    from continuum.clienthooks import _is_legacy_precompact_hook

    assert _is_legacy_precompact_hook({"command": 7}) is False


def test_an_unbalanced_legacy_precompact_command_is_never_recognised() -> None:
    from continuum.clienthooks import _is_legacy_precompact_hook

    command = "continuum --config precompact-resume.json 'unbalanced"
    assert _is_legacy_precompact_hook({"command": command}) is False


def test_the_observe_recogniser_matches_only_observe_hooks() -> None:
    from continuum.clienthooks import _is_observe_hook

    assert _is_observe_hook({"type": "command", "command": "continuum observe"}) is True
    assert _is_observe_hook({"type": "command", "command": "continuum gate"}) is False
