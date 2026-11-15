"""Load pip configuration files and compute the effective settings.

This module also owns the single credential-redaction helper, :func:`redact`.
Every user-visible string that may contain a URL goes through it.
"""

from __future__ import annotations

import configparser
import locale
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable

from pipconf2uv.discover import KIND_ENV, KIND_ORDER, KIND_USER, ConfigFile, is_null_device

MASK = "****"
KIND_ENV_VAR = "env-var"
GLOBAL_SECTION = "global"

#: ``PIP_*`` variables that are not options.
_NON_OPTION_VARIABLES = frozenset({"version", "help", "config-file"})

# scheme://user-info@ (the scheme may be missing, as in "//user:pw@host"); greedy up to the last "@" of the authority so that a
# password containing "@" is covered in full.
_URL_USERINFO = re.compile(r"((?:[A-Za-z][A-Za-z0-9+.\-]*:)?//)[^/?#\s]*@")
# user:password@host with no scheme (pip accepts this for the proxy).
_BARE_USERINFO = re.compile(r"[^\s/@:='\"]+:[^\s/]*@(?=[^\s/@])")


def _mask_bare_userinfo(match: re.Match[str]) -> str:
    """Mask ``user:password@`` unless it is a Windows path such as ``C:\\dir@name``."""
    user, _, password = match.group(0)[:-1].partition(":")
    if len(user) == 1 and user.isalpha() and password[:1] in ("\\", "/"):
        return match.group(0)
    return MASK + "@"


def redact(text: str, key: str | None = None) -> str:
    """Replace user-info in every URL found in ``text`` with ``****``.

    ``key`` is the pip option the text is a value of, when known. A proxy
    value may carry user-info without a scheme or a password (``token@host``),
    which only the option name makes recognisable.
    """
    text = _URL_USERINFO.sub(lambda match: match.group(1) + MASK + "@", text)
    text = _BARE_USERINFO.sub(_mask_bare_userinfo, text)
    if key == "proxy" and "://" not in text and "@" in text and not text.startswith(MASK + "@"):
        text = MASK + "@" + text.rsplit("@", 1)[1]
    return text


def has_credentials(url: str) -> bool:
    """True when ``url`` carries user-info."""
    return _URL_USERINFO.search(url) is not None


def strip_credentials(url: str) -> str:
    """Remove user-info from ``url``."""
    return _URL_USERINFO.sub(lambda match: match.group(1), url)


def redact_tree(value: Any) -> Any:
    """Apply :func:`redact` to every string in a nested dict/list structure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_tree(item) for key, item in value.items()}
    return value


def normalize_name(name: str) -> str:
    """Normalise an option name the way pip does."""
    name = name.lower().replace("_", "-")
    name = name.removeprefix("--")
    return name


class ConfigError(Exception):
    """A pip configuration file exists but cannot be read or parsed."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class Source:
    """Where a value comes from: a section of a file, or an environment variable."""

    kind: str  # "global" | "user" | "site" | "env" | "env-var"
    path: str | None = None
    section: str | None = None
    variable: str | None = None

    def as_dict(self) -> dict[str, str]:
        if self.kind == KIND_ENV_VAR:
            return {"kind": self.kind, "variable": self.variable or ""}
        return {"kind": self.kind, "path": self.path or "", "section": self.section or ""}


@dataclass(frozen=True)
class Setting:
    """One effective pip setting."""

    key: str
    value: str
    source: Source
    overrides: tuple[Source, ...] = ()
    #: Position in the precedence order among all effective settings; a higher
    #: number wins when two option names are aliases of each other.
    rank: int = 0

    @property
    def display_value(self) -> str:
        """The value with credentials masked; the only form that may be shown."""
        return redact(" ".join(self.value.split()), self.key)


@dataclass(frozen=True)
class FileStatus:
    kind: str
    path: str
    exists: bool
    loaded: bool


@dataclass
class PipConfig:
    command: str
    files: list[FileStatus] = field(default_factory=list)
    settings: list[Setting] = field(default_factory=list)
    #: ``PIP_*`` variables that supplied an option, sorted by name.
    variables: list[str] = field(default_factory=list)
    #: Sections that hold settings but are neither ``[global]`` nor the selected
    #: command, with the number of settings found in each.
    other_sections: dict[str, int] = field(default_factory=dict)
    #: Why files were skipped because of ``PIP_CONFIG_FILE``, if they were.
    null_device: bool = False
    user_files_skipped: bool = False


def _parse_error(path: str, exc: Exception) -> ConfigError:
    """Describe a parse failure without quoting the file's content.

    configparser messages embed the offending line, which may hold a URL with
    credentials, so only the error type and line numbers are reported.
    """
    reason = "not a valid pip configuration file"
    if isinstance(exc, configparser.MissingSectionHeaderError):
        reason = f"no section header before line {exc.lineno}"
    elif isinstance(exc, configparser.ParsingError):
        lines = sorted({error[0] for error in getattr(exc, "errors", [])})
        if lines:
            plural = "s" if len(lines) > 1 else ""
            reason = f"cannot parse line{plural} {', '.join(str(n) for n in lines)}"
    elif isinstance(exc, configparser.DuplicateSectionError):
        reason = f"section [{exc.section}] appears twice (line {exc.lineno})"
    elif isinstance(exc, configparser.DuplicateOptionError):
        reason = f"option {exc.option!r} appears twice in [{exc.section}] (line {exc.lineno})"
    elif isinstance(exc, UnicodeDecodeError):
        reason = f"cannot decode the file as {exc.encoding}"
    elif isinstance(exc, OSError):
        reason = f"cannot read the file ({exc.strerror or type(exc).__name__})"
    return ConfigError(path, redact(reason))


def read_file(path: str) -> dict[str, dict[str, str]]:
    """Parse one pip configuration file into ``{section: {option: value}}``."""
    parser = configparser.RawConfigParser()
    try:
        with open(path, encoding=locale.getpreferredencoding(False)) as handle:
            parser.read_file(handle)
    except (configparser.Error, UnicodeDecodeError, OSError) as exc:
        raise _parse_error(path, exc) from None
    result: dict[str, dict[str, str]] = {}
    for section in parser.sections():
        options: dict[str, str] = {}
        for name, value in parser.items(section):
            options[normalize_name(name)] = (value or "").strip()
        result[section] = options
    return result


def load(
    candidates: Sequence[ConfigFile],
    *,
    env: Mapping[str, str],
    platform: str,
    command: str = "install",
    use_env: bool = True,
    exists: Callable[[str], bool] = os.path.exists,
    read: Callable[[str], dict[str, dict[str, str]]] = read_file,
) -> PipConfig:
    """Load ``candidates`` and ``PIP_*`` variables into the effective settings.

    Raises :class:`ConfigError` for a file that exists but cannot be parsed.
    """
    command = normalize_name(command)
    config = PipConfig(command=command)

    env_file = env.get("PIP_CONFIG_FILE") or None
    config.null_device = env_file is not None and is_null_device(env_file, platform)
    config.user_files_skipped = env_file is not None and not config.null_device and exists(env_file)

    section_rank = {GLOBAL_SECTION: 0, command: 1}
    # (precedence, key, value, source), in load order.
    found: list[tuple[tuple[int, int, int], str, str, Source]] = []

    for position, candidate in enumerate(candidates):
        null = is_null_device(candidate.path, platform)
        present = exists(candidate.path)
        loaded = present and not config.null_device and not null
        if candidate.kind == KIND_USER and config.user_files_skipped:
            loaded = False
        config.files.append(FileStatus(candidate.kind, candidate.path, present, loaded))
        if not loaded:
            continue
        kind_rank = KIND_ORDER.index(candidate.kind) if candidate.kind in KIND_ORDER else 0
        for section, options in read(candidate.path).items():
            filled = {name: value for name, value in options.items() if value.strip()}
            if section not in section_rank:
                if filled:
                    config.other_sections[section] = config.other_sections.get(section, 0) + len(
                        filled
                    )
                continue
            source = Source(candidate.kind, path=candidate.path, section=section)
            for name, value in filled.items():
                precedence = (section_rank[section], kind_rank, position)
                found.append((precedence, name, value, source))

    if use_env:
        for variable in sorted(env):
            if not variable.startswith("PIP_"):
                continue
            name = normalize_name(variable[4:])
            value = env[variable]
            if not name or name in _NON_OPTION_VARIABLES or not value.strip():
                continue
            config.variables.append(variable)
            found.append(((2, 0, 0), name, value, Source(KIND_ENV_VAR, variable=variable)))

    by_key: dict[str, list[tuple[tuple[int, int, int], str, Source]]] = {}
    for precedence, name, value, source in found:
        by_key.setdefault(name, []).append((precedence, value, source))

    winners: list[tuple[tuple[int, int, int], str, str, Source, tuple[Source, ...]]] = []
    for name, entries in by_key.items():
        # Stable sort: among equal precedence the later entry wins.
        ordered = sorted(enumerate(entries), key=lambda item: (item[1][0], item[0]), reverse=True)
        precedence, value, source = ordered[0][1]
        overridden = tuple(entry[2] for _, entry in ordered[1:])
        winners.append((precedence, name, value, source, overridden))

    ranks = {
        name: rank
        for rank, (_, name) in enumerate(sorted((winner[0], winner[1]) for winner in winners))
    }
    config.settings = [
        Setting(name, value, source, overridden, ranks[name])
        for _, name, value, source, overridden in winners
    ]
    return config


__all__ = [
    "KIND_ENV",
    "KIND_ENV_VAR",
    "MASK",
    "ConfigError",
    "FileStatus",
    "PipConfig",
    "Setting",
    "Source",
    "has_credentials",
    "load",
    "normalize_name",
    "read_file",
    "redact",
    "redact_tree",
    "strip_credentials",
]
