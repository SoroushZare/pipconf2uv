"""Translate effective pip settings into uv configuration.

The mapping follows the table in ``docs/design.md``. Every setting receives
exactly one status; nothing is dropped silently.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Callable

from pipconf2uv.pipconfig import (
    MASK,
    PipConfig,
    Setting,
    has_credentials,
    redact,
    strip_credentials,
)

TRANSLATED = "translated"
ENV = "env"
UNSUPPORTED = "unsupported"
IGNORED = "ignored"
UNKNOWN = "unknown"

INFO = "info"
WARNING = "warning"

_TRUE = frozenset({"1", "y", "yes", "t", "true", "on"})
_FALSE = frozenset({"0", "n", "no", "f", "false", "off"})

_PYPI = "https://pypi.org/simple"

#: Order of top-level keys in the generated TOML (the order of the mapping table).
_TOP_ORDER = (
    "index-strategy",
    "no-index",
    "find-links",
    "allow-insecure-host",
    "http-proxy",
    "https-proxy",
    "no-cache",
    "prerelease",
    "no-binary",
    "no-binary-package",
    "no-build",
    "no-build-package",
    "no-build-isolation",
    "keyring-provider",
    "exclude-newer",
)
_PIP_ORDER = ("require-hashes", "break-system-packages", "no-deps", "target", "prefix", "python")
_ENV_ORDER = (
    "SSL_CERT_FILE",
    "SSL_CLIENT_CERT",
    "UV_HTTP_TIMEOUT",
    "UV_HTTP_RETRIES",
    "UV_CONSTRAINT",
    "UV_BUILD_CONSTRAINT",
)

_IGNORED = frozenset(
    {
        "quiet",
        "verbose",
        "log",
        "log-file",
        "local-log",
        "no-color",
        "progress-bar",
        "no-input",
        "disable-pip-version-check",
        "no-python-version-warning",
        "root-user-action",
        "no-warn-script-location",
        "no-warn-conflicts",
        "debug",
        "isolated",
        "require-virtualenv",
        "require-venv",
    }
)

#: pip options with no uv equivalent. The first group is named in the design
#: document; the rest are the remaining options of pip's commands.
_UNSUPPORTED = frozenset(
    {
        "prefer-binary",
        "user",
        "use-pep517",
        "use-feature",
        "use-deprecated",
        "resume-retries",
        "ignore-requires-python",
        "src",
        "platform",
        "python-version",
        "implementation",
        "abi",
        "no-clean",
        "exists-action",
        "no-proxy-env",
        # Other pip options.
        "abis",
        "all",
        "all-releases",
        "check-build-dependencies",
        "compile",
        "debug-mode",
        "dest",
        "dry-run",
        "editable",
        "exclude",
        "exclude-editable",
        "force-reinstall",
        "format",
        "global-option",
        "group",
        "ignore-installed",
        "include-editable",
        "local",
        "no-compile",
        "no-use-pep517",
        "not-required",
        "only-final",
        "outdated",
        "path",
        "report",
        "requirement",
        "requirements-from-script",
        "root",
        "upgrade",
        "upgrade-strategy",
        "uptodate",
        "wheel-dir",
        "yes",
    }
)

_PIP_ONLY = "applies to `uv pip` commands only."


@dataclass(frozen=True)
class Note:
    level: str  # "info" | "warning"
    key: str
    message: str


@dataclass(frozen=True)
class EnvVar:
    name: str
    value: str
    source_key: str


@dataclass
class Translation:
    """The result of translating a :class:`PipConfig`."""

    statuses: dict[str, str] = field(default_factory=dict)
    #: The generated uv configuration. Holds credentials only when they were
    #: requested with ``include_credentials``.
    uv: dict[str, Any] = field(default_factory=dict)
    env: list[EnvVar] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    def status(self, key: str) -> str:
        return self.statuses.get(key, UNKNOWN)


def parse_bool(value: str) -> bool | None:
    """Parse a boolean the way pip does; ``None`` when the value is invalid."""
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return None


def index_env_name(index_name: str) -> str:
    """The ``<NAME>`` part of ``UV_INDEX_<NAME>_USERNAME`` for an index name."""
    return re.sub(r"[^A-Za-z0-9]", "_", index_name).upper()


class _Translator:
    def __init__(self, settings: Sequence[Setting], include_credentials: bool) -> None:
        self.settings = {setting.key: setting for setting in settings}
        self.include_credentials = include_credentials
        self.statuses: dict[str, str] = {}
        self.top: dict[str, Any] = {}
        self.indexes: list[dict[str, Any]] = []
        self.config_settings: dict[str, Any] = {}
        self.pip: dict[str, Any] = {}
        self.env: dict[str, EnvVar] = {}
        self.notes: list[Note] = []

    # -- helpers ---------------------------------------------------------

    def note(self, level: str, key: str, message: str) -> None:
        # Notes are user-visible: never let a credential through.
        self.notes.append(Note(level, key, redact(message)))

    def boolean(self, setting: Setting) -> bool | None:
        """Parse a boolean; on failure warn and mark the setting unsupported."""
        value = parse_bool(setting.value)
        if value is None:
            self.statuses[setting.key] = UNSUPPORTED
            self.note(
                WARNING,
                setting.key,
                f"{setting.display_value!r} is not a valid boolean for {setting.key}; "
                "the setting was not translated.",
            )
        return value

    def url(self, setting: Setting, url: str, how: str) -> str:
        """Return ``url`` as it should appear in the generated configuration."""
        if not has_credentials(url):
            return url
        if self.include_credentials:
            self.note(
                WARNING,
                setting.key,
                f"The generated configuration contains the credentials from {setting.key}; "
                "keep the file private.",
            )
            return url
        self.note(
            WARNING,
            setting.key,
            f"Credentials were removed from {redact(url)}. {how}",
        )
        return strip_credentials(url)

    def set_env(self, name: str, setting: Setting, value: str) -> None:
        shown = redact(value)
        if shown != value:
            self.note(
                WARNING,
                setting.key,
                f"The value of {name} contains credentials, shown as {MASK}; "
                "put the real value in the variable yourself.",
            )
        self.env[name] = EnvVar(name, shown, setting.key)
        self.statuses[setting.key] = ENV

    def winner(self, *keys: str) -> Setting | None:
        """Among alias option names, return the setting pip would use."""
        present = [self.settings[key] for key in keys if key in self.settings]
        if not present:
            return None
        best = max(present, key=lambda setting: setting.rank)
        for setting in present:
            if setting is not best:
                self.note(
                    INFO,
                    setting.key,
                    f"{setting.key} is overridden by {best.key}, which is the same pip option "
                    "set with higher precedence.",
                )
        return best

    # -- indexes ---------------------------------------------------------

    def indexes_and_strategy(self) -> None:
        no_index = False
        setting = self.settings.get("no-index")
        if setting is not None:
            value = self.boolean(setting)
            if value is not None:
                no_index = value
                if value:
                    self.top["no-index"] = True
                    self.statuses[setting.key] = TRANSLATED
                else:
                    self.statuses[setting.key] = IGNORED

        count = 0
        extra = self.settings.get("extra-index-url")
        if extra is not None:
            for number, url in enumerate(extra.value.split(), start=1):
                name = f"extra-{number}"
                self.indexes.append({"name": name, "url": self.index_url(extra, name, url)})
                count += 1
            self.statuses[extra.key] = TRANSLATED

        primary = self.settings.get("index-url")
        if primary is not None:
            count += 1
            url = primary.value.strip()
            if strip_credentials(url).rstrip("/") == _PYPI:
                self.statuses[primary.key] = IGNORED
            else:
                self.indexes.append(
                    {
                        "name": "primary",
                        "url": self.index_url(primary, "primary", url),
                        "default": True,
                    }
                )
                self.statuses[primary.key] = TRANSLATED
        elif not no_index:
            count += 1  # PyPI, pip's default index.

        if count >= 2:
            self.top["index-strategy"] = "unsafe-best-match"
            self.note(
                WARNING,
                "index-strategy",
                "pip considers every index, while uv by default stops at the first index "
                'that has the package. index-strategy = "unsafe-best-match" keeps pip\'s '
                "behaviour, including its exposure to dependency confusion; delete the line "
                "to use uv's safer default.",
            )

    def index_url(self, setting: Setting, name: str, url: str) -> str:
        variable = f"UV_INDEX_{index_env_name(name)}"
        return self.url(
            setting,
            url,
            f"Supply them with {variable}_USERNAME and {variable}_PASSWORD.",
        )

    # -- individual options ---------------------------------------------

    def find_links(self, setting: Setting) -> None:
        how = (
            "uv can read them from a netrc file or a keyring; "
            "--include-credentials keeps them in the generated file."
        )
        self.top["find-links"] = [self.url(setting, url, how) for url in setting.value.split()]
        self.statuses[setting.key] = TRANSLATED

    def trusted_host(self, setting: Setting) -> None:
        hosts = setting.value.split()
        self.top["allow-insecure-host"] = hosts
        self.statuses[setting.key] = TRANSLATED
        self.note(
            WARNING,
            setting.key,
            f"TLS certificate verification is disabled for: {', '.join(hosts)}.",
        )

    def proxy(self, setting: Setting) -> None:
        value = setting.value.strip()
        if "://" not in value:
            value = "http://" + value
        value = self.url(setting, value, "Supply them through the HTTPS_PROXY variable.")
        self.top["http-proxy"] = value
        self.top["https-proxy"] = value
        self.statuses[setting.key] = TRANSLATED
        self.note(
            INFO,
            setting.key,
            "http-proxy and https-proxy were added to uv in January 2026; "
            "on older uv use the HTTP_PROXY and HTTPS_PROXY variables.",
        )

    def cert(self, setting: Setting) -> None:
        self.set_env("SSL_CERT_FILE", setting, setting.value.strip())
        self.note(
            INFO,
            setting.key,
            "SSL_CERT_FILE must be a PEM file and replaces uv's default trust roots; if the "
            "CA is installed in the operating system store, system-certs = true in uv.toml "
            "is the alternative.",
        )

    def timeout(self) -> None:
        setting = self.winner("timeout", "default-timeout")
        if setting is None:
            return
        for key in ("timeout", "default-timeout"):
            if key in self.settings:
                self.statuses[key] = ENV
        value = setting.value.strip()
        self.set_env("UV_HTTP_TIMEOUT", setting, value)
        if not value.isdigit():
            self.note(
                WARNING,
                setting.key,
                "UV_HTTP_TIMEOUT must be a whole number of seconds; adjust the value.",
            )

    def no_cache_dir(self, setting: Setting) -> None:
        value = self.boolean(setting)
        if value is None:
            return
        self.top["no-cache"] = True
        self.statuses[setting.key] = TRANSLATED
        if not value:
            self.note(
                INFO,
                setting.key,
                "pip disables its cache whenever no-cache-dir is provided, even with a false "
                "value, so no-cache = true was emitted.",
            )

    def cache_dir(self, setting: Setting) -> None:
        self.statuses[setting.key] = IGNORED
        self.note(
            INFO,
            setting.key,
            "uv keeps its own cache and should not be pointed at pip's cache directory.",
        )

    def pre(self, setting: Setting) -> None:
        value = self.boolean(setting)
        if value is None:
            return
        if value:
            self.top["prerelease"] = "allow"
            self.statuses[setting.key] = TRANSLATED
        else:
            self.statuses[setting.key] = IGNORED

    def binary_control(self, setting: Setting, all_key: str, package_key: str) -> None:
        # pip processes the items in order: ":all:" and ":none:" reset the list.
        everything = False
        names: list[str] = []
        for item in (part.strip() for part in setting.value.split(",")):
            if not item:
                continue
            if item == ":all:":
                everything, names = True, []
            elif item == ":none:":
                everything, names = False, []
            elif not everything:
                names.append(item)
        if everything:
            self.top[all_key] = True
            self.statuses[setting.key] = TRANSLATED
        elif names:
            self.top[package_key] = names
            self.statuses[setting.key] = TRANSLATED
        else:
            self.statuses[setting.key] = IGNORED

    def no_build_isolation(self, setting: Setting) -> None:
        value = self.boolean(setting)
        if value is None:
            return
        if value:
            self.top["no-build-isolation"] = True
            self.statuses[setting.key] = TRANSLATED
        else:
            self.statuses[setting.key] = IGNORED
        self.note(
            WARNING,
            setting.key,
            "pip has historically read no-build-isolation from configuration files and "
            "environment variables inverted (a false value disables isolation). Check which "
            "behaviour you rely on before keeping this translation.",
        )

    def config_settings_option(self, setting: Setting) -> None:
        for item in setting.value.split():
            name, _, value = item.partition("=")
            existing = self.config_settings.get(name)
            if existing is None:
                self.config_settings[name] = value
            elif isinstance(existing, list):
                existing.append(value)
            else:
                self.config_settings[name] = [existing, value]
        self.statuses[setting.key] = TRANSLATED if self.config_settings else IGNORED

    def keyring_provider(self, setting: Setting) -> None:
        value = setting.value.strip().lower()
        if value in ("subprocess", "disabled"):
            self.top["keyring-provider"] = value
            self.statuses[setting.key] = TRANSLATED
            return
        self.statuses[setting.key] = UNSUPPORTED
        self.note(
            WARNING,
            setting.key,
            f"keyring-provider = {setting.display_value} has no uv equivalent; "
            'uv supports only "subprocess" and "disabled".',
        )

    def uploaded_prior_to(self, setting: Setting) -> None:
        self.top["exclude-newer"] = setting.value.strip()
        self.statuses[setting.key] = TRANSLATED
        self.note(
            INFO,
            setting.key,
            "Check the format of exclude-newer: uv expects an RFC 3339 timestamp or a date, "
            "and pip assumes local time when no zone is given.",
        )

    def pip_flag(self, setting: Setting, uv_key: str) -> None:
        value = self.boolean(setting)
        if value is None:
            return
        if not value:
            self.statuses[setting.key] = IGNORED
            return
        self.pip[uv_key] = True
        self.statuses[setting.key] = TRANSLATED
        self.note(INFO, setting.key, f"[pip] {uv_key} {_PIP_ONLY}")

    def pip_value(self, setting: Setting) -> None:
        self.pip[setting.key] = setting.value.strip()
        self.statuses[setting.key] = TRANSLATED
        self.note(INFO, setting.key, f"[pip] {setting.key} {_PIP_ONLY}")

    def no_deps(self) -> None:
        setting = self.winner("no-deps", "no-dependencies")
        if setting is None:
            return
        self.pip_flag(setting, "no-deps")
        for key in ("no-deps", "no-dependencies"):
            if key in self.settings:
                self.statuses[key] = self.statuses[setting.key]

    # -- driver ----------------------------------------------------------

    def run(self) -> Translation:
        simple: dict[str, Callable[[Setting], None]] = {
            "find-links": self.find_links,
            "trusted-host": self.trusted_host,
            "proxy": self.proxy,
            "cert": self.cert,
            "client-cert": lambda s: self.set_env("SSL_CLIENT_CERT", s, s.value.strip()),
            "retries": lambda s: self.set_env("UV_HTTP_RETRIES", s, s.value.strip()),
            "no-cache-dir": self.no_cache_dir,
            "cache-dir": self.cache_dir,
            "pre": self.pre,
            "no-binary": lambda s: self.binary_control(s, "no-binary", "no-binary-package"),
            "only-binary": lambda s: self.binary_control(s, "no-build", "no-build-package"),
            "no-build-isolation": self.no_build_isolation,
            "config-settings": self.config_settings_option,
            "keyring-provider": self.keyring_provider,
            "uploaded-prior-to": self.uploaded_prior_to,
            "constraint": lambda s: self.set_env("UV_CONSTRAINT", s, " ".join(s.value.split())),
            "build-constraint": lambda s: self.set_env(
                "UV_BUILD_CONSTRAINT", s, " ".join(s.value.split())
            ),
            "require-hashes": lambda s: self.pip_flag(s, "require-hashes"),
            "break-system-packages": lambda s: self.pip_flag(s, "break-system-packages"),
            "target": self.pip_value,
            "prefix": self.pip_value,
            "python": self.pip_value,
        }

        self.indexes_and_strategy()
        self.timeout()
        self.no_deps()
        for key, setting in self.settings.items():
            if key in self.statuses:
                continue
            handler = simple.get(key)
            if handler is not None:
                handler(setting)
            elif key in _IGNORED:
                self.statuses[key] = IGNORED
            elif key in _UNSUPPORTED:
                self.statuses[key] = UNSUPPORTED
            else:
                self.statuses[key] = UNKNOWN

        uv: dict[str, Any] = {key: self.top[key] for key in _TOP_ORDER if key in self.top}
        if self.indexes:
            uv["index"] = self.indexes
        if self.config_settings:
            uv["config-settings"] = self.config_settings
        if self.pip:
            uv["pip"] = {key: self.pip[key] for key in _PIP_ORDER if key in self.pip}

        return Translation(
            statuses=self.statuses,
            uv=uv,
            env=[self.env[name] for name in _ENV_ORDER if name in self.env],
            notes=self.notes,
        )


def translate(config: PipConfig, *, include_credentials: bool = False) -> Translation:
    """Translate the effective settings of ``config`` into uv configuration."""
    result = _Translator(config.settings, include_credentials).run()
    for section, count in config.other_sections.items():
        plural = "s" if count != 1 else ""
        verb = "were" if count != 1 else "was"
        result.notes.append(
            Note(
                INFO,
                f"[{section}]",
                redact(
                    f"Section [{section}] has {count} setting{plural} that {verb} not "
                    f"translated: only [global] and [{config.command}] are read "
                    "(select another command section with --pip-command)."
                ),
            )
        )
    return result
