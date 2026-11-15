"""A minimal TOML emitter for the value types this tool generates.

Supported values: strings, booleans, arrays of strings, tables whose values are
of those types, and arrays of such tables. Anything else is a programming error.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _string(value: str) -> str:
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else _string(key)


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, Mapping) for v in value)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "[" + ", ".join(_string(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def _pairs(table: Mapping[str, Any]) -> list[str]:
    return [f"{_key(key)} = {_value(value)}" for key, value in table.items()]


def dumps(document: Mapping[str, Any]) -> str:
    """Serialise ``document`` to TOML.

    Top-level scalars and arrays come first, then tables and arrays of tables in
    mapping order. An empty document produces an empty string.
    """
    blocks: list[list[str]] = []
    plain = {
        key: value
        for key, value in document.items()
        if not isinstance(value, Mapping) and not _is_table_array(value)
    }
    if plain:
        blocks.append(_pairs(plain))
    for key, value in document.items():
        if isinstance(value, Mapping):
            blocks.append([f"[{_key(key)}]", *_pairs(value)])
        elif _is_table_array(value):
            for item in value:
                blocks.append([f"[[{_key(key)}]]", *_pairs(item)])
    if not blocks:
        return ""
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"
