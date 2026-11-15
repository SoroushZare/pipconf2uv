"""Guard the discovery rules against the real pip on the host platform.

``pip config debug`` prints, per kind, the configuration files pip would
read.  The ``global`` and ``user`` lists must equal what ``candidate_files``
returns for the same platform and environment.  (``site`` is not compared:
pip uses its own ``sys.prefix``, this tool cannot.)
"""

import importlib.util
import os
import re
import subprocess
import sys

import pytest

from pipconf2uv.discover import candidate_files

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        importlib.util.find_spec("pip") is None,
        reason="pip is not installed in the test environment",
    ),
]

_SECTION = re.compile(r"^(?P<name>[A-Za-z_]+):\s*$")
_FILE = re.compile(r"^\s+(?P<path>.+), exists: (?:True|False)\s*$")
_KINDS = ("env_var", "env", "global", "site", "user")


def parse_pip_config_debug(output):
    """Return {kind: [path, ...]} from ``pip config debug``, or None if unrecognised."""
    files = {}
    current = None
    for line in output.splitlines():
        section = _SECTION.match(line)
        if section:
            current = section.group("name")
            if current in _KINDS:
                files.setdefault(current, [])
            continue
        entry = _FILE.match(line)
        if entry and current in files:
            files[current].append(entry.group("path"))
    if "global" not in files or "user" not in files:
        return None
    if not files["global"] or not files["user"]:
        return None
    return files


def test_parser_understands_the_documented_output_shape():
    sample = (
        "env_var:\n"
        "env:\n"
        "global:\n"
        "  /etc/xdg/pip/pip.conf, exists: False\n"
        "  /etc/pip.conf, exists: True\n"
        "    global.index-url: https://mirror.example.com/simple\n"
        "site:\n"
        "  /venv/pip.conf, exists: False\n"
        "user:\n"
        "  /home/alice/.pip/pip.conf, exists: False\n"
        "  /home/alice/.config/pip/pip.conf, exists: False\n"
    )
    assert parse_pip_config_debug(sample) == {
        "env_var": [],
        "env": [],
        "global": ["/etc/xdg/pip/pip.conf", "/etc/pip.conf"],
        "site": ["/venv/pip.conf"],
        "user": ["/home/alice/.pip/pip.conf", "/home/alice/.config/pip/pip.conf"],
    }
    assert parse_pip_config_debug("something else entirely\n") is None


def _clean_environment(tmp_path):
    """The host environment without anything that redirects pip's config."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("PIP_")
        and name.upper()
        not in ("XDG_CONFIG_HOME", "XDG_CONFIG_DIRS", "VIRTUAL_ENV", "CONDA_PREFIX")
    }
    if sys.platform == "win32":
        # pip asks the Windows shell API for these folders, not the
        # environment, so the real profile has to be used.
        home = os.path.expanduser("~")
    else:
        home = str(tmp_path / "home")
        os.makedirs(home)
        env["HOME"] = home
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env, home


def _normalise(paths):
    return [os.path.normcase(os.path.normpath(p)) for p in paths]


def test_global_and_user_files_match_real_pip(tmp_path):
    env, home = _clean_environment(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "config", "debug"],
        env=env,
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    output = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        pytest.skip(
            "`pip config debug` failed (exit {}); this pip may be too old".format(
                proc.returncode
            )
        )
    pip_files = parse_pip_config_debug(output)
    if pip_files is None:
        pytest.skip("the output format of `pip config debug` was not recognised")

    discover_env = dict(env)
    del discover_env["PIP_DISABLE_PIP_VERSION_CHECK"]
    ours = candidate_files(env=discover_env, platform=sys.platform, home=home)

    for kind in ("global", "user"):
        assert _normalise(c.path for c in ours if c.kind == kind) == _normalise(
            pip_files[kind]
        ), "{} files differ from pip's".format(kind)
