"""Sections 4 and 5 of the design: output formats and the command line."""

import json
import os
import re
import sys

import pytest

FORMATS = ["text", "toml", "json"]
MIRROR = "https://mirror.example.com/simple"

SAMPLE = (
    "[global]\n"
    f"index-url = {MIRROR}\n"
    "extra-index-url = https://extra.example.com/simple\n"
    "trusted-host = mirror.example.com\n"
    "timeout = 60\n"
    "prefer-binary = true\n"
    "[install]\n"
    "find-links = /srv/wheels\n"
    "require-hashes = true\n"
)

ONLY_TRANSLATABLE = f"[global]\nindex-url = {MIRROR}\nfind-links = /srv/wheels\n"


def tilde(cli, path):
    """``path`` the way the text report shows it: home prefix replaced by ``~``."""
    home = str(cli.home)
    assert str(path).startswith(home)
    return "~" + str(path)[len(home) :]


def shows_path(cli, text, path):
    path = str(path)
    if path in text:
        return True
    return path.startswith(str(cli.home)) and tilde(cli, path) in text


def key_line(toml_text, key):
    match = re.search(rf"^{re.escape(key)}\s*=", toml_text, re.MULTILINE)
    assert match, f"no top-level line for {key!r} in:\n{toml_text}"
    return match.start()


def header_line(toml_text, header):
    match = re.search(rf"^{re.escape(header)}\s*$", toml_text, re.MULTILINE)
    assert match, f"no {header!r} header in:\n{toml_text}"
    return match.start()


# -- entry points ------------------------------------------------------------


def test_main_returns_an_int(cli):
    from pipconf2uv.cli import main

    code = main(["--format", "json"])
    assert isinstance(code, int) and code == 0


def test_main_without_arguments_reads_sys_argv(cli, monkeypatch):
    from pipconf2uv.cli import main

    cli.user_conf(ONLY_TRANSLATABLE)
    monkeypatch.setattr(sys, "argv", ["pipconf2uv", "--format", "toml"])
    cli.capsys.readouterr()
    assert main() == 0
    assert cli.parse_toml(cli.capsys.readouterr().out)["find-links"] == ["/srv/wheels"]


def test_version_option(cli):
    result = cli.run("--version")
    assert result.code == 0
    assert re.search(r"\d+\.\d+", result.combined)


def test_python_dash_m_version(cli):
    result = cli.run_module("--version")
    assert result.code == 0, result
    assert re.search(r"\d+\.\d+", result.combined)


def test_python_dash_m_produces_the_same_json_as_main(cli):
    cli.user_conf(SAMPLE)
    in_process = cli.run("--format", "json")
    child = cli.run_module("--format", "json")
    assert child.code == 0, child
    assert json.loads(child.out) == json.loads(in_process.out)


def test_python_dash_m_propagates_exit_codes(cli):
    cli.user_conf(SAMPLE)
    assert cli.run_module("--strict", "--format", "json").code == 1
    cli.user_conf("index-url = no-section-header\n")
    broken = cli.run_module("--format", "json")
    assert broken.code == 2
    assert "Traceback" not in broken.err


@pytest.mark.parametrize(
    "argv",
    [
        ["--no-such-option"],
        ["--format", "xml"],
        ["--format"],
        ["--pip-command"],
        ["unexpected-positional"],
    ],
)
def test_usage_error_exits_2(cli, argv):
    result = cli.run(*argv)
    assert result.code == 2
    assert result.err.strip()
    assert "Traceback" not in result.err


# -- json format -------------------------------------------------------------


def test_json_document_shape(cli):
    cli.user_conf(SAMPLE)
    report = cli.json()
    assert set(report.data) >= {
        "schema_version",
        "pip_command",
        "files",
        "settings",
        "uv",
        "uv_toml",
        "env",
        "notes",
    }
    assert report["schema_version"] == 1
    assert report["pip_command"] == "install"
    assert isinstance(report["uv_toml"], str)
    for entry in report["files"]:
        assert set(entry) >= {"kind", "path", "exists", "loaded"}
        assert entry["kind"] in {"global", "user", "site", "env"}
        assert isinstance(entry["exists"], bool) and isinstance(entry["loaded"], bool)
    for setting in report.settings:
        assert set(setting) >= {"key", "value", "status", "source", "overrides"}
        assert isinstance(setting["overrides"], list)
    for entry in report.env:
        assert set(entry) == {"name", "value", "from"}
    for note in report["notes"]:
        assert note["level"] in {"info", "warning"}
        assert isinstance(note["message"], str) and note["message"]


def test_json_stdout_is_exactly_one_document(cli):
    cli.user_conf(SAMPLE)
    result = cli.run("--format", "json")
    assert result.code == 0
    json.loads(result.out)  # raises on leading or trailing non-JSON text


def test_json_files_lists_the_candidates_in_load_order(cli):
    # ASSUMPTION: "files" holds every candidate location (with exists/loaded
    # flags) in pip's load order, with full paths (no "~" abbreviation).
    cli.user_conf(ONLY_TRANSLATABLE)
    cli.setenv("PIP_CONFIG_FILE", cli.tmp_path / "custom" / "missing.conf")
    report = cli.json("--site-prefix", cli.site_prefix)
    expected = [(c.kind, c.path) for c in cli.candidates(site_prefix=cli.site_prefix)]
    assert [(f["kind"], f["path"]) for f in report["files"]] == expected


def test_json_settings_keep_the_raw_value(cli):
    # ASSUMPTION: "value" is the raw configuration string (masked if it holds
    # credentials), also for booleans and list options.
    cli.user_conf("[global]\nno-cache-dir = Off\nfind-links = /a /b\n")
    report = cli.json()
    assert report.setting("no-cache-dir")["value"] == "Off"
    assert report.setting("find-links")["value"] == "/a /b"


# -- toml format -------------------------------------------------------------


def test_toml_format_prints_only_the_generated_toml(cli):
    cli.user_conf(SAMPLE)
    expected = cli.json().uv
    result = cli.run("--format", "toml")
    assert result.code == 0, result
    assert cli.parse_toml(result.out) == expected


def test_toml_format_sends_notes_to_stderr(cli):
    cli.user_conf("[global]\ntrusted-host = mirror.example.com\n")
    message = cli.json().notes(key="trusted-host", level="warning")[0]["message"]
    result = cli.run("--format", "toml")
    assert cli.parse_toml(result.out) == {"allow-insecure-host": ["mirror.example.com"]}
    assert result.err.strip()
    assert message not in result.out


def test_toml_format_does_not_list_settings_or_env_on_stdout(cli):
    cli.user_conf("[global]\ntimeout = 60\nprefer-binary = true\n")
    result = cli.run("--format", "toml")
    assert result.code == 0, result
    assert cli.parse_toml(result.out) == {}
    assert "prefer-binary" not in result.out


def test_toml_format_with_no_configuration_is_valid_empty_toml(cli):
    result = cli.run("--format", "toml")
    assert result.code == 0, result
    assert cli.parse_toml(result.out) == {}


def test_toml_key_order_is_fixed(cli):
    # Written in reverse of the mapping table on purpose.
    cli.user_conf(
        "[global]\n"
        "require-hashes = true\n"
        "config-settings = editable_mode=compat\n"
        "uploaded-prior-to = 2025-06-01\n"
        "keyring-provider = subprocess\n"
        "no-build-isolation = false\n"
        "only-binary = numpy\n"
        "no-binary = lxml\n"
        "pre = true\n"
        "no-cache-dir = true\n"
        "proxy = http://proxy.example.com:3128\n"
        "trusted-host = mirror.example.com\n"
        "find-links = /srv/wheels\n"
        "no-index = true\n"
        "extra-index-url = https://one.example.com/simple https://two.example.com/simple\n"
        f"index-url = {MIRROR}\n"
    )
    toml_text = cli.json().uv_toml
    scalars = [
        "index-strategy",
        "no-index",
        "find-links",
        "allow-insecure-host",
        "http-proxy",
        "https-proxy",
        "no-cache",
        "prerelease",
        "no-binary-package",
        "no-build-package",
        "no-build-isolation",
        "keyring-provider",
        "exclude-newer",
    ]
    positions = [key_line(toml_text, key) for key in scalars]
    assert positions == sorted(positions), "scalar keys out of order:\n" + toml_text
    first_index = header_line(toml_text, "[[index]]")
    config_settings = header_line(toml_text, "[config-settings]")
    pip_table = header_line(toml_text, "[pip]")
    assert max(positions) < first_index < config_settings < pip_table


def test_toml_key_order_does_not_depend_on_source_order(cli):
    cli.user_conf("[global]\nfind-links = /srv/wheels\npre = true\nno-index = true\n")
    first = cli.json().uv_toml
    cli.user_conf("[global]\nno-index = true\npre = true\nfind-links = /srv/wheels\n")
    assert cli.json().uv_toml == first


# -- TOML escaping -----------------------------------------------------------

ESCAPING_IN_FILE = [
    ("backslashes", "C:\\wheels\\new\\table\\u1234"),
    ("unc-path", "\\\\fileserver\\share\\wheels"),
    ("double-quotes", '/opt/my "quoted" dir'),
    ("single-quotes", "/opt/it's/here"),
    ("trailing-backslash", "C:\\site\\"),
    ("hash-and-brackets", "/opt/a #b [c] {d}"),
]


@pytest.mark.parametrize(
    "value", [c[1] for c in ESCAPING_IN_FILE], ids=[c[0] for c in ESCAPING_IN_FILE]
)
def test_special_characters_round_trip_through_toml(cli, value):
    cli.user_conf(f"[global]\ntarget = {value}\n")
    report = cli.json()  # also checks uv_toml parses back to the uv object
    assert report.uv == {"pip": {"target": value}}
    result = cli.run("--format", "toml")
    assert cli.parse_toml(result.out) == {"pip": {"target": value}}


def test_backslashes_round_trip_inside_arrays(cli):
    cli.user_conf("[global]\nfind-links = C:\\wheels\\new D:\\tmp\\x\n")
    assert cli.json().uv == {"find-links": ["C:\\wheels\\new", "D:\\tmp\\x"]}


def test_unicode_round_trips_through_toml(cli):
    # Through an environment variable: pip reads files in the locale
    # encoding, which is not UTF-8 everywhere.
    value = '/opt/\u00fcn\u00ef-\u5305/site "q" \\back'
    cli.setenv("PIP_TARGET", value)
    report = cli.json()
    assert report.uv == {"pip": {"target": value}}
    result = cli.run("--format", "toml")
    assert cli.parse_toml(result.out) == {"pip": {"target": value}}


def test_config_settings_key_that_needs_quoting_round_trips(cli):
    cli.user_conf("[global]\nconfig-settings = cmake.define.X=1 plain=2\n")
    assert cli.json().uv == {"config-settings": {"cmake.define.X": "1", "plain": "2"}}


# -- text format -------------------------------------------------------------


def test_text_is_the_default_format(cli):
    cli.user_conf(SAMPLE)
    assert cli.run().out == cli.run("--format", "text").out


def test_text_report_contains_all_five_parts(cli):
    path = cli.write_conf("user-legacy", SAMPLE)
    result = cli.run()
    assert result.code == 0, result
    out = result.out
    assert shows_path(cli, out, path)  # files read
    for key in ("index-url", "trusted-host", "prefer-binary"):  # settings
        assert key in out
    assert "unsupported" in out  # ... with status
    assert "[[index]]" in out and "primary" in out  # generated uv.toml
    assert "UV_HTTP_TIMEOUT" in out  # environment variables
    assert "unsafe-best-match" in out


def test_text_report_shows_environment_variable_origin(cli):
    cli.setenv("PIP_INDEX_URL", MIRROR)
    assert "PIP_INDEX_URL" in cli.run().out


def test_text_report_embeds_the_same_toml_as_the_toml_format(cli):
    cli.user_conf(SAMPLE)
    toml_out = cli.run("--format", "toml").out
    text_out = cli.run().out
    for line in toml_out.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            assert line.strip() in text_out


def test_text_shows_tilde_instead_of_the_home_directory(cli):
    path = cli.write_conf("user-legacy", SAMPLE)
    result = cli.run()
    assert result.code == 0, result
    assert tilde(cli, path) in result.out
    assert str(cli.home) not in result.out
    assert str(cli.home) not in result.err


def test_text_does_not_abbreviate_paths_outside_home(cli):
    path = cli.write_conf("env", ONLY_TRANSLATABLE)
    result = cli.run()
    assert result.code == 0, result
    assert path in result.out


def test_no_colour_codes_when_not_a_terminal(cli):
    cli.user_conf(SAMPLE)
    result = cli.run()
    assert "\x1b[" not in result.out and "\x1b[" not in result.err


# -- no configuration found --------------------------------------------------


def test_no_configuration_exits_0_and_lists_checked_locations(cli):
    result = cli.run()
    assert result.code == 0, result
    for candidate in cli.candidates():
        assert shows_path(cli, result.combined, candidate.path), candidate.path


def test_no_configuration_lists_site_and_env_locations_too(cli):
    missing = cli.tmp_path / "custom" / "missing.conf"
    cli.setenv("PIP_CONFIG_FILE", missing)
    result = cli.run("--site-prefix", cli.site_prefix)
    assert result.code == 0, result
    assert shows_path(cli, result.combined, cli.path_for("site"))
    assert shows_path(cli, result.combined, missing)


def test_no_configuration_as_json(cli):
    report = cli.json()
    assert report.settings == []
    assert report.uv == {}
    assert report.env == []
    assert report["files"]
    assert not any(f["exists"] or f["loaded"] for f in report["files"])


@pytest.mark.parametrize("fmt", FORMATS)
def test_no_configuration_with_strict_still_exits_0(cli, fmt):
    assert cli.run("--format", fmt, "--strict").code == 0


# -- unparseable or unreadable configuration ---------------------------------

BROKEN = [
    ("missing-section-header", "index-url = https://mirror.example.com/simple\n"),
    ("duplicate-option", "[global]\ntimeout = 1\ntimeout = 2\n"),
    ("duplicate-section", "[global]\ntimeout = 1\n[global]\nretries = 2\n"),
    ("garbage-line", "[global]\ntimeout = 1\nthis line is not an option\n"),
]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("text", [c[1] for c in BROKEN], ids=[c[0] for c in BROKEN])
def test_unparseable_file_exits_2_and_names_the_file(cli, fmt, text):
    path = cli.user_conf(text)
    result = cli.run("--format", fmt)
    assert result.code == 2, result
    assert shows_path(cli, result.err, path)
    assert "Traceback" not in result.combined


@pytest.mark.parametrize("fmt", ["toml", "json"])
def test_unparseable_file_prints_nothing_on_stdout(cli, fmt):
    # So that ``pipconf2uv --format toml > uv.toml`` never writes garbage.
    cli.user_conf(BROKEN[0][1])
    result = cli.run("--format", fmt)
    assert result.code == 2
    assert result.out.strip() == ""


def test_unparseable_file_is_fatal_even_when_other_files_are_fine(cli):
    cli.write_conf("user-legacy", ONLY_TRANSLATABLE)
    broken = cli.write_conf("site", BROKEN[0][1])
    result = cli.run("--site-prefix", cli.site_prefix)
    assert result.code == 2
    assert shows_path(cli, result.err, broken)


def test_unparseable_file_does_not_write_anything(cli):
    cli.user_conf(BROKEN[0][1])
    target = cli.tmp_path / "out" / "uv.toml"
    assert cli.run("--write", target).code == 2
    assert not target.exists()


def test_broken_file_that_is_not_loaded_is_not_an_error(cli):
    cli.write_conf("user", BROKEN[0][1])  # suppressed by the env file below
    cli.write_conf("site", BROKEN[0][1])  # no prefix given, so never a candidate
    cli.write_conf("env", ONLY_TRANSLATABLE)
    report = cli.json()
    assert report.uv["find-links"] == ["/srv/wheels"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_unreadable_file_exits_2_and_names_the_file(cli):
    if os.geteuid() == 0:
        pytest.skip("root can read every file")
    path = cli.user_conf(ONLY_TRANSLATABLE)
    os.chmod(path, 0)
    try:
        result = cli.run()
    finally:
        os.chmod(path, 0o600)
    assert result.code == 2, result
    assert shows_path(cli, result.err, path)
    assert "Traceback" not in result.combined


# -- --strict ----------------------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "text",
    ["[global]\nprefer-binary = true\n", "[global]\nnot-a-pip-option = 1\n", SAMPLE],
    ids=["unsupported", "unknown", "mixed"],
)
def test_strict_exits_1_for_unsupported_or_unknown_settings(cli, fmt, text):
    cli.user_conf(text)
    assert cli.run("--format", fmt, "--strict").code == 1


@pytest.mark.parametrize(
    "text",
    ["[global]\nprefer-binary = true\n", "[global]\nnot-a-pip-option = 1\n", SAMPLE],
    ids=["unsupported", "unknown", "mixed"],
)
def test_without_strict_unsupported_or_unknown_settings_exit_0(cli, text):
    cli.user_conf(text)
    assert cli.run().code == 0


@pytest.mark.parametrize(
    "text",
    [
        ONLY_TRANSLATABLE,
        "[global]\ntimeout = 60\n",
        "[global]\nquiet = 1\ncache-dir = /var/cache/pip\n",
        "[global]\ntrusted-host = mirror.example.com\n",
        "[freeze]\nprefer-binary = true\n",
    ],
    ids=["translated", "env", "ignored", "translated-with-warning", "unselected-section"],
)
def test_strict_exits_0_when_everything_is_handled(cli, text):
    cli.user_conf(text)
    assert cli.run("--strict").code == 0


def test_strict_still_prints_the_report(cli):
    cli.user_conf(SAMPLE)
    report = cli.json("--strict", expect_code=1)
    assert report.status("prefer-binary") == "unsupported"
    assert report.uv["find-links"] == ["/srv/wheels"]


def test_strict_counts_an_invalid_boolean_as_unsupported(cli):
    cli.user_conf("[global]\nno-index = maybe\n")
    assert cli.run("--strict").code == 1


# -- --write -----------------------------------------------------------------


def test_write_creates_the_file_and_its_parents(cli):
    cli.user_conf(SAMPLE)
    target = cli.tmp_path / "deep" / "er" / "dir" / "uv.toml"
    expected = cli.json().uv
    result = cli.run("--write", target)
    assert result.code == 0, result
    assert cli.parse_toml(target.read_text(encoding="utf-8")) == expected


def test_write_leaves_no_temporary_or_backup_files(cli):
    cli.user_conf(SAMPLE)
    target = cli.tmp_path / "out" / "uv.toml"
    assert cli.run("--write", target).code == 0
    assert sorted(p.name for p in target.parent.iterdir()) == ["uv.toml"]


def test_write_refuses_to_overwrite(cli):
    cli.user_conf(SAMPLE)
    target = cli.tmp_path / "out" / "uv.toml"
    target.parent.mkdir()
    target.write_text("# existing, keep me\n", encoding="utf-8")
    result = cli.run("--write", target)
    assert result.code == 1, result
    assert result.err.strip()
    assert target.read_text(encoding="utf-8") == "# existing, keep me\n"
    assert sorted(p.name for p in target.parent.iterdir()) == ["uv.toml"]


def test_write_force_overwrites_and_keeps_a_backup(cli):
    cli.user_conf(SAMPLE)
    target = cli.tmp_path / "out" / "uv.toml"
    target.parent.mkdir()
    target.write_text("# existing, back me up\n", encoding="utf-8")
    expected = cli.json().uv
    result = cli.run("--write", target, "--force")
    assert result.code == 0, result
    assert cli.parse_toml(target.read_text(encoding="utf-8")) == expected
    backup = target.parent / "uv.toml.bak"
    assert backup.read_text(encoding="utf-8") == "# existing, back me up\n"
    assert sorted(p.name for p in target.parent.iterdir()) == ["uv.toml", "uv.toml.bak"]


def test_write_force_without_existing_file_just_writes(cli):
    cli.user_conf(SAMPLE)
    target = cli.tmp_path / "out" / "uv.toml"
    assert cli.run("--write", target, "--force").code == 0
    assert sorted(p.name for p in target.parent.iterdir()) == ["uv.toml"]


def test_force_without_write_does_not_write(cli):
    cli.user_conf(SAMPLE)
    assert cli.run("--force").code == 0
    assert not (cli.xdg_config_home / "uv").exists()
    assert not (cli.appdata / "uv").exists()


@pytest.mark.parametrize(
    "text",
    ["", "[global]\nquiet = 1\ntimeout = 60\nprefer-binary = true\n"],
    ids=["no-configuration", "nothing-translatable"],
)
def test_write_with_nothing_to_write_creates_no_file(cli, text):
    if text:
        cli.user_conf(text)
    target = cli.tmp_path / "out" / "uv.toml"
    result = cli.run("--write", target)
    assert result.code == 0, result
    assert not target.exists()


def test_nothing_is_written_without_write(cli):
    cli.user_conf(SAMPLE)
    for fmt in FORMATS:
        assert cli.run("--format", fmt).code == 0
    assert not (cli.xdg_config_home / "uv").exists()
    assert not (cli.appdata / "uv").exists()
    assert not (cli.home / ".config" / "uv").exists()


def default_write_target(cli):
    if sys.platform == "win32":
        return cli.appdata / "uv" / "uv.toml"
    return cli.xdg_config_home / "uv" / "uv.toml"


def test_default_write_target_is_uvs_user_level_file(cli):
    # XDG_CONFIG_HOME points away from ~/.config in the fixture, so on Linux
    # and macOS this proves the variable is honoured; on Windows it is APPDATA.
    cli.user_conf(SAMPLE)
    expected = cli.json().uv
    result = cli.run("--write")
    assert result.code == 0, result
    target = default_write_target(cli)
    assert cli.parse_toml(target.read_text(encoding="utf-8")) == expected
    if sys.platform != "win32":
        assert not (cli.home / ".config" / "uv").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="XDG default applies to Linux and macOS")
def test_default_write_target_without_xdg_config_home(cli):
    cli.delenv("XDG_CONFIG_HOME")
    cli.write_conf("user-legacy", SAMPLE)
    result = cli.run("--write")
    assert result.code == 0, result
    assert (cli.home / ".config" / "uv" / "uv.toml").is_file()


def test_default_write_target_also_refuses_to_overwrite(cli):
    cli.user_conf(SAMPLE)
    target = default_write_target(cli)
    target.parent.mkdir(parents=True)
    target.write_text("# mine\n", encoding="utf-8")
    assert cli.run("--write").code == 1
    assert target.read_text(encoding="utf-8") == "# mine\n"


def test_written_file_matches_the_toml_format_output(cli):
    cli.user_conf(SAMPLE)
    target = cli.tmp_path / "out" / "uv.toml"
    assert cli.run("--write", target).code == 0
    toml_out = cli.run("--format", "toml").out
    assert cli.parse_toml(target.read_text(encoding="utf-8")) == cli.parse_toml(toml_out)


# -- determinism -------------------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
def test_two_runs_are_byte_identical(cli, fmt):
    cli.write_conf("user-legacy", "[global]\nno-binary = b,a,c\nconstraint = /z.txt /a.txt\n")
    cli.write_conf("user", SAMPLE)
    cli.write_conf(
        "site", "[install]\nconfig-settings = z=1 a=2 m=3\n[wheel]\nx = 1\n[freeze]\ny = 2\n"
    )
    cli.setenv("PIP_RETRIES", "3")
    cli.setenv("PIP_NO_BUILD_ISOLATION", "1")
    cli.setenv("PIP_ABI", "cp311")
    first = cli.run("--format", fmt, "--site-prefix", cli.site_prefix)
    second = cli.run("--format", fmt, "--site-prefix", cli.site_prefix)
    assert first.code == second.code == 0
    assert first.out == second.out
    assert first.err == second.err


def test_two_child_processes_are_byte_identical(cli):
    # Separate interpreters have different hash seeds.
    cli.user_conf(SAMPLE)
    cli.setenv("PIP_RETRIES", "3")
    cli.setenv("PIP_ABI", "cp311")
    outputs = []
    for seed in ("1", "2"):
        cli.monkeypatch.setenv("PYTHONHASHSEED", seed)
        outputs.append(cli.run_module("--format", "json"))
    assert outputs[0].code == outputs[1].code == 0
    assert outputs[0].out == outputs[1].out


def test_list_order_follows_the_configuration_not_the_alphabet(cli):
    cli.user_conf("[global]\nno-binary = zlib-ng,aiohttp,numpy\nfind-links = /z /a /m\n")
    report = cli.json()
    assert report.uv["no-binary-package"] == ["zlib-ng", "aiohttp", "numpy"]
    assert report.uv["find-links"] == ["/z", "/a", "/m"]
