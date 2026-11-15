"""Shared fixtures for the pipconf2uv test suite.

The tests are written against ``docs/design.md`` only.  Everything here goes
through the public surface: ``pipconf2uv.cli.main`` and
``pipconf2uv.discover.candidate_files``.

The central piece is the ``cli`` fixture.  It builds an environment in which
no pip configuration of the machine running the tests can be picked up:
HOME, the XDG variables and the Windows folders all point into ``tmp_path``
and every ``PIP_*`` variable is removed.  A few system-wide locations
(``/etc/pip.conf``, ``/Library/Application Support/pip/pip.conf``) cannot be
redirected; if one of them exists on the host the test is skipped rather
than allowed to read it.
"""

import json
import os
import subprocess
import sys

import pytest

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover - Python 3.9 / 3.10
    try:
        import tomli as _toml
    except ImportError:
        _toml = None

IS_WINDOWS = sys.platform == "win32"

# Variables that would change what the tool reads or how it prints.
_REMOVED_EXACT = ("VIRTUAL_ENV", "CONDA_PREFIX", "FORCE_COLOR", "CLICOLOR_FORCE")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: compares the discovery rules with the pip installed in "
        "the test environment (skipped when pip is unavailable)",
    )


def parse_toml(text):
    """Parse TOML with tomllib, or tomli on Python < 3.11."""
    if _toml is None:  # pragma: no cover
        pytest.skip("neither tomllib nor tomli is available")
    return _toml.loads(text)


def _same_path(a, b):
    def norm(p):
        return os.path.normcase(os.path.normpath(str(p)))

    return norm(a) == norm(b)


def _is_under(path, root):
    path = os.path.normcase(os.path.normpath(str(path)))
    root = os.path.normcase(os.path.normpath(str(root)))
    return path == root or path.startswith(root + os.sep)


class Result:
    """Outcome of one CLI run."""

    def __init__(self, code, out, err):
        self.code = code
        self.out = out
        self.err = err

    @property
    def combined(self):
        return self.out + "\n" + self.err

    def __repr__(self):
        return f"Result(code={self.code!r})\n--- stdout ---\n{self.out}\n--- stderr ---\n{self.err}"


class Report:
    """The ``--format json`` document with a few readable accessors."""

    def __init__(self, data, result):
        self.data = data
        self.result = result

    def __getitem__(self, key):
        return self.data[key]

    # -- generated uv configuration -------------------------------------

    @property
    def uv(self):
        return self.data["uv"]

    @property
    def uv_toml(self):
        return self.data["uv_toml"]

    @property
    def indexes(self):
        """``[[index]]`` entries as (name, url, is_default) tuples, in order."""
        return [
            (entry["name"], entry["url"], bool(entry.get("default", False)))
            for entry in self.uv.get("index", [])
        ]

    # -- effective pip settings -------------------------------------------

    @property
    def settings(self):
        return self.data["settings"]

    @property
    def keys(self):
        return [s["key"] for s in self.settings]

    def setting(self, key):
        found = [s for s in self.settings if s["key"] == key]
        assert len(found) == 1, f"expected exactly one setting {key!r}, got {self.settings!r}"
        return found[0]

    def status(self, key):
        return self.setting(key)["status"]

    # -- environment variables and notes ----------------------------------

    @property
    def env(self):
        return self.data["env"]

    def env_var(self, name):
        found = [e for e in self.env if e["name"] == name]
        assert len(found) == 1, f"expected exactly one env entry {name!r}, got {self.env!r}"
        return found[0]

    def env_names(self):
        return [e["name"] for e in self.env]

    def notes(self, key=None, level=None):
        notes = self.data["notes"]
        if key is not None:
            notes = [n for n in notes if n.get("key") == key]
        if level is not None:
            notes = [n for n in notes if n["level"] == level]
        return notes

    def note_text(self):
        return "\n".join(n["message"] for n in self.data["notes"])

    # -- files ---------------------------------------------------------------

    def file(self, path):
        found = [f for f in self.data["files"] if _same_path(f["path"], path)]
        assert len(found) == 1, "expected {!r} once in files, got {!r}".format(
            str(path), self.data["files"]
        )
        return found[0]


class Cli:
    """Runs ``pipconf2uv.cli.main`` in-process inside the isolated environment."""

    def __init__(self, tmp_path, monkeypatch, capsys):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.capsys = capsys

        self.home = tmp_path / "home"
        self.xdg_config_home = tmp_path / "xdg-config-home"
        self.xdg_config_dir = tmp_path / "xdg-config-dir"
        self.appdata = self.home / "AppData" / "Roaming"
        self.programdata = tmp_path / "ProgramData"
        self.site_prefix = tmp_path / "venv"
        for directory in (
            self.home,
            self.xdg_config_home,
            self.xdg_config_dir,
            self.appdata,
            self.programdata,
            self.site_prefix,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        for name in list(os.environ):
            if name.upper().startswith("PIP_") or name.upper() in _REMOVED_EXACT:
                monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("USERPROFILE", str(self.home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(self.xdg_config_home))
        monkeypatch.setenv("XDG_CONFIG_DIRS", str(self.xdg_config_dir))
        monkeypatch.setenv("APPDATA", str(self.appdata))
        monkeypatch.setenv("PROGRAMDATA", str(self.programdata))
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.chdir(tmp_path)

        self._skip_if_host_config_can_leak()

    parse_toml = staticmethod(parse_toml)

    # -- environment -----------------------------------------------------

    def setenv(self, name, value):
        self.monkeypatch.setenv(name, str(value))

    def delenv(self, name):
        self.monkeypatch.delenv(name, raising=False)

    def subprocess_env(self):
        """The isolated environment, for ``python -m pipconf2uv`` child processes."""
        import pipconf2uv

        env = dict(os.environ)
        package_parent = os.path.dirname(os.path.dirname(os.path.abspath(pipconf2uv.__file__)))
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = package_parent + os.pathsep + existing if existing else package_parent
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def run_module(self, *args):
        """Run ``python -m pipconf2uv`` in a child process."""
        proc = subprocess.run(
            [sys.executable, "-m", "pipconf2uv"] + [str(a) for a in args],
            check=False,
            env=self.subprocess_env(),
            cwd=str(self.tmp_path),
            capture_output=True,
            timeout=120,
        )
        return Result(
            proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
        )

    # -- pip configuration files -----------------------------------------

    def candidates(self, site_prefix=None):
        from pipconf2uv.discover import candidate_files

        return candidate_files(
            env=dict(os.environ),
            platform=sys.platform,
            home=str(self.home),
            site_prefix=None if site_prefix is None else str(site_prefix),
        )

    def _skip_if_host_config_can_leak(self):
        for candidate in self.candidates():
            if not _is_under(candidate.path, self.tmp_path) and os.path.exists(candidate.path):
                pytest.skip(
                    "this machine has a system-wide pip configuration file that "
                    "cannot be isolated from the tests: " + candidate.path
                )

    def path_for(self, where):
        """Platform-correct location of a pip configuration file.

        ``where`` is one of ``"global"``, ``"user-legacy"``, ``"user"``,
        ``"site"`` (inside ``self.site_prefix``; pass ``--site-prefix`` or set
        ``VIRTUAL_ENV`` to have it read) and ``"env"`` (a file outside every
        default location, for ``PIP_CONFIG_FILE``).
        """
        if where == "env":
            return str(self.tmp_path / "custom" / "pip-from-env.conf")
        candidates = self.candidates(site_prefix=self.site_prefix)
        kind = "user" if where == "user-legacy" else where
        paths = [c.path for c in candidates if c.kind == kind]
        assert paths, f"candidate_files returned no {kind!r} file"
        path = paths[0] if where in ("user-legacy", "global", "site") else paths[-1]
        if not _is_under(path, self.tmp_path):
            pytest.skip(
                f"the {where} pip configuration file cannot be redirected into a "
                f"temporary directory on this platform: {path}"
            )
        return path

    def write_conf(self, where, text):
        """Write ``text`` as the pip configuration file at ``where``.

        For ``"env"`` the file is also selected through ``PIP_CONFIG_FILE``.
        Files are kept ASCII because pip reads them in the locale encoding.
        """
        path = self.path_for(where)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        if where == "env":
            self.setenv("PIP_CONFIG_FILE", path)
        return path

    def user_conf(self, text):
        """The common case: put ``text`` in the user's pip configuration file."""
        return self.write_conf("user", text)

    def options(self, section="global", **options):
        """Write ``[section]`` with ``options`` (``_`` in names becomes ``-``)."""
        lines = [f"[{section}]"]
        for name, value in options.items():
            lines.append("{} = {}".format(name.replace("_", "-"), value))
        return self.user_conf("\n".join(lines) + "\n")

    # -- running -----------------------------------------------------------

    def run(self, *args):
        from pipconf2uv.cli import main

        self.capsys.readouterr()  # drop anything printed before this run
        try:
            code = main([str(a) for a in args])
        except SystemExit as exc:  # argparse: --version, usage errors
            code = exc.code
        if code is None:
            code = 0
        captured = self.capsys.readouterr()
        return Result(code, captured.out, captured.err)

    def json(self, *args, expect_code=0):
        """Run with ``--format json`` and return the parsed :class:`Report`.

        Every report is checked for the invariant that the structured ``uv``
        object and the ``uv_toml`` text describe the same data.
        """
        result = self.run("--format", "json", *args)
        assert result.code == expect_code, result
        try:
            data = json.loads(result.out)
        except ValueError:
            pytest.fail(f"stdout is not a single JSON document:\n{result!r}")
        assert isinstance(data, dict), result
        assert parse_toml(data["uv_toml"]) == data["uv"], (
            "uv_toml does not parse to the same data as the uv object"
        )
        return Report(data, result)


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    return Cli(tmp_path, monkeypatch, capsys)
