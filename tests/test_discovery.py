"""Section 1 of the design: ``pipconf2uv.discover.candidate_files``.

The function is pure, so every platform is simulated here regardless of the
machine running the tests.
"""

import dataclasses

import pytest

from pipconf2uv.discover import ConfigFile, candidate_files

LINUX_HOME = "/home/alice"
MAC_HOME = "/Users/alice"
WIN_HOME = "C:\\Users\\alice"


def never_a_dir(path):
    return False


def always_a_dir(path):
    return True


def discover(platform, home, env=None, site_prefix=None, isdir=never_a_dir):
    found = candidate_files(
        env=env or {}, platform=platform, home=home, site_prefix=site_prefix, isdir=isdir
    )
    return [(c.kind, c.path) for c in found]


def paths(found, kind):
    return [path for k, path in found if k == kind]


# -- the ConfigFile type -----------------------------------------------------


def test_returns_a_list_of_configfile():
    found = candidate_files(env={}, platform="linux", home=LINUX_HOME, isdir=never_a_dir)
    assert isinstance(found, list)
    assert found and all(isinstance(c, ConfigFile) for c in found)


def test_configfile_has_kind_and_path_and_is_frozen():
    item = ConfigFile(kind="user", path="/x/pip.conf")
    assert (item.kind, item.path) == ("user", "/x/pip.conf")
    assert item == ConfigFile("user", "/x/pip.conf")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.path = "/y/pip.conf"


def test_isdir_argument_is_optional():
    # Linux never consults isdir, so the default must not matter.
    found = candidate_files(env={}, platform="linux", home=LINUX_HOME)
    assert [c.kind for c in found] == ["global", "global", "user", "user"]


# -- linux -------------------------------------------------------------------


def test_linux_defaults():
    assert discover("linux", LINUX_HOME) == [
        ("global", "/etc/xdg/pip/pip.conf"),
        ("global", "/etc/pip.conf"),
        ("user", "/home/alice/.pip/pip.conf"),
        ("user", "/home/alice/.config/pip/pip.conf"),
    ]


def test_linux_xdg_config_dirs_single_entry_replaces_the_default():
    found = discover("linux", LINUX_HOME, env={"XDG_CONFIG_DIRS": "/opt/xdg"})
    assert paths(found, "global") == ["/opt/xdg/pip/pip.conf", "/etc/pip.conf"]


def test_linux_xdg_config_dirs_several_entries_in_order_then_etc():
    found = discover(
        "linux", LINUX_HOME, env={"XDG_CONFIG_DIRS": "/opt/b:/opt/a:/usr/local/etc/xdg"}
    )
    assert paths(found, "global") == [
        "/opt/b/pip/pip.conf",
        "/opt/a/pip/pip.conf",
        "/usr/local/etc/xdg/pip/pip.conf",
        "/etc/pip.conf",
    ]


def test_linux_xdg_config_home_moves_the_second_user_file_only():
    found = discover("linux", LINUX_HOME, env={"XDG_CONFIG_HOME": "/data/cfg"})
    assert paths(found, "user") == [
        "/home/alice/.pip/pip.conf",
        "/data/cfg/pip/pip.conf",
    ]


def test_linux_does_not_use_isdir_for_the_user_file():
    found = discover("linux", LINUX_HOME, isdir=always_a_dir)
    assert paths(found, "user") == [
        "/home/alice/.pip/pip.conf",
        "/home/alice/.config/pip/pip.conf",
    ]


def test_linux_ignores_windows_variables():
    found = discover(
        "linux",
        LINUX_HOME,
        env={"APPDATA": "C:\\Roaming", "PROGRAMDATA": "C:\\PD"},
    )
    assert found == discover("linux", LINUX_HOME)


def test_other_posix_platforms_follow_the_linux_rules():
    # ASSUMPTION: "linux (and any other POSIX platform)" means an unrecognised
    # platform string such as "freebsd14" is accepted and treated like linux.
    env = {"XDG_CONFIG_DIRS": "/opt/xdg", "XDG_CONFIG_HOME": "/data/cfg"}
    assert discover("freebsd14", LINUX_HOME, env=env) == discover("linux", LINUX_HOME, env=env)


# -- darwin ------------------------------------------------------------------


def test_darwin_defaults_without_application_support_directory():
    assert discover("darwin", MAC_HOME, isdir=never_a_dir) == [
        ("global", "/Library/Application Support/pip/pip.conf"),
        ("user", "/Users/alice/.pip/pip.conf"),
        ("user", "/Users/alice/.config/pip/pip.conf"),
    ]


def test_darwin_uses_application_support_when_the_directory_exists():
    assert discover("darwin", MAC_HOME, isdir=always_a_dir) == [
        ("global", "/Library/Application Support/pip/pip.conf"),
        ("user", "/Users/alice/.pip/pip.conf"),
        ("user", "/Users/alice/Library/Application Support/pip/pip.conf"),
    ]


def test_darwin_asks_isdir_about_the_application_support_pip_directory():
    asked = []

    def isdir(path):
        asked.append(path)
        return True

    discover("darwin", MAC_HOME, isdir=isdir)
    assert "/Users/alice/Library/Application Support/pip" in asked


@pytest.mark.parametrize("isdir", [never_a_dir, always_a_dir])
def test_darwin_does_not_consult_xdg_config_home(isdir):
    found = discover("darwin", MAC_HOME, env={"XDG_CONFIG_HOME": "/data/cfg"}, isdir=isdir)
    assert found == discover("darwin", MAC_HOME, isdir=isdir)
    assert not any("/data/cfg" in path for _, path in found)


def test_darwin_global_does_not_use_xdg_config_dirs_or_etc():
    found = discover("darwin", MAC_HOME, env={"XDG_CONFIG_DIRS": "/opt/a:/opt/b"})
    assert paths(found, "global") == ["/Library/Application Support/pip/pip.conf"]


# -- win32 -------------------------------------------------------------------


def test_win32_defaults_when_appdata_and_programdata_are_unset():
    assert discover("win32", WIN_HOME) == [
        ("global", "C:\\ProgramData\\pip\\pip.ini"),
        ("user", "C:\\Users\\alice\\pip\\pip.ini"),
        ("user", "C:\\Users\\alice\\AppData\\Roaming\\pip\\pip.ini"),
    ]


def test_win32_honours_programdata():
    found = discover("win32", WIN_HOME, env={"PROGRAMDATA": "D:\\Shared"})
    assert paths(found, "global") == ["D:\\Shared\\pip\\pip.ini"]


def test_win32_honours_appdata_for_the_second_user_file_only():
    found = discover("win32", WIN_HOME, env={"APPDATA": "D:\\Roam"})
    assert paths(found, "user") == [
        "C:\\Users\\alice\\pip\\pip.ini",
        "D:\\Roam\\pip\\pip.ini",
    ]


def test_win32_ignores_xdg_variables_and_isdir():
    found = discover(
        "win32",
        WIN_HOME,
        env={"XDG_CONFIG_HOME": "/data/cfg", "XDG_CONFIG_DIRS": "/opt/a:/opt/b"},
        isdir=always_a_dir,
    )
    assert found == discover("win32", WIN_HOME)


def test_win32_paths_use_backslashes_and_pip_ini_on_any_host():
    found = discover("win32", WIN_HOME, site_prefix="C:\\venvs\\proj")
    for _, path in found:
        assert "/" not in path
        assert path.endswith("\\pip.ini")


@pytest.mark.parametrize("platform, home", [("linux", LINUX_HOME), ("darwin", MAC_HOME)])
def test_posix_paths_use_forward_slashes_and_pip_conf_on_any_host(platform, home):
    found = discover(platform, home, site_prefix="/venvs/proj")
    for _, path in found:
        assert "\\" not in path
        assert path.endswith("/pip.conf")


# -- site --------------------------------------------------------------------


@pytest.mark.parametrize(
    "platform, home", [("linux", LINUX_HOME), ("darwin", MAC_HOME), ("win32", WIN_HOME)]
)
def test_no_site_file_without_any_prefix(platform, home):
    assert paths(discover(platform, home), "site") == []


SITE_CASES = [
    ("linux", LINUX_HOME, "/venvs/{}", "/venvs/{}/pip.conf"),
    ("darwin", MAC_HOME, "/venvs/{}", "/venvs/{}/pip.conf"),
    ("win32", WIN_HOME, "C:\\venvs\\{}", "C:\\venvs\\{}\\pip.ini"),
]


@pytest.mark.parametrize("platform, home, prefix, expected", SITE_CASES)
def test_site_prefix_argument(platform, home, prefix, expected):
    found = discover(platform, home, site_prefix=prefix.format("arg"))
    assert paths(found, "site") == [expected.format("arg")]


@pytest.mark.parametrize("platform, home, prefix, expected", SITE_CASES)
def test_site_from_virtual_env(platform, home, prefix, expected):
    found = discover(platform, home, env={"VIRTUAL_ENV": prefix.format("venv")})
    assert paths(found, "site") == [expected.format("venv")]


@pytest.mark.parametrize("platform, home, prefix, expected", SITE_CASES)
def test_site_from_conda_prefix(platform, home, prefix, expected):
    found = discover(platform, home, env={"CONDA_PREFIX": prefix.format("conda")})
    assert paths(found, "site") == [expected.format("conda")]


@pytest.mark.parametrize("platform, home, prefix, expected", SITE_CASES)
def test_virtual_env_beats_conda_prefix(platform, home, prefix, expected):
    env = {
        "VIRTUAL_ENV": prefix.format("venv"),
        "CONDA_PREFIX": prefix.format("conda"),
    }
    assert paths(discover(platform, home, env=env), "site") == [expected.format("venv")]


@pytest.mark.parametrize("platform, home, prefix, expected", SITE_CASES)
def test_site_prefix_argument_beats_both_variables(platform, home, prefix, expected):
    env = {
        "VIRTUAL_ENV": prefix.format("venv"),
        "CONDA_PREFIX": prefix.format("conda"),
    }
    found = discover(platform, home, env=env, site_prefix=prefix.format("arg"))
    assert paths(found, "site") == [expected.format("arg")]


# -- env ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "platform, home", [("linux", LINUX_HOME), ("darwin", MAC_HOME), ("win32", WIN_HOME)]
)
def test_no_env_file_without_pip_config_file(platform, home):
    assert paths(discover(platform, home), "env") == []


@pytest.mark.parametrize(
    "platform, home, value",
    [
        ("linux", LINUX_HOME, "/work/ci/pip.conf"),
        ("darwin", MAC_HOME, "/work/ci/custom name.cfg"),
        ("win32", WIN_HOME, "D:\\ci\\pip-custom.ini"),
    ],
)
def test_pip_config_file_is_the_env_candidate_verbatim(platform, home, value):
    found = discover(platform, home, env={"PIP_CONFIG_FILE": value})
    assert paths(found, "env") == [value]
    assert found[-1] == ("env", value)


def test_pip_config_file_does_not_remove_other_candidates():
    # candidate_files does not check existence, so it cannot apply the
    # "env file exists -> user files not loaded" rule; that is the loader's job.
    with_env = discover("linux", LINUX_HOME, env={"PIP_CONFIG_FILE": "/work/pip.conf"})
    assert with_env[:-1] == discover("linux", LINUX_HOME)


# -- ordering ----------------------------------------------------------------

ORDER_CASES = [
    (
        "linux",
        LINUX_HOME,
        {
            "XDG_CONFIG_DIRS": "/opt/a:/opt/b",
            "XDG_CONFIG_HOME": "/data/cfg",
            "VIRTUAL_ENV": "/venvs/proj",
            "PIP_CONFIG_FILE": "/work/pip.conf",
        },
        [
            ("global", "/opt/a/pip/pip.conf"),
            ("global", "/opt/b/pip/pip.conf"),
            ("global", "/etc/pip.conf"),
            ("user", "/home/alice/.pip/pip.conf"),
            ("user", "/data/cfg/pip/pip.conf"),
            ("site", "/venvs/proj/pip.conf"),
            ("env", "/work/pip.conf"),
        ],
    ),
    (
        "darwin",
        MAC_HOME,
        {"CONDA_PREFIX": "/opt/conda/envs/proj", "PIP_CONFIG_FILE": "/work/pip.conf"},
        [
            ("global", "/Library/Application Support/pip/pip.conf"),
            ("user", "/Users/alice/.pip/pip.conf"),
            ("user", "/Users/alice/.config/pip/pip.conf"),
            ("site", "/opt/conda/envs/proj/pip.conf"),
            ("env", "/work/pip.conf"),
        ],
    ),
    (
        "win32",
        WIN_HOME,
        {
            "PROGRAMDATA": "D:\\PD",
            "APPDATA": "D:\\Roam",
            "VIRTUAL_ENV": "D:\\venvs\\proj",
            "PIP_CONFIG_FILE": "D:\\ci\\pip.ini",
        },
        [
            ("global", "D:\\PD\\pip\\pip.ini"),
            ("user", "C:\\Users\\alice\\pip\\pip.ini"),
            ("user", "D:\\Roam\\pip\\pip.ini"),
            ("site", "D:\\venvs\\proj\\pip.ini"),
            ("env", "D:\\ci\\pip.ini"),
        ],
    ),
]


@pytest.mark.parametrize(
    "platform, home, env, expected", ORDER_CASES, ids=[c[0] for c in ORDER_CASES]
)
def test_full_load_order_is_global_user_site_env(platform, home, env, expected):
    assert discover(platform, home, env=env) == expected


def test_does_not_check_that_files_exist():
    # Nothing under these made-up roots exists, yet every candidate is listed.
    found = discover(
        "linux",
        "/nonexistent-home-for-pipconf2uv-tests",
        env={"PIP_CONFIG_FILE": "/nonexistent-dir-for-pipconf2uv-tests/pip.conf"},
        site_prefix="/nonexistent-prefix-for-pipconf2uv-tests",
    )
    assert [kind for kind, _ in found] == ["global", "global", "user", "user", "site", "env"]


def test_does_not_mutate_the_env_mapping():
    env = {"XDG_CONFIG_DIRS": "/opt/a", "PIP_CONFIG_FILE": "/work/pip.conf"}
    before = dict(env)
    discover("linux", LINUX_HOME, env=env)
    assert env == before
