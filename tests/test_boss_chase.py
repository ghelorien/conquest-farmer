"""A boss that keeps closing ends the hunt (return_required "boss_chase").

Suicide 2026-09-30 14:46-14:48 on thunderape-nw: 18 boss escapes in two
minutes while a ThunderApeKing followed it round the box, then three boss
flights and death at (313, 318). Leaving the field beats dancing round him.

Failure modes, written before the change:
1. Ten boss escapes inside the box within two minutes do not end the hunt.
2. Escapes on the walk in (outside the box), other escape reasons, or old
   ones count, so the ring's bosses would abort every approach.
3. The check scans the whole journal (it must read new rows by rowid only).
"""

import json
import sqlite3
from types import SimpleNamespace as NS

from conquest.overnight import BOSS_CHASE_ESCAPES, BOSS_CHASE_WINDOW, OvernightLoop

BOX = (254, 160, 345, 235)


def journal(tmp_path):
    path = tmp_path / "trial.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("create table events(time real, event text, payload text)")
        db.execute("insert into events values(?,?,?)", (0.0, "trial_started", "{}"))
    return path


def escape(path, when, reason="boss_nearby", source=(300, 200)):
    with sqlite3.connect(path) as db:
        db.execute(
            "insert into events values(?,?,?)",
            (when, "ranged_escape", json.dumps({"reason": reason, "source": list(source)})),
        )


def loop():
    return NS(route=NS(hunting_boundary=BOX))


def chase(farmer, path, now):
    return OvernightLoop.boss_chase(farmer, now=now, journal=path)


def test_ten_boss_escapes_in_the_box_within_two_minutes_end_the_hunt(tmp_path):
    # 1
    path = journal(tmp_path)
    escape(path, 5.0)  # before the hunt began: never counted
    farmer = loop()
    assert chase(farmer, path, 100.0) is False  # notes where the journal ends
    for i in range(BOSS_CHASE_ESCAPES - 1):
        escape(path, 100.0 + i * 5)
    assert chase(farmer, path, 150.0) is False
    escape(path, 151.0, reason="boss_flight")
    assert chase(farmer, path, 152.0) is True


def test_the_walk_in_other_reasons_and_old_escapes_do_not_count(tmp_path):
    # 2
    path = journal(tmp_path)
    farmer = loop()
    chase(farmer, path, 100.0)
    for i in range(BOSS_CHASE_ESCAPES):
        escape(path, 100.0 + i, source=(356, 310))  # the passage, outside the box
        escape(path, 100.0 + i, reason="enemies_within_reach")
    assert chase(farmer, path, 120.0) is False
    for i in range(BOSS_CHASE_ESCAPES - 1):
        escape(path, 130.0 + i)
    # The first nine age out of the window before the tenth comes.
    assert chase(farmer, path, 140.0) is False
    escape(path, 130.0 + BOSS_CHASE_WINDOW + 20)
    assert chase(farmer, path, 130.0 + BOSS_CHASE_WINDOW + 21) is False


def test_a_bold_route_dodges_more_before_a_chase_ends_the_hunt(tmp_path):
    # Alex 2026-10-01 05:2x: "be more bold ... find bigger packs". A route
    # hunting the packs round its bosses sets its own limit (snakeman-bold 25).
    path = journal(tmp_path)
    farmer = NS(route=NS(hunting_boundary=BOX, boss_chase_escapes=25))
    chase(farmer, path, 100.0)
    for i in range(24):
        escape(path, 100.0 + i)
    assert chase(farmer, path, 130.0) is False
    escape(path, 131.0)
    assert chase(farmer, path, 132.0) is True
    # None keeps the default.
    farmer = NS(route=NS(hunting_boundary=BOX, boss_chase_escapes=None))
    chase(farmer, path, 200.0)
    for i in range(BOSS_CHASE_ESCAPES):
        escape(path, 200.0 + i)
    assert chase(farmer, path, 215.0) is True


def test_it_reads_new_rows_by_rowid_only(tmp_path):
    # 3
    path = journal(tmp_path)
    farmer = loop()
    chase(farmer, path, 100.0)
    statements = []
    real = sqlite3.connect

    def traced(*args, **kwargs):
        db = real(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    import conquest.overnight  # noqa: F401 (boss_chase imports sqlite3 lazily)

    sqlite3.connect, saved = traced, sqlite3.connect
    try:
        chase(farmer, path, 101.0)
    finally:
        sqlite3.connect = saved
    reads = [s for s in statements if "ranged_escape" in s]
    assert reads and all("rowid>" in s.replace(" ", "") for s in reads)
    # Without a journal (or a route box) it quietly says no.
    assert OvernightLoop.boss_chase(loop(), journal=tmp_path / "missing.sqlite3") is False
    assert OvernightLoop.boss_chase(NS(route=NS()), journal=path) is False
