"""Visible native app entry point; no console and no repeating elevation."""
from pathlib import Path
import os
import sys
import traceback

root = Path(__file__).resolve().parents[1]
os.chdir(root)
try:
    from conquest.desktop_app import main
    if '--check-imports' not in sys.argv:
        main()
except Exception:
    (root/'reports').mkdir(exist_ok=True)
    (root/'reports/desktop-startup-error.txt').write_text(traceback.format_exc())
    raise
