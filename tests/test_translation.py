"""Section 3 of the design: the pip -> uv mapping table.

Each row has a positive case and at least one case where the rule must not
fire.  ``cli.json()`` additionally checks, for every report, that
``uv_toml`` parses to exactly the structured ``uv`` object.

ASSUMPTION: a note about one pip option carries that option's normalised
name in its ``key`` field (the design shows ``"key": "trusted-host"``).
"""

import re

import pytest

STATUSES = {"translated", "env", "unsupported", "ignored", "unknown"}
MIRROR = "https://mirror.example.com/simple"
EXTRA_1 = "https://one.example.com/simple"
EXTRA_2 = "https://two.example.com/simple"

TRUE_WORDS = ["1", "y", "yes", "t", "true", "on", "TRUE", "Yes", "On"]
FALSE_WORDS = ["0", "n", "no", "f", "false", "off", "FALSE", "No", "Off"]


def mentions_index_strategy(note):
    text = note["message"].lower()
    return "unsafe-best-match" in text or "index-strategy" in text or "dependency confusion" in text


# -- statuses ----------------------------------------------------------------


def test_every_setting_has_exactly_one_known_status(cli):
    cli.user_conf(
        "[global]\n"
        f"index-url = {MIRROR}\n"
        "timeout = 30\n"
        "prefer-binary = true\n"
        "quiet = 1\n"
        "definitely-not-a-pip-option = 1\n"
    )
    report = cli.json()
    assert len(report.settings) == 5
    for setting in report.settings:
        assert setting["status"] in STATUSES
    assert report.status("index-url") == "translated"
    assert report.status("timeout") == "env"
    assert report.status("prefer-binary") == "unsupported"
    assert report.status("quiet") == "ignored"
    assert report.status("definitely-not-a-pip-option") == "unknown"


def test_nothing_configured_translates_to_nothing(cli):
    report = cli.json()
    assert report.settings == []
    assert report.uv == {}
    assert report.env == []


# -- index-url ---------------------------------------------------------------


def test_index_url_becomes_the_default_primary_index(cli):
    cli.options(index_url=MIRROR)
    report = cli.json()
    assert report.uv["index"] == [{"name": "primary", "url": MIRROR, "default": True}]
    assert report.status("index-url") == "translated"


@pytest.mark.parametrize("url", ["https://pypi.org/simple", "https://pypi.org/simple/"])
def test_pypi_index_url_is_ignored_and_emits_nothing(cli, url):
    cli.options(index_url=url)
    report = cli.json()
    assert report.status("index-url") == "ignored"
    assert report.uv == {}


def test_a_url_that_merely_resembles_pypi_is_translated(cli):
    cli.options(index_url="https://pypi.org.example.com/simple")
    report = cli.json()
    assert report.status("index-url") == "translated"
    assert report.indexes == [("primary", "https://pypi.org.example.com/simple", True)]


def test_single_custom_index_does_not_set_index_strategy(cli):
    cli.options(index_url=MIRROR)
    report = cli.json()
    assert "index-strategy" not in report.uv
    assert not any(mentions_index_strategy(n) for n in report["notes"])


# -- extra-index-url ---------------------------------------------------------


def test_extra_index_urls_are_named_in_order_and_come_before_the_default(cli):
    cli.options(index_url=MIRROR, extra_index_url=f"{EXTRA_1} {EXTRA_2}")
    report = cli.json()
    assert report.indexes == [
        ("extra-1", EXTRA_1, False),
        ("extra-2", EXTRA_2, False),
        ("primary", MIRROR, True),
    ]
    assert report.status("extra-index-url") == "translated"


def test_extra_index_urls_split_on_newlines_too(cli):
    cli.user_conf(f"[global]\nextra-index-url =\n    {EXTRA_1}\n    {EXTRA_2}\n")
    report = cli.json()
    assert [url for _, url, _ in report.indexes] == [EXTRA_1, EXTRA_2]


def test_extra_index_without_index_url_emits_no_default_index(cli):
    cli.options(extra_index_url=EXTRA_1)
    report = cli.json()
    assert report.indexes == [("extra-1", EXTRA_1, False)]


def test_no_extra_index_url_means_no_extra_indexes(cli):
    cli.options(index_url=MIRROR)
    assert [name for name, _, _ in cli.json().indexes] == ["primary"]


# -- index-strategy ----------------------------------------------------------


def test_custom_index_plus_extra_index_sets_unsafe_best_match_and_warns(cli):
    cli.options(index_url=MIRROR, extra_index_url=EXTRA_1)
    report = cli.json()
    assert report.uv["index-strategy"] == "unsafe-best-match"
    assert any(mentions_index_strategy(n) for n in report.notes(level="warning"))


def test_extra_index_plus_implicit_pypi_counts_as_two_indexes(cli):
    cli.options(extra_index_url=EXTRA_1)
    report = cli.json()
    assert report.uv["index-strategy"] == "unsafe-best-match"
    assert any(mentions_index_strategy(n) for n in report.notes(level="warning"))


def test_two_extra_indexes_with_no_index_count_as_two_indexes(cli):
    # ASSUMPTION: no-index only removes the implicit PyPI from the count;
    # explicitly configured indexes are still emitted and counted.
    cli.options(no_index="true", extra_index_url=f"{EXTRA_1} {EXTRA_2}")
    assert cli.json().uv["index-strategy"] == "unsafe-best-match"


def test_no_index_suppresses_the_implicit_pypi_count(cli):
    cli.options(no_index="true", extra_index_url=EXTRA_1)
    report = cli.json()
    assert "index-strategy" not in report.uv
    assert not any(mentions_index_strategy(n) for n in report["notes"])


def test_no_indexes_at_all_does_not_set_index_strategy(cli):
    cli.options(find_links="/srv/wheels")
    assert "index-strategy" not in cli.json().uv


# -- no-index ----------------------------------------------------------------


@pytest.mark.parametrize("word", TRUE_WORDS)
def test_no_index_true(cli, word):
    cli.options(no_index=word)
    report = cli.json()
    assert report.uv == {"no-index": True}
    assert report.status("no-index") == "translated"


@pytest.mark.parametrize("word", FALSE_WORDS)
def test_no_index_false_does_not_enable_no_index(cli, word):
    cli.options(no_index=word)
    report = cli.json()
    assert report.uv.get("no-index") is not True
    assert not report.notes(level="warning")


# -- invalid booleans --------------------------------------------------------


@pytest.mark.parametrize(
    "option, uv_key",
    [
        ("no-index", "no-index"),
        ("pre", "prerelease"),
        ("no-cache-dir", "no-cache"),
        ("no-build-isolation", "no-build-isolation"),
    ],
)
@pytest.mark.parametrize("value", ["maybe", "2", "truthy"])
def test_invalid_boolean_is_unsupported_with_a_warning(cli, option, uv_key, value):
    cli.user_conf(f"[global]\n{option} = {value}\n")
    report = cli.json()
    assert report.status(option) == "unsupported"
    assert uv_key not in report.uv
    assert report.notes(key=option, level="warning")


# -- find-links --------------------------------------------------------------


def test_find_links_becomes_an_array(cli):
    cli.options(find_links="/srv/wheels https://wheels.example.com/index.html ./local")
    report = cli.json()
    assert report.uv["find-links"] == [
        "/srv/wheels",
        "https://wheels.example.com/index.html",
        "./local",
    ]
    assert report.status("find-links") == "translated"


def test_single_find_links_is_still_an_array(cli):
    cli.options(find_links="/srv/wheels")
    assert cli.json().uv == {"find-links": ["/srv/wheels"]}


def test_find_links_does_not_create_an_index(cli):
    cli.options(find_links="https://wheels.example.com/index.html")
    report = cli.json()
    assert "index" not in report.uv


# -- trusted-host ------------------------------------------------------------


def test_trusted_host_becomes_allow_insecure_host_with_a_warning(cli):
    cli.options(trusted_host="internal.example.com mirror.example.com:8080")
    report = cli.json()
    assert report.uv["allow-insecure-host"] == [
        "internal.example.com",
        "mirror.example.com:8080",
    ]
    assert report.status("trusted-host") == "translated"
    assert report.notes(key="trusted-host", level="warning")


def test_no_trusted_host_means_no_insecure_hosts_and_no_warning(cli):
    cli.options(index_url=MIRROR)
    report = cli.json()
    assert "allow-insecure-host" not in report.uv
    assert report.notes(key="trusted-host") == []
    assert report.notes(level="warning") == []


# -- proxy -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "http://proxy.example.com:3128",
        "https://proxy.example.com:3128",
        "socks5://proxy.example.com:1080",
    ],
)
def test_proxy_with_scheme_is_used_as_is_for_both_keys(cli, value):
    cli.options(proxy=value)
    report = cli.json()
    assert report.uv == {"http-proxy": value, "https-proxy": value}
    assert report.status("proxy") == "translated"


def test_proxy_without_scheme_gets_http_prefix(cli):
    cli.options(proxy="proxy.example.com:3128")
    report = cli.json()
    assert report.uv == {
        "http-proxy": "http://proxy.example.com:3128",
        "https-proxy": "http://proxy.example.com:3128",
    }


def test_proxy_has_a_note_about_older_uv(cli):
    cli.options(proxy="http://proxy.example.com:3128")
    message = " ".join(n["message"] for n in cli.json().notes(key="proxy"))
    assert "HTTP_PROXY" in message and "HTTPS_PROXY" in message


def test_proxy_is_not_exported_as_an_environment_variable(cli):
    cli.options(proxy="http://proxy.example.com:3128")
    assert cli.json().env == []


def test_no_proxy_means_no_proxy_keys(cli):
    cli.options(index_url=MIRROR)
    uv = cli.json().uv
    assert "http-proxy" not in uv and "https-proxy" not in uv


# -- settings that become environment variables ------------------------------

ENV_ROWS = [
    ("cert", "/etc/ssl/corp-ca.pem", "SSL_CERT_FILE", "/etc/ssl/corp-ca.pem"),
    ("client-cert", "/etc/ssl/client.pem", "SSL_CLIENT_CERT", "/etc/ssl/client.pem"),
    ("timeout", "60", "UV_HTTP_TIMEOUT", "60"),
    ("default-timeout", "45", "UV_HTTP_TIMEOUT", "45"),
    ("retries", "8", "UV_HTTP_RETRIES", "8"),
    ("constraint", "/srv/constraints.txt", "UV_CONSTRAINT", "/srv/constraints.txt"),
    ("constraint", "/srv/a.txt /srv/b.txt", "UV_CONSTRAINT", "/srv/a.txt /srv/b.txt"),
    ("build-constraint", "/srv/build.txt", "UV_BUILD_CONSTRAINT", "/srv/build.txt"),
]


@pytest.mark.parametrize("option, value, variable, expected", ENV_ROWS)
def test_option_becomes_an_environment_variable(cli, option, value, variable, expected):
    cli.user_conf(f"[global]\n{option} = {value}\n")
    report = cli.json()
    assert report.status(option) == "env"
    assert report.env == [{"name": variable, "value": expected, "from": option}]


@pytest.mark.parametrize("option, value, variable, expected", ENV_ROWS)
def test_environment_only_option_writes_nothing_to_uv_toml(cli, option, value, variable, expected):
    cli.user_conf(f"[global]\n{option} = {value}\n")
    assert cli.json().uv == {}


def test_constraints_on_several_lines_are_joined_with_spaces(cli):
    cli.user_conf("[global]\nconstraint =\n    /srv/a.txt\n    /srv/b.txt\n")
    assert cli.json().env_var("UV_CONSTRAINT")["value"] == "/srv/a.txt /srv/b.txt"


def test_cert_note_mentions_the_system_store_alternative(cli):
    cli.options(cert="/etc/ssl/corp-ca.pem")
    message = " ".join(n["message"] for n in cli.json().notes(key="cert"))
    assert "system-certs" in message


def test_cert_does_not_enable_system_certs_by_itself(cli):
    cli.options(cert="/etc/ssl/corp-ca.pem")
    assert "system-certs" not in cli.json().uv


def test_translated_options_do_not_appear_as_environment_variables(cli):
    cli.options(index_url=MIRROR, find_links="/srv/wheels", pre="true")
    assert cli.json().env == []


# -- no-cache-dir / cache-dir ------------------------------------------------


@pytest.mark.parametrize("word", ["true", "1", "yes", "on"])
def test_no_cache_dir_true_disables_the_cache_without_a_note(cli, word):
    cli.options(no_cache_dir=word)
    report = cli.json()
    assert report.uv == {"no-cache": True}
    assert report.status("no-cache-dir") == "translated"
    assert report.notes(key="no-cache-dir") == []


@pytest.mark.parametrize("word", ["false", "0", "no", "off"])
def test_no_cache_dir_false_still_disables_the_cache_and_explains_why(cli, word):
    cli.options(no_cache_dir=word)
    report = cli.json()
    assert report.uv == {"no-cache": True}
    assert report.status("no-cache-dir") == "translated"
    assert report.notes(key="no-cache-dir")


def test_cache_is_left_alone_when_no_cache_dir_is_absent(cli):
    cli.options(index_url=MIRROR)
    assert "no-cache" not in cli.json().uv


def test_cache_dir_is_ignored_with_a_note(cli):
    cli.options(cache_dir="/var/cache/pip")
    report = cli.json()
    assert report.status("cache-dir") == "ignored"
    assert report.uv == {}
    assert report.env == []
    assert report.notes(key="cache-dir")


# -- pre ---------------------------------------------------------------------


@pytest.mark.parametrize("word", ["true", "1", "yes"])
def test_pre_true_allows_prereleases(cli, word):
    cli.options(pre=word)
    report = cli.json()
    assert report.uv == {"prerelease": "allow"}
    assert report.status("pre") == "translated"


@pytest.mark.parametrize("word", ["false", "0", "no"])
def test_pre_false_is_ignored(cli, word):
    cli.options(pre=word)
    report = cli.json()
    assert report.status("pre") == "ignored"
    assert report.uv == {}


# -- no-binary / only-binary -------------------------------------------------

BINARY_ROWS = [
    ("no-binary", "no-binary", "no-binary-package"),
    ("only-binary", "no-build", "no-build-package"),
]


@pytest.mark.parametrize("option, all_key, package_key", BINARY_ROWS)
def test_binary_option_all(cli, option, all_key, package_key):
    cli.user_conf(f"[global]\n{option} = :all:\n")
    report = cli.json()
    assert report.uv == {all_key: True}
    assert report.status(option) == "translated"


@pytest.mark.parametrize("option, all_key, package_key", BINARY_ROWS)
def test_binary_option_none_is_ignored(cli, option, all_key, package_key):
    cli.user_conf(f"[global]\n{option} = :none:\n")
    report = cli.json()
    assert report.status(option) == "ignored"
    assert report.uv == {}


@pytest.mark.parametrize("option, all_key, package_key", BINARY_ROWS)
def test_binary_option_names_split_on_commas(cli, option, all_key, package_key):
    cli.user_conf(f"[global]\n{option} = numpy,scipy,lxml\n")
    report = cli.json()
    assert report.uv == {package_key: ["numpy", "scipy", "lxml"]}
    assert report.status(option) == "translated"


@pytest.mark.parametrize("option, all_key, package_key", BINARY_ROWS)
def test_binary_option_single_name_is_a_package_list(cli, option, all_key, package_key):
    cli.user_conf(f"[global]\n{option} = numpy\n")
    assert cli.json().uv == {package_key: ["numpy"]}


def test_no_binary_and_only_binary_do_not_cross_over(cli):
    cli.options(no_binary="numpy", only_binary=":all:")
    assert cli.json().uv == {"no-binary-package": ["numpy"], "no-build": True}


# -- no-build-isolation ------------------------------------------------------


# pip stores this option as "build isolation enabled" and assigns the parsed
# boolean from a config file or environment variable to it directly, so the
# value reads inverted: false disables isolation, true leaves it enabled.
# Checked against real pip: PIP_NO_BUILD_ISOLATION=0 gives build_isolation
# false, PIP_NO_BUILD_ISOLATION=1 gives build_isolation true.


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_no_build_isolation_false_disables_isolation(cli, value):
    cli.options(no_build_isolation=value)
    report = cli.json()
    assert report.uv == {"no-build-isolation": True}
    assert report.status("no-build-isolation") == "translated"
    assert report.notes(key="no-build-isolation")


@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_no_build_isolation_true_leaves_isolation_enabled(cli, value):
    cli.options(no_build_isolation=value)
    report = cli.json()
    assert "no-build-isolation" not in report.uv
    assert report.status("no-build-isolation") == "ignored"
    assert report.notes(key="no-build-isolation", level="warning")


# -- config-settings ---------------------------------------------------------


def test_config_settings_become_a_table(cli):
    cli.options(config_settings="editable_mode=compat builddir=out")
    report = cli.json()
    assert report.uv == {"config-settings": {"editable_mode": "compat", "builddir": "out"}}
    assert report.status("config-settings") == "translated"
    assert re.search(r"^\[config-settings\]\s*$", report.uv_toml, re.MULTILINE)


def test_config_settings_value_may_contain_an_equals_sign(cli):
    # ASSUMPTION: KEY=VALUE is split on the first "=" (as pip does).
    cli.options(config_settings="--build-option=--plat-name=linux_x86_64")
    assert cli.json().uv == {"config-settings": {"--build-option": "--plat-name=linux_x86_64"}}


def test_no_config_settings_means_no_table(cli):
    cli.options(pre="true")
    assert "config-settings" not in cli.json().uv


# -- keyring-provider --------------------------------------------------------


@pytest.mark.parametrize("value", ["subprocess", "disabled"])
def test_keyring_provider_supported_values_are_copied(cli, value):
    cli.options(keyring_provider=value)
    report = cli.json()
    assert report.uv == {"keyring-provider": value}
    assert report.status("keyring-provider") == "translated"


@pytest.mark.parametrize("value", ["auto", "import"])
def test_keyring_provider_unsupported_values(cli, value):
    cli.options(keyring_provider=value)
    report = cli.json()
    assert report.status("keyring-provider") == "unsupported"
    assert report.uv == {}


# -- uploaded-prior-to -------------------------------------------------------


@pytest.mark.parametrize("value", ["2025-06-01", "2025-06-01T12:30:00Z"])
def test_uploaded_prior_to_becomes_exclude_newer_with_a_note(cli, value):
    cli.options(uploaded_prior_to=value)
    report = cli.json()
    assert report.uv == {"exclude-newer": value}
    assert report.status("uploaded-prior-to") == "translated"
    assert report.notes(key="uploaded-prior-to")


def test_no_uploaded_prior_to_means_no_exclude_newer(cli):
    cli.options(pre="true")
    assert "exclude-newer" not in cli.json().uv


# -- [pip] table -------------------------------------------------------------


def test_require_hashes_goes_under_pip_with_a_note(cli):
    cli.options(require_hashes="true")
    report = cli.json()
    assert report.uv == {"pip": {"require-hashes": True}}
    assert report.status("require-hashes") == "translated"
    assert report.notes(key="require-hashes")
    assert re.search(r"^\[pip\]\s*$", report.uv_toml, re.MULTILINE)


def test_require_hashes_is_not_a_top_level_key(cli):
    cli.options(require_hashes="true")
    assert "require-hashes" not in cli.json().uv


@pytest.mark.parametrize(
    "option, value, expected",
    [
        ("break-system-packages", "true", {"break-system-packages": True}),
        ("no-deps", "true", {"no-deps": True}),
        ("no-dependencies", "true", {"no-deps": True}),
        ("target", "/opt/site", {"target": "/opt/site"}),
        ("prefix", "/opt/prefix", {"prefix": "/opt/prefix"}),
        ("python", "/usr/bin/python3.12", {"python": "/usr/bin/python3.12"}),
    ],
)
def test_uv_pip_only_options_go_under_pip_with_a_note(cli, option, value, expected):
    cli.user_conf(f"[global]\n{option} = {value}\n")
    report = cli.json()
    assert report.uv == {"pip": expected}
    assert report.status(option) == "translated"
    assert report.notes(key=option)


@pytest.mark.parametrize(
    "option", ["require-hashes", "break-system-packages", "no-deps", "no-dependencies"]
)
def test_false_uv_pip_boolean_is_not_enabled(cli, option):
    cli.user_conf(f"[global]\n{option} = false\n")
    pip_table = cli.json().uv.get("pip", {})
    assert all(value is not True for value in pip_table.values())


def test_several_uv_pip_options_share_one_table(cli):
    cli.options(require_hashes="1", no_deps="1", target="/opt/site")
    assert cli.json().uv == {
        "pip": {"require-hashes": True, "no-deps": True, "target": "/opt/site"}
    }


def test_top_level_options_do_not_create_a_pip_table(cli):
    cli.options(index_url=MIRROR, pre="true", find_links="/srv/wheels")
    assert "pip" not in cli.json().uv


# -- ignored -----------------------------------------------------------------

IGNORED = [
    ("require-virtualenv", "true"),
    ("require-venv", "true"),
    ("quiet", "1"),
    ("verbose", "2"),
    ("log", "/var/log/pip.log"),
    ("log-file", "/var/log/pip.log"),
    ("local-log", "/var/log/pip.log"),
    ("no-color", "true"),
    ("progress-bar", "off"),
    ("no-input", "true"),
    ("disable-pip-version-check", "true"),
    ("no-python-version-warning", "true"),
    ("root-user-action", "ignore"),
    ("no-warn-script-location", "true"),
    ("no-warn-conflicts", "true"),
    ("debug", "true"),
    ("isolated", "true"),
]


@pytest.mark.parametrize("option, value", IGNORED, ids=[o for o, _ in IGNORED])
def test_pip_only_option_is_ignored(cli, option, value):
    cli.user_conf(f"[global]\n{option} = {value}\n")
    report = cli.json()
    assert report.status(option) == "ignored"
    assert report.uv == {}
    assert report.env == []


# -- unsupported -------------------------------------------------------------

UNSUPPORTED = [
    ("prefer-binary", "true"),
    ("user", "true"),
    ("use-pep517", "true"),
    ("use-feature", "fast-deps"),
    ("use-deprecated", "legacy-resolver"),
    ("resume-retries", "3"),
    ("ignore-requires-python", "true"),
    ("src", "/opt/src"),
    ("platform", "manylinux2014_x86_64"),
    ("python-version", "3.11"),
    ("implementation", "cp"),
    ("abi", "cp311"),
    ("no-clean", "true"),
    ("exists-action", "w"),
    ("no-proxy-env", "true"),
    # "any other pip option not listed here"
    ("upgrade-strategy", "eager"),
    ("no-compile", "true"),
]


@pytest.mark.parametrize("option, value", UNSUPPORTED, ids=[o for o, _ in UNSUPPORTED])
def test_pip_option_without_uv_equivalent_is_unsupported(cli, option, value):
    cli.user_conf(f"[global]\n{option} = {value}\n")
    report = cli.json()
    assert report.status(option) == "unsupported"
    assert report.uv == {}
    assert report.env == []


def test_unsupported_setting_is_reported_not_dropped(cli):
    cli.options(prefer_binary="true", index_url=MIRROR)
    report = cli.json()
    assert sorted(report.keys) == ["index-url", "prefer-binary"]
    assert report.setting("prefer-binary")["value"] == "true"


# -- unknown -----------------------------------------------------------------


@pytest.mark.parametrize(
    "option", ["definitely-not-a-pip-option", "indexurl", "index-urls", "extra-index"]
)
def test_non_pip_option_is_unknown(cli, option):
    cli.user_conf(f"[global]\n{option} = https://mirror.example.com/simple\n")
    report = cli.json()
    assert report.status(option) == "unknown"
    assert report.uv == {}
    assert report.env == []


def test_unknown_environment_variable_option_is_unknown(cli):
    cli.setenv("PIP_DEFINITELY_NOT_AN_OPTION", "1")
    report = cli.json()
    assert report.status("definitely-not-an-option") == "unknown"


def test_known_options_are_never_unknown(cli):
    cli.options(index_url=MIRROR, prefer_binary="true", quiet="1", timeout="5")
    assert "unknown" not in [s["status"] for s in cli.json().settings]


# -- a realistic combination -------------------------------------------------


def test_combined_configuration(cli):
    cli.user_conf(
        "[global]\n"
        f"index-url = {MIRROR}\n"
        f"extra-index-url = {EXTRA_1}\n"
        "trusted-host = mirror.example.com\n"
        "timeout = 60\n"
        "no-cache-dir = false\n"
        "disable-pip-version-check = true\n"
        "[install]\n"
        "find-links = /srv/wheels\n"
        "no-binary = lxml\n"
        "require-hashes = true\n"
        "prefer-binary = true\n"
    )
    report = cli.json()
    assert report.uv == {
        "index-strategy": "unsafe-best-match",
        "find-links": ["/srv/wheels"],
        "allow-insecure-host": ["mirror.example.com"],
        "no-cache": True,
        "no-binary-package": ["lxml"],
        "index": report.uv["index"],
        "pip": {"require-hashes": True},
    }
    assert report.indexes == [("extra-1", EXTRA_1, False), ("primary", MIRROR, True)]
    assert report.env == [{"name": "UV_HTTP_TIMEOUT", "value": "60", "from": "timeout"}]
    assert {s["key"]: s["status"] for s in report.settings} == {
        "index-url": "translated",
        "extra-index-url": "translated",
        "trusted-host": "translated",
        "timeout": "env",
        "no-cache-dir": "translated",
        "disable-pip-version-check": "ignored",
        "find-links": "translated",
        "no-binary": "translated",
        "require-hashes": "translated",
        "prefer-binary": "unsupported",
    }
