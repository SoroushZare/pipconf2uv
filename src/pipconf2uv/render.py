"""Render a translation as text, TOML or JSON.

Nothing in this module prints credentials: setting values, notes and the TOML
shown in the text report all go through :func:`pipconf2uv.pipconfig.redact`.
"""

from __future__ import annotations

import json
import shlex
import textwrap
from typing import Any

from pipconf2uv import toml_writer
from pipconf2uv.pipconfig import (
    KIND_ENV_VAR,
    FileStatus,
    PipConfig,
    Source,
    redact,
    redact_tree,
)
from pipconf2uv.translate import (
    ENV,
    IGNORED,
    TRANSLATED,
    UNKNOWN,
    UNSUPPORTED,
    WARNING,
    Translation,
)

SCHEMA_VERSION = 1
WIDTH = 100
_MAX_VALUE = 48
_MIN_VALUE = 16

_STATUS_COLOURS = {
    TRANSLATED: "32",
    ENV: "36",
    UNSUPPORTED: "31",
    UNKNOWN: "35",
    IGNORED: "2",
}


def tilde(path: str, home: str | None) -> str:
    """Show ``path`` with the home directory abbreviated to ``~``."""
    if not home:
        return path
    base = home.rstrip("/\\")
    if not base:
        return path
    if path == base:
        return "~"
    if path.startswith(base) and path[len(base)] in "/\\":
        return "~" + path[len(base) :]
    return path


def generated_toml(translation: Translation) -> str:
    """The generated ``uv.toml`` exactly as it would be written."""
    return toml_writer.dumps(translation.uv)


def to_json(config: PipConfig, translation: Translation) -> str:
    """The ``--format json`` document."""
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "pip_command": config.command,
        "files": [
            {"kind": f.kind, "path": f.path, "exists": f.exists, "loaded": f.loaded}
            for f in config.files
        ],
        "settings": [
            {
                "key": setting.key,
                "value": redact(setting.value, setting.key),
                "status": translation.status(setting.key),
                "source": setting.source.as_dict(),
                "overrides": [source.as_dict() for source in setting.overrides],
            }
            for setting in config.settings
        ],
        "uv": translation.uv,
        "uv_toml": generated_toml(translation),
        "env": [
            {"name": var.name, "value": var.value, "from": var.source_key}
            for var in translation.env
        ],
        "notes": [
            {"level": note.level, "key": note.key, "message": note.message}
            for note in translation.notes
        ],
    }
    return json.dumps(document, indent=2) + "\n"


class _Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def paint(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled and text else text

    def heading(self, text: str) -> str:
        return self.paint(text, "1")

    def dim(self, text: str) -> str:
        return self.paint(text, "2")


def _file_state(status: FileStatus, config: PipConfig) -> str:
    if status.loaded:
        return "loaded"
    if config.null_device:
        return "skipped (PIP_CONFIG_FILE is the null device)"
    if not status.exists:
        return "not found"
    if status.kind == "user" and config.user_files_skipped:
        return "skipped (PIP_CONFIG_FILE is set)"
    return "skipped"


def _origin(source: Source, config: PipConfig, home: str | None) -> str:
    if source.kind == KIND_ENV_VAR:
        return source.variable or ""
    loaded_same_kind = [f for f in config.files if f.loaded and f.kind == source.kind]
    where = tilde(source.path or "", home) if len(loaded_same_kind) > 1 else source.kind
    return f"{where} [{source.section}]"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _wrap(prefix: str, text: str) -> list[str]:
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )


def _files_section(config: PipConfig, home: str | None, use_env: bool) -> list[str]:
    states = [_file_state(f, config) for f in config.files]
    short = max((len(state) for state in states if len(state) <= 9), default=0)
    lines = [
        f"  {f.kind:<6}  {state:<{short}}  {tilde(f.path, home)}"
        for f, state in zip(config.files, states)
    ]
    if not use_env:
        lines.append("  PIP_* environment variables: not read (--no-env)")
    elif config.variables:
        lines.extend(_wrap("  PIP_* environment variables: ", ", ".join(config.variables)))
    else:
        lines.append("  PIP_* environment variables: none")
    return lines


def _settings_section(
    config: PipConfig, translation: Translation, home: str | None, style: _Style
) -> list[str]:
    origins = [_origin(setting.source, config, home) for setting in config.settings]
    statuses = [translation.status(setting.key) for setting in config.settings]
    key_w = max(len("KEY"), *(len(setting.key) for setting in config.settings))
    origin_w = max(len("ORIGIN"), *(len(origin) for origin in origins))
    status_w = max(len("STATUS"), *(len(status) for status in statuses))
    # Give the value column whatever is left of the line, within sensible bounds.
    budget = WIDTH - (2 + key_w + 2 + 2 + origin_w + 2 + status_w)
    limit = max(_MIN_VALUE, min(_MAX_VALUE, budget))
    rows = [
        (setting, _clip(setting.display_value, limit), origin, status)
        for setting, origin, status in zip(config.settings, origins, statuses)
    ]
    value_w = max(len("VALUE"), *(len(row[1]) for row in rows))
    header = f"  {'KEY':<{key_w}}  {'VALUE':<{value_w}}  {'ORIGIN':<{origin_w}}  STATUS"
    lines = [style.dim(header)]
    for setting, value, origin, status in rows:
        painted = style.paint(status, _STATUS_COLOURS.get(status, "0"))
        lines.append(
            f"  {setting.key:<{key_w}}  {value:<{value_w}}  {origin:<{origin_w}}  {painted}"
        )
        if setting.overrides:
            overridden = ", ".join(_origin(source, config, home) for source in setting.overrides)
            lines.extend(style.dim(line) for line in _wrap("    overrides: ", overridden))
    return lines


def to_text(
    config: PipConfig,
    translation: Translation,
    *,
    home: str | None = None,
    color: bool = False,
    use_env: bool = True,
) -> str:
    """The ``--format text`` report."""
    style = _Style(color)
    out: list[str] = []

    if not config.settings and not any(f.loaded for f in config.files):
        out.append(style.heading("No pip configuration found.") + " Locations checked:")
        out.extend(_files_section(config, home, use_env))
        return "\n".join(out) + "\n"

    out.append(style.heading(f"pip configuration read (command section: [{config.command}])"))
    out.extend(_files_section(config, home, use_env))

    out.append("")
    out.append(style.heading("Effective pip settings"))
    if config.settings:
        out.extend(_settings_section(config, translation, home, style))
    else:
        out.append("  (none)")

    out.append("")
    out.append(style.heading("Generated uv.toml"))
    # Credentials are masked here even with --include-credentials; the real
    # values go only to --format toml, --format json ("uv") and --write.
    shown = toml_writer.dumps(redact_tree(translation.uv))
    out.append(shown.rstrip("\n") if shown else "  (nothing to write)")

    out.append("")
    out.append(style.heading("Environment variables to set"))
    if translation.env:
        out.extend(f"export {var.name}={shlex.quote(var.value)}" for var in translation.env)
    else:
        out.append("  (none)")

    out.append("")
    out.append(style.heading("Notes"))
    if translation.notes:
        for note in translation.notes:
            if note.level == WARNING:
                label = "  warning  "
                lines = _wrap(label, f"{note.key}: {note.message}")
                lines[0] = style.paint(label, "33;1") + lines[0][len(label) :]
            else:
                lines = _wrap("  note     ", f"{note.key}: {note.message}")
            out.extend(lines)
    else:
        out.append("  (none)")

    return "\n".join(out) + "\n"


def stderr_lines(config: PipConfig, translation: Translation) -> list[str]:
    """What ``--format toml`` reports on stderr so that nothing is dropped silently."""
    lines = [f"{note.level}: {note.key}: {note.message}" for note in translation.notes]
    for var in translation.env:
        lines.append(f"info: set {var.name}={shlex.quote(var.value)} (from {var.source_key})")
    for setting in config.settings:
        status = translation.status(setting.key)
        if status in (UNSUPPORTED, UNKNOWN):
            lines.append(f"info: {setting.key} was not translated ({status})")
    return lines
