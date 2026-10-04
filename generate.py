#!/usr/bin/env python3
"""Atajo: `python generate.py ...` equivale a `orquesta generate ...`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from orquesta.cli import main  # noqa: E402

sys.exit(main(["generate", *sys.argv[1:]]))
