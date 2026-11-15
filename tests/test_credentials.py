"""Credential handling ("Credentials" in section 3 of the design).

Security-critical: by default no part of a URL's user-info may reach stdout,
stderr or a written file, in any output format and through any code path
(normal output, notes, overridden values, untranslated sections, errors).

All credentials here are fake.
"""

import json
import os
import stat
import sys

import pytest

FORMATS = ["text", "toml", "json"]

USER = "build-bot"
SECRET = "s3cr3t-token"
AUTH = "{}:{}".format(USER, SECRET)

INDEX_WITH_AUTH = "https://{}@pkgs.example.com/simple".format(AUTH)
INDEX_CLEAN = "https://pkgs.example.com/simple"

# (id, config text, extra environment)
LEAK_SOURCES = [
    ("index-url", "[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH), {}),
    (
        "extra-index-url",
        "[global]\nextra-index-url = https://{}@extra.example.com/simple\n".format(AUTH),
        {},
    ),
    (
        "extra-index-url-second-of-two",
        "[global]\nextra-index-url = https://open.example.com/simple "
        "https://{}@extra.example.com/simple\n".format(AUTH),
        {},
    ),
    (
        "find-links",
        "[global]\nfind-links = https://{}@wheels.example.com/links/\n".format(AUTH),
        {},
    ),
    (
        "proxy",
        "[global]\nproxy = http://{}@proxy.example.com:3128\n".format(AUTH),
        {},
    ),
    (
        "proxy-without-scheme",
        "[global]\nproxy = {}@proxy.example.com:3128\n".format(AUTH),
        {},
    ),
    (
        "command-section",
        "[install]\nindex-url = {}\n".format(INDEX_WITH_AUTH),
        {},
    ),
    (
        "unselected-section",
        "[download]\nindex-url = {}\nproxy = http://{}@proxy.example.com:3128\n".format(
            INDEX_WITH_AUTH, AUTH
        ),
        {},
    ),
    (
        "overridden-by-command-section",
        "[global]\nindex-url = {}\n[install]\nindex-url = {}\n".format(
            INDEX_WITH_AUTH, INDEX_CLEAN
        ),
        {},
    ),
    (
        "overridden-by-env-var",
        "[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH),
        {"PIP_INDEX_URL": INDEX_CLEAN},
    ),
    ("env-var-index-url", "", {"PIP_INDEX_URL": INDEX_WITH_AUTH}),
    (
        "env-var-extra-index-url",
        "",
        {"PIP_EXTRA_INDEX_URL": "https://{}@extra.example.com/simple".format(AUTH)},
    ),
    ("env-var-proxy", "", {"PIP_PROXY": "http://{}@proxy.example.com:3128".format(AUTH)}),
    (
        "all-at-once",
        "[global]\n"
        "index-url = {index}\n"
        "extra-index-url = https://{auth}@extra.example.com/simple\n"
        "find-links = https://{auth}@wheels.example.com/links/\n"
        "proxy = http://{auth}@proxy.example.com:3128\n"
        "trusted-host = pkgs.example.com\n".format(index=INDEX_WITH_AUTH, auth=AUTH),
        {},
    ),
]


def setup_source(cli, text, env):
    if text:
        cli.user_conf(text)
    for name, value in env.items():
        cli.setenv(name, value)


def assert_absent(result, *needles):
    for needle in needles:
        assert needle not in result.out, "{!r} leaked to stdout:\n{}".format(
            needle, result.out
        )
        assert needle not in result.err, "{!r} leaked to stderr:\n{}".format(
            needle, result.err
        )


# -- nothing leaks by default ------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "text, env", [c[1:] for c in LEAK_SOURCES], ids=[c[0] for c in LEAK_SOURCES]
)
def test_password_never_appears_in_any_output(cli, fmt, text, env):
    setup_source(cli, text, env)
    result = cli.run("--format", fmt)
    assert result.code == 0, result
    assert_absent(result, SECRET)


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "text, env", [c[1:] for c in LEAK_SOURCES], ids=[c[0] for c in LEAK_SOURCES]
)
def test_username_never_appears_in_any_output(cli, fmt, text, env):
    # The whole user-info is replaced by ****; in "token@" form the user
    # name *is* the secret, so it must not be shown either.
    setup_source(cli, text, env)
    result = cli.run("--format", fmt)
    assert result.code == 0, result
    assert_absent(result, USER)


@pytest.mark.parametrize("fmt", FORMATS)
def test_strict_failure_does_not_leak(cli, fmt):
    cli.user_conf(
        "[global]\nindex-url = {}\nprefer-binary = true\nnot-an-option = 1\n".format(
            INDEX_WITH_AUTH
        )
    )
    result = cli.run("--format", fmt, "--strict")
    assert result.code == 1, result
    assert_absent(result, SECRET)


@pytest.mark.parametrize("fmt", FORMATS)
def test_credentials_in_an_unsupported_or_unknown_value_do_not_leak(cli, fmt):
    # ASSUMPTION: masking is applied to every displayed value that contains
    # URL user-info, not only to the three options the design names.
    cli.user_conf(
        "[global]\nsrc = https://{auth}@src.example.com/\nnot-an-option = https://{auth}@x.example.com/\n".format(
            auth=AUTH
        )
    )
    result = cli.run("--format", fmt)
    assert result.code == 0, result
    assert_absent(result, SECRET)


# -- error messages ----------------------------------------------------------

BROKEN_FILES = [
    # No section header: configparser quotes the offending line in its error.
    ("missing-section-header", "index-url = {}\n[global]\npre = true\n".format(INDEX_WITH_AUTH)),
    # Duplicate option in one section.
    (
        "duplicate-option",
        "[global]\nindex-url = {0}\nindex-url = {0}\n".format(INDEX_WITH_AUTH),
    ),
    # Same, with the credential on a later line of a headerless file.
    (
        "missing-section-header-later-line",
        "pre = true\nextra-index-url = {}\n".format(INDEX_WITH_AUTH),
    ),
]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "text", [c[1] for c in BROKEN_FILES], ids=[c[0] for c in BROKEN_FILES]
)
def test_parse_error_does_not_leak_credentials(cli, fmt, text):
    cli.user_conf(text)
    result = cli.run("--format", fmt)
    assert result.code == 2, result
    assert_absent(result, SECRET)
    assert "Traceback" not in result.err


# -- masking in the settings listing ----------------------------------------


def test_json_setting_value_shows_masked_user_info(cli):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    report = cli.json()
    assert report.setting("index-url")["value"] == "https://****@pkgs.example.com/simple"


@pytest.mark.parametrize(
    "option, value",
    [
        ("extra-index-url", "https://{}@extra.example.com/simple".format(AUTH)),
        ("find-links", "https://{}@wheels.example.com/links/".format(AUTH)),
        ("proxy", "http://{}@proxy.example.com:3128".format(AUTH)),
    ],
)
def test_json_setting_values_are_masked_for_every_credential_option(cli, option, value):
    cli.user_conf("[global]\n{} = {}\n".format(option, value))
    shown = cli.json().setting(option)["value"]
    assert "****@" in shown
    assert SECRET not in shown and USER not in shown


def test_text_output_shows_the_mask(cli):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    result = cli.run()
    assert result.code == 0, result
    assert "https://****@pkgs.example.com/simple" in result.out


def test_values_without_user_info_are_not_masked(cli):
    cli.user_conf(
        "[global]\nindex-url = {}\nproxy = http://proxy.example.com:3128\n".format(INDEX_CLEAN)
    )
    report = cli.json()
    assert report.setting("index-url")["value"] == INDEX_CLEAN
    assert report.setting("proxy")["value"] == "http://proxy.example.com:3128"
    assert "****" not in report.result.out


# -- generated configuration: user-info removed ------------------------------


def test_index_url_user_info_is_removed_from_uv_config(cli):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    report = cli.json()
    assert report.indexes == [("primary", INDEX_CLEAN, True)]
    assert "@" not in report.uv_toml


def test_extra_index_url_user_info_is_removed_from_uv_config(cli):
    cli.user_conf(
        "[global]\nextra-index-url = https://open.example.com/simple "
        "https://{}@extra.example.com/simple\n".format(AUTH)
    )
    assert [url for _, url, _ in cli.json().indexes] == [
        "https://open.example.com/simple",
        "https://extra.example.com/simple",
    ]


def test_find_links_user_info_is_removed_from_uv_config(cli):
    cli.user_conf(
        "[global]\nfind-links = https://{}@wheels.example.com/links/ /srv/wheels\n".format(AUTH)
    )
    assert cli.json().uv["find-links"] == [
        "https://wheels.example.com/links/",
        "/srv/wheels",
    ]


def test_proxy_user_info_is_removed_from_uv_config(cli):
    cli.user_conf("[global]\nproxy = http://{}@proxy.example.com:3128\n".format(AUTH))
    assert cli.json().uv == {
        "http-proxy": "http://proxy.example.com:3128",
        "https-proxy": "http://proxy.example.com:3128",
    }


def test_scheme_less_proxy_user_info_is_removed_from_uv_config(cli):
    cli.user_conf("[global]\nproxy = {}@proxy.example.com:3128\n".format(AUTH))
    assert cli.json().uv == {
        "http-proxy": "http://proxy.example.com:3128",
        "https-proxy": "http://proxy.example.com:3128",
    }


# -- notes on how to supply the credentials ----------------------------------


def test_note_names_the_variables_for_the_primary_index(cli):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    text = cli.json().note_text()
    assert "UV_INDEX_PRIMARY_USERNAME" in text
    assert "UV_INDEX_PRIMARY_PASSWORD" in text


def test_note_names_the_variables_for_the_right_extra_index(cli):
    cli.user_conf(
        "[global]\nextra-index-url = https://open.example.com/simple "
        "https://{}@extra.example.com/simple\n".format(AUTH)
    )
    text = cli.json().note_text()
    assert "UV_INDEX_EXTRA_2_USERNAME" in text
    assert "UV_INDEX_EXTRA_2_PASSWORD" in text
    assert "UV_INDEX_EXTRA_1_" not in text
    assert "UV_INDEX_PRIMARY_" not in text


def test_note_names_each_index_that_had_credentials(cli):
    cli.user_conf(
        "[global]\nindex-url = {}\n"
        "extra-index-url = https://{}@extra.example.com/simple\n".format(INDEX_WITH_AUTH, AUTH)
    )
    text = cli.json().note_text()
    for name in (
        "UV_INDEX_PRIMARY_USERNAME",
        "UV_INDEX_PRIMARY_PASSWORD",
        "UV_INDEX_EXTRA_1_USERNAME",
        "UV_INDEX_EXTRA_1_PASSWORD",
    ):
        assert name in text


def test_token_only_user_info_still_gets_an_index_variable_note(cli):
    # ASSUMPTION: for "token@host" the note names at least the USERNAME
    # variable of the index (uv sends a lone token as the user name).
    cli.user_conf("[global]\nindex-url = https://tok-9f8e7d6c5b@pkgs.example.com/simple\n")
    assert "UV_INDEX_PRIMARY_USERNAME" in cli.json().note_text()


def test_proxy_note_names_https_proxy_and_no_index_variable(cli):
    cli.user_conf("[global]\nproxy = http://{}@proxy.example.com:3128\n".format(AUTH))
    text = cli.json().note_text()
    assert "HTTPS_PROXY" in text
    assert "UV_INDEX_" not in text


def test_no_credential_note_without_credentials(cli):
    cli.user_conf(
        "[global]\nindex-url = {}\nextra-index-url = https://open.example.com/simple\n".format(
            INDEX_CLEAN
        )
    )
    text = cli.json().note_text()
    assert "UV_INDEX_" not in text
    assert "_USERNAME" not in text and "_PASSWORD" not in text


def test_credentials_are_not_put_in_the_env_listing(cli):
    cli.user_conf(
        "[global]\nindex-url = {}\nproxy = http://{}@proxy.example.com:3128\n".format(
            INDEX_WITH_AUTH, AUTH
        )
    )
    for entry in cli.json().env:
        assert SECRET not in entry["value"]


# -- odd user-info shapes ----------------------------------------------------

ODD_INDEX_URLS = [
    (
        "token-only",
        "https://tok-9f8e7d6c5b@pkgs.example.com/simple/",
        "https://pkgs.example.com/simple/",
        ["tok-9f8e7d6c5b"],
    ),
    (
        "percent-encoded",
        "https://build-bot:p%40ss%2Fw0rd-XYZ@pkgs.example.com/simple",
        "https://pkgs.example.com/simple",
        ["p%40ss%2Fw0rd-XYZ", "p@ss/w0rd-XYZ", "w0rd-XYZ", "build-bot"],
    ),
    (
        "at-sign-in-password",
        "https://build-bot:pa@ss-QQ9z@pkgs.example.com/simple",
        "https://pkgs.example.com/simple",
        ["pa@ss-QQ9z", "ss-QQ9z", "build-bot"],
    ),
    (
        "colon-in-password",
        "https://build-bot:first-KK1:second-KK2@pkgs.example.com/simple",
        "https://pkgs.example.com/simple",
        ["first-KK1", "second-KK2", "build-bot"],
    ),
    (
        "ipv6-host",
        "https://build-bot:s3cr3t-token@[2001:db8::1]:8443/simple",
        "https://[2001:db8::1]:8443/simple",
        ["s3cr3t-token", "build-bot"],
    ),
    (
        "port",
        "https://build-bot:s3cr3t-token@pkgs.example.com:8443/simple",
        "https://pkgs.example.com:8443/simple",
        ["s3cr3t-token", "build-bot"],
    ),
    (
        "empty-password",
        "https://tok-9f8e7d6c5b:@pkgs.example.com/simple",
        "https://pkgs.example.com/simple",
        ["tok-9f8e7d6c5b"],
    ),
    (
        "query-and-fragment",
        "https://build-bot:s3cr3t-token@pkgs.example.com/simple?format=json#top",
        "https://pkgs.example.com/simple?format=json#top",
        ["s3cr3t-token", "build-bot"],
    ),
    (
        "http-scheme",
        "http://build-bot:s3cr3t-token@pkgs.example.com/simple",
        "http://pkgs.example.com/simple",
        ["s3cr3t-token", "build-bot"],
    ),
]


@pytest.mark.parametrize(
    "url, clean, secrets",
    [c[1:] for c in ODD_INDEX_URLS],
    ids=[c[0] for c in ODD_INDEX_URLS],
)
def test_odd_user_info_is_stripped_from_the_index_url(cli, url, clean, secrets):
    cli.user_conf("[global]\nindex-url = {}\n".format(url))
    assert cli.json().indexes == [("primary", clean, True)]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "url, clean, secrets",
    [c[1:] for c in ODD_INDEX_URLS],
    ids=[c[0] for c in ODD_INDEX_URLS],
)
def test_odd_user_info_never_appears_in_any_output(cli, fmt, url, clean, secrets):
    cli.user_conf("[global]\nindex-url = {}\n".format(url))
    result = cli.run("--format", fmt)
    assert result.code == 0, result
    assert_absent(result, *secrets)


@pytest.mark.parametrize(
    "url, clean, secrets",
    [c[1:] for c in ODD_INDEX_URLS],
    ids=[c[0] for c in ODD_INDEX_URLS],
)
def test_odd_user_info_is_masked_in_the_setting_value(cli, url, clean, secrets):
    cli.user_conf("[global]\nindex-url = {}\n".format(url))
    shown = cli.json().setting("index-url")["value"]
    assert shown == clean.replace("://", "://****@", 1)


# -- things that look like credentials but are not ---------------------------

NOT_CREDENTIALS = [
    "/srv/wheels",
    "./wheels",
    "/opt/mirror@v2/wheels",
    "relative/dir@2024",
    "file:///srv/wheels@2/",
    "https://wheels.example.com/@scope/links/",
    "https://wheels.example.com/links/?owner=team@example.com",
]


@pytest.mark.parametrize("value", NOT_CREDENTIALS)
def test_find_links_without_user_info_is_left_intact(cli, value):
    cli.user_conf("[global]\nfind-links = {}\n".format(value))
    report = cli.json()
    assert report.uv["find-links"] == [value]
    assert report.setting("find-links")["value"] == value
    assert "UV_INDEX_" not in report.note_text()


def test_windows_path_in_find_links_is_left_intact(cli):
    value = "C:\\wheels@home\\dir"
    cli.user_conf("[global]\nfind-links = {}\n".format(value))
    report = cli.json()
    assert report.uv["find-links"] == [value]
    assert report.setting("find-links")["value"] == value


def test_at_sign_in_index_path_is_not_treated_as_user_info(cli):
    url = "https://pkgs.example.com/team@eu/simple"
    cli.user_conf("[global]\nindex-url = {}\n".format(url))
    report = cli.json()
    assert report.indexes == [("primary", url, True)]
    assert report.setting("index-url")["value"] == url


# -- --include-credentials ---------------------------------------------------


def test_include_credentials_keeps_user_info_in_the_generated_toml(cli):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    report = cli.json("--include-credentials")
    assert report.indexes == [("primary", INDEX_WITH_AUTH, True)]
    assert SECRET in report.uv_toml


@pytest.mark.parametrize(
    "option, value, uv_key",
    [
        ("find-links", "https://{}@wheels.example.com/links/".format(AUTH), "find-links"),
        ("proxy", "http://{}@proxy.example.com:3128".format(AUTH), "https-proxy"),
    ],
)
def test_include_credentials_applies_to_find_links_and_proxy(cli, option, value, uv_key):
    cli.user_conf("[global]\n{} = {}\n".format(option, value))
    report = cli.json("--include-credentials")
    kept = report.uv[uv_key]
    assert kept == value or kept == [value]


def test_include_credentials_does_not_unmask_json_settings_or_notes(cli):
    cli.user_conf(
        "[global]\nindex-url = {}\nproxy = http://{}@proxy.example.com:3128\n".format(
            INDEX_WITH_AUTH, AUTH
        )
    )
    report = cli.json("--include-credentials")
    assert report.setting("index-url")["value"] == "https://****@pkgs.example.com/simple"
    # The secret may live in the generated configuration and nowhere else.
    rest = {k: v for k, v in report.data.items() if k not in ("uv", "uv_toml")}
    assert SECRET not in json.dumps(rest)
    assert SECRET not in report.result.err


def test_include_credentials_text_shows_secret_only_inside_the_generated_toml(cli):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    toml_only = cli.run("--format", "toml", "--include-credentials")
    text = cli.run("--include-credentials")
    assert toml_only.code == 0 and text.code == 0
    in_toml = toml_only.out.count(SECRET)
    assert in_toml >= 1
    # The text report embeds the generated TOML; the secret appears there
    # exactly as often as in the TOML itself, so nowhere else.
    assert text.out.count(SECRET) == in_toml
    assert "https://****@pkgs.example.com/simple" in text.out


@pytest.mark.parametrize("fmt", FORMATS)
def test_include_credentials_never_writes_the_secret_to_stderr(cli, fmt):
    cli.user_conf(
        "[global]\nindex-url = {}\ntrusted-host = pkgs.example.com\n".format(INDEX_WITH_AUTH)
    )
    result = cli.run("--format", fmt, "--include-credentials")
    assert result.code == 0, result
    assert SECRET not in result.err


def test_include_credentials_does_not_unmask_overridden_or_unselected_values(cli):
    other = "0ther-s3cr3t-value"
    cli.user_conf(
        "[global]\nindex-url = https://old-user:{other}@old.example.com/simple\n"
        "[install]\nindex-url = {index}\n"
        "[download]\nindex-url = https://dl-user:{other}@dl.example.com/simple\n".format(
            other=other, index=INDEX_WITH_AUTH
        )
    )
    for fmt in FORMATS:
        result = cli.run("--format", fmt, "--include-credentials")
        assert result.code == 0, result
        assert_absent(result, other)


@pytest.mark.parametrize("fmt", FORMATS)
def test_include_credentials_changes_nothing_without_credentials(cli, fmt):
    cli.user_conf("[global]\nindex-url = {}\nfind-links = /srv/wheels\n".format(INDEX_CLEAN))
    plain = cli.run("--format", fmt)
    included = cli.run("--format", fmt, "--include-credentials")
    assert (included.code, included.out) == (plain.code, plain.out)


@pytest.mark.parametrize("fmt", FORMATS)
def test_include_credentials_parse_error_still_does_not_leak(cli, fmt):
    cli.user_conf("index-url = {}\n".format(INDEX_WITH_AUTH))
    result = cli.run("--format", fmt, "--include-credentials")
    assert result.code == 2, result
    assert_absent(result, SECRET)


# -- written files -----------------------------------------------------------


def test_written_file_has_no_credentials_by_default(cli):
    cli.user_conf(
        "[global]\nindex-url = {}\nproxy = http://{}@proxy.example.com:3128\n".format(
            INDEX_WITH_AUTH, AUTH
        )
    )
    target = cli.tmp_path / "out" / "uv.toml"
    result = cli.run("--write", target)
    assert result.code == 0, result
    content = target.read_text(encoding="utf-8")
    assert SECRET not in content and USER not in content
    assert_absent(result, SECRET)


def test_written_file_keeps_credentials_with_include_credentials(cli):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    target = cli.tmp_path / "out" / "uv.toml"
    result = cli.run("--write", target, "--include-credentials")
    assert result.code == 0, result
    assert INDEX_WITH_AUTH in target.read_text(encoding="utf-8")


def test_writing_with_credentials_does_not_print_the_secret_outside_the_toml(cli):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    target = cli.tmp_path / "out" / "uv.toml"
    result = cli.run("--format", "json", "--write", target, "--include-credentials")
    assert result.code == 0, result
    assert SECRET not in result.err


posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX file modes do not apply on Windows"
)


@pytest.fixture
def open_umask():
    """A permissive umask, so 0600 can only come from an explicit mode."""
    old = os.umask(0)
    try:
        yield
    finally:
        os.umask(old)


@posix_only
def test_file_written_with_credentials_has_mode_0600(cli, open_umask):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    target = cli.tmp_path / "out" / "uv.toml"
    result = cli.run("--write", target, "--include-credentials")
    assert result.code == 0, result
    assert stat.S_IMODE(os.stat(str(target)).st_mode) == 0o600


@posix_only
def test_file_overwritten_with_credentials_has_mode_0600(cli, open_umask):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    target = cli.tmp_path / "uv.toml"
    target.write_text("# old\n", encoding="utf-8")
    os.chmod(str(target), 0o644)
    result = cli.run("--write", target, "--include-credentials", "--force")
    assert result.code == 0, result
    assert SECRET in target.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(str(target)).st_mode) == 0o600


@posix_only
def test_no_temporary_file_with_credentials_is_left_behind(cli, open_umask):
    cli.user_conf("[global]\nindex-url = {}\n".format(INDEX_WITH_AUTH))
    out_dir = cli.tmp_path / "out"
    result = cli.run("--write", out_dir / "uv.toml", "--include-credentials")
    assert result.code == 0, result
    assert sorted(p.name for p in out_dir.iterdir()) == ["uv.toml"]
