"""Command-line interface."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Sequence

from pipconf2uv import __version__, render
from pipconf2uv.discover import candidate_files, uv_user_config_path
from pipconf2uv.pipconfig import ConfigError, load, redact
from pipconf2uv.translate import UNKNOWN, UNSUPPORTED, translate

PROG = "pipconf2uv"
_DEFAULT_TARGET = object()

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Read pip's configuration the way pip does and print the equivalent uv "
        "configuration, with a report of what cannot be translated.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "toml", "json"),
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument(
        "--write",
        nargs="?",
        const=_DEFAULT_TARGET,
        default=None,
        metavar="PATH",
        help="write the generated uv.toml to PATH (default: uv's user-level uv.toml)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --write, replace an existing file after copying it to <name>.bak",
    )
    parser.add_argument(
        "--pip-command",
        default="install",
        metavar="NAME",
        help="pip command section to read besides [global] (default: install)",
    )
    parser.add_argument(
        "--site-prefix",
        default=None,
        metavar="DIR",
        help="environment whose site-level pip configuration is read "
        "(default: $VIRTUAL_ENV, then $CONDA_PREFIX)",
    )
    parser.add_argument("--no-env", action="store_true", help="do not read PIP_* option variables")
    parser.add_argument(
        "--include-credentials",
        action="store_true",
        help="keep credentials found in URLs in the generated TOML",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with status 1 if any setting is unsupported or unknown",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    return parser


def _platform() -> str:
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _error(message: str) -> None:
    # Error text may quote paths or values from configuration; mask credentials.
    print(f"{PROG}: error: {redact(message)}", file=sys.stderr)


def _info(message: str) -> None:
    print(f"{PROG}: {redact(message)}", file=sys.stderr)


def _write_atomic(path: str, text: str, private: bool) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = os.path.join(directory, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    mode = 0o600 if private else 0o666
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        if private and os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write(target: str, text: str, *, force: bool, private: bool, shown: str) -> int:
    """Write the generated TOML; returns an exit code."""
    if not text:
        _info(f"nothing to write; {shown} was not created")
        return EXIT_OK
    if os.path.lexists(target) and not force:
        _error(f"{shown} already exists; use --force to replace it (a .bak copy is kept)")
        return EXIT_FAILED
    try:
        if os.path.lexists(target):
            shutil.copy2(target, target + ".bak")
        _write_atomic(target, text, private)
    except OSError as exc:
        _error(f"cannot write {shown}: {exc.strerror or type(exc).__name__}")
        return EXIT_FAILED
    _info(f"wrote {shown}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; returns the process exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # usage error, --help or --version
        code = exc.code
        if code is None:
            return EXIT_OK
        return code if isinstance(code, int) else EXIT_USAGE

    env = dict(os.environ)
    platform = _platform()
    home = os.path.expanduser("~")

    candidates = candidate_files(
        env=env, platform=platform, home=home, site_prefix=args.site_prefix or None
    )
    try:
        config = load(
            candidates,
            env=env,
            platform=platform,
            command=args.pip_command,
            use_env=not args.no_env,
        )
    except ConfigError as exc:
        _error(f"cannot read pip configuration file {render.tilde(exc.path, home)}: {exc.reason}")
        return EXIT_USAGE

    translation = translate(config, include_credentials=args.include_credentials)
    toml_text = render.generated_toml(translation)

    if args.format == "json":
        sys.stdout.write(render.to_json(config, translation))
    elif args.format == "toml":
        sys.stdout.write(toml_text)
        if not config.settings and not any(f.loaded for f in config.files):
            _info("no pip configuration found")
        for line in render.stderr_lines(config, translation):
            _info(line)
    else:
        color = sys.stdout.isatty() and not env.get("NO_COLOR")
        sys.stdout.write(
            render.to_text(config, translation, home=home, color=color, use_env=not args.no_env)
        )

    status = EXIT_OK
    if args.write is not None:
        if args.write is _DEFAULT_TARGET:
            target = uv_user_config_path(env=env, platform=platform, home=home)
        else:
            target = os.path.abspath(os.path.expanduser(args.write))
        status = _write(
            target,
            toml_text,
            force=args.force,
            private=args.include_credentials,
            shown=render.tilde(target, home),
        )

    if args.strict and any(
        translation.status(setting.key) in (UNSUPPORTED, UNKNOWN) for setting in config.settings
    ):
        status = max(status, EXIT_FAILED)
    return status


if __name__ == "__main__":
    sys.exit(main())
