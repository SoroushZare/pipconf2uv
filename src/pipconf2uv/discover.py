"""Locate pip configuration files the way pip does.

Everything here is a pure function of its arguments. Paths are joined with the
conventions of the requested platform (``posixpath`` or ``ntpath``), not of the
machine running the code, so every platform's rules can be exercised anywhere.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

KIND_GLOBAL = "global"
KIND_USER = "user"
KIND_SITE = "site"
KIND_ENV = "env"

#: pip's load order; a later kind overrides an earlier one.
KIND_ORDER = (KIND_GLOBAL, KIND_USER, KIND_SITE, KIND_ENV)


@dataclass(frozen=True)
class ConfigFile:
    """A location pip would look at for configuration."""

    kind: str  # "global" | "user" | "site" | "env"
    path: str


def _get(env: Mapping[str, str], name: str) -> str | None:
    """Return a variable's value, treating an empty value as unset."""
    value = env.get(name)
    return value if value else None


def config_basename(platform: str) -> str:
    """Name of pip's configuration file on ``platform``."""
    return "pip.ini" if platform == "win32" else "pip.conf"


def is_null_device(path: str, platform: str) -> bool:
    """True when ``path`` is the null device pip treats as "load nothing"."""
    if platform == "win32":
        return path.lower() == "nul"
    return path == "/dev/null"


def candidate_files(
    *,
    env: Mapping[str, str],
    platform: str,
    home: str,
    site_prefix: str | None = None,
    isdir: Callable[[str], bool] = os.path.isdir,
) -> list[ConfigFile]:
    """Return every pip configuration file candidate in pip's load order.

    The result is ordered global, user, site, env. Files are not checked for
    existence, and the ``PIP_CONFIG_FILE`` rules (null device, suppression of
    the user files) are applied later, when the files are loaded.
    """
    name = config_basename(platform)
    files: list[ConfigFile] = []

    if platform == "win32":
        program_data = _get(env, "PROGRAMDATA") or "C:\\ProgramData"
        files.append(ConfigFile(KIND_GLOBAL, ntpath.join(program_data, "pip", name)))
        files.append(ConfigFile(KIND_USER, ntpath.join(home, "pip", name)))
        app_data = _get(env, "APPDATA") or ntpath.join(home, "AppData", "Roaming")
        files.append(ConfigFile(KIND_USER, ntpath.join(app_data, "pip", name)))
    elif platform == "darwin":
        files.append(
            ConfigFile(KIND_GLOBAL, posixpath.join("/Library/Application Support/pip", name))
        )
        files.append(ConfigFile(KIND_USER, posixpath.join(home, ".pip", name)))
        mac_dir = posixpath.join(home, "Library", "Application Support", "pip")
        user_dir = mac_dir if isdir(mac_dir) else posixpath.join(home, ".config", "pip")
        files.append(ConfigFile(KIND_USER, posixpath.join(user_dir, name)))
    else:
        xdg_dirs = _get(env, "XDG_CONFIG_DIRS") or "/etc/xdg"
        for entry in xdg_dirs.split(":"):
            if entry:
                files.append(ConfigFile(KIND_GLOBAL, posixpath.join(entry, "pip", name)))
        files.append(ConfigFile(KIND_GLOBAL, posixpath.join("/etc", name)))
        files.append(ConfigFile(KIND_USER, posixpath.join(home, ".pip", name)))
        xdg_home = _get(env, "XDG_CONFIG_HOME") or posixpath.join(home, ".config")
        files.append(ConfigFile(KIND_USER, posixpath.join(xdg_home, "pip", name)))

    prefix = site_prefix or _get(env, "VIRTUAL_ENV") or _get(env, "CONDA_PREFIX")
    if prefix:
        join = ntpath.join if platform == "win32" else posixpath.join
        files.append(ConfigFile(KIND_SITE, join(prefix, name)))

    env_file = _get(env, "PIP_CONFIG_FILE")
    if env_file:
        files.append(ConfigFile(KIND_ENV, env_file))

    return files


def uv_user_config_path(*, env: Mapping[str, str], platform: str, home: str) -> str:
    """Path of uv's user-level ``uv.toml`` on ``platform``."""
    if platform == "win32":
        app_data = _get(env, "APPDATA") or ntpath.join(home, "AppData", "Roaming")
        return ntpath.join(app_data, "uv", "uv.toml")
    xdg_home = _get(env, "XDG_CONFIG_HOME") or posixpath.join(home, ".config")
    return posixpath.join(xdg_home, "uv", "uv.toml")
