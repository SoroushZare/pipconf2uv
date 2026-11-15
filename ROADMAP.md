# Roadmap

Bugs and feature requests are tracked in [GitHub Issues](https://github.com/SoroushZare/pipconf2uv/issues).

## v0.1.0 — first working release

- [ ] Discover pip configuration layers (global, user, site, `PIP_CONFIG_FILE`, `PIP_*` environment variables) with pip's precedence, on Linux, macOS and Windows
- [ ] Show the effective settings and where each one comes from
- [ ] Translate to `uv.toml`, printing by default and writing only with an explicit flag
- [ ] Report settings with no uv equivalent or with changed behaviour
- [ ] Redact credentials in every output
- [ ] Tests for discovery, precedence, translation and redaction
- [ ] CI on Linux, macOS and Windows
- [ ] README with real output and a reproducible demo

## Later, if there is demand

- `--check` mode: exit non-zero when pip configuration and existing uv configuration disagree (for CI and onboarding scripts)
- Output as `[tool.uv]` for `pyproject.toml` and as `UV_*` environment variables
- Publication on PyPI

## Out of scope

- Migrating project metadata or dependencies (see `migrate-to-uv` for that)
- Making uv read `pip.conf` at run time
