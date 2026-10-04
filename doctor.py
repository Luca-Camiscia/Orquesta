#!/usr/bin/env python3
"""Atajo: `python doctor.py ...` equivale a `orquesta doctor ...`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from orquesta.cli import main  # noqa: E402

sys.exit(main(["doctor", *sys.argv[1:]]))
