"""End-to-end: a Back2Classic trade request froze the farmer (live 2026-09-27).

Live on Laptop1 (Toxic, level 1, Back2Classic): a manual-ownership hold from
00:06 fenced every town action; the route failed into needs_attention and the
farmer stood idle until 09:55. 97fe6e8 stopped the hold from becoming
permanent, but a farming-only server still never declines. The observer
fences all farmer input (combat, healing, town) for as long as a stranger's
request stays displayed, because the only decline path requires an America
delivery qualification: the live observation reported "Delivery
qualification does not belong to the selected farmer".

Real code under test: MerchantRuntime's manual farmer observation
(manual_farmer.observe/controller/decline_permission), decline_unrelated_request
with the native 1078 Cancel proof (trade_driver_1078.decline_control and the
GuiReader hover proof), MerchantDriver, InputCoordinator input_scope,
FarmingControl, OvernightLoop.run/town and TownTrade.__call__'s manual
boundary. The boundary fakes (1078 snapshot decoder, native geometry, focus,
physical click, route bridge transport) are shared with
test_farmer_request_decline_e2e. Time is virtual.

Failure modes, written before the fix:
 B1 Request while hunting (Farming On): declined exactly once, about five
    seconds after it appeared, by one press on the memory-proved Cancel
    (never Accept); no visitor session is created; the route's town reads
    wait instead of failing and Farming stays On.
 B2 No delivery qualification exists on a farming-only server: the decline
    still runs on the native request proof alone.
 B3 Request at a town action (the route's own stop_farm turned the control
    Off): declined, because a route-owned Off is not a user Off; the town
    actions then run and the route does not fail.
 B4 Farming Off by the user (overnight.stop marker): no decline input and
    the blocker is reported.
 B5 Global Stop, then F12 held: no input while either holds; one decline
    after both clear.
 B6 A press that leaves the request displayed: at most three presses, at
    least ten seconds apart, never Accept, never a rapid loop.
 B7 The process identity changes under the request: never pressed.
 B8 Only the dedicated observer thread presses; the route's town boundary
    only fences.
 B9 The artifact is not repeatable.

The scenario writes <tmp>/<run>/farming-only-request-decline.json, re-reads
it for the assertions, and runs twice to prove the artifact is byte-identical.
"""

import json
import os
import time

from conquest.merchants.coordination import install

import test_farmer_request_decline_e2e as base


class Client(base.Client):
    def snapshot(self):
        return {**super().snapshot(), "server": "Back2Classic"}

    def click(self, target, x, y, size, **kwargs):
        before = len(self.presses)
        super().click(target, x, y, size, **kwargs)
        for press in self.presses[before:]:
            press["observer_thread"] = self.world.observer_thread


class World(base.World):
    def __init__(self, root, monkeypatch, name):
        self.observer_thread = False
        self.ticking = False
        self.next_tick = None
        # The base world binds the client's click/focus methods while patching.
        monkeypatch.setattr(base, "Client", Client)
        super().__init__(root, monkeypatch, name)

    def qualify(self):
        # A farming-only server has no delivery qualification at all.
        return None

    def sleep(self, seconds):
        """Virtual time; during route scenarios the app's observer thread
        (run_manual_farmer) keeps ticking every 0.5 s, as it does live."""
        end = self.now + max(0.0, seconds)
        if not self.ticking or self.observer_thread:
            self.now = end
            return
        if self.next_tick is None:
            self.next_tick = self.now
        while self.now < end:
            if self.next_tick <= self.now:
                self.next_tick = self.now + 0.5
                self.tick()
            # A tick's own waits advance the clock; never move it backwards.
            self.now = max(self.now, min(end, self.next_tick))

    def observe(self, seconds, *, every=0.5, at=None):
        self.observer_thread = True
        try:
            super().observe(seconds, every=every, at=at)
        finally:
            self.observer_thread = False

    def tick(self):
        """One pass of the app's dedicated manual-farmer observer thread."""
        self.observer_thread = True
        try:
            self.runtime.observe_manual_farmer()
        finally:
            self.observer_thread = False
        blocker = self.runtime.manual_farmer_status()["observation"].get(
            "decline_blocker"
        )
        if blocker and blocker not in self.blockers:
            self.blockers.append(blocker)

    def route(self, scenario):
        self.ticking = True
        try:
            return super().route(scenario)
        finally:
            self.ticking = False


def hunting(world, *, seconds=30, request_tick=2):
    """The hunt loop's per-second shape: living -> town('supplies') -> sleep."""

    def scenario(loop):
        from conquest.overnight import request

        loop.phase = "hunting"
        request(
            loop.info,
            "controls",
            {"enabled": True, "target_type_ids": [1], "target_ids": []},
        )
        loop.record("hunt_started")
        for tick in range(seconds):
            loop.living()
            if tick == request_tick:
                world.client.show_request()
            loop.town("supplies")
            time.sleep(1)
        world.route_result["farming_enabled_at_end"] = world.control.snapshot()[
            "enabled"
        ]

    return world.route(scenario)


def town_action(world):
    """prepare_supplies/restock shape: stop_farm -> close panels -> supplies."""

    def scenario(loop):
        from conquest.overnight import request

        loop.phase = "hunting"
        request(loop.info, "controls", {"enabled": True})
        loop.record("hunt_started")
        loop.phase = "restocking"
        loop.record("return_required")
        loop.stop_farm()
        world.client.show_request()
        loop.town("close", window="Shop")
        loop.town("close", window="Inventory")
        loop.town("supplies")
        world.route_result["farming_enabled_at_end"] = world.control.snapshot()[
            "enabled"
        ]

    return world.route(scenario)


def evidence(world, route=None):
    data = world.evidence(route)
    observation = world.runtime.manual_farmer_status()["observation"]
    data["modal_fence"] = bool(observation.get("farming_only_modal"))
    return data


def scenario(root, monkeypatch):
    artifact = {}
    try:
        # B1/B2/B8: request while hunting, no delivery qualification anywhere.
        world = World(root, monkeypatch, "hunting")
        artifact["hunting"] = evidence(world, hunting(world))

        # B3: request at a town action after the route's own stop_farm.
        world = World(root, monkeypatch, "town_action")
        artifact["town_action"] = evidence(world, town_action(world))

        # B4: Farming Off by the user while a route status still looks live.
        world = World(root, monkeypatch, "user_off")
        stop = world.root / ".runtime" / "overnight.stop"
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text("Stopped by user: Farming Off", encoding="utf-8")
        status = world.root / "reports" / "overnight" / "status.json"
        status.parent.mkdir(parents=True)
        status.write_text(
            json.dumps(
                {"pid": os.getpid(), "phase": "hunting", "updated_at": world.now}
            ),
            encoding="utf-8",
        )
        world.client.show_request()
        world.observe(15)
        artifact["user_off"] = evidence(world)

        # B5: Global Stop, then F12 held, then both released.
        world = World(root, monkeypatch, "stop_and_f12")
        world.control.update({"enabled": True})
        world.client.show_request()
        world.guard.stop()

        def controls(t):
            if t >= 10 and world.guard.stopped:
                world.guard.resume()
                world.keys.add(0x7B)
            if t >= 15:
                world.keys.discard(0x7B)

        world.observe(30, at=controls)
        artifact["stop_and_f12"] = evidence(world)

        # B6: the Cancel press leaves the request displayed.
        world = World(root, monkeypatch, "uncertain")
        world.client.cancel_effective = False
        world.control.update({"enabled": True})
        world.client.show_request()
        world.observe(90)
        artifact["uncertain"] = evidence(world)

        # B7: the process identity changes under the pending request.
        world = World(root, monkeypatch, "identity_changed")
        world.control.update({"enabled": True})
        world.client.show_request()

        def replace(t):
            if t >= 2:
                world.client.identity = {
                    **world.client.identity,
                    "pid": 20001,
                    "creation_time_100ns": 134345064188679999,
                }

        world.observe(20, at=replace)
        artifact["identity_changed"] = evidence(world)
    finally:
        install(None)
    return artifact


def run(tmp_path, monkeypatch, name):
    with monkeypatch.context() as patch:
        artifact = scenario(tmp_path / name, patch)
    path = tmp_path / name / "farming-only-request-decline.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_farming_only_request_is_declined_without_delivery_qualification(
    tmp_path, monkeypatch
):
    first = run(tmp_path, monkeypatch, "first")
    second = run(tmp_path, monkeypatch, "second")
    # B9: the artifact is byte-identical across runs.
    assert first.read_bytes() == second.read_bytes()
    a = json.loads(first.read_text(encoding="utf-8"))

    # B1/B2/B8: one Cancel about five seconds after the request appeared,
    # pressed by the observer thread, with no visitor session or failure.
    hunt = a["hunting"]
    shown = hunt["marks"]["request_shown"]
    assert [p["control"] for p in hunt["presses"]] == ["Cancel"]
    assert all(p["observer_thread"] for p in hunt["presses"])
    assert 5 <= hunt["presses"][0]["t"] - shown <= 7
    assert hunt["session"] is None and hunt["claims"] == 0
    assert hunt["decline_journal"] == "verified"
    assert not hunt["request_visible"] and not hunt["trade_open"]
    assert not hunt["route"]["failed"]
    assert hunt["route"]["farming_enabled_at_end"] is True

    # B3: the town actions run after the decline and the route does not fail.
    town = a["town_action"]
    assert [p["control"] for p in town["presses"]] == ["Cancel"]
    assert not town["route"]["failed"]
    assert [row[1] for row in town["route"]["town_actions"]] == [
        "close",
        "close",
        "supplies",
    ]
    assert town["route"]["town_actions"][0][0] >= town["presses"][0]["t"]

    # B4: the user's Off holds all decline input.
    off = a["user_off"]
    assert off["presses"] == [] and off["request_visible"]
    assert any("Farming Off by user" in b for b in off["blockers"])

    # B5: nothing while Global Stop or F12 holds; one Cancel afterwards.
    held = a["stop_and_f12"]
    assert [p["control"] for p in held["presses"]] == ["Cancel"]
    assert held["presses"][0]["t"] >= 15

    # B6: bounded retries of an ineffective Cancel, never Accept.
    uncertain = a["uncertain"]
    controls = [p["control"] for p in uncertain["presses"]]
    assert controls == ["Cancel"] * len(controls) and 1 <= len(controls) <= 3
    times = [p["t"] for p in uncertain["presses"]]
    assert all(b - a_ >= 10 for a_, b in zip(times, times[1:]))
    assert uncertain["request_visible"] and uncertain["modal_fence"]

    # B7: a changed client identity is never pressed.
    changed = a["identity_changed"]
    assert changed["presses"] == []
