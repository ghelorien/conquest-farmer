"""Recompute bilateral proof from isolated journals, never mock its verdict."""

from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace as NS
import pytest

from conquest import settled_delivery_town_recovery as recovery
from conquest.merchants import delivery, delivery_operation, delivery_route
from conquest.discord_notify import write_json


def item(uid, kind):
    return dict(
        uid=uid, type_id=kind, plus=0, gem1=0, gem2=0, bound=False, quantity=1, slot=0
    )


@pytest.fixture
def proof(tmp_path, monkeypatch):
    from conquest.character_context import farmer_name

    x = NS(source=tmp_path / "source.sqlite3", receiver=tmp_path / "receiver.sqlite3")
    monkeypatch.setattr(delivery_operation, "JOURNAL", x.source)
    monkeypatch.setattr(recovery, "state_path", lambda _: x.receiver)
    x.target = dict(pid=7, path="game.exe", creation_time_100ns=80)
    x.row = dict(town_visit_id="town", farmer_profile_id="farmer", required_at=800.0)
    x.origin = dict(town_visit_id="town", farmer_profile_id="farmer", visit_id="market")
    gear = item(3, 130923)
    potion = item(1, 1000020)

    def snapshot(name, uid, items):
        return dict(
            character=name,
            character_uid=uid,
            identity=deepcopy(x.target) if uid == 1 else dict(pid=8),
            server="America",
            map_id=1036,
            position=[220, 210],
            timestamp=849.0,
            hp=100,
            silver=600,
            capacity=40,
            inventory=deepcopy(items),
            booth=[],
            own_booth_uid=0,
            booth_open=False,
            trade=None,
            request=None,
        )

    x.intent = dict(
        operation_id="delivery",
        **x.origin,
        items=[gear],
        farmer=snapshot(farmer_name(), 1, [potion, gear]),
        merchant=snapshot("Dutch", 2, []),
    )
    farmer = deepcopy(x.intent["farmer"])
    farmer["inventory"] = [potion]
    merchant = deepcopy(x.intent["merchant"])
    merchant["inventory"] = [gear]
    x.trace = [
        dict(
            stage="receiver_receipt",
            status="observed",
            payload={"phase": "verified"},
            timestamp=850.0,
        )
    ]
    x.result = delivery.reconciliation_outcome(
        x.intent, farmer, merchant, trace=x.trace, now=850.0
    )
    x.meteor = dict(
        started_at=820.0,
        after=dict(
            silver=600,
            capacity=40,
            items=[
                dict(uid=1, type_id=1000020, plus=0, amount=1, limit=1, slot=0),
                dict(uid=3, type_id=130923, plus=0, amount=2241, limit=3000, slot=1),
            ],
            equipped_ammo=dict(
                uid=2, type_id=1050002, amount=1207, limit=5000, slot=None
            ),
        ),
    )
    x.market = dict(
        visit_id="market",
        started_at=840.0,
        attempts=[dict(outcome="transferred", at=851.0)],
    )
    x.failure = dict(time=855.0)
    x.route = dict(
        receipts=[
            dict(
                request_id="delivery",
                outcome="transferred",
                proof_digest=x.result["proof_digest"],
                items=[gear],
                verified_at=851.0,
                **x.origin,
            )
        ]
    )
    with sqlite3.connect(x.source) as db:
        db.executescript("""CREATE TABLE transactions(id TEXT,kind TEXT,phase TEXT,created REAL,updated REAL,
            character TEXT,before_json TEXT,result_json TEXT);
            CREATE TABLE delivery_admissions(request_id TEXT,created REAL,phase TEXT,character TEXT,
            origin_json TEXT,uids_json TEXT,items_json TEXT);
            CREATE TABLE transaction_steps(id INTEGER,transaction_id TEXT,stage TEXT,status TEXT,
            payload TEXT,timestamp REAL);""")
        db.execute(
            "INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?)",
            (
                "delivery",
                "farmer_delivery",
                "verified",
                845.0,
                850.0,
                "Dutch",
                json.dumps(x.intent),
                json.dumps(x.result),
            ),
        )
        db.execute(
            "INSERT INTO delivery_admissions VALUES(?,?,?,?,?,?,?)",
            (
                "delivery",
                845.0,
                "transaction_started",
                "Dutch",
                json.dumps(dict(operation_id="delivery", **x.origin)),
                "[3]",
                json.dumps([gear]),
            ),
        )
        db.execute(
            "INSERT INTO transaction_steps VALUES(?,?,?,?,?,?)",
            (
                1,
                "delivery",
                "receiver_receipt",
                "observed",
                json.dumps({"phase": "verified"}),
                850.0,
            ),
        )
    with sqlite3.connect(x.receiver) as db:
        db.execute("CREATE TABLE delivery_reservations(request_id TEXT,state TEXT)")
        db.execute(
            "INSERT INTO delivery_reservations VALUES(?,?)",
            (
                "delivery",
                json.dumps(dict(phase="verified", intent=x.intent, verified_at=850.0)),
            ),
        )
    write_json(delivery_route.STATE, x.route)
    x.run = lambda: recovery._delivery_proof(
        x.row, x.meteor, x.market, x.failure, x.target
    )
    return x


def test_exact_terminal_bilateral_transfer_subtracts_only_delivered_uid(proof):
    x = proof
    before = deepcopy(x.meteor)
    expected, records = x.run()
    assert [i["uid"] for i in expected["items"]] == [1]
    assert expected["equipped_ammo"] == x.meteor["after"]["equipped_ammo"]
    assert expected["silver"] == 600 and x.meteor == before
    assert records == [
        dict(
            request_id="delivery",
            proof_digest=x.result["proof_digest"],
            uids=[3],
            verified_at=851.0,
        )
    ]


@pytest.mark.parametrize(
    "change",
    [
        "source_missing",
        "source_changed",
        "receiver_missing",
        "receiver_extra",
        "currency",
        "identity",
        "wrong_digest",
        "pending",
        "receiver_unverified",
        "extra_admission",
        "route_receipt",
        "attempt",
    ],
)
def test_any_unexplained_asset_or_unsettled_receipt_blocks(proof, change):
    x = proof
    if change == "source_missing":
        x.result["farmer"]["inventory"] = []
    if change == "source_changed":
        x.result["farmer"]["inventory"][0]["quantity"] = 2
    if change == "receiver_missing":
        x.result["merchant"]["inventory"] = []
    if change == "receiver_extra":
        x.result["merchant"]["inventory"].append(item(99, 130923))
    if change == "currency":
        x.result["farmer"]["silver"] -= 1
    if change == "identity":
        x.result["farmer"]["identity"]["pid"] = 99
    if change == "wrong_digest":
        x.result["proof_digest"] = "not-the-proof"
    with sqlite3.connect(x.source) as db:
        db.execute("UPDATE transactions SET result_json=?", (json.dumps(x.result),))
        if change == "pending":
            db.execute("UPDATE transactions SET phase='uncertain'")
        if change == "extra_admission":
            db.execute(
                "INSERT INTO delivery_admissions(request_id,created) VALUES('other',846)"
            )
    if change == "receiver_unverified":
        with sqlite3.connect(x.receiver) as db:
            db.execute(
                "UPDATE delivery_reservations SET state=?",
                (
                    json.dumps(
                        dict(phase="uncertain", intent=x.intent, verified_at=850.0)
                    ),
                ),
            )
    if change == "route_receipt":
        x.route["receipts"][0]["items"] = []
        write_json(delivery_route.STATE, x.route)
    if change == "attempt":
        x.market["attempts"][0]["outcome"] = "uncertain"
    with pytest.raises(ValueError):
        x.run()


def test_equipment_durability_is_not_trade_quantity_and_known_attributes_remain_exact(
    proof,
):
    x = proof
    assert recovery._bag_matches_snapshot(x.meteor["after"], x.intent["farmer"])
    x.meteor["after"]["items"][1]["gem1"] = 1
    assert not recovery._bag_matches_snapshot(x.meteor["after"], x.intent["farmer"])


def test_receiver_observation_metadata_may_differ_but_assets_may_not(proof):
    x = proof
    peer = deepcopy(x.intent)
    peer["farmer"]["timestamp"] = 850.0
    peer["farmer"]["inventory"][0]["category"] = None
    assert recovery._same_intent(x.intent, peer)
    peer["farmer"]["inventory"][0]["quantity"] = 2
    assert not recovery._same_intent(x.intent, peer)
