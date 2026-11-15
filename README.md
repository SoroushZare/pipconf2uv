# pipconf2uv

**uv does not read your `pip.conf`. This tool translates it.**

`pipconf2uv` finds every place pip takes its configuration from (`pip.conf` / `pip.ini` at the global, user and site levels, `PIP_CONFIG_FILE`, and `PIP_*` environment variables), shows which layer each effective setting comes from, and prints the equivalent uv configuration. Settings that have no uv equivalent, or that behave differently in uv, are reported instead of being silently dropped.

> **Status: in development.** Nothing is released yet and the command described here does not exist yet. This README states the goal; it will be replaced with real usage and real output when v0.1.0 is tagged. Follow [ROADMAP.md](ROADMAP.md) for progress.

## Why

The uv documentation says uv "does not read configuration files or environment variables that are specific to pip, like `pip.conf` or `PIP_INDEX_URL`". Teams that install from a private index or through a corporate proxy have that setup in pip's configuration, so their first `uv` command fails and they have to work out the translation by hand. The request for uv to read `pip.conf` ([astral-sh/uv#1404](https://github.com/astral-sh/uv/issues/1404)) has been open since February 2024.

The translation is not a rename. For example, with more than one index pip considers candidates from all of them, while uv by default stops at the first index that has the package, so a naive copy can change which package gets installed.

## Planned for v0.1.0

- Discover pip configuration the way pip does, on Linux, macOS and Windows.
- Show each effective setting and the file or variable it comes from.
- Emit the equivalent `uv.toml` (print by default; write only when asked).
- Report every setting that cannot be mapped, or whose meaning changes.
- Never print credentials embedded in index URLs.
- No dependencies outside the Python standard library.

## Not affiliated

This is an independent project. It is not affiliated with or endorsed by Astral (uv) or the Python Packaging Authority (pip).

## License

[MIT](LICENSE)
