from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
import threading
from types import SimpleNamespace as NS
import pytest

from test_settled_delivery_town_recovery import proof as bilateral_fixture
from conquest.merchants import empty_delivery_sales as recovery


@pytest.fixture
def sales(bilateral_fixture, monkeypatch):
    from conquest.merchants import delivery_operation, delivery_bridge

    x = bilateral_fixture
    x.intent["merchant"]["identity"] = dict(
        pid=8, path="game.exe", creation_time_100ns=81
    )
    x.listed = [
        dict(
            uid=10,
            type_id=130324,
            plus=1,
            gem1=0,
            gem2=0,
            bound=False,
            quantity=1,
            price=50000,
        ),
        dict(
            uid=11,
            type_id=118735,
            plus=1,
            gem1=0,
            gem2=0,
            bound=False,
            quantity=1,
            price=49500,
        ),
    ]
    x.intent["merchant"]["booth"] = deepcopy(x.listed)
    x.intent["merchant"]["booth_open"] = True
    x.intent["merchant"]["own_booth_uid"] = 77
    x.current = {role: deepcopy(x.intent[role]) for role in ("farmer", "merchant")}
    x.current["merchant"]["booth"] = []
    x.current["merchant"]["silver"] += 96515
    for role, other in (("farmer", "merchant"), ("merchant", "farmer")):
        x.current[role]["trade"] = dict(
            participant=x.intent[other]["character"],
            participant_uid=x.intent[other]["character_uid"],
            own_items=[],
            items=[],
            own_silver=0,
            other_silver=0,
            accepted=False,
            other_accepted=False,
        )
    x.pairs = [deepcopy(x.current), deepcopy(x.current)]
    for index, pair in enumerate(x.pairs):
        for role in pair:
            pair[role]["timestamp"] = 879.0 + index
    x.confirmation = dict(
        request_id="delivery",
        no_manual_changes=True,
        statement="No manual changes during this incident",
        reference="operator-message",
        confirmed_at=875.0,
    )
    with sqlite3.connect(x.source) as db:
        db.execute(
            "UPDATE transactions SET phase='uncertain',before_json=?",
            (json.dumps(x.intent),),
        )
        db.execute("DELETE FROM transaction_steps")
        for number, (stage, status, payload) in enumerate(
            (
                ("action_trace", "initialized", {"version": 1}),
                ("trade_request", "before_action", {}),
                ("trade_request", "observed", {"trade_open": True}),
            )
        ):
            db.execute(
                "INSERT INTO transaction_steps VALUES(?,?,?,?,?,?)",
                (number, "delivery", stage, status, json.dumps(payload), 850.0),
            )
    with sqlite3.connect(x.receiver) as db:
        db.execute(
            "UPDATE delivery_reservations SET state=?",
            (json.dumps(dict(phase="reserved", intent=x.intent)),),
        )
        db.executescript("""CREATE TABLE sales(id INTEGER,character TEXT,observed_at REAL,phase TEXT,
            items TEXT,silver INTEGER,note TEXT);
            CREATE TABLE events(id INTEGER PRIMARY KEY,character TEXT,event TEXT,payload TEXT,timestamp REAL);
            CREATE TABLE state(character TEXT,name TEXT,value TEXT,PRIMARY KEY(character,name));
            CREATE TABLE transactions(character TEXT,created REAL,updated REAL);
            CREATE TABLE manual_sessions(target_profile_id TEXT,created_at REAL,updated_at REAL,phase TEXT);
            CREATE TABLE manual_handoffs(id TEXT,created_at REAL,completed_at REAL);
            CREATE TABLE manual_handoff_participants(session_id TEXT,target_profile_id TEXT);""")
        balance = 600
        for uid, at, item, gain in zip(
            (371, 372), (860.0, 864.0), x.listed, (48500, 48015)
        ):
            payload = dict(
                items=[item],
                before_silver=balance,
                after_silver=balance + gain,
                **{"from": at - 1},
                gross=item["price"],
                net_bounds=[gain, gain],
                foreign_request_observed=False,
            )
            db.execute(
                "INSERT INTO sales VALUES(?,?,?,?,?,?,?)",
                (
                    uid,
                    "Dutch",
                    at,
                    "unconfirmed",
                    json.dumps([item]),
                    0,
                    "ambiguous trade",
                ),
            )
            db.execute(
                "INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)",
                ("Dutch", "sale_unconfirmed", json.dumps(payload), at),
            )
            balance += gain

    @contextmanager
    def db():
        with sqlite3.connect(x.receiver) as connection:
            connection.row_factory = sqlite3.Row
            yield connection

    x.journal = NS(path=x.receiver, db=db)
    x.ui = NS(
        closed=False,
        app=NS(
            closing=False,
            control=NS(snapshot=lambda: dict(enabled=False, paused=False, revision=2)),
        ),
        coordinator=NS(
            lock=threading.RLock(),
            stopped=False,
            owner=None,
            manual_active=lambda: False,
        ),
        runtime=NS(journal=x.journal, manual_handoff_status=lambda: None),
        delivery_workers={},
    )
    reads = iter(x.pairs)
    monkeypatch.setattr(
        delivery_bridge,
        "pair",
        lambda ui, character: (lambda p: (p["farmer"], p["merchant"]))(next(reads)),
    )
    monkeypatch.setattr(delivery_operation, "JOURNAL", x.source)
    monkeypatch.setattr(recovery.time, "time", lambda: 880.0)
    x.preview = lambda: recovery.preview(
        x.source,
        x.receiver,
        "delivery",
        [371, 372],
        x.pairs,
        confirmation=x.confirmation,
        now=880.0,
    )
    x.promote = lambda: recovery.promote(
        x.ui, "delivery", [371, 372], confirmation=x.confirmation
    )
    return x


def test_preview_never_mutates_rows_and_accounts_exact_stock_currency(sales):
    x = sales
    evidence = x.preview()
    assert [r["verified_silver"] for r in evidence["sales_before"]] == [48500, 48015]
    assert evidence["delivery_receipt"] is False
    with x.journal.db() as db:
        assert [r[0] for r in db.execute("SELECT phase FROM sales")] == [
            "unconfirmed",
            "unconfirmed",
        ]


def test_promotion_is_atomic_idempotent_and_does_not_double_count(sales):
    from conquest.merchants.sales import qualified_delivery_receipts

    x = sales
    evidence = x.promote()
    assert x.promote() == evidence
    with x.journal.db() as db:
        assert [
            tuple(r) for r in db.execute("SELECT phase,silver FROM sales ORDER BY id")
        ] == [("verified", 48500), ("verified", 48015)]
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE event='empty_delivery_sales_reconciled'"
            ).fetchone()[0]
            == 1
        )
        assert db.execute("SELECT count(*) FROM state").fetchone()[0] == 1
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sales_reconciliations'"
        ).fetchone()
    receipts = qualified_delivery_receipts(x.journal, x.intent, x.pairs[-1]["merchant"])
    assert [r["id"] for r in receipts] == [371, 372]


@pytest.mark.parametrize(
    "change",
    [
        "no_statement",
        "early_statement",
        "offer",
        "confirm",
        "gap",
        "other_input",
        "manual",
        "other_transaction",
        "currency",
        "farmer_asset",
        "accepted",
        "nonempty",
        "closed",
        "identity",
        "event_delta",
        "missing_event",
    ],
)
def test_ambiguous_history_or_changed_native_state_never_promotes(sales, change):
    x = sales
    if change == "no_statement":
        x.confirmation["no_manual_changes"] = False
    if change == "early_statement":
        x.confirmation["confirmed_at"] = 861.0
    if change in ("offer", "confirm"):
        with sqlite3.connect(x.source) as db:
            db.execute(
                "INSERT INTO transaction_steps VALUES(?,?,?,?,?,?)",
                (
                    9,
                    "delivery",
                    "offer_item:3" if change == "offer" else "farmer_confirm",
                    "before_action",
                    "{}",
                    851.0,
                ),
            )
    with sqlite3.connect(x.receiver) as db:
        if change in ("gap", "other_input"):
            db.execute(
                "INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)",
                (
                    "Dutch",
                    "sales_observation_gap" if change == "gap" else "listing_started",
                    "{}",
                    855.0,
                ),
            )
        if change == "manual":
            db.execute(
                "INSERT INTO manual_sessions VALUES('Dutch',854,855,'completed')"
            )
        if change == "other_transaction":
            db.execute("INSERT INTO transactions VALUES('Dutch',854,856)")
        if change == "event_delta":
            event = json.loads(
                db.execute("SELECT payload FROM events WHERE id=1").fetchone()[0]
            )
            event["after_silver"] += 1
            db.execute("UPDATE events SET payload=? WHERE id=1", (json.dumps(event),))
        if change == "missing_event":
            db.execute("DELETE FROM events WHERE id=1")
    for pair in x.pairs:
        if change == "currency":
            pair["merchant"]["silver"] += 1
        if change == "farmer_asset":
            pair["farmer"]["inventory"].pop()
        if change == "accepted":
            pair["merchant"]["trade"]["accepted"] = True
        if change == "nonempty":
            pair["farmer"]["trade"]["own_items"] = [deepcopy(x.intent["items"][0])]
        if change == "closed":
            pair["merchant"]["trade"] = None
        if change == "identity":
            pair["merchant"]["identity"]["pid"] = 99
    with pytest.raises(ValueError):
        x.preview()
    with x.journal.db() as db:
        assert all(r[0] == "unconfirmed" for r in db.execute("SELECT phase FROM sales"))


def test_manual_input_and_live_delivery_worker_block_atomic_promotion(sales):
    x = sales
    x.ui.coordinator.owner = "Dutch"
    with pytest.raises(ValueError, match="idle"):
        x.promote()
    x.ui.coordinator.owner = None
    x.ui.delivery_workers = {"delivery": NS(is_alive=lambda: True)}
    with pytest.raises(ValueError, match="idle"):
        x.promote()
