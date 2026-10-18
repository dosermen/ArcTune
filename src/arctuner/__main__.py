"""Точка входа для `python -m arctuner`."""

from __future__ import annotations

import sys

from arctuner.cli import main

if __name__ == "__main__":
    sys.exit(main())
