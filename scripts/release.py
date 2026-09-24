"""Stable script entry point for immutable release build/activation commands."""

from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))

from conquest.release import main

raise SystemExit(main())
