"""Entry point for ``python -m pipconf2uv``."""

from __future__ import annotations

import sys

from pipconf2uv.cli import main

if __name__ == "__main__":
    sys.exit(main())
