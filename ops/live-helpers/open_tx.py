"""Read-only: merchant journal transactions that are not settled, plus recent ones."""
import sqlite3
import time
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
    db.row_factory = sqlite3.Row
    print("open:")
    for row in db.execute(
        "SELECT id, character, kind, phase, created, updated, substr(result_json,1,300) AS result "
        "FROM transactions WHERE phase NOT IN ('verified','aborted','operator_overridden')"
    ):
        print(" ", dict(row))
    print("recent (last 20 min):")
    for row in db.execute(
        "SELECT id, character, kind, phase, created, updated FROM transactions WHERE updated > ? ORDER BY updated",
        (time.time() - 1200,),
    ):
        r = dict(row)
        print(" ", time.strftime("%H:%M:%S", time.localtime(r["updated"])), r["id"], r["character"], r["kind"], r["phase"])
