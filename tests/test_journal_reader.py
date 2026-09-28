"""Journal reads reuse one connection per thread; the farm loop's replan check
takes no write lock when nothing is pending.

Live 2026-09-28 (Suicide's app on b529812, thread_sampler): the combat thread
spent 11% of its samples opening and closing the merchant journal. Each
journal.get connected, set two PRAGMAs, read one row and closed
(delivery_reservation.active, once per merchant per observation). And
manual_recovery.consume_farmer began a write transaction (BEGIN IMMEDIATE) on
every inventory observation to find nothing pending, so a busy writer
elsewhere could hold the farm loop up to the five-second busy timeout.

Failure modes, written before the change:
1. Every get() opens a new connection.
2. A get() misses a value committed a moment before (a stale snapshot).
3. Threads share one connection (sqlite3 forbids it).
4. A get() inside another connection's open write transaction blocks, or sees
   its uncommitted rows (it never did: it used its own connection).
5. The reader holds a transaction between calls (pinning the WAL).
6. A discarded Journal keeps its database file open.
7. consume_farmer takes the write lock when no replan is pending.
8. consume_farmer stops consuming a pending replan.
"""

import gc
import sqlite3
import threading
import time

import pytest

from conquest.merchants import journal as journal_module
from conquest.merchants.journal import Journal


@pytest.fixture
def connects(monkeypatch):
    calls = []
    real = sqlite3.connect

    def counting(*args, **kwargs):
        calls.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(journal_module.sqlite3, "connect", counting)
    return calls


def test_reads_reuse_one_connection_and_see_fresh_commits(tmp_path, connects):
    # 1, 2, 5
    journal = Journal(tmp_path / "journal.sqlite3")
    journal.set("Dutch", "enabled", False)
    connects.clear()
    for value in range(10):
        journal.set("Dutch", "counter", value)
        assert journal.get("Dutch", "counter") == value
        assert not journal.reader().in_transaction
    reads = [t for t in connects]
    # Ten set() calls still connect each time; the ten get() calls add one.
    assert len(reads) == 11


def test_each_thread_reads_through_its_own_connection(tmp_path, connects):
    # 3
    journal = Journal(tmp_path / "journal.sqlite3")
    journal.set("Dutch", "enabled", True)
    connects.clear()
    seen, errors = [], []

    def read():
        try:
            seen.append(journal.get("Dutch", "enabled"))
            seen.append(journal.get("Dutch", "enabled"))
        except Exception as error:  # a shared connection raises here
            errors.append(error)

    workers = [threading.Thread(target=read) for _ in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert not errors and seen == [True] * 6
    assert len(connects) == 3 and len(set(connects)) == 3


def test_a_read_during_another_write_transaction_sees_committed_state(tmp_path):
    # 4
    journal = Journal(tmp_path / "journal.sqlite3")
    journal.set("Dutch", "enabled", False)
    journal.get("Dutch", "enabled")  # open the reader first
    started = time.monotonic()
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?,?)", ("Dutch", "enabled", "true")
        )
        assert journal.get("Dutch", "enabled") is False
    assert time.monotonic() - started < 1
    assert journal.get("Dutch", "enabled") is True


def test_a_discarded_journal_releases_its_file(tmp_path):
    # 6
    path = tmp_path / "journal.sqlite3"
    journal = Journal(path)
    journal.set("Dutch", "enabled", True)
    assert journal.get("Dutch", "enabled") is True
    del journal
    gc.collect()
    for suffix in ("", "-wal", "-shm"):
        target = path.with_name(path.name + suffix)
        if target.exists():
            target.unlink()  # raises on Windows while a connection holds it
    assert not path.exists()


def replans(journal):
    from conquest.merchants.manual_recovery import SCHEMA

    with journal.db() as db:
        db.executescript(SCHEMA)


def evidence(now):
    return {"timestamp": now, "map_id": 1011, "inventory": [], "urgent_banking": False}


def test_consume_farmer_takes_no_write_lock_when_nothing_is_pending(tmp_path):
    # 7
    from conquest.merchants.manual_recovery import consume_farmer

    journal = Journal(tmp_path / "journal.sqlite3")
    replans(journal)
    blocker = sqlite3.connect(journal.path, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")  # another writer holds the lock
    try:
        started = time.monotonic()
        assert consume_farmer(journal, evidence(time.time())) == []
        assert time.monotonic() - started < 1
    finally:
        blocker.rollback()
        blocker.close()


def test_consume_farmer_still_consumes_a_pending_replan(tmp_path):
    # 8
    from conquest.merchants.manual_recovery import consume_farmer

    journal = Journal(tmp_path / "journal.sqlite3")
    replans(journal)
    with journal.db() as db:
        db.execute(
            "INSERT INTO manual_replans(session_id,target_profile_id,settled_at,"
            "merchant_pending,farmer_pending) VALUES('session-1','farmer',?,0,1)",
            (time.time() - 5,),
        )
    assert consume_farmer(journal, evidence(time.time())) == ["session-1"]
    assert consume_farmer(journal, evidence(time.time())) == []
