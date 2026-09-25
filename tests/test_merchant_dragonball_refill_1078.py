"""Merchants may sell Dragonballs they hold, under strict comparable guards.

AGENTS.md "Latest loot and urgent banking rule": merchants may sell
Dragonballs (all kinds) held in their own inventory during refill, highest
value first like other stock. Never list a bound item, never below the lowest
live comparable, and never from a comparable older than the last market
refresh; otherwise keep it queued. The farmer still banks carried Dragonballs
urgently. Meteors are sold only as MeteorScrolls, never loose.

The "last market refresh" is the price-history catalog ``observed_at`` (written
by PriceHistory.remember together with every quote of that refresh). A quote
from that refresh carries exactly that ``observed_at``; an older quote is a
stale comparable. The quote's unit price is that refresh's lowest valid
comparable (outliers excluded), so its total is the Dragonball price floor.

Failure modes this module must catch (written before the implementation):

1. A merchant-held Dragonball of any kind (DragonBall, DBScroll, 1-7 Star,
   Epic) with a comparable from the last refresh stays queued (planner or
   engine still treat it as storage-only / protected), or is listed below that
   refresh's lowest valid comparable, or is not ranked highest value first
   among ordinary stock.
2. A Dragonball whose only comparable is older than the last market refresh is
   listed from that stale price instead of staying queued.
3. A Dragonball with no recorded market refresh time (unreadable catalog) is
   priced instead of queued.
4. A live owned-booth price for the same Dragonball kind that is below the last
   refresh's lowest valid comparable is matched (listing below the floor).
5. Every Dragonball price source is guarded: a verified prior-booth restoration
   price below the floor, or without a fresh comparable, is not restored.
6. A bound Dragonball is priced, planned or admitted by the engine.
7. A loose Meteor (1088001) becomes listable anywhere (preview, planner or
   engine), even with a fresh comparable. MeteorScrolls change behaviour (the
   Dragonball guard leaks onto them).
8. The farmer side changes: storage_only() semantics, urgent banking of a
   carried Dragonball, or the default require_marketable() refusal used by the
   legacy driver/controller for rare Dragonballs; or a name-only star match
   with an unknown type ID becomes sellable.
9. Preview and planner disagree on any Dragonball/Meteor row (price, reason,
   floor or order).
10. Engine admission (booth_listing_once_1078.dispatch) refuses the planned
    top Dragonball, or admits an unplanned (stale / not top) or bound one.
11. The legacy MerchantController path (1% undercut, no refresh guard) lists a
    Dragonball.

The end-to-end test runs refill_preview_1078.preview -> listing_plan_1078.plan
-> booth_listing_once_1078.dispatch admission with fakes only at the memory
reader, native-window/input-ownership and input-worker boundaries, writes
``dragonball-refill-1078.json`` and requires two independent runs to be
byte-identical.
"""

from copy import deepcopy
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conquest.character_profiles import ProfileRegistry
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants import booth_listing_once_1078 as listing
from conquest.merchants import listing_plan_1078, observe_1078, refill_preview_1078
from conquest.merchants.journal import Journal
from conquest.merchants.market import SOURCE, MarketSnapshot
from conquest.merchants.price_history import PriceHistory
from conquest.valuables import (
    DRAGONBALL_NAMES,
    DRAGONBALL_TYPES,
    STORAGE_ONLY_TYPES,
    require_marketable,
    storage_only,
    urgent_storage,
)

LATEST_AGE, OLDER_AGE = 600, 7200
METEOR, METEOR_SCROLL = 1088001, 720027
# Lowest valid comparable per kind at the latest refresh (second seller higher).
FLOORS = {
    1088000: 2_300_000,
    720028: 23_500_000,
    2000031: 3_000_000,
    2000032: 4_000_000,
    2000033: 5_000_000,
    2000034: 6_000_000,
    2000035: 7_000_000,
    2000036: 8_000_000,
    2000037: 9_000_000,
    2000038: 60_000_000,
}
PRICES = {**FLOORS, METEOR_SCROLL: 1_500_000}
NAMES = {**DRAGONBALL_NAMES, METEOR: "Meteor", METEOR_SCROLL: "MeteorScroll"}


def market(at, prices):
    listings = []
    for name, values in prices.items():
        for seller, price in zip(("Outsider", "Trader"), values):
            listings.append(
                dict(
                    name=name,
                    category="Special",
                    quality="Normal",
                    plus=0,
                    sockets=["No socket", "No socket"],
                    seller=seller,
                    server="America",
                    price=price,
                    quantity=1,
                    currency="silver",
                )
            )
    return dict(
        source=SOURCE,
        server="America",
        complete=True,
        observed_at=at,
        total=len(listings),
        listings=listings,
    )


def history(path, *, latest_kinds, older_kinds=()):
    """Two real market refreshes; only ``latest_kinds`` appear in the newest."""
    now = time.time()
    store = PriceHistory(path)
    older = {NAMES[kind]: (FLOORS.get(kind, 1_000_000) // 2,) for kind in older_kinds}
    older[NAMES[METEOR_SCROLL]] = (1_200_000,)
    store.remember(MarketSnapshot(market(now - OLDER_AGE, older), now=now - OLDER_AGE))
    latest = {
        NAMES[kind]: (PRICES[kind], PRICES[kind] + 100_000) for kind in latest_kinds
    }
    latest[NAMES[METEOR]] = (150_000,)
    store.remember(
        MarketSnapshot(market(now - LATEST_AGE, latest), now=now - LATEST_AGE)
    )
    return path


def stock(uid, kind, *, bound=False, slot=0, price=None):
    row = dict(
        uid=uid,
        type_id=kind,
        name=NAMES[kind],
        plus=0,
        gem1=0,
        gem2=0,
        bound=bound,
        quantity=1,
        slot=slot,
    )
    if price is not None:
        row["price"] = price
    return row


class PC:
    """Real profiles, journal and price history; fake process memory only."""

    def __init__(self, root, monkeypatch, *, inventory, peer_booth=()):
        root.mkdir(parents=True, exist_ok=True)
        registry = ProfileRegistry(root / "profiles")
        farmer = registry.add("Varric")
        self.merchant = registry.add("Kalhiam", role="Merchant", character_uid=7001)
        self.peer = registry.add("Brix", role="Merchant", character_uid=7002)
        monkeypatch.setenv("CONQUEST_DATA_ROOT", str(registry.root))
        monkeypatch.setenv("CONQUEST_PROFILE_ID", farmer.id)
        (root / "market").mkdir(exist_ok=True)
        self.history_path = root / "market" / "price-history.sqlite3"
        self.journal = Journal(root / "journal.sqlite3")
        self.runtime = SimpleNamespace(
            journal=self.journal, market_path=root / "market" / "market.json"
        )
        self.memory = {
            "Kalhiam": self._snapshot(self.merchant, 11, 18, inventory, ()),
            "Brix": self._snapshot(self.peer, 12, 28, (), peer_booth),
        }
        for module in (refill_preview_1078, listing_plan_1078, observe_1078):
            monkeypatch.setattr(module, "observe", self.observe)

    @staticmethod
    def _snapshot(profile, pid, booth_uid, inventory, booth):
        return dict(
            character=profile.name,
            character_uid=profile.character_uid,
            identity={"pid": pid, "creation_time_100ns": 2, "path": "C:/ImConquer.exe"},
            server="America",
            map_id=1036,
            position=[262, 211],
            hp=900,
            silver=10,
            capacity=40,
            inventory=[dict(item) for item in inventory],
            booth=[dict(item) for item in booth],
            own_booth_uid=booth_uid,
            booth_open=True,
            closed_modal=True,
            trade=None,
            request=None,
            profile_id=profile.id,
            profile_uid_verified=True,
            client_sha256=CLIENT_SHA256_1078,
        )

    def observe(self, runtime, target, listing_preflight=False):
        """The read-only process-memory boundary."""
        name = str(target)
        if name not in self.memory:
            name = {self.merchant.id: "Kalhiam", self.peer.id: "Brix"}[name]
        snapshot = {**deepcopy(self.memory[name]), "timestamp": time.time()}
        if listing_preflight:
            snapshot["listing_preflight"] = {
                "layout_observed": True,
                "price_modal": {"observed": False},
            }
        return snapshot

    def preview(self):
        return refill_preview_1078.preview(
            self.runtime, "Kalhiam", history_path=self.history_path
        )

    def plan(self):
        return listing_plan_1078.plan(
            self.runtime, "Kalhiam", self.observe(self.runtime, "Kalhiam")
        )


def view(rows):
    return [
        (
            row["uid"],
            row["name"],
            row["total_listing_price"],
            row["source"],
            row["reason"],
            row.get("dragonball_floor"),
        )
        for row in rows
    ]


def run(tmp_path, monkeypatch, inventory, *, latest, older=(), peer_booth=()):
    pc = PC(tmp_path, monkeypatch, inventory=inventory, peer_booth=peer_booth)
    history(pc.history_path, latest_kinds=latest, older_kinds=older)
    preview = pc.preview()
    plan = pc.plan()
    assert view(preview["queue"]) == view(plan)  # failure mode 9
    return pc, preview, plan


def row_for(rows, uid):
    return next(row for row in rows if row["uid"] == uid)


# 1 + 9: every kind with a fresh comparable lists at the floor, ranked by value.
@pytest.mark.parametrize("kind", sorted(DRAGONBALL_TYPES))
def test_each_dragonball_kind_with_fresh_comparable_lists_at_or_above_floor(
    tmp_path, monkeypatch, kind
):
    inventory = [stock(1, kind), stock(2, METEOR_SCROLL, slot=1)]
    pc, preview, plan = run(
        tmp_path, monkeypatch, inventory, latest=[kind, METEOR_SCROLL]
    )
    row = row_for(plan, 1)
    assert row["price"] == row["total_listing_price"] >= FLOORS[kind]
    assert row["dragonball_floor"] == FLOORS[kind]
    assert row["source"] == "saved_comparable_price"
    # Highest value first against ordinary stock, same as every other item.
    assert [r["uid"] for r in plan if r["price"] is not None] == [1, 2]
    assert preview["next_slots_if_qualified"][0]["uid"] == 1


# 2: a comparable older than the last refresh keeps the Dragonball queued.
@pytest.mark.parametrize("kind", sorted(DRAGONBALL_TYPES))
def test_dragonball_with_only_stale_comparable_stays_queued(
    tmp_path, monkeypatch, kind
):
    inventory = [stock(1, kind), stock(2, METEOR_SCROLL, slot=1)]
    pc, preview, plan = run(
        tmp_path, monkeypatch, inventory, latest=[METEOR_SCROLL], older=[kind]
    )
    row = row_for(plan, 1)
    assert row["price"] is None
    assert "older than the last market refresh" in row["reason"]
    assert [r["uid"] for r in preview["deferred"]] == [1]
    # The queue keeps value order: known prices first, the Dragonball waits.
    assert [r["uid"] for r in plan] == [2, 1]


# 3: without a recorded refresh time the Dragonball cannot be priced.
def test_dragonball_without_recorded_refresh_stays_queued(tmp_path, monkeypatch):
    pc = PC(tmp_path, monkeypatch, inventory=[stock(1, 1088000)])
    history(pc.history_path, latest_kinds=[1088000])
    catalog, quotes = refill_preview_1078._saved_prices(pc.history_path)
    assert catalog["market_refreshed_at"] == max(
        quote["observed_at"] for quote in quotes.values()
    )
    catalog = {k: v for k, v in catalog.items() if k != "market_refreshed_at"}
    snapshot = pc.observe(pc.runtime, "Kalhiam")
    rows = refill_preview_1078._queue(snapshot, catalog, quotes, None)
    assert rows[0]["total_listing_price"] is None
    assert "market refresh" in rows[0]["reason"]


def test_unreadable_catalog_refresh_time_is_not_invented(tmp_path, monkeypatch):
    pc = PC(tmp_path, monkeypatch, inventory=[stock(1, 1088000)])
    history(pc.history_path, latest_kinds=[1088000])
    with sqlite3.connect(pc.history_path) as db:
        db.execute("UPDATE catalog SET observed_at=?", (time.time() + 3600,))
    catalog, _ = refill_preview_1078._saved_prices(pc.history_path)
    assert "market_refreshed_at" not in catalog
    row = pc.plan()[0]
    assert row["price"] is None and "market refresh" in row["reason"]


# 4: a live owned-booth price below the refresh floor is never matched.
def test_owned_booth_price_below_refresh_floor_keeps_dragonball_queued(
    tmp_path, monkeypatch
):
    kind = 2000033
    peer = [stock(900, kind, price=FLOORS[kind] - 1_000_000)]
    pc, preview, plan = run(
        tmp_path, monkeypatch, [stock(1, kind)], latest=[kind], peer_booth=peer
    )
    row = row_for(plan, 1)
    assert row["price"] is None
    assert "below the lowest live comparable" in row["reason"]


def test_owned_booth_price_at_or_above_floor_is_matched(tmp_path, monkeypatch):
    kind = 2000033
    peer = [stock(900, kind, price=FLOORS[kind] + 250_000)]
    pc, preview, plan = run(
        tmp_path, monkeypatch, [stock(1, kind)], latest=[kind], peer_booth=peer
    )
    row = row_for(plan, 1)
    assert row["price"] == FLOORS[kind] + 250_000
    assert row["source"] == "fresh_owned_booth_price"
    assert row["dragonball_floor"] == FLOORS[kind]


def test_owned_booth_price_without_fresh_comparable_keeps_dragonball_queued(
    tmp_path, monkeypatch
):
    kind = 2000033
    peer = [stock(900, kind, price=FLOORS[kind] * 2)]
    pc, preview, plan = run(
        tmp_path,
        monkeypatch,
        [stock(1, kind)],
        latest=[METEOR_SCROLL],
        older=[kind],
        peer_booth=peer,
    )
    row = row_for(plan, 1)
    assert row["price"] is None
    assert "older than the last market refresh" in row["reason"]


# 5: a restoration price is guarded exactly like a comparable price.
@pytest.mark.parametrize(
    "prior,fresh,expected",
    [
        (FLOORS[1088000] + 1, True, FLOORS[1088000] + 1),
        (FLOORS[1088000] - 1, True, None),
        (FLOORS[1088000] + 1, False, None),
    ],
)
def test_restored_prior_dragonball_price_is_guarded(
    tmp_path, monkeypatch, prior, fresh, expected
):
    pc = PC(tmp_path, monkeypatch, inventory=[stock(1, 1088000)])
    if fresh:
        history(pc.history_path, latest_kinds=[1088000])
    else:
        history(pc.history_path, latest_kinds=[METEOR_SCROLL], older_kinds=[1088000])
    catalog, quotes = refill_preview_1078._saved_prices(pc.history_path)
    snapshot = pc.observe(pc.runtime, "Kalhiam")
    restoration = {"restore_prior_listings": [{"uid": 1, "prior_total_price": prior}]}
    row = refill_preview_1078._queue(snapshot, catalog, quotes, restoration)[0]
    assert row["total_listing_price"] == expected


# 6 + 7: bound Dragonballs and loose Meteors stay queued; scrolls unchanged.
def test_bound_dragonball_loose_meteor_and_scroll_policy(tmp_path, monkeypatch):
    inventory = [
        stock(1, 2000038, bound=True),
        stock(2, METEOR, slot=1),
        stock(3, METEOR_SCROLL, slot=2),
    ]
    pc, preview, plan = run(
        tmp_path, monkeypatch, inventory, latest=[2000038], older=[]
    )
    assert row_for(plan, 1)["price"] is None
    assert row_for(plan, 1)["reason"] == "Bound item"
    assert row_for(plan, 2)["price"] is None
    assert row_for(plan, 2)["reason"] == (
        "Loose Meteors require verified scroll consolidation"
    )
    # MeteorScroll keeps its ordinary saved-comparable pricing (older refresh),
    # proving the Dragonball freshness guard does not leak onto it.
    scroll = row_for(plan, 3)
    assert scroll["price"] == 1_200_000 and "dragonball_floor" not in scroll


# 8: farmer/warehouse semantics are unchanged.
@pytest.mark.parametrize("kind", sorted(DRAGONBALL_TYPES))
def test_farmer_still_banks_and_storage_policy_is_unchanged(kind):
    item = {"uid": 1, "type_id": kind, "name": DRAGONBALL_NAMES[kind], "slot": 4}
    from conquest.banking import urgent_valuables

    assert urgent_storage(item) and urgent_valuables([item]) == [item]
    assert storage_only(item) is (kind in STORAGE_ONLY_TYPES)
    if kind in STORAGE_ONLY_TYPES:
        with pytest.raises(ValueError, match="storage-only"):
            require_marketable(item)
    require_marketable(item, merchant_dragonball=True)


def test_name_only_star_match_is_never_merchant_sellable():
    item = {"type_id": 2222222, "name": "8-StarDragonBall"}
    with pytest.raises(ValueError, match="storage-only"):
        require_marketable(item, merchant_dragonball=True)


# 10: engine admission accepts only the planned top Dragonball.
def _engine(pc, monkeypatch):
    started, ran = [], threading.Event()
    monkeypatch.setattr(listing, "_policy", lambda *a, **k: None)
    monkeypatch.setattr(
        listing, "_farmer_safe_market", lambda *a, **k: {"pid": 90, "hwnd": 91}
    )

    def worker(ui, character, profile, before, token):
        """The input boundary: record the admitted work, never send input."""
        started.append(before)
        ran.set()

    monkeypatch.setattr(listing, "_run", worker)

    class Target:
        def __init__(self, pid, hwnd):
            self.pid, self.hwnd = pid, hwnd

        def snapshot(self):
            return {"root_hwnd": self.hwnd, "foreground": self.hwnd}

    from conquest import input_probe

    monkeypatch.setattr(input_probe, "MessageTarget", Target)
    identity = pc.memory["Kalhiam"]["identity"]
    pc.runtime.merchant_windows = lambda: [SimpleNamespace(identity=identity, hwnd=55)]
    ui = SimpleNamespace(
        runtime=pc.runtime,
        coordinator=SimpleNamespace(fence=None),
        app=SimpleNamespace(
            control=SimpleNamespace(
                snapshot=lambda: {"enabled": False, "paused": False, "revision": 1}
            )
        ),
    )
    return ui, started, ran


def _request(pc, uid, price, suffix):
    snapshot = pc.memory["Kalhiam"]
    item = next(item for item in snapshot["inventory"] if item["uid"] == uid)
    return dict(
        action="merchant-booth-list-once-1078",
        character="Kalhiam",
        request_id="booth-list1078-dragonball-" + suffix,
        item_uid=uid,
        item_fingerprint=listing._item_fingerprint(item),
        price=price,
        expected_identity=snapshot["identity"],
        expected_character_uid=snapshot["character_uid"],
        expected_own_booth_uid=snapshot["own_booth_uid"],
    )


def _dispatch(ui, request):
    try:
        receipt = listing.dispatch(ui, request)
    except ValueError as error:
        return {"admitted": False, "error": str(error)}
    worker = listing.WORKERS.pop(request["request_id"], None)
    if worker is not None:
        worker.join(5)
    return {"admitted": True, "phase": receipt["phase"]}


def test_engine_admits_planned_dragonball_and_refuses_unplanned_or_bound(
    tmp_path, monkeypatch
):
    inventory = [
        stock(1, 2000038, slot=0),
        stock(2, 2000037, slot=1),
        stock(3, 2000031, bound=True, slot=2),
    ]
    pc, preview, plan = run(
        tmp_path, monkeypatch, inventory, latest=[2000038, 2000031], older=[2000037]
    )
    ui, started, ran = _engine(pc, monkeypatch)
    stale = _dispatch(ui, _request(pc, 2, FLOORS[2000037], "stale0001"))
    bound = _dispatch(ui, _request(pc, 3, FLOORS[2000031], "bound0001"))
    assert not stale["admitted"] and "highest-valued" in stale["error"]
    assert not bound["admitted"] and "Bound" in bound["error"]
    assert not started
    accepted = _dispatch(ui, _request(pc, 1, FLOORS[2000038], "epic00001"))
    assert accepted == {"admitted": True, "phase": "prepared"}
    assert ran.wait(5) and len(started) == 1
    assert started[0]["price_plan"]["dragonball_floor"] == FLOORS[2000038]
    with pc.journal.db() as db:
        assert [row[0] for row in db.execute("SELECT id FROM transactions")] == [
            "booth-list1078-dragonball-epic00001"
        ]


def test_engine_refuses_loose_meteor_even_with_fresh_comparable(tmp_path, monkeypatch):
    pc, preview, plan = run(tmp_path, monkeypatch, [stock(1, METEOR)], latest=[])
    ui, started, ran = _engine(pc, monkeypatch)
    result = _dispatch(ui, _request(pc, 1, 150_000, "meteor001"))
    assert not result["admitted"] and "loose Meteor" in result["error"]
    assert not started


def test_engine_rechecks_dragonball_floor_on_the_plan(tmp_path, monkeypatch):
    pc, preview, plan = run(
        tmp_path, monkeypatch, [stock(1, 1088000)], latest=[1088000]
    )
    item = pc.memory["Kalhiam"]["inventory"][0]
    unguarded = [{**plan[0], "dragonball_floor": None}]
    monkeypatch.setattr(listing_plan_1078, "plan", lambda *a: deepcopy(unguarded))
    ui = SimpleNamespace(runtime=pc.runtime)
    with pytest.raises(ValueError, match="lowest live comparable"):
        listing._price_plan(ui, "Kalhiam", pc.observe(pc.runtime, "Kalhiam"), item)


# 11: the legacy undercutting controller never lists any Dragonball.
@pytest.mark.parametrize("kind", sorted(DRAGONBALL_TYPES))
def test_legacy_controller_never_lists_dragonballs(kind):
    from conquest.merchants.controller import MerchantController

    at = time.time()
    data = market(at, {NAMES[kind]: (FLOORS[kind], FLOORS[kind] + 1)})
    snapshot = dict(
        character="Kalhiam",
        server="America",
        booth_open=True,
        identity={"pid": 1},
        timestamp=at,
        booth=[],
        inventory=[stock(1, kind)],
    )
    plans = MerchantController.plan(
        SimpleNamespace(clock=time.time),
        snapshot,
        MarketSnapshot(data, now=at),
        inventory_only=True,
    )
    assert plans[0]["price"] is None


# End to end through the real 1078 refill path.
def scenario(root, monkeypatch):
    inventory = [
        stock(101, 1088000, slot=0),
        stock(102, 2000037, slot=1),  # only a stale comparable
        stock(103, 2000038, bound=True, slot=2),
        stock(104, 2000033, slot=3),  # owned live price below the floor
        stock(105, METEOR_SCROLL, slot=4),
        stock(106, METEOR, slot=5),
        stock(107, 2000031, slot=6),
        stock(108, 720028, slot=7),
    ]
    peer = [stock(900, 2000033, price=FLOORS[2000033] - 1_000_000)]
    pc = PC(root, monkeypatch, inventory=inventory, peer_booth=peer)
    history(
        pc.history_path,
        latest_kinds=[1088000, 2000038, 2000033, 2000031, 720028, METEOR_SCROLL],
        older_kinds=[2000037],
    )
    preview = pc.preview()
    plan = pc.plan()
    ui, started, ran = _engine(pc, monkeypatch)
    attempts = {}
    for uid, suffix in (
        (102, "stale"),
        (103, "bound"),
        (106, "meteor"),
        (107, "nottop"),
    ):
        kind = next(item["type_id"] for item in inventory if item["uid"] == uid)
        attempts[NAMES[kind]] = _dispatch(
            ui, _request(pc, uid, PRICES.get(kind, 150_000), suffix + "0001")
        )
    top = next(row for row in plan if row["price"] is not None)
    admitted = _dispatch(ui, _request(pc, top["uid"], top["price"], "top00001"))
    ran.wait(5)
    with pc.journal.db() as db:
        transactions = [
            {"id": row[0], "phase": row[1]}
            for row in db.execute("SELECT id,phase FROM transactions ORDER BY id")
        ]
    artifact = {
        "rule": "AGENTS.md: Latest loot and urgent banking rule (Dragonball refill)",
        "preview_queue": [list(row) for row in view(preview["queue"])],
        "planner_queue": [list(row) for row in view(plan)],
        "preview_matches_planner": view(preview["queue"]) == view(plan),
        "next_slots_if_qualified": [
            row["uid"] for row in preview["next_slots_if_qualified"]
        ],
        "engine_refusals": attempts,
        "engine_admitted": {
            "uid": top["uid"],
            "name": top["name"],
            "price": top["price"],
            "dragonball_floor": top.get("dragonball_floor"),
            **admitted,
            "worker_price_plan_price": started[0]["price_plan"]["price"]
            if started
            else None,
            "workers_started": len(started),
        },
        "journal_transactions": transactions,
        "farmer_urgent_banking": {
            DRAGONBALL_NAMES[kind]: urgent_storage(
                {"type_id": kind, "name": DRAGONBALL_NAMES[kind], "slot": 0}
            )
            for kind in sorted(DRAGONBALL_TYPES)
        },
        "farmer_storage_only": {
            DRAGONBALL_NAMES[kind]: storage_only(
                {"type_id": kind, "name": DRAGONBALL_NAMES[kind]}
            )
            for kind in sorted(DRAGONBALL_TYPES)
        },
    }
    path = Path(root) / "dragonball-refill-1078.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_dragonball_refill_1078_e2e(tmp_path, monkeypatch):
    first = scenario(tmp_path / "run-a", monkeypatch)
    second = scenario(tmp_path / "run-b", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    artifact = json.loads(first.read_text(encoding="utf-8"))
    assert artifact["preview_matches_planner"] is True
    assert artifact["preview_queue"] == artifact["planner_queue"]
    priced = [row[:3] for row in artifact["planner_queue"] if row[2] is not None]
    assert priced == [
        [108, "DBScroll", 23_500_000],
        [107, "1-StarDragonBall", 3_000_000],
        [101, "DragonBall", 2_300_000],
        [105, "MeteorScroll", 1_500_000],
    ]
    floors = {row[0]: row[5] for row in artifact["planner_queue"]}
    assert floors[108] == 23_500_000 and floors[101] == 2_300_000
    assert floors[105] is None
    reasons = {row[0]: row[4] for row in artifact["planner_queue"] if row[2] is None}
    assert "older than the last market refresh" in reasons[102]
    assert reasons[103] == "Bound item"
    assert "below the lowest live comparable" in reasons[104]
    assert reasons[106] == "Loose Meteors require verified scroll consolidation"
    assert artifact["next_slots_if_qualified"] == [108, 107, 101, 105]
    refusals = artifact["engine_refusals"]
    assert set(refusals) == {
        "7-StarDragonBall",
        "EpicDragonBall",
        "Meteor",
        "1-StarDragonBall",
    }
    assert not any(attempt["admitted"] for attempt in refusals.values())
    assert "highest-valued" in refusals["7-StarDragonBall"]["error"]
    assert "highest-valued" in refusals["1-StarDragonBall"]["error"]
    assert "Bound" in refusals["EpicDragonBall"]["error"]
    assert "loose Meteor" in refusals["Meteor"]["error"]
    assert artifact["engine_admitted"] == {
        "uid": 108,
        "name": "DBScroll",
        "price": 23_500_000,
        "dragonball_floor": 23_500_000,
        "admitted": True,
        "phase": "prepared",
        "worker_price_plan_price": 23_500_000,
        "workers_started": 1,
    }
    assert artifact["journal_transactions"] == [
        {"id": "booth-list1078-dragonball-top00001", "phase": "prepared"}
    ]
    assert all(artifact["farmer_urgent_banking"].values())
    assert artifact["farmer_storage_only"] == {
        DRAGONBALL_NAMES[kind]: kind in STORAGE_ONLY_TYPES
        for kind in sorted(DRAGONBALL_TYPES)
    }
