"""Synthetic town-batch refill regression; no game process, input or journals.

Provenance: merchants/town_batch.py was written before these tests and before
the AGENTS.md testing rule (3b497a0). These cases were written before the
merchants.handoff.service_window hook existed; the end-to-end scenario is in
test_town_batch_refill_e2e.py. Isolated failure modes covered here:
 - policy: malformed/unknown values or types must disable the batch;
 - backlog signal: stale, paused, not-due, unqualified, non-waiting,
   disconnected or non-integer merchant evidence must not count;
 - trigger: a backlog of four keeps the existing field window, five starts
   city parking;
 - refusal: manual handoff/session/farmer fence, pending transaction,
   attention, recovery, shop return, unreconciled listing, active input
   owner, town visit, delivery journey, Meteor banking, death return, other
   map, non-hunting phase, Global Stop and paused refill never start a batch;
 - stop: threat, damage, leaving the spot, death, manual mouse, manual Stop,
   F11, uncertain listing, no listing progress, cap and unreleased input;
 - fallback: failed city parking uses the existing field window; a merchant
   incident while travelling aborts without any grant or fallback.
 - travel (listed before its code): a merchant request that briefly
   disappears while travelling (peer re-read, re-request) must not abort the
   walk; one missing for REQUEST_WAIT_SECONDS aborts without a grant.
"""

import copy
import ctypes
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import town_batch
from conquest.merchants.handoff import WorkWindows

CITY = {
    "map_id": 1011,
    "name": "PhoenixCity",
    "town_anchor": [191, 250],
    "town_boundary": [111, 111, 254, 258],
    "terrain_sha256": "phoenix-terrain",
}
SPOT = [191, 249]
FIELD = [405, 413]


class Merchants:
    """Minimal merchant app: one exact refill request, grants, listings."""

    def __init__(self, now, backlog):
        self.now = now
        self.backlog = dict(backlog)
        self.names = sorted(backlog)
        self.key = None
        self.grant = None
        self.turn = 0
        self.listing_seconds = 17
        self.listed = {name: 0 for name in self.names}
        self.proof = {}
        self.blocker = {name: "waiting_farmer_handoff" for name in self.names}
        self.requests = {}
        self.calls = []
        self.grants = []
        self.releases = []
        self.extra = {}
        self.character_extra = {name: {} for name in self.names}
        self.never_list = False
        self.uncertain = False
        self.release_ok = True
        self.on_status = None
        self.next_request()

    def next_request(self):
        waiting = [n for n in self.names if self.backlog[n] > 0]
        if waiting and self.key is None and self.grant is None:
            name = waiting[self.turn % len(waiting)]
            self.turn += 1
            self.key = f"merchant-refill:{name}:{int(self.now[0] * 1000)}"
            self.blocker[name] = "waiting_farmer_handoff"

    def advance(self):
        grant = self.grant
        if not grant or grant["listed"] or self.never_list:
            return
        name = grant["character"]
        if self.now[0] - grant["started"] < self.listing_seconds:
            if self.backlog[name] > 0:
                self.requests[name] = {"request_id": "booth-list1078-refill-x"}
            return
        if self.uncertain:
            return  # Receipt stays prepared/uncertain; the engine reconciles.
        self.requests[name] = None
        self.backlog[name] -= 1
        self.listed[name] += 1
        grant["listed"] = True
        self.proof[name] = {
            "request_id": f"booth-list1078-refill-{name}-{self.listed[name]}",
            "verified_at": self.now[0],
            "character": name,
        }
        self.blocker[name] = "listing_work_budget_insufficient"

    def status(self):
        self.advance()
        if self.on_status:
            self.on_status(self)
        characters = {}
        for name in self.names:
            characters[name] = {
                "connected": True,
                "pending": [],
                "needs_attention": None,
                "manual_input_fence": False,
                "shop_return": None,
                "recovery_safety": None,
                "qualification": {"foreground_open_booth_listing_1078": True},
                "refill": {
                    "enabled": True,
                    "pending": True,
                    "listing1078_request": self.requests.get(name),
                    "last_verified_listing": self.proof.get(name),
                },
                "foreground_refill_1078": {
                    "blocker": self.blocker[name] if self.backlog[name] else None,
                    "eligible_backlog": self.backlog[name],
                    "backlog_observed_at": self.now[0],
                }
                if self.backlog[name]
                else {"state": "capacity_checked"},
                **copy.deepcopy(self.character_extra[name]),
            }
        return {
            "characters": characters,
            "handoff_requested": self.key,
            "input_owner": None,
            "handoff_active": self.grant is not None,
            "handoff_granted": self.grant is not None,
            "host_request": None,
            "manual_handoff": None,
            "manual_sessions": [],
            "manual_farmer": {"input_fenced": False},
            **self.extra,
        }

    def __call__(self, body):
        self.calls.append(copy.deepcopy(body))
        action = body["action"]
        if action == "status":
            return self.status()
        if action == "handoff-grant":
            assert self.grant is None and body["request_id"] == self.key
            assert body["scope"] == "listing_1078" and body["safe"] is True
            assert body["character"] == self.key.split(":")[1]
            assert 0 < body["expires_at"] - self.now[0] <= 45
            self.grant = {
                "character": body["character"],
                "started": self.now[0],
                "listed": False,
                "expires_at": body["expires_at"],
            }
            self.grants.append(dict(body, granted_at=self.now[0]))
            return {"granted": True}
        if action == "handoff-release":
            self.releases.append((body["request_id"], self.now[0]))
            if not self.release_ok:
                return {"released": False, "waiting_for_input_release": True}
            assert body["request_id"] == self.key
            self.advance()
            self.grant = None
            self.key = None
            self.next_request()
            return {"released": True}
        raise AssertionError(action)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    from conquest.merchants import handoff, bridge
    from conquest import worker, safe_reload, overnight, manual_storage_recovery
    from conquest.merchants import delivery_operation
    import time

    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    monkeypatch.setattr(ctypes.windll.user32, "GetAsyncKeyState", lambda key: 0)
    monkeypatch.setattr(overnight, "RECOVERY_CHECKPOINT", tmp_path / "death.json")
    monkeypatch.setattr(delivery_operation, "JOURNAL", tmp_path / "delivery.sqlite3")
    monkeypatch.setattr(manual_storage_recovery, "REPORT", tmp_path / "manual.json")
    monkeypatch.setattr("conquest.city_travel.city_for", lambda map_id: CITY)
    windows = WorkWindows(tmp_path / "window.json", clock=lambda: now[0])
    monkeypatch.setattr(handoff, "WorkWindows", lambda: windows)
    merchants = Merchants(now, {"Dutch": 6, "Spiritual": 7})
    monkeypatch.setattr(bridge, "request", merchants)
    health = {
        "target": {"pid": 7},
        "embedded_controls": {
            "control": {"enabled": True, "revision": 1, "paused": False},
            "observations_available": True,
            "external_execution": False,
            "monsters": [],
            "life": {
                "object_address": 1,
                "map_id": 1011,
                "position": list(FIELD),
                "dead_candidate": False,
                "current_hp": 1000,
                "max_hp": 1000,
            },
        },
    }
    controls = []

    def farm(info, operation, body=None):
        if operation == "controls":
            controls.append(dict(body))
            health["embedded_controls"]["control"].update(body)
        result = copy.deepcopy(health)
        result["embedded_controls"]["observed_at"] = now[0]
        return result

    monkeypatch.setattr(worker, "request", farm)
    parks = []
    city = {"fails": False, "travel_seconds": 60}

    def park(loop, cancelled, notify, **kwargs):
        parks.append(kwargs)
        cancelled.is_set()
        if kwargs.get("require_city"):
            for _ in range(int(city["travel_seconds"] // 3)):
                now[0] += 3
                cancelled.is_set()
            if city["fails"]:
                raise ValueError("No quiet nearby spot verified; reload deferred")
            health["embedded_controls"]["life"]["position"] = list(SPOT)
            return {
                "target": health["target"],
                "position": list(SPOT),
                "map_id": 1011,
                "hp": 1000,
                "verified_at": now[0],
                "city_required": True,
                "city_anchor": list(SPOT),
                "city_terrain_sha256": CITY["terrain_sha256"],
            }
        now[0] += 5
        return {"target": health["target"]}

    monkeypatch.setattr(safe_reload, "park", park)
    events = []
    stop = {"at": None, "reason": "Stopped by user"}

    def check_stop():
        if stop["at"] is not None and now[0] >= stop["at"]:
            raise overnight.OvernightStopped(stop["reason"])

    def stop_farm():
        health["embedded_controls"]["control"].update(enabled=False, revision=2)

    loop = NS(
        info="test",
        phase="hunting",
        health=lambda: (check_stop(), farm("test", "health"))[1],
        stop_farm=stop_farm,
        check_stop=check_stop,
        record=lambda event, **fields: events.append((event, copy.deepcopy(fields))),
        focus=lambda h: True,
        route=NS(restock_map_id=1011, map_id=1011),
        town_visit=None,
    )
    return NS(
        now=now,
        merchants=merchants,
        health=health,
        controls=controls,
        parks=parks,
        city=city,
        events=events,
        stop=stop,
        windows=windows,
        loop=loop,
        run=lambda: handoff.service_window(loop),
    )


def names(events):
    return [event for event, _ in events]


def field(events, name):
    return [fields for event, fields in events if event == name]


def test_policy_defaults_and_malformed_values_fail_closed():
    assert town_batch.policy({}) == town_batch.DEFAULTS
    assert town_batch.DEFAULTS["backlog_threshold"] == 5
    assert town_batch.DEFAULTS["max_seconds"] == 600
    custom = town_batch.policy({"town_batch_refill": {"backlog_threshold": 8}})
    assert custom["enabled"] and custom["backlog_threshold"] == 8
    for bad in (
        {"backlog_threshold": 0},
        {"backlog_threshold": True},
        {"max_seconds": 5},
        {"enabled": "yes"},
        {"unknown": 1},
        [],
    ):
        assert not town_batch.policy({"town_batch_refill": bad})["enabled"]
    assert not town_batch.policy({"town_batch_refill": {"enabled": False}})["enabled"]


@pytest.mark.parametrize(
    "change",
    ["stale", "paused", "not_due", "unqualified", "blocker", "disconnected", "type"],
)
def test_backlog_requires_fresh_enabled_due_qualified_waiting_merchant(change):
    now = 1000.0
    status = Merchants([now], {"Dutch": 6}).status()
    assert town_batch.backlog(status, "Dutch", now=now) == 6
    c = status["characters"]["Dutch"]
    if change == "stale":
        c["foreground_refill_1078"]["backlog_observed_at"] = now - 11
    elif change == "paused":
        c["refill"]["enabled"] = False
    elif change == "not_due":
        c["refill"].update(pending=False, next_check=now + 5)
    elif change == "unqualified":
        c["qualification"] = {}
    elif change == "blocker":
        c["foreground_refill_1078"]["blocker"] = "unknown_prices_deferred"
    elif change == "disconnected":
        c["connected"] = False
    elif change == "type":
        c["foreground_refill_1078"]["eligible_backlog"] = "6"
    assert town_batch.backlog(status, "Dutch", now=now) is None


@pytest.mark.parametrize("threshold_met", [False, True])
def test_trigger_threshold_selects_city_batch_only_at_five(batch, threshold_met):
    r = batch
    r.merchants.backlog = {"Dutch": 5 if threshold_met else 4, "Spiritual": 0}
    r.merchants.key = None
    r.merchants.next_request()
    assert r.run()
    assert bool(r.parks[0].get("require_city")) is threshold_met
    assert ("merchant_town_batch_started" in names(r.events)) is threshold_met
    if not threshold_met:
        # Existing field behaviour: one exact 45-second listing grant.
        assert len(r.merchants.grants) == 1 and r.parks[0]["seconds"] == 30


def test_batch_serves_both_merchants_until_backlogs_drain_then_resumes(batch):
    r = batch
    assert r.run()
    assert r.merchants.listed == {"Dutch": 6, "Spiritual": 7}
    assert r.merchants.backlog == {"Dutch": 0, "Spiritual": 0}
    served = {g["character"] for g in r.merchants.grants}
    assert served == {"Dutch", "Spiritual"}
    # Every grant is the existing exact-merchant listing scope, one at a time,
    # and each is released before the next grant is issued.
    assert len(r.merchants.grants) == 13 and len(r.merchants.releases) == 13
    for grant, (key, released_at) in zip(r.merchants.grants, r.merchants.releases):
        assert grant["request_id"] == key and released_at <= grant["expires_at"]
    park = r.parks[0]
    assert park["require_city"] is True and park["allow_town_retreat"] is False
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"] == "backlog_cleared"
    assert finished["listings"] == {"Dutch": 6, "Spiritual": 7}
    assert finished["listed"] == 13 and finished["grants"] == 13
    assert finished["backlogs_before"] == {"Dutch": 6, "Spiritual": 7}
    assert finished["elapsed_seconds"] > 0 and finished["parking_seconds"] >= 60
    parking = field(r.events, "merchant_parking_finished")[0]["parking"]
    assert parking["mode"] == "city" and parking["outcome"] == "safe"
    # Normal hunt-return path: the farmer is re-enabled after release.
    assert r.controls[-1] == {"enabled": True}
    assert names(r.events)[-1] == "merchant_work_finished"
    state = r.windows.state()
    assert state["phase"] == "town_batch_finished"
    assert state["next_check"] >= r.now[0] - 1 + 900
    r.merchants.backlog["Dutch"] = 6
    r.merchants.next_request()
    grants = len(r.merchants.grants)
    assert not r.run()  # Cooldown: no immediate second batch or field window.
    assert len(r.merchants.grants) == grants and len(r.parks) == 1


def test_each_grant_rechecks_farmer_safety_and_threat_stops_batch(batch):
    r = batch
    life = r.health["embedded_controls"]

    def threat(merchants):
        if len(merchants.releases) == 2 and merchants.grant is None:
            life["monsters"] = [{"position": [195, 249], "alive": True}]

    r.merchants.on_status = threat
    assert r.run()
    assert len(r.merchants.grants) == 2
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"] == "farmer_unsafe:threat_or_low_health"


@pytest.mark.parametrize("unsafe", ["damage", "moved", "dead", "manual_mouse"])
def test_other_farmer_changes_stop_before_next_grant(batch, unsafe):
    r = batch
    data = r.health["embedded_controls"]

    def change(merchants):
        if len(merchants.releases) == 1 and merchants.grant is None:
            if unsafe == "damage":
                data["life"]["current_hp"] = 990
            elif unsafe == "moved":
                data["life"]["position"] = [200, 249]
            elif unsafe == "dead":
                data["life"]["dead_candidate"] = True
            else:
                data["manual_mouse"] = True

    r.merchants.on_status = change
    r.run()
    assert len(r.merchants.grants) == 1
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"].startswith("farmer_unsafe:")


def test_cap_bounds_total_batch_time_and_every_grant(batch):
    r = batch
    r.merchants.backlog = {"Dutch": 30, "Spiritual": 30}
    started = r.now[0]
    assert r.run()
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"] == "cap_reached"
    assert all(g["expires_at"] <= started + 600 for g in r.merchants.grants)
    assert all(g["expires_at"] - g["granted_at"] <= 45 for g in r.merchants.grants)
    assert finished["elapsed_seconds"] <= 600
    assert 0 < finished["listed"] < 60


def test_manual_stop_mid_batch_revokes_grant_and_never_resumes(batch):
    from conquest.overnight import OvernightStopped

    r = batch
    r.stop["at"] = r.now[0] + 60 + 30  # During the second listing grant.
    with pytest.raises(OvernightStopped):
        r.run()
    assert len(r.merchants.grants) == 2
    assert len(r.merchants.releases) == 2  # The active grant was released.
    assert {"enabled": True} not in r.controls
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"] == "interrupted"


def test_f11_pause_mid_batch_releases_and_never_resumes(batch, monkeypatch):
    from conquest.overnight import OvernightStopped

    r = batch
    monkeypatch.setattr(
        ctypes.windll.user32,
        "GetAsyncKeyState",
        lambda key: 0x8000 if key == 0x7A and len(r.merchants.grants) >= 1 else 0,
    )
    with pytest.raises(OvernightStopped, match="F11"):
        r.run()
    assert len(r.merchants.grants) == 1 and len(r.merchants.releases) == 1
    assert {"enabled": True} not in r.controls


def test_uncertain_listing_is_never_replayed_by_another_grant(batch):
    r = batch
    r.merchants.uncertain = True
    assert r.run()
    assert len(r.merchants.grants) == 1
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"] == "listing_needs_reconciliation"
    assert finished["listed"] == 0


def test_grants_without_verified_listings_stop_the_batch(batch):
    r = batch
    r.merchants.never_list = True
    assert r.run()
    assert len(r.merchants.grants) == 2
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"] == "no_listing_progress"


def test_city_parking_failure_falls_back_to_existing_field_window(batch):
    r = batch
    r.city["fails"] = True
    assert r.run()
    assert [bool(p.get("require_city")) for p in r.parks] == [True, False]
    assert r.parks[1]["seconds"] == 30 and r.parks[1]["allow_town_retreat"] is False
    assert len(r.merchants.grants) == 1
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"] == "city_parking_failed" and finished["grants"] == 0
    assert r.controls[-1] == {"enabled": True}


def test_merchant_incident_while_travelling_aborts_without_any_grant(batch):
    r = batch

    def incident(merchants):
        if merchants.now[0] >= 1020:
            merchants.character_extra["Dutch"]["recovery_safety"] = {"active": True}

    r.merchants.on_status = incident
    assert r.run() is False
    assert not r.merchants.grants
    parking = field(r.events, "merchant_parking_finished")[0]["parking"]
    assert parking["outcome"] == "aborted" and "Dutch:recovery" in parking["reason"]
    finished = field(r.events, "merchant_town_batch_finished")[0]["town_batch"]
    assert finished["outcome"] == "merchant_state_changed"
    assert r.controls[-1] == {"enabled": True}
    assert len(r.parks) == 1  # No field fallback after a merchant incident.


@pytest.mark.parametrize("missing_seconds,aborted", [(4, False), (30, True)])
def test_request_missing_while_travelling_aborts_only_when_persistent(
    batch, missing_seconds, aborted
):
    r = batch
    held = {}

    def gap(merchants):
        if 1010 <= merchants.now[0] < 1010 + missing_seconds:
            if merchants.key:
                held["key"] = merchants.key
            merchants.key = None
        elif held.get("key") and merchants.key is None and merchants.grant is None:
            merchants.key = held.pop("key")

    r.merchants.on_status = gap
    outcome = r.run()
    parking = field(r.events, "merchant_parking_finished")[0]["parking"]
    if aborted:
        assert outcome is False and not r.merchants.grants
        assert parking["outcome"] == "aborted"
        assert "no_qualified_listing_request" in parking["reason"]
    else:
        assert outcome is True and parking["outcome"] == "safe"
        assert r.merchants.grants


def test_unreleased_grant_keeps_farmer_stopped(batch):
    r = batch
    r.merchants.release_ok = False
    with pytest.raises(ValueError, match="did not release"):
        r.run()
    assert len(r.merchants.grants) == 1
    assert {"enabled": True} not in r.controls


@pytest.mark.parametrize(
    "refusal",
    [
        "manual_handoff",
        "manual_session",
        "manual_farmer",
        "pending_transaction",
        "attention",
        "merchant_recovery",
        "shop_return",
        "unreconciled_listing",
        "input_owner",
        "town_visit",
        "delivery_journey",
        "meteor_banking",
        "death_return",
        "other_map",
        "not_hunting",
    ],
)
def test_refusals_keep_existing_field_behaviour(batch, monkeypatch, refusal):
    r = batch
    m = r.merchants
    if refusal == "manual_handoff":
        m.extra["manual_handoff"] = {"active": True}
    elif refusal == "manual_session":
        m.extra["manual_sessions"] = [{"character": "Dutch"}]
    elif refusal == "manual_farmer":
        m.extra["manual_farmer"] = {"input_fenced": True}
    elif refusal == "pending_transaction":
        m.character_extra["Spiritual"]["pending"] = [{"phase": "uncertain"}]
    elif refusal == "attention":
        m.character_extra["Spiritual"]["needs_attention"] = {"kind": "x"}
    elif refusal == "merchant_recovery":
        m.character_extra["Spiritual"]["recovery_safety"] = {"active": True}
    elif refusal == "shop_return":
        m.character_extra["Spiritual"]["shop_return"] = {"phase": "travelling"}
    elif refusal == "unreconciled_listing":
        m.requests["Spiritual"] = {"request_id": "booth-list1078-refill-old"}
    elif refusal == "input_owner":
        m.extra["input_owner"] = "Spiritual"
    elif refusal == "town_visit":
        r.loop.town_visit = NS(active_id=lambda: "visit-1")
    elif refusal == "delivery_journey":
        monkeypatch.setattr("conquest.merchants.delivery_journey.pending", lambda: True)
    elif refusal == "meteor_banking":
        monkeypatch.setattr("conquest.meteor_banking.pending", lambda: True)
    elif refusal == "death_return":
        from conquest import overnight

        overnight.RECOVERY_CHECKPOINT.write_text('{"phase": "returning"}')
    elif refusal == "other_map":
        r.loop.route.restock_map_id = 1002
    elif refusal == "not_hunting":
        r.loop.phase = "restocking"
    r.run()
    assert not any(p.get("require_city") for p in r.parks)
    assert "merchant_town_batch_started" not in names(r.events)
    refused = field(r.events, "merchant_town_batch_refused")
    assert refused and refused[0]["town_batch_reasons"]


@pytest.mark.parametrize("stop", ["global_stop", "refill_paused"])
def test_global_stop_or_paused_refill_never_triggers(batch, stop):
    r = batch
    for name in r.merchants.names:
        r.merchants.character_extra[name]["refill"] = {
            "enabled": False,
            "pending": True,
        }
    if stop == "refill_paused":
        del r.merchants.character_extra["Dutch"]["refill"]
        r.merchants.key = "merchant-refill:Dutch:1"
        r.merchants.backlog["Dutch"] = 3
    r.run()
    assert not any(p.get("require_city") for p in r.parks)
    assert "merchant_town_batch_started" not in names(r.events)
