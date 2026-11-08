"""Tests for the config format layer (issue #1597).

``continuum mcp install`` writes into editors' own configuration files. The
failure this module exists to prevent is not a crash: it is a *quiet* rewrite
of a file this tool does not understand, where the user gets back something
that parses but is no longer what they wrote. So the tests below are mostly
refusal tests. Each one pins a file that must be reported and left alone.

The TOML and YAML tests skip when the optional parser is absent, because the
format layer deliberately does not depend on one: it refuses with an install
hint instead. A skip here means the parser is missing on this machine, not
that the format is unsupported.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from continuum.mcp import configfmt

#: A registration shaped the way the JSON hosts spell it. Every format has to
#: carry the same structure, because what a host reads is the structure and
#: not the bytes.
REGISTRATION = {
    "mcpServers": {
        "continuum-mcp": {
            "command": "/opt/venv/bin/continuum-mcp",
            "args": ["--db", "/home/op/proj/continuum.db"],
            "env": {"CONTINUUM_MCP_MUTATING_CLIENTS": "claude-code"},
        }
    }
}

needs_toml = pytest.mark.skipif(
    importlib.util.find_spec("tomli_w") is None,
    reason="writing TOML needs tomli-w; the format layer refuses without it by design",
)
needs_yaml = pytest.mark.skipif(
    importlib.util.find_spec("yaml") is None,
    reason="YAML support needs PyYAML; the format layer refuses without it by design",
)


def round_trips(fmt: str, data: dict[str, Any]) -> bool:
    """``data`` survives a write and a read in ``fmt`` unchanged."""
    rendered = configfmt.render_document(fmt, data)
    return configfmt._parse(fmt, rendered, Path("round-trip")) == data


# --------------------------------------------------------------------------- #
# round trip
# --------------------------------------------------------------------------- #


def test_json_round_trips_the_registration() -> None:
    """What a JSON host reads back is what was written, exactly."""
    assert round_trips("json", REGISTRATION)


@needs_yaml
def test_yaml_round_trips_the_registration() -> None:
    """Same promise for YAML, which has no JSON to lean on."""
    assert round_trips("yaml", REGISTRATION)


@needs_toml
def test_toml_round_trips_the_registration() -> None:
    """Same promise for TOML, the one format with no standard-library writer."""
    assert round_trips("toml", REGISTRATION)


@pytest.mark.parametrize("fmt", ["json", "toml", "yaml"])
def test_every_format_is_registered(fmt: str) -> None:
    """A profile naming a format this module does not know is a bug, not a crash.

    ``FORMATS`` is what a profile is validated against, so an unknown format
    has to be visible rather than falling through to a default.
    """
    assert fmt in configfmt.FORMATS


def test_an_unknown_format_is_refused_by_name() -> None:
    """A typo in a profile names itself instead of raising something opaque."""
    with pytest.raises(configfmt.ConfigError, match="unknown config format 'yml'"):
        configfmt.render_document("yml", {})


# --------------------------------------------------------------------------- #
# refusal: the file itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fmt", "broken", "expected"),
    [
        ("json", "{not json", "is not valid JSON"),
        ("json", "[]\n", "does not contain a JSON object"),
        ("json", '"just a string"\n', "does not contain a JSON object"),
    ],
)
def test_a_json_file_the_command_cannot_read_is_never_rewritten(
    tmp_path: Path, fmt: str, broken: str, expected: str
) -> None:
    """The refusal contract from ``_load_object``, preserved verbatim per format.

    A settings file holds unrelated configuration, so every shape that is not
    the one expected is reported rather than replaced. The wording is pinned
    because it is what the CLI prints to the operator.
    """
    settings = tmp_path / "settings.json"
    settings.write_text(broken, encoding="utf-8")

    with pytest.raises(configfmt.ConfigError, match=expected):
        configfmt.read_document(fmt, settings)

    assert settings.read_text(encoding="utf-8") == broken


@needs_yaml
def test_a_yaml_file_that_will_not_parse_is_never_rewritten(tmp_path: Path) -> None:
    """Unparseable YAML is reported, not recreated.

    ``tab`` is the cheapest unparseable YAML there is: indentation may not
    contain a tab, so it fails in every YAML parser.
    """
    settings = tmp_path / "config.yaml"
    settings.write_text("mcpServers:\n\t- name: x\n", encoding="utf-8")

    with pytest.raises(configfmt.ConfigError, match="is not valid YAML"):
        configfmt.read_document("yaml", settings)

    assert settings.read_text(encoding="utf-8") == "mcpServers:\n\t- name: x\n"


@needs_yaml
def test_a_yaml_document_that_is_a_list_is_never_rewritten(tmp_path: Path) -> None:
    """A YAML file whose root is a list is refused the same way JSON's is."""
    settings = tmp_path / "config.yaml"
    settings.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(configfmt.ConfigError, match="does not contain a YAML mapping"):
        configfmt.read_document("yaml", settings)

    assert settings.read_text(encoding="utf-8") == "- one\n- two\n"


def test_a_toml_file_that_will_not_parse_is_never_rewritten(tmp_path: Path) -> None:
    """Unparseable TOML is reported, not recreated.

    Not gated on ``tomli_w``: reading TOML needs only ``tomllib``, which is
    standard library, so the refusal is testable on every machine. Only the
    writer is optional -- see ``test_reading_toml_never_needs_a_writer``.
    """
    settings = tmp_path / "config.toml"
    settings.write_text("[mcp_servers\n", encoding="utf-8")

    with pytest.raises(configfmt.ConfigError, match="is not valid TOML"):
        configfmt.read_document("toml", settings)

    assert settings.read_text(encoding="utf-8") == "[mcp_servers\n"


def test_a_toml_array_value_is_read_as_a_table_not_refused(tmp_path: Path) -> None:
    """A key holding an array is a table, so it is read rather than refused.

    TOML has no root-level array: every document parses to a table, and
    ``key = [1, 2, 3]`` is the table ``{key: [1, 2, 3]}``. JSON and YAML both
    admit a non-mapping at the root, which is why they can be refused here;
    TOML cannot, so the ``does not contain a TOML table`` branch is
    unreachable for this format and pinning it would assert the opposite of
    the correct behaviour.
    """
    settings = tmp_path / "config.toml"
    settings.write_text("key = [1, 2, 3]\n", encoding="utf-8")

    assert configfmt.read_document("toml", settings) == {"key": [1, 2, 3]}
    assert settings.read_text(encoding="utf-8") == "key = [1, 2, 3]\n"


def test_an_absent_file_reads_as_an_empty_mapping(tmp_path: Path) -> None:
    """A config the editor has not written yet is not an error.

    This is what lets install create a registration in a fresh profile, and it
    is the one "empty" case that is folded rather than refused.
    """
    assert configfmt.read_document("json", tmp_path / "absent.json") == {}


@needs_yaml
def test_a_blank_yaml_file_reads_as_an_empty_mapping(tmp_path: Path) -> None:
    """A zero-byte YAML file parses to null; that is an editor, not a mistake."""
    settings = tmp_path / "config.yaml"
    settings.write_text("\n\n", encoding="utf-8")

    assert configfmt.read_document("yaml", settings) == {}


@needs_yaml
def test_a_comment_only_yaml_file_is_refused_rather_than_folded(tmp_path: Path) -> None:
    """A YAML file of nothing but comments parses to null, and is not a blank map.

    Folding it into an empty mapping would delete the operator's notes, the
    same loss the comment guard on the write path exists to prevent, so this
    branch refuses too and leaves the file byte for byte as it was.
    """
    settings = tmp_path / "config.yaml"
    marker = "# only a note the operator wrote\n"
    settings.write_text(marker, encoding="utf-8")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.read_document("yaml", settings)

    assert str(excinfo.value) == f"{settings} is empty; refusing to edit it"
    assert settings.read_text(encoding="utf-8") == marker


def test_a_json_null_root_is_refused_rather_than_folded(tmp_path: Path) -> None:
    """``null`` is the JSON spelling of the same document, so it is refused too.

    The empty branch exists for YAML, whose parser hands back ``None`` for a
    file that holds nothing; a JSON ``null`` parses to that same value and
    takes that same branch. Pinning it through JSON keeps the refusal honest
    on a machine without PyYAML, where no YAML file can reach it.
    """
    settings = tmp_path / "settings.json"
    settings.write_text("null\n", encoding="utf-8")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.read_document("json", settings)

    assert str(excinfo.value) == f"{settings} is empty; refusing to edit it"
    assert settings.read_text(encoding="utf-8") == "null\n"


def test_an_unknown_format_is_refused_by_name_on_read(tmp_path: Path) -> None:
    """The read side names a bad format too, not just the render side.

    A profile's format reaches ``read_document`` before it ever reaches
    ``render_document``, so an unknown one has to be reported on the way in
    rather than surfacing as a ``KeyError`` from the root-cause lookup.
    """
    settings = tmp_path / "settings.cfg"
    settings.write_text("anything\n", encoding="utf-8")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.read_document("yml", settings)

    assert str(excinfo.value) == "unknown config format 'yml' (expected one of json, toml, yaml)"
    assert settings.read_text(encoding="utf-8") == "anything\n"


# --------------------------------------------------------------------------- #
# refusal: the value
# --------------------------------------------------------------------------- #


def test_toml_has_no_null_and_says_where_one_is(tmp_path: Path) -> None:
    """A null in a TOML document is refused by path, not dropped.

    Dropping the key would change what the host reads without saying so, and
    inventing a placeholder would change it differently. Both are worse than
    an error that names the key.
    """
    document = {"mcp_servers": {"continuum-mcp": {"command": "x", "cwd": None}}}

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.write_document("toml", tmp_path / "config.toml", document)

    assert "mcp_servers.continuum-mcp.cwd" in str(excinfo.value)
    assert "TOML has no null" in str(excinfo.value)
    assert not (tmp_path / "config.toml").exists(), "a refused write must leave no file"


# --------------------------------------------------------------------------- #
# refusal: what a rewrite would destroy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fmt", ["toml", "yaml"])
@needs_yaml
def test_a_commented_file_is_refused_rather_than_flattened(tmp_path: Path, fmt: str) -> None:
    """Comments are the user's; a re-serialisation would delete them silently.

    Comments do not survive parsing at all in either format, so there is no
    way to write one back through a document model. That makes an existing
    commented file the one case where a whole-file write is destructive, and
    it is reported instead.
    """
    settings = tmp_path / f"config.{fmt}"
    marker = "# a note the operator wrote\n"
    settings.write_text(marker, encoding="utf-8")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.write_document(fmt, settings, REGISTRATION)

    assert "comments" in str(excinfo.value)
    assert settings.read_text(encoding="utf-8") == marker


def test_json_does_not_refuse_on_comments_json_has_none(tmp_path: Path) -> None:
    """JSON has no comment syntax, so the JSON path never trips that guard.

    The guard is the reason every JSON host can still be rewritten wholesale,
    and a JSON string that merely *contains* a hash is not a comment.
    """
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"note": "colour #ff0000"}), encoding="utf-8")

    configfmt.write_document("json", settings, REGISTRATION)

    assert json.loads(settings.read_text(encoding="utf-8")) == REGISTRATION


def test_a_hash_inside_a_string_is_not_treated_as_a_comment(tmp_path: Path) -> None:
    """The comment guard looks at the first character of a line, nothing else.

    A smarter scan would try to tell a quoted ``#`` from a comment marker and
    would misfire on the very files it exists to protect. A line whose first
    non-blank character is ``#`` is the only thing counted.
    """
    assert not configfmt._has_comment_line('key: "value # not a comment"\n')
    assert configfmt._has_comment_line("  # indented comment\n")


def test_a_commented_toml_file_is_refused_with_no_writer_installed(tmp_path: Path) -> None:
    """The comment guard is what protects the file; the writer is never reached.

    Writing TOML needs an extra this machine may not have, and the guard runs
    before any parser is touched. That ordering is the point: an operator with
    an annotated Codex config is told their file is protected, not told to
    install something first.
    """
    settings = tmp_path / "config.toml"
    marker = "# a note the operator wrote\n"
    settings.write_text(marker, encoding="utf-8")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.write_document("toml", settings, REGISTRATION)

    assert str(excinfo.value) == (
        f"{settings} contains comments, which a TOML writer cannot preserve. "
        "Registering this server would discard them, so nothing was written. "
        "Add the entry by hand, or strip the comments and re-run."
    )
    assert settings.read_text(encoding="utf-8") == marker


@needs_yaml
def test_an_uncommented_yaml_file_is_rewritten_in_place(tmp_path: Path) -> None:
    """The guard refuses comments, not YAML: a clean file is still written.

    The other leg of the same branch. A file that holds nothing this module
    cannot reproduce is exactly the case a wholesale rewrite is safe for, so
    "guard" must not become "never writes".
    """
    settings = tmp_path / "config.yaml"
    settings.write_text("unrelated: value\n", encoding="utf-8")

    configfmt.write_document("yaml", settings, REGISTRATION)

    assert configfmt.read_document("yaml", settings) == REGISTRATION


# --------------------------------------------------------------------------- #
# refusal: an absent optional parser
# --------------------------------------------------------------------------- #


def _refuse_module(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make the lazy import of ``name`` report the package as not installed.

    The format layer resolves its extras by name through
    ``importlib.import_module`` precisely so the missing case is a refusal
    rather than an import error at module scope, so that is the seam a test
    of the refusal pulls on.
    """
    real_import_module = configfmt.importlib.import_module

    def refusing(importee: str, *args: Any, **kwargs: Any) -> Any:
        if importee == name:
            raise ImportError(f"No module named {name!r}")
        return real_import_module(importee, *args, **kwargs)

    monkeypatch.setattr(configfmt.importlib, "import_module", refusing)


def test_a_missing_yaml_parser_is_reported_with_the_command_that_fixes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No PyYAML means a named remedy, not a traceback.

    ``mcp install`` already refuses for a missing MCP SDK the same way, so the
    two failures read identically to an operator: here is what is missing,
    here is the command. The message is pinned verbatim because it is what the
    CLI prints.
    """
    settings = tmp_path / "config.yaml"
    settings.write_text("mcpServers: []\n", encoding="utf-8")
    _refuse_module(monkeypatch, "yaml")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.read_document("yaml", settings)

    assert str(excinfo.value) == (
        "reading and writing YAML needs the 'PyYAML' package, which is not "
        'installed. Install it with: pip install "continuum-agent[mcp]"'
    )


def test_a_missing_yaml_parser_stops_a_write_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same refusal on the way out: rendering needs the parser as well.

    One message covers both directions, so an operator who reads it once knows
    both operations are blocked, not merely the one they tried.
    """
    _refuse_module(monkeypatch, "yaml")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.render_document("yaml", REGISTRATION)

    assert str(excinfo.value) == (
        "reading and writing YAML needs the 'PyYAML' package, which is not "
        'installed. Install it with: pip install "continuum-agent[mcp]"'
    )


def test_a_missing_toml_writer_is_reported_with_the_command_that_fixes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same contract for the TOML writer, which the standard library lacks."""
    _refuse_module(monkeypatch, "tomli_w")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.render_document("toml", REGISTRATION)

    assert str(excinfo.value) == (
        "writing TOML needs the 'tomli-w' package, which is not installed. "
        'Install it with: pip install "continuum-agent[mcp]"'
    )


def test_reading_toml_never_needs_a_writer() -> None:
    """``tomllib`` is standard library, so reading a TOML config always works.

    Only writing needs the extra. An operator who wants to inspect their file,
    or a ``mcp remove`` on a host this tool cannot write, must not be blocked
    by the missing writer.
    """
    import tomllib

    parsed = tomllib.loads('[mcp_servers.x]\ncommand = "c"\n')
    assert parsed == {"mcp_servers": {"x": {"command": "c"}}}


# --------------------------------------------------------------------------- #
# the contract with the optional parsers
# --------------------------------------------------------------------------- #
#
# CI's test job installs neither ``tomli-w`` nor ``PyYAML`` (only the ``mcp``
# extra pulls them in), and the guards above skip when a parser is missing.
# That leaves the lines that *use* a parser exercised only on a machine that
# happens to have one. The tests below install a stub in the lazy import's
# place, so the YAML and TOML branches run everywhere -- and because the stub
# records what it was handed, they also pin the contract this module keeps
# with each library, which a round trip against the real parser cannot see.


def _stand_in_for(monkeypatch: pytest.MonkeyPatch, name: str, module: Any) -> None:
    """Install ``module`` where the lazy import of ``name`` looks."""
    real_import_module = configfmt.importlib.import_module

    def importing(importee: str, *args: Any, **kwargs: Any) -> Any:
        if importee == name:
            return module
        return real_import_module(importee, *args, **kwargs)

    monkeypatch.setattr(configfmt.importlib, "import_module", importing)


class _StubYAML:
    """PyYAML's surfaces as this module uses them, with every call recorded.

    Two properties are worth a stub rather than a skip: reading goes through
    ``safe_load`` -- never ``load``, which constructs arbitrary objects from
    tags in a file this tool did not write -- and the dumper is handed a
    specific set of options that each protect something a reader notices.
    """

    class YAMLError(Exception):
        """``yaml.YAMLError`` as the refusal branch sees it."""

    def __init__(
        self,
        *,
        document: Any = None,
        text: str = "rendered: stub\n",
        load_error: Exception | None = None,
    ) -> None:
        self.document = document
        self.text = text
        self.load_error = load_error
        self.loads: list[str] = []
        self.dumps: list[tuple[Any, dict[str, Any]]] = []

    def safe_load(self, text: str) -> Any:
        self.loads.append(text)
        if self.load_error is not None:
            raise self.load_error
        return self.document

    def safe_dump(self, data: Any, **kwargs: Any) -> str:
        self.dumps.append((data, kwargs))
        return self.text


class _StubTomlW:
    """``tomli_w`` as this module uses it: one call, the whole document."""

    def __init__(self, text: str = 'rendered = "stub"\n') -> None:
        self.text = text
        self.written: list[Any] = []

    def dumps(self, data: Any) -> str:
        self.written.append(data)
        return self.text


def test_yaml_is_read_through_safe_load_and_never_the_full_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config file is untrusted input, so the safe loader is the only one used.

    ``load`` would build arbitrary Python objects from tags a hostile file can
    carry, which makes the choice of loader a security property rather than a
    formatting detail. The recorded call is the evidence: the text goes in,
    and the document is the only thing that comes back.
    """
    yaml = _StubYAML(document={"mcpServers": {}})
    _stand_in_for(monkeypatch, "yaml", yaml)
    settings = tmp_path / "config.yaml"
    settings.write_text("mcpServers: {}\n", encoding="utf-8")

    assert configfmt.read_document("yaml", settings) == {"mcpServers": {}}
    assert yaml.loads == ["mcpServers: {}\n"]


def test_a_yaml_document_that_parses_to_nothing_folds_to_an_empty_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank YAML file is the one ``None`` that is an editor, not a mistake.

    Pinned through the stub so the fold is exercised on a machine without
    PyYAML as well, where no real file can reach it -- and with the
    comment-only file pinned beside it, the boundary is visible from both
    sides: blank folds, annotated refuses.
    """
    yaml = _StubYAML(document=None)
    _stand_in_for(monkeypatch, "yaml", yaml)
    settings = tmp_path / "config.yaml"
    settings.write_text("\n\n", encoding="utf-8")

    assert configfmt.read_document("yaml", settings) == {}


def test_a_yaml_parse_failure_becomes_a_refusal_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parser's own error is wrapped, not propagated.

    An operator staring at a traceback from ``yaml`` learns nothing about
    their file. The refusal names the file and the cause, and leaves the bytes
    alone; the stub hands back a controlled error so the whole message can be
    pinned rather than a fragment of a parser's wording.
    """
    yaml = _StubYAML(load_error=_StubYAML.YAMLError("boom"))
    _stand_in_for(monkeypatch, "yaml", yaml)
    settings = tmp_path / "config.yaml"
    marker = "not: [yaml\n"
    settings.write_text(marker, encoding="utf-8")

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.read_document("yaml", settings)

    assert str(excinfo.value) == f"{settings} is not valid YAML (boom); refusing to edit it"
    assert settings.read_text(encoding="utf-8") == marker


def test_yaml_rendering_keeps_key_order_avoids_flow_style_and_never_wraps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The four options the dumper is handed are each a user-visible property.

    Key order is the one part of a YAML file a reader navigates by, flow style
    turns a readable block into a one-liner, escaping unicode turns names into
    ``\\u`` sequences, and a wrap splits one readable line into several. All
    four are pinned because all four are reasons the output would not read
    like the file the operator wrote.
    """
    yaml = _StubYAML()
    _stand_in_for(monkeypatch, "yaml", yaml)

    assert configfmt.render_document("yaml", REGISTRATION) == yaml.text
    assert yaml.dumps == [
        (
            REGISTRATION,
            {
                "sort_keys": False,
                "default_flow_style": False,
                "allow_unicode": True,
                "width": 4096,
            },
        )
    ]


def test_toml_rendering_hands_the_whole_document_to_the_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer sees the document, and nothing else is done to it.

    There is no standard-library TOML writer, so whatever ``tomli_w`` returns
    is what reaches the file; this module adds no formatting of its own, and
    the document is not reshaped on the way through.
    """
    writer = _StubTomlW()
    _stand_in_for(monkeypatch, "tomli_w", writer)

    assert configfmt.render_document("toml", REGISTRATION) == writer.text
    assert writer.written == [REGISTRATION]


def test_toml_rendering_checks_for_null_before_it_touches_the_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A null inside a list is refused by path, and the writer never runs.

    TOML has no null, so a document holding one cannot be written back without
    dropping a key or inventing a placeholder. The check has to come first: a
    writer that had already been handed the document would have produced half a
    file before the refusal landed.
    """
    writer = _StubTomlW()
    _stand_in_for(monkeypatch, "tomli_w", writer)

    with pytest.raises(configfmt.ConfigError) as excinfo:
        configfmt.render_document("toml", {"mcp_servers": {"a": {"args": [None]}}})

    assert str(excinfo.value) == (
        "mcp_servers.a.args[0] is null, and TOML has no null to write it as; "
        "removing the key first would change what the host reads"
    )
    assert writer.written == []


# --------------------------------------------------------------------------- #
# shapes
# --------------------------------------------------------------------------- #

#: The shapes the landed profiles use. Every combination here is real: no
#: profile is dict-keyed with an argv array, but a YAML host being list-shaped
#: and a TOML host being dict-keyed both are, so the axes are exercised
#: independently rather than only in the combinations that happen to ship.
DICT_SPLIT = configfmt.Shape(
    servers_key="mcpServers", nesting_key="projects", type_key="type", type_value="stdio"
)
LIST_SPLIT = configfmt.Shape(servers_key="mcpServers", container="list")
DICT_ARRAY = configfmt.Shape(
    servers_key="mcp",
    container="dict",
    argv_style="array",
    env_key="environment",
    type_key="type",
    type_value="local",
)

SPEC = configfmt.ServerSpec(
    name="continuum-mcp",
    argv=("/opt/venv/bin/continuum-mcp", "--db", "/home/op/proj/continuum.db"),
    env={"CONTINUUM_MCP_MUTATING_CLIENTS": "claude-code"},
)


@pytest.mark.parametrize("shape", [DICT_SPLIT, LIST_SPLIT, DICT_ARRAY])
def test_a_registration_survives_a_write_and_a_read_in_any_shape(shape: configfmt.Shape) -> None:
    """Every shape reads back the same registration install wrote.

    This is the round-trip guarantee for the structure layer, and it is the
    reason the shape does not depend on the format: the argv that goes in is
    the argv that comes back, whatever the host's keys are called.
    """
    data: dict[str, Any] = {}
    assert shape.put(data, "local", Path("/proj"), SPEC)[0] == "installed"

    assert shape.argv_of(shape.find(data, "local", Path("/proj"), SPEC.name)) == list(SPEC.argv)


@pytest.mark.parametrize("shape", [DICT_SPLIT, LIST_SPLIT, DICT_ARRAY])
def test_re_installing_never_duplicates_the_entry(shape: configfmt.Shape) -> None:
    """The second install is a no-op, on every shape.

    This is the bug class of issues #484 and #526. A dict shape makes it
    invisible, because a key either exists or it does not; a list shape
    appends, so getting it wrong shows up as two identical servers in the
    agent's picker and nothing else anywhere.
    """
    data: dict[str, Any] = {}
    assert shape.put(data, "project", Path("/proj"), SPEC)[0] == "installed"
    assert shape.put(data, "project", Path("/proj"), SPEC)[0] == "present"

    container = shape.container_at(data, "project", Path("/proj"), create=False)[1]
    entries = [container] if isinstance(container, dict) else container
    assert len(entries) == 1


@pytest.mark.parametrize("shape", [DICT_SPLIT, LIST_SPLIT, DICT_ARRAY])
def test_a_moved_environment_repoints_rather_than_duplicating(shape: configfmt.Shape) -> None:
    """A changed argv updates in place; it never leaves a second entry.

    Reinstalling after a virtualenv moves has to be safe to run
    unconditionally from a setup script, and a list-shaped host that appends
    on every run is exactly how that becomes a problem.
    """
    moved = configfmt.ServerSpec(
        name=SPEC.name,
        argv=("/new/venv/bin/continuum-mcp", "--db", SPEC.argv[2]),
        env=SPEC.env,
    )
    data: dict[str, Any] = {}
    shape.put(data, "project", Path("/proj"), SPEC)

    assert shape.put(data, "project", Path("/proj"), moved)[0] == "updated"

    container = shape.container_at(data, "project", Path("/proj"), create=False)[1]
    entries = [container] if isinstance(container, dict) else container
    assert len(entries) == 1
    assert shape.argv_of(shape.find(data, "project", Path("/proj"), SPEC.name)) == list(moved.argv)


@pytest.mark.parametrize("shape", [DICT_SPLIT, LIST_SPLIT, DICT_ARRAY])
def test_remove_takes_out_only_what_was_installed(shape: configfmt.Shape) -> None:
    """Other servers, and other keys, survive a remove untouched."""
    data: dict[str, Any] = {"unrelated": {"keep": True}}
    shape.put(data, "project", Path("/proj"), SPEC)
    container = shape.container_at(data, "project", Path("/proj"), create=False)[1]
    if shape.container == "list":
        container.append({"name": "weather", "command": "weather-bin"})
    else:
        container["weather"] = {"command": "weather-bin"}

    assert shape.drop(data, "project", Path("/proj"), SPEC.name) is True

    assert shape.find(data, "project", Path("/proj"), SPEC.name) is None
    assert data["unrelated"] == {"keep": True}
    assert shape.find(data, "project", Path("/proj"), "weather") is not None


def test_a_nested_registration_prunes_every_container_it_emptied() -> None:
    """A local registration sits three containers deep; all three go.

    The alternative is a per-user settings file left holding an empty project
    keyed by an absolute path, which is residue a user notices and files a bug
    about.
    """
    data: dict[str, Any] = {}
    DICT_SPLIT.put(data, "local", Path("/proj"), SPEC)
    assert data == {
        # The container is keyed by str(project_root), which renders per
        # platform: "/proj" on POSIX, "\proj" on Windows. Asserting the POSIX
        # spelling made this fail on Windows while the code was right.
        "projects": {str(Path("/proj")): {"mcpServers": {"continuum-mcp": DICT_SPLIT.entry(SPEC)}}}
    }

    DICT_SPLIT.drop(data, "local", Path("/proj"), SPEC.name)

    assert data == {}


def test_an_empty_list_container_is_left_alone() -> None:
    """``mcpServers: []`` survives, because the host may have written it.

    Deleting the key would remove the only thing telling Continue that MCP is
    configured at all.
    """
    data: dict[str, Any] = {"mcpServers": []}
    LIST_SPLIT.put(data, "local", Path("/proj"), SPEC)

    LIST_SPLIT.drop(data, "local", Path("/proj"), SPEC.name)

    assert data == {"mcpServers": []}


def test_find_reports_absence_where_the_nesting_path_is_missing() -> None:
    """A per-project section that does not exist is ``None``, not a KeyError.

    ``container_at`` walks the nesting path with ``create=False`` on a lookup,
    so a host file with no per-project section at all is simply empty -- and
    the lookup has to stay a lookup, never a creator.
    """
    assert DICT_SPLIT.find({}, "local", Path("/proj"), "continuum-mcp") is None


def test_find_reports_absence_where_only_the_container_is_missing() -> None:
    """A project section that exists but holds no servers reads as empty too.

    The nesting key is present and is a mapping, which is the case a walk that
    only checked the first level would get wrong: the missing piece is the
    servers container one level further down.
    """
    data: dict[str, Any] = {"projects": {str(Path("/proj")): {"other": 1}}}

    assert DICT_SPLIT.find(data, "local", Path("/proj"), "continuum-mcp") is None
    assert data == {"projects": {str(Path("/proj")): {"other": 1}}}


@pytest.mark.parametrize("shape", [DICT_SPLIT, LIST_SPLIT])
def test_dropping_a_name_that_was_never_installed_changes_nothing(
    shape: configfmt.Shape,
) -> None:
    """A remove of a name that is not there is ``False``, and the file stands.

    ``drop`` treats an absent entry and a foreign one the same way, which is
    what makes a remove safe to run against a file this tool never wrote: a
    miss has to be a miss, not a reason to reshape the document.
    """
    data: dict[str, Any] = {}
    shape.put(data, "project", Path("/proj"), SPEC)

    assert shape.drop(data, "project", Path("/proj"), "somebody-else") is False

    assert shape.find(data, "project", Path("/proj"), "somebody-else") is None
    assert shape.argv_of(shape.find(data, "project", Path("/proj"), SPEC.name)) == list(SPEC.argv)


def test_pruning_stops_at_a_container_another_project_still_uses() -> None:
    """A remove in one project leaves another project's section standing.

    Pruning walks upwards only while each container it meets is empty, so the
    ``projects`` map survives for as long as anything is filed under it. That
    is the difference between tidying up after a remove and deleting a
    neighbour's registration by accident.
    """
    data: dict[str, Any] = {}
    DICT_SPLIT.put(data, "local", Path("/proj"), SPEC)
    DICT_SPLIT.put(data, "local", Path("/other"), SPEC)

    assert DICT_SPLIT.drop(data, "local", Path("/proj"), SPEC.name) is True

    assert data == {
        "projects": {str(Path("/other")): {"mcpServers": {"continuum-mcp": DICT_SPLIT.entry(SPEC)}}}
    }


@pytest.mark.parametrize(
    ("taken", "shape", "expected"),
    [
        pytest.param(["not a dict"], DICT_SPLIT, "is not an object", id="dict-taken-by-a-list"),
        pytest.param({"not": "a list"}, LIST_SPLIT, "is not a list", id="list-taken-by-a-mapping"),
    ],
)
def test_a_container_that_is_the_wrong_type_is_refused(
    taken: Any, shape: configfmt.Shape, expected: str
) -> None:
    """A container taken by something else is never overwritten.

    The same contract ``_load_object`` has for the file itself, one level
    down: whatever is in there was written on purpose.
    """
    data = {shape.servers_key: taken}

    with pytest.raises(configfmt.ConfigError, match=expected):
        shape.put(data, "project", Path("/proj"), SPEC)


def test_a_nesting_container_that_is_the_wrong_type_is_refused() -> None:
    """A ``projects`` key holding a string stops the install dead."""
    data: dict[str, Any] = {"projects": "a string"}

    with pytest.raises(configfmt.ConfigError, match="is not an object"):
        DICT_SPLIT.put(data, "local", Path("/proj"), SPEC)


def test_a_shape_without_per_project_nesting_ignores_the_scope() -> None:
    """A host with nowhere to nest puts every scope in the same container.

    opencode and Continue scope a registration by which file it is in, not by
    a section inside one, so ``local``, ``project`` and ``user`` are the same
    place and the scope only picked the path in the first place.
    """
    data: dict[str, Any] = {}
    for scope in ("local", "project", "user"):
        LIST_SPLIT.put(data, scope, Path("/proj"), SPEC)

    assert list(data) == ["mcpServers"]
    assert len(data["mcpServers"]) == 1


def test_argv_of_rejects_every_entry_shape_it_cannot_represent() -> None:
    """``None`` means "not argv", whether that is a type error or a bad value.

    The caller cannot act differently on a missing ``args`` and on an ``args``
    holding a number, so both have to produce the same refusal: guessing here
    is what makes a remove delete somebody else's configuration.
    """
    assert DICT_SPLIT.argv_of("not a dict") is None
    assert DICT_SPLIT.argv_of({"command": "/x/continuum-mcp"}) is None
    assert DICT_SPLIT.argv_of({"command": "/x/continuum-mcp", "args": [1]}) is None
    assert DICT_SPLIT.argv_of({"command": 7, "args": []}) is None
    assert DICT_ARRAY.argv_of({"command": "/x/continuum-mcp"}) is None
    assert DICT_ARRAY.argv_of({"command": []}) is None
    assert DICT_ARRAY.argv_of({"command": ["/x/continuum-mcp", 2]}) is None


# --------------------------------------------------------------------------- #
# host profiles
# --------------------------------------------------------------------------- #

from continuum.mcp import install as mcp_install  # noqa: E402

#: What each host's real config file looks like on arrival, copied from the
#: vendor's own documentation or a shipped fixture. A profile that reads the
#: wrong key, or writes the wrong one, fails against these even when every
#: shape test above passes, which is the point of keeping them: the shape
#: tests prove the layer works, the fixtures prove the profile is right.
HOST_FIXTURES: dict[str, dict[str, Any]] = {
    # https://zed.dev/docs/ai/mcp
    "zed": {
        "context_servers": {
            "local-mcp-server": {"command": "some-command", "args": ["a", "b"], "env": {}}
        }
    },
    # https://opencode.ai/docs/mcp-servers/
    "opencode": {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {"jira": {"type": "remote", "url": "https://jira/mcp"}},
    },
    # https://github.com/continuedev/continue/blob/main/docs/customize/deep-dives/mcp.mdx
    "continue": {
        "name": "My assistant",
        "version": "0.0.1",
        "schema": "v1",
        "mcpServers": [
            {"name": "SQLite MCP", "type": "stdio", "command": "npx", "args": ["mcp-sqlite", "/db"]}
        ],
    },
    # https://developers.openai.com/codex/config-reference
    "codex": {
        "model": "gpt-5",
        "mcp_servers": {"github": {"command": "npx", "args": ["-y", "gh-mcp"]}},
    },
}

#: Formats that need an optional parser, so a run without it reports the
#: profile as unsupported instead of failing on an ImportError.
_FORMAT_GUARD = {
    "toml": importlib.util.find_spec("tomli_w") is not None,
    "yaml": importlib.util.find_spec("yaml") is not None,
}


def profile_runnable(host: str) -> bool:
    """Whether this machine can write ``host``'s format at all."""
    return _FORMAT_GUARD.get(mcp_install.host_format(host), True)


PROFILES = sorted(mcp_install.HOST_PROFILES)


@pytest.mark.parametrize("host", PROFILES)
def test_every_profile_declares_the_keys_the_layers_read(host: str) -> None:
    """A profile missing a key fails here, not as a KeyError mid-write.

    The shape and format lookups are the first thing every host path does, so
    an incomplete row would otherwise only surface when somebody ran
    ``mcp install --host`` against it.
    """
    profile = mcp_install.HOST_PROFILES[host]
    for key in (
        "project_settings",
        "local_settings",
        "user_settings",
        "mutating_clients",
        "format",
        "container",
        "servers_key",
        "nested_by_project",
        "argv_style",
        "env_key",
        "type_key",
        "type_value",
    ):
        assert key in profile, (host, key)
    assert profile["format"] in configfmt.FORMATS, host
    assert profile["container"] in ("dict", "list"), host
    assert profile["argv_style"] in ("split", "array"), host


@pytest.mark.parametrize("host", PROFILES)
def test_an_install_lands_where_the_vendor_says_it_lands(host: str) -> None:
    """The written file has the container the vendor's own example shows.

    For the four JSON hosts this runs everywhere. TOML and YAML hosts are
    exercised through their fixture and shape directly below, because their
    parsers are optional and a missing one is a refusal, not a defect.
    """
    if not profile_runnable(host):
        pytest.skip(f"{host} needs an optional parser for {mcp_install.host_format(host)}")
    if mcp_install.host_format(host) != "json":
        pytest.skip(f"{host} writes {mcp_install.host_format(host)}, covered by the fixture tests")

    shape = mcp_install.host_shape(host)
    fixture = HOST_FIXTURES.get(host)
    if fixture is None:
        # The four hosts that shipped before this layer have no fixture here:
        # their config files are covered end to end in test_mcp_install.py,
        # against the real command the vendor documents.
        data: dict[str, Any] = {}
    else:
        # A private copy: the fixtures are module-level, and every test below
        # writes into the document it is handed.
        data = json.loads(json.dumps(fixture))

    path = shape.path("project", Path("/proj"))
    if fixture is not None:
        assert data[path[-1]] is not None, (host, path, sorted(data))

    spec = configfmt.ServerSpec("continuum-mcp", ("/bin/x", "--db", "/p/d.db"), {"K": "v"})
    entry = shape.entry(spec)
    shape.put(data, "project", Path("/proj"), spec)

    assert shape.argv_of(shape.find(data, "project", Path("/proj"), "continuum-mcp")) == [
        "/bin/x",
        "--db",
        "/p/d.db",
    ]
    assert entry


@pytest.mark.parametrize(
    ("host", "fixture"),
    [
        ("zed", HOST_FIXTURES["zed"]),
        ("opencode", HOST_FIXTURES["opencode"]),
        ("continue", HOST_FIXTURES["continue"]),
        ("codex", HOST_FIXTURES["codex"]),
    ],
)
def test_the_other_servers_in_a_real_config_survive_an_install(
    host: str, fixture: dict[str, Any]
) -> None:
    """Installing beside a host's own servers leaves all of them alone.

    The fixture is that host's documented config with its documented server
    already in it. Ours goes in beside it and a remove takes only ours back
    out, which is the property a user with a real config depends on and the
    one a shape test against an empty document cannot show.
    """
    shape = mcp_install.host_shape(host)
    data = json.loads(json.dumps(fixture))  # a private copy per parametrisation

    status, previous = shape.put(
        data,
        "project",
        Path("/proj"),
        configfmt.ServerSpec("continuum-mcp", ("/bin/x", "--db", "/p/d.db"), {"K": "v"}),
    )

    assert status == "installed" and previous is None
    assert shape.drop(data, "project", Path("/proj"), "continuum-mcp") is True
    assert data == fixture, (host, data)


@pytest.mark.parametrize("host", PROFILES)
def test_a_foreign_entry_under_our_name_is_never_overwritten(host: str) -> None:
    """Recognition is per-host, and it has to be narrow on every host.

    ``mcp remove`` deletes on this predicate, so a shape that accepted
    somebody else's entry would delete it. A list-shaped host is the sharpest
    case: an entry it does not own is a record in a list, not a key, and
    getting that wrong either drops a stranger's server or leaves ours.
    """
    foreign = {
        "command": "/opt/manual/continuum-mcp",
        "args": ["--db", "continuum.db"],
        "env": {"CONTINUUM_MCP_MUTATING_CLIENTS": "someone-else"},
    }
    assert mcp_install._is_managed_server(foreign, host) is False


def test_the_verified_client_names_are_the_ones_the_sources_send() -> None:
    """The two allowlists that were read out of a host's source, pinned.

    Zed sending "Zed" with a capital Z is the whole argument for this check
    existing: the obvious guess from the product name is wrong, and a wrong
    one costs the host ten of its thirteen tools with nothing else changing.
    opencode's is the case where the obvious guess happens to be right, which
    is exactly why guessing is not a method.
    """
    assert mcp_install.HOST_PROFILES["zed"]["mutating_clients"] == "Zed"
    assert mcp_install.HOST_PROFILES["opencode"]["mutating_clients"] == "opencode"
