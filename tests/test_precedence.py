"""Section 2 of the design: loading and effective settings.

``find-links`` is used as the probe wherever possible: its value is copied
to the uv configuration unchanged, so the winner is easy to read off.
"""

import os

import pytest


def conf(section, **options):
    lines = ["[{}]".format(section)]
    lines += ["{} = {}".format(k.replace("_", "-"), v) for k, v in options.items()]
    return "\n".join(lines) + "\n"


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


# -- file precedence ---------------------------------------------------------


def test_second_user_file_beats_legacy_user_file(cli):
    cli.write_conf("user-legacy", conf("global", find_links="/legacy"))
    cli.write_conf("user", conf("global", find_links="/user"))
    assert cli.json().uv["find-links"] == ["/user"]


def test_user_file_beats_global_file(cli):
    cli.write_conf("global", conf("global", find_links="/global"))
    cli.write_conf("user", conf("global", find_links="/user"))
    assert cli.json().uv["find-links"] == ["/user"]


def test_site_file_beats_user_file(cli):
    cli.write_conf("user", conf("global", find_links="/user"))
    cli.write_conf("site", conf("global", find_links="/site"))
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/site"]


def test_env_file_beats_site_file(cli):
    cli.write_conf("site", conf("global", find_links="/site"))
    cli.write_conf("env", conf("global", find_links="/env-file"))
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/env-file"]


def test_lower_files_still_contribute_keys_the_higher_file_lacks(cli):
    cli.write_conf("user", conf("global", find_links="/user", no_index="true"))
    cli.write_conf("site", conf("global", find_links="/site"))
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/site"]
    assert report.uv["no-index"] is True
    assert report.setting("no-index")["source"]["kind"] == "user"


def test_missing_files_are_skipped(cli):
    path = cli.user_conf(conf("global", find_links="/user"))
    report = cli.json()
    assert report.uv["find-links"] == ["/user"]
    loaded = [f for f in report["files"] if f["loaded"]]
    assert len(loaded) == 1 and same_path(loaded[0]["path"], path)
    for entry in report["files"]:
        assert entry["exists"] == os.path.exists(entry["path"])
        assert not (entry["loaded"] and not entry["exists"])


def test_site_file_is_found_through_virtual_env(cli):
    cli.write_conf("site", conf("global", find_links="/site"))
    cli.setenv("VIRTUAL_ENV", cli.site_prefix)
    report = cli.json()
    assert report.uv["find-links"] == ["/site"]
    assert report.setting("find-links")["source"]["kind"] == "site"


def test_site_prefix_option_beats_virtual_env(cli):
    other = cli.tmp_path / "other-venv"
    other.mkdir()
    cli.setenv("VIRTUAL_ENV", other)
    name = os.path.basename(cli.path_for("site"))
    (other / name).write_text(conf("global", find_links="/from-virtual-env"))
    cli.write_conf("site", conf("global", find_links="/from-option"))
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/from-option"]


def test_site_file_is_not_read_without_a_prefix(cli):
    cli.write_conf("site", conf("global", find_links="/site"))
    report = cli.json()
    assert report.settings == []
    assert report.uv == {}


# -- section precedence ------------------------------------------------------


def test_command_section_beats_global_section_in_the_same_file(cli):
    cli.user_conf(
        conf("install", find_links="/install") + conf("global", find_links="/global")
    )
    report = cli.json()
    assert report.uv["find-links"] == ["/install"]
    assert report.setting("find-links")["source"]["section"] == "install"


def test_section_precedence_beats_file_precedence(cli):
    # [install] in the lower-precedence user file beats [global] in the
    # higher-precedence site file.
    cli.write_conf("user", conf("install", find_links="/user-install"))
    cli.write_conf("site", conf("global", find_links="/site-global"))
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/user-install"]
    source = report.setting("find-links")["source"]
    assert (source["kind"], source["section"]) == ("user", "install")


def test_section_precedence_beats_file_precedence_with_a_global_file(cli):
    # The literal example from the design document.
    cli.write_conf("global", conf("install", index_url="https://install.example.com/simple"))
    cli.write_conf("user", conf("global", index_url="https://global.example.com/simple"))
    report = cli.json()
    assert report.indexes == [("primary", "https://install.example.com/simple", True)]
    source = report.setting("index-url")["source"]
    assert (source["kind"], source["section"]) == ("global", "install")


def test_file_precedence_applies_within_the_command_section(cli):
    cli.write_conf("user", conf("install", find_links="/user"))
    cli.write_conf("site", conf("install", find_links="/site"))
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/site"]


# -- environment variables ---------------------------------------------------


def test_environment_variable_beats_every_file_and_section(cli):
    cli.write_conf("user", conf("global", find_links="/user-global"))
    cli.write_conf("site", conf("install", find_links="/site-install"))
    cli.write_conf("env", conf("install", find_links="/env-file-install"))
    cli.setenv("PIP_FIND_LINKS", "/env-var")
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/env-var"]
    assert report.setting("find-links")["source"] == {
        "kind": "env-var",
        "variable": "PIP_FIND_LINKS",
    }


def test_environment_variable_alone_is_a_setting(cli):
    cli.setenv("PIP_INDEX_URL", "https://mirror.example.com/simple")
    report = cli.json()
    assert report.indexes == [("primary", "https://mirror.example.com/simple", True)]
    assert report.setting("index-url")["source"] == {
        "kind": "env-var",
        "variable": "PIP_INDEX_URL",
    }


def test_no_env_ignores_pip_option_variables(cli):
    cli.user_conf(conf("global", find_links="/user"))
    cli.setenv("PIP_FIND_LINKS", "/env-var")
    cli.setenv("PIP_NO_INDEX", "1")
    report = cli.json("--no-env")
    assert report.uv["find-links"] == ["/user"]
    assert "no-index" not in report.uv
    assert "no-index" not in report.keys
    assert all(s["source"]["kind"] != "env-var" for s in report.settings)


def test_no_env_still_honours_pip_config_file(cli):
    # ASSUMPTION: --no-env only disables PIP_<NAME> *option* variables;
    # PIP_CONFIG_FILE selects a file and keeps working.
    cli.write_conf("env", conf("global", find_links="/env-file"))
    assert cli.json("--no-env").uv["find-links"] == ["/env-file"]


@pytest.mark.parametrize("variable", ["PIP_VERSION", "PIP_HELP"])
def test_pip_version_and_pip_help_are_not_settings(cli, variable):
    cli.setenv(variable, "1")
    report = cli.json()
    assert report.settings == []
    assert report.uv == {}


def test_pip_config_file_is_not_a_setting(cli):
    cli.write_conf("env", conf("global", find_links="/env-file"))
    report = cli.json()
    assert report.keys == ["find-links"]


# -- empty values ------------------------------------------------------------


def test_empty_value_in_a_higher_file_does_not_override(cli):
    cli.write_conf("user", conf("global", find_links="/user"))
    cli.write_conf("site", "[global]\nfind-links =\n")
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/user"]
    assert report.setting("find-links")["source"]["kind"] == "user"


def test_empty_value_in_command_section_does_not_override_global_section(cli):
    cli.user_conf("[global]\nfind-links = /global\n[install]\nfind-links =\n")
    assert cli.json().uv["find-links"] == ["/global"]


def test_empty_environment_variable_does_not_override(cli):
    cli.user_conf(conf("global", find_links="/user"))
    cli.setenv("PIP_FIND_LINKS", "")
    report = cli.json()
    assert report.uv["find-links"] == ["/user"]
    assert report.setting("find-links")["source"]["kind"] == "user"


def test_empty_value_alone_produces_no_setting(cli):
    cli.user_conf("[global]\nfind-links =\nindex-url =\n")
    report = cli.json()
    assert report.settings == []
    assert report.uv == {}


# -- lists are replaced, not merged ------------------------------------------


def test_list_from_higher_file_replaces_list_from_lower_file(cli):
    cli.write_conf("user", conf("global", find_links="/user-a /user-b"))
    cli.write_conf("site", conf("global", find_links="/site-c"))
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/site-c"]


def test_list_from_command_section_replaces_list_from_global_section(cli):
    cli.user_conf(
        conf("global", extra_index_url="https://a.example.com/simple https://b.example.com/simple")
        + conf("install", extra_index_url="https://c.example.com/simple")
    )
    report = cli.json()
    assert [url for _, url, _ in report.indexes] == ["https://c.example.com/simple"]


def test_list_from_environment_variable_replaces_list_from_file(cli):
    cli.user_conf(
        conf("global", extra_index_url="https://a.example.com/simple https://b.example.com/simple")
    )
    cli.setenv("PIP_EXTRA_INDEX_URL", "https://c.example.com/simple")
    report = cli.json()
    assert [url for _, url, _ in report.indexes] == ["https://c.example.com/simple"]


# -- PIP_CONFIG_FILE ---------------------------------------------------------


def test_existing_pip_config_file_prevents_loading_user_files(cli):
    legacy = cli.write_conf("user-legacy", conf("global", no_index="true"))
    user = cli.write_conf("user", conf("global", find_links="/user", pre="true"))
    env_file = cli.write_conf("env", conf("global", retries="7"))
    report = cli.json()
    # Nothing from either user file survives, not even keys the env file lacks.
    assert report.keys == ["retries"]
    assert report.uv == {}
    assert report.file(legacy)["loaded"] is False
    assert report.file(user)["loaded"] is False
    assert report.file(user)["exists"] is True
    assert report.file(env_file)["loaded"] is True
    assert report.setting("retries")["source"]["kind"] == "env"


def test_existing_pip_config_file_does_not_prevent_loading_the_site_file(cli):
    cli.write_conf("site", conf("global", find_links="/site"))
    cli.write_conf("env", conf("global", retries="7"))
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.uv["find-links"] == ["/site"]
    assert sorted(report.keys) == ["find-links", "retries"]


def test_missing_pip_config_file_leaves_user_files_loaded(cli):
    user = cli.write_conf("user", conf("global", find_links="/user"))
    missing = cli.tmp_path / "custom" / "does-not-exist.conf"
    cli.setenv("PIP_CONFIG_FILE", missing)
    report = cli.json()
    assert report.uv["find-links"] == ["/user"]
    assert report.file(user)["loaded"] is True
    env_entry = report.file(missing)
    assert (env_entry["kind"], env_entry["exists"], env_entry["loaded"]) == (
        "env",
        False,
        False,
    )


def test_null_device_pip_config_file_loads_no_file_at_all(cli):
    cli.write_conf("user-legacy", conf("global", no_index="true"))
    cli.write_conf("user", conf("global", find_links="/user"))
    cli.write_conf("site", conf("global", pre="true"))
    cli.setenv("PIP_CONFIG_FILE", os.devnull)
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.settings == []
    assert report.uv == {}
    assert not any(f["loaded"] for f in report["files"])


def test_null_device_pip_config_file_still_allows_option_variables(cli):
    # ASSUMPTION: the null-device rule is about configuration *files* only
    # (as in pip); PIP_<NAME> variables keep working.
    cli.user_conf(conf("global", find_links="/user"))
    cli.setenv("PIP_CONFIG_FILE", os.devnull)
    cli.setenv("PIP_FIND_LINKS", "/env-var")
    assert cli.json().uv["find-links"] == ["/env-var"]


# -- --pip-command and other sections ----------------------------------------


def test_default_pip_command_is_install(cli):
    assert cli.json()["pip_command"] == "install"


def test_pip_command_selects_the_command_section(cli):
    cli.user_conf(
        conf("global", find_links="/global")
        + conf("install", find_links="/install")
        + conf("download", find_links="/download")
    )
    report = cli.json("--pip-command", "download")
    assert report["pip_command"] == "download"
    assert report.uv["find-links"] == ["/download"]
    assert report.setting("find-links")["source"]["section"] == "download"


def test_default_command_section_is_skipped_when_another_is_selected(cli):
    # (The test name avoids the word matched in the note below, because the
    # temporary directory is named after the test and may appear in messages.)
    cli.user_conf(conf("install", find_links="/install", no_index="true"))
    report = cli.json("--pip-command", "download")
    assert report.settings == []
    assert report.uv == {}
    assert len([n for n in report["notes"] if "install" in n["message"]]) == 1


def test_other_section_is_not_translated(cli):
    cli.user_conf(conf("freeze", timeout="10", find_links="/freeze"))
    report = cli.json()
    assert report.settings == []
    assert report.uv == {}
    assert report.env == []


def test_other_section_does_not_override_selected_sections(cli):
    cli.user_conf(conf("global", find_links="/global") + conf("wheel", find_links="/wheel"))
    assert cli.json().uv["find-links"] == ["/global"]


def test_each_other_section_with_settings_gets_exactly_one_note(cli):
    cli.user_conf(
        conf("freeze", timeout="10", retries="2")
        + conf("wheel", find_links="/wheel")
        + conf("global", pre="true")
    )
    report = cli.json()
    # One note per section, however many settings it holds.
    assert len([n for n in report["notes"] if "freeze" in n["message"]]) == 1
    assert len([n for n in report["notes"] if "wheel" in n["message"]]) == 1


def test_other_section_in_two_files_still_gets_one_note(cli):
    # ASSUMPTION: "each such section" is counted by section name, not once
    # per file that contains it.
    cli.write_conf("user-legacy", conf("freeze", timeout="10"))
    cli.write_conf("user", conf("freeze", retries="2"))
    report = cli.json()
    assert len([n for n in report["notes"] if "freeze" in n["message"]]) == 1


def test_empty_other_section_gets_no_note(cli):
    cli.user_conf("[freeze]\n\n" + conf("global", pre="true"))
    report = cli.json()
    assert [n for n in report["notes"] if "freeze" in n["message"]] == []


def test_selected_sections_get_no_untranslated_section_note(cli):
    cli.user_conf(conf("global", pre="true") + conf("install", no_index="true"))
    assert cli.json()["notes"] == []


# -- name normalisation ------------------------------------------------------


@pytest.mark.parametrize(
    "spelling", ["Index_URL", "INDEX-URL", "index_url", "--index-url", "--Index_Url"]
)
def test_option_names_in_files_are_normalised(cli, spelling):
    cli.user_conf("[global]\n{} = https://mirror.example.com/simple\n".format(spelling))
    report = cli.json()
    assert report.keys == ["index-url"]
    assert report.indexes == [("primary", "https://mirror.example.com/simple", True)]


def test_environment_variable_names_are_normalised(cli):
    cli.setenv("PIP_EXTRA_INDEX_URL", "https://extra.example.com/simple")
    cli.setenv("PIP_NO_BUILD_ISOLATION", "1")
    report = cli.json()
    assert sorted(report.keys) == ["extra-index-url", "no-build-isolation"]
    assert report.uv["no-build-isolation"] is True


def test_different_spellings_of_one_option_are_one_setting(cli):
    cli.write_conf("user", "[global]\nFind_Links = /user\n")
    cli.write_conf("site", "[global]\nfind-links = /site\n")
    report = cli.json("--site-prefix", cli.site_prefix)
    assert report.keys == ["find-links"]
    assert report.uv["find-links"] == ["/site"]


# -- provenance --------------------------------------------------------------


def test_source_of_a_file_setting_has_kind_path_and_section(cli):
    path = cli.user_conf(conf("install", find_links="/user"))
    source = cli.json().setting("find-links")["source"]
    assert source["kind"] == "user"
    assert source["section"] == "install"
    assert same_path(source["path"], path)


def test_setting_without_competitors_has_empty_overrides(cli):
    cli.user_conf(conf("global", find_links="/user"))
    assert cli.json().setting("find-links")["overrides"] == []


def test_overrides_names_the_overridden_file(cli):
    user = cli.write_conf("user", conf("global", find_links="/user"))
    site = cli.write_conf("site", conf("global", find_links="/site"))
    setting = cli.json("--site-prefix", cli.site_prefix).setting("find-links")
    assert setting["source"]["kind"] == "site"
    assert same_path(setting["source"]["path"], site)
    assert len(setting["overrides"]) == 1
    overridden = setting["overrides"][0]
    assert (overridden["kind"], overridden["section"]) == ("user", "global")
    assert same_path(overridden["path"], user)


def test_overrides_names_the_overridden_section(cli):
    cli.user_conf(conf("global", find_links="/global") + conf("install", find_links="/install"))
    setting = cli.json().setting("find-links")
    assert setting["source"]["section"] == "install"
    assert [(o["kind"], o["section"]) for o in setting["overrides"]] == [("user", "global")]


def test_overrides_lists_every_overridden_source(cli):
    cli.write_conf("user-legacy", conf("global", find_links="/legacy"))
    cli.write_conf("user", conf("install", find_links="/user-install"))
    cli.write_conf("site", conf("global", find_links="/site"))
    cli.setenv("PIP_FIND_LINKS", "/env-var")
    setting = cli.json("--site-prefix", cli.site_prefix).setting("find-links")
    assert setting["source"] == {"kind": "env-var", "variable": "PIP_FIND_LINKS"}
    # ASSUMPTION: the order of "overrides" is not specified; compare as a set.
    assert sorted((o["kind"], o["section"]) for o in setting["overrides"]) == [
        ("site", "global"),
        ("user", "global"),
        ("user", "install"),
    ]


def test_overrides_does_not_list_sources_that_were_never_loaded(cli):
    cli.write_conf("user", conf("global", find_links="/user"))
    cli.write_conf("site", conf("global", find_links="/site"))
    cli.write_conf("env", conf("global", find_links="/env-file"))
    setting = cli.json("--site-prefix", cli.site_prefix).setting("find-links")
    assert setting["source"]["kind"] == "env"
    # The user file is suppressed by the existing PIP_CONFIG_FILE.
    assert [o["kind"] for o in setting["overrides"]] == ["site"]


def test_overridden_environment_free_run_reports_file_source_with_no_env(cli):
    cli.user_conf(conf("global", find_links="/user"))
    cli.setenv("PIP_FIND_LINKS", "/env-var")
    setting = cli.json("--no-env").setting("find-links")
    assert setting["source"]["kind"] == "user"
    assert setting["overrides"] == []
