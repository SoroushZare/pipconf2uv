# Design

This document is the contract for v0.1.0. Code and tests are written against it. Where it describes pip or uv behaviour, the source is cited so the rule can be re-checked when either tool changes.

## Scope

One job: read pip's configuration the way pip does, show the effective settings with their origin, and produce the equivalent uv configuration plus an honest list of what could not be translated.

- Python 3.9+, standard library only at run time.
- Deterministic. No network access. No subprocesses.
- Reads pip configuration files and `PIP_*` environment variables. Writes a file only with `--write`.
- Credentials are never printed.

## Sources of truth

| Topic | Source |
|---|---|
| pip config file locations, kinds, precedence | `pip/_internal/configuration.py`, `pip/_internal/utils/appdirs.py` |
| pip section precedence, value parsing | `pip/_internal/cli/parser.py` (`_get_ordered_configuration_items`, `_update_defaults`) |
| pip option names | `pip/_internal/cli/cmdoptions.py` |
| uv settings | `uv.schema.json` in astral-sh/uv |
| uv environment variables | `crates/uv-static/src/env_vars.rs` in astral-sh/uv |
| uv config file locations | `docs/concepts/configuration-files.md` in astral-sh/uv |
| uv index behaviour | `docs/concepts/indexes.md` in astral-sh/uv |

## 1. Discovery (`pipconf2uv.discover`)

```python
@dataclass(frozen=True)
class ConfigFile:
    kind: str   # "global" | "user" | "site" | "env"
    path: str

def candidate_files(*, env, platform, home, site_prefix=None, isdir=os.path.isdir) -> list[ConfigFile]
```

A pure function: `env` is a mapping, `platform` is `"linux"`, `"darwin"` or `"win32"`, `home` is the user's home directory as a string. It returns candidates in pip's load order (global, user, site, env) without checking that they exist. Path joining follows the conventions of `platform`, not of the machine running the code (use `posixpath` / `ntpath`), so every platform can be tested anywhere.

File name is `pip.ini` on `win32`, otherwise `pip.conf`.

**global**

- linux (and any other POSIX platform): for each entry `d` of `XDG_CONFIG_DIRS` (split on `:`, default `/etc/xdg`): `d/pip/pip.conf`; then `/etc/pip.conf`.
- darwin: `/Library/Application Support/pip/pip.conf`.
- win32: `%PROGRAMDATA%\pip\pip.ini` (default `C:\ProgramData`).

**user** (two files, legacy first; the second overrides the first)

- linux: `~/.pip/pip.conf`, then `$XDG_CONFIG_HOME/pip/pip.conf` (default `~/.config`).
- darwin: `~/.pip/pip.conf`, then `~/Library/Application Support/pip/pip.conf` if the directory `~/Library/Application Support/pip` exists (`isdir`), otherwise `~/.config/pip/pip.conf`. `XDG_CONFIG_HOME` is not consulted on darwin.
- win32: `~\pip\pip.ini`, then `%APPDATA%\pip\pip.ini` (default `~\AppData\Roaming`).

**site**: `<prefix>/pip.conf` where prefix is `site_prefix`, else `VIRTUAL_ENV`, else `CONDA_PREFIX`. If none is set there is no site file. (pip uses its own interpreter's `sys.prefix`; this tool usually runs in a different environment, so it cannot use its own.)

**env**: the value of `PIP_CONFIG_FILE`, if set.

Rules from pip:

- If `PIP_CONFIG_FILE` is the null device (`/dev/null`, or `nul` on win32), no configuration file is loaded at all.
- If `PIP_CONFIG_FILE` is set and that file exists, the user files are not loaded.

## 2. Loading and effective settings (`pipconf2uv.pipconfig`)

- Files are parsed with `configparser.RawConfigParser` (as pip does). A file that does not exist is skipped. A file that exists but cannot be parsed is a fatal error (exit code 2) naming the file.
- Option names are normalised: lower-case, `_` → `-`, leading `--` removed.
- Environment variables `PIP_<NAME>` supply option `<name>` (same normalisation), except `PIP_VERSION`, `PIP_HELP` and `PIP_CONFIG_FILE`. `--no-env` disables this.
- Empty values are ignored.
- File precedence for the same section and key, lowest to highest: global, user, site, env.
- Section precedence for the same key, lowest to highest: `[global]`, the command section (default `[install]`, chosen with `--pip-command`), environment variables. Section precedence wins over file precedence: `[install] index-url` in a global file beats `[global] index-url` in a user file.
- A higher-precedence value replaces a lower one entirely, including for list options. Lists are not merged across sources.
- Sections other than `[global]` and the selected command are not translated. Each such section that contains settings produces one note.

Each effective setting records its key, raw value, source (kind, path, section, or variable name) and the sources it overrode.

## 3. Translation (`pipconf2uv.translate`)

Every effective setting gets exactly one status:

| Status | Meaning |
|---|---|
| `translated` | Written to the generated `uv.toml`. |
| `env` | uv only supports this through an environment variable; listed under "environment variables". |
| `unsupported` | No uv equivalent known. Reported, never dropped silently. |
| `ignored` | pip-only behaviour (logging, prompts) or already uv's default. No action needed. |
| `unknown` | Not a pip option this tool knows. |

Booleans follow pip: `1 y yes t true on` are true, `0 n no f false off` are false (case-insensitive). An invalid boolean produces a warning note and the setting is `unsupported`.

List options split on whitespace, as pip does for config values. `no-binary` and `only-binary` split on commas.

### Mapping

| pip option | Result |
|---|---|
| `index-url` | `[[index]]` with `name = "primary"`, `url`, `default = true`. If the URL is PyPI (`https://pypi.org/simple`, with or without trailing slash) nothing is emitted and the status is `ignored`. |
| `extra-index-url` | One `[[index]]` per URL, named `extra-1`, `extra-2`, …, emitted before the default index. |
| (two or more indexes in total, counting PyPI when `index-url` is unset and `no-index` is not set) | `index-strategy = "unsafe-best-match"` plus a warning note: pip considers every index, uv by default stops at the first index that has the package; this setting keeps pip's behaviour and its exposure to dependency confusion; delete the line to use uv's safer default. |
| `no-index` | `no-index = true` |
| `find-links` | `find-links = [...]` |
| `trusted-host` | `allow-insecure-host = [...]` plus a warning note that TLS verification is disabled for those hosts. |
| `proxy` | `http-proxy` and `https-proxy` (same value; `http://` is prefixed when the value has no scheme). Note: these keys were added to uv in January 2026; on older uv use `HTTP_PROXY` / `HTTPS_PROXY`. |
| `cert` | env `SSL_CERT_FILE`. Note: PEM only; it replaces uv's default trust roots; if the CA is installed in the operating system store, `system-certs = true` in `uv.toml` is the alternative. |
| `client-cert` | env `SSL_CLIENT_CERT` |
| `timeout`, `default-timeout` | env `UV_HTTP_TIMEOUT` |
| `retries` | env `UV_HTTP_RETRIES` |
| `no-cache-dir` | `no-cache = true` for **any** valid boolean value, because pip disables its cache whenever the option is provided, even as `false`. A falsy value adds a note explaining this. |
| `cache-dir` | `ignored`, with a note: uv keeps its own cache and should not be pointed at pip's. |
| `pre` | true → `prerelease = "allow"`; false → `ignored`. |
| `no-binary` | `:all:` → `no-binary = true`; `:none:` → `ignored`; names → `no-binary-package = [...]` |
| `only-binary` | `:all:` → `no-build = true`; `:none:` → `ignored`; names → `no-build-package = [...]` |
| `no-build-isolation` | `no-build-isolation = true` |
| `config-settings` | `[config-settings]` table from `KEY=VALUE` items |
| `keyring-provider` | `subprocess` / `disabled` → same value; `auto` / `import` → `unsupported` (uv supports only `subprocess`). |
| `uploaded-prior-to` | `exclude-newer` with the same value, plus a note to check the format (pip assumes local time when no zone is given). |
| `constraint` | env `UV_CONSTRAINT` (space-separated) |
| `build-constraint` | env `UV_BUILD_CONSTRAINT` |
| `require-hashes` | `[pip] require-hashes = true`, note: applies to `uv pip` commands only. |
| `break-system-packages`, `no-deps` / `no-dependencies`, `target`, `prefix`, `python` | Same key under `[pip]` (`no-deps` for both spellings), note: applies to `uv pip` commands only. |
| `require-virtualenv`, `require-venv` | `ignored`: `uv pip` requires a virtual environment by default. |
| `prefer-binary`, `user`, `use-pep517`, `use-feature`, `use-deprecated`, `resume-retries`, `ignore-requires-python`, `src`, `platform`, `python-version`, `implementation`, `abi`, `no-clean`, `exists-action`, `no-proxy-env` and any other pip option not listed here | `unsupported` |
| `quiet`, `verbose`, `log`, `log-file`, `local-log`, `no-color`, `progress-bar`, `no-input`, `disable-pip-version-check`, `no-python-version-warning`, `root-user-action`, `no-warn-script-location`, `no-warn-conflicts`, `debug`, `isolated` | `ignored` |
| anything that is not a pip option | `unknown` |

### Credentials

User-info (`user:password@` or `token@`) can appear in index URLs, find-links URLs and the proxy.

- In the settings listing, notes, JSON `settings` and any error message, user-info is always shown as `****`.
- In generated uv configuration, user-info is removed by default and a note says how to supply it: `UV_INDEX_<NAME>_USERNAME` / `UV_INDEX_<NAME>_PASSWORD` for an index (name upper-cased, non-alphanumerics → `_`), or the `HTTPS_PROXY` variable for a proxy.
- `--include-credentials` keeps user-info in the generated TOML (and only there). A file written this way is created with mode `0600` on POSIX.

## 4. Output (`pipconf2uv.render`, `pipconf2uv.toml_writer`)

`toml_writer` is a minimal emitter for the value types used here: strings (basic strings with correct escaping), booleans, arrays of strings, tables, arrays of tables. Output must parse with `tomllib`.

Key order in the generated TOML is fixed: scalar and array keys in the order of the mapping table, then `[[index]]` tables, then `[config-settings]`, then `[pip]`.

`--format text` (default) prints five parts: the files and variables read, the effective pip settings with origin and status, the generated `uv.toml`, the environment variables to set, and the notes. Home directory prefixes are shown as `~`. Colour is used only when stdout is a terminal and `NO_COLOR` is unset.

`--format toml` prints only the generated TOML (notes go to stderr), so `pipconf2uv --format toml > uv.toml` works.

`--format json` prints one object:

```json
{
  "schema_version": 1,
  "pip_command": "install",
  "files": [{"kind": "user", "path": "...", "exists": true, "loaded": true}],
  "settings": [{
    "key": "index-url", "value": "https://****@example.com/simple", "status": "translated",
    "source": {"kind": "user", "path": "...", "section": "global"},
    "overrides": [{"kind": "global", "path": "...", "section": "global"}]
  }],
  "uv": {"index": [{"name": "primary", "url": "https://example.com/simple", "default": true}]},
  "uv_toml": "...",
  "env": [{"name": "UV_HTTP_TIMEOUT", "value": "60", "from": "timeout"}],
  "notes": [{"level": "warning", "key": "trusted-host", "message": "..."}]
}
```

An environment-variable source is `{"kind": "env-var", "variable": "PIP_INDEX_URL"}`. Note levels are `info` and `warning`.

## 5. Command line (`pipconf2uv.cli`)

```
pipconf2uv [--format {text,toml,json}] [--write [PATH]] [--force]
           [--pip-command NAME] [--site-prefix DIR] [--no-env]
           [--include-credentials] [--strict] [--version]
```

`main(argv=None) -> int`. Also runnable as `python -m pipconf2uv`.

- `--write` without a path targets uv's user-level file: `$XDG_CONFIG_HOME/uv/uv.toml` (default `~/.config/uv/uv.toml`) on Linux and macOS, `%APPDATA%\uv\uv.toml` on Windows. Parent directories are created. If the target exists the command refuses (exit 1) unless `--force` is given, in which case the old file is first copied to `<name>.bak`. The write is atomic (temporary file, then rename). With nothing to write (empty TOML) no file is created.
- Exit codes: `0` success, including when there is nothing to translate; `1` `--strict` and at least one setting is `unsupported` or `unknown`, or `--write` refused to overwrite; `2` usage error or a pip configuration file that cannot be read or parsed.
- When no pip configuration is found, say so and list the locations that were checked.

## 6. Non-goals for v0.1.0

- Comparing against an existing uv configuration (`--check`).
- Emitting `[tool.uv]` for `pyproject.toml`.
- Merging into an existing `uv.toml`.
- Per-command sections other than the selected one.
