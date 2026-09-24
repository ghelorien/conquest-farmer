"""Synthetic safety tests; none substitutes for the live panel receipt."""

from contextlib import contextmanager
from copy import deepcopy
import json
import struct
import threading
import time
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import owned_booth_panel_1078 as panel
from conquest.merchants import owned_booth_hover_1078 as hover
from conquest.merchants import listing_handoff_1078 as handoff
from conquest.merchants.journal import Journal
from conquest.memory_build_layout import CLIENT_SHA256_1078


@pytest.fixture
def state(tmp_path, monkeypatch):
    profile = NS(id="dutch-profile", name="Dutch", character_uid=8, server="America")
    monkeypatch.setattr(panel, "_profile", lambda c: profile)
    monkeypatch.setattr(
        "conquest.merchants.booth_listing_once_1078._profile", lambda c: profile
    )
    snap = dict(
        identity={"pid": 2, "creation_time_100ns": 3},
        character="Dutch",
        character_uid=8,
        server="America",
        map_id=1036,
        position=[230, 205],
        hp=93,
        silver=9,
        capacity=40,
        inventory=[{"uid": 5, "type_id": 720027, "quantity": 1}],
        booth=[],
        own_booth_uid=77,
        booth_open=False,
        closed_modal=True,
        timestamp=time.time(),
        client_sha256=CLIENT_SHA256_1078,
    )
    body = dict(
        action="merchant-owned-panel-prepare-1078",
        character="Dutch",
        request_id="booth-open1078-synthetic-one",
        expected_identity=snap["identity"],
        expected_character_uid=8,
        expected_own_booth_uid=77,
    )
    before = dict(
        request=body,
        profile_id=profile.id,
        snapshot=snap,
        hwnd=12,
        client_sha256=CLIENT_SHA256_1078,
    )
    journal = Journal(tmp_path / "journal.sqlite3")
    journal.begin(body["request_id"], "Dutch", panel.KIND, before)
    journal.set("Dutch", panel.PENDING, body["request_id"])
    runtime = NS(journal=journal, can_start_work=lambda n: True)
    ui = NS(runtime=runtime)
    return NS(
        j=journal,
        snap=snap,
        body=body,
        before=before,
        ui=ui,
        key=body["request_id"],
        profile=profile,
    )


def opened(x):
    s = deepcopy(x.snap)
    s.update(
        booth_open=True,
        timestamp=time.time(),
        listing_preflight={
            "layout_observed": True,
            "inventory_grid": {"id": 1},
            "booth_grid": {"id": 2},
            "owned_booth": {
                "model_key": 25,
                "owner_uid": 77,
                "model_owner_verified": True,
            },
            "price_modal": {"observed": False},
        },
    )
    return s


def marked(x):
    value = {
        "candidate": {"actor_address": "0x123456", "owned_booth_uid": 77},
        "selected": {"selected_actor": "0x123466", "owned_booth_uid": 77},
        "identity": x.snap["identity"],
        "hwnd": 12,
        "native_code_sha256": [pin[2] for pin in hover.PINS],
    }
    x.j.step(x.key, "panel_click", "before_action", value)


def test_closed_panel_full_assets_and_new_actor_binding(state):
    x = state
    panel._closed(x.snap, x.body, x.profile)
    for field, value in [
        ("own_booth_uid", 78),
        ("character_uid", 9),
        ("identity", {"pid": 3}),
        ("booth_open", True),
        ("closed_modal", False),
    ]:
        with pytest.raises(ValueError):
            panel._closed({**x.snap, field: value}, x.body, x.profile)


@pytest.mark.parametrize(
    "change", ["inventory", "silver", "booth", "owner", "grid", "modal"]
)
def test_changed_assets_or_incomplete_native_open_cannot_settle(state, change):
    x = state
    s = opened(x)
    if change == "inventory":
        s["inventory"][0]["uid"] = 6
    elif change == "silver":
        s["silver"] += 1
    elif change == "booth":
        s["booth"] = [{"uid": 9}]
    elif change == "owner":
        s["listing_preflight"]["owned_booth"]["owner_uid"] = 88
    elif change == "grid":
        s["listing_preflight"]["inventory_grid"] = None
    else:
        s["listing_preflight"]["price_modal"]["observed"] = True
    with pytest.raises(ValueError):
        panel._verify_open(s, x.snap)


def test_open_without_input_marker_does_not_qualify(state, monkeypatch):
    x = state
    monkeypatch.setattr(
        "conquest.merchants.observe_1078.observe",
        lambda *a, **k: pytest.fail("Unsent proof is not reconciled"),
    )
    assert panel.reconcile(x.ui, "Dutch", x.key)["phase"] == "prepared"
    assert x.j.get("Dutch", panel.CAPABILITY) is None


def test_crash_after_marker_before_phase_update_never_starts_worker(state, monkeypatch):
    x = state
    x.j.step(
        x.key, "panel_click", "before_action", {"selected": {"owned_booth_uid": 77}}
    )
    assert panel.status(x.j, "Dutch", x.key)["phase"] == "prepared"
    monkeypatch.setattr(
        panel.threading,
        "Thread",
        lambda *a, **k: pytest.fail("A marked click cannot be replayed"),
    )
    monkeypatch.setattr(
        "conquest.merchants.observe_1078.observe", lambda *a, **k: opened(x)
    )
    assert panel.step(x.ui, "Dutch", opened(x))["phase"] == "verified"


@pytest.mark.parametrize("field", ["expected_character_uid", "expected_own_booth_uid"])
@pytest.mark.parametrize("value", [0, -1, True, "77"])
def test_request_uids_require_exact_positive_integers(state, field, value):
    x = state
    with pytest.raises(ValueError):
        panel._closed(x.snap, {**x.body, field: value}, x.profile)


def test_submitted_receipt_settles_read_only_and_survives_restart(state, monkeypatch):
    x = state
    marked(x)
    x.j.transition(x.key, "uncertain", {"click_attempted": True})
    monkeypatch.setattr(
        "conquest.merchants.observe_1078.observe", lambda *a, **k: opened(x)
    )
    result = panel.reconcile(x.ui, "Dutch", x.key)
    assert result["phase"] == "verified" and result["result"]["assets_unchanged"]
    x.ui.runtime.journal = Journal(x.j.path)
    saved = panel.require_receipt(x.ui.runtime, "Dutch", x.snap)
    assert saved["request_id"] == x.key
    assert panel.reconcile(x.ui, "Dutch", x.key) == result
    with pytest.raises(ValueError):
        panel.require_receipt(
            x.ui.runtime, "Dutch", {**x.snap, "identity": {"pid": 99}}
        )


@pytest.fixture
def qualified(state, monkeypatch):
    x = state
    marked(x)
    monkeypatch.setattr(
        "conquest.merchants.observe_1078.observe", lambda *a, **k: opened(x)
    )
    panel.reconcile(x.ui, "Dutch", x.key)
    x.ui.runtime.refills = {"Dutch": NS(due=lambda: True)}
    x.ui.runtime.refill_enabled = lambda c: True
    return x


def test_due_new_booth_preparation_uses_verified_historical_receipt_without_rebinding(
    qualified, monkeypatch
):
    x = qualified
    calls = []
    historical = panel.status(x.j, "Dutch", x.key)
    current = {**deepcopy(x.snap), "own_booth_uid": 99, "timestamp": time.time()}

    def prepare(ui, body):
        calls.append(body)
        assert (
            body["expected_own_booth_uid"] == 99
            and body["expected_identity"] == x.snap["identity"]
        )
        assert body["request_id"] != x.key
        x.j.begin(body["request_id"], "Dutch", panel.KIND, {"request": body})
        x.j.set("Dutch", panel.PENDING, body["request_id"])
        return {"request_id": body["request_id"], "phase": "prepared"}

    monkeypatch.setattr(panel, "prepare", prepare)
    assert panel.prepare_due(x.ui, "Dutch", current)["state"] == "owned_panel_prepared"
    assert (
        panel.prepare_due(x.ui, "Dutch", current)["blocker"]
        == "owned_panel_request_needs_reconciliation"
    )
    assert len(calls) == 1 and panel.status(x.j, "Dutch", x.key) == historical


@pytest.mark.parametrize(
    "case",
    ["no_receipt", "process", "build", "not_due", "refill_paused", "already_open"],
)
def test_automatic_prepare_retains_receipt_due_and_intent_guards(
    qualified, monkeypatch, case
):
    x = qualified
    current = deepcopy(x.snap)
    monkeypatch.setattr(
        panel, "prepare", lambda *a: pytest.fail("No automatic preparation admitted")
    )
    if case == "no_receipt":
        x.j.set("Dutch", panel.CAPABILITY, None)
    elif case == "process":
        current["identity"] = {"pid": 999}
    elif case == "build":
        current["client_sha256"] = "changed"
    elif case == "not_due":
        x.ui.runtime.refills["Dutch"].due = lambda: False
    elif case == "refill_paused":
        x.ui.runtime.refill_enabled = lambda c: False
    else:
        current["booth_open"] = True
    result = panel.prepare_due(x.ui, "Dutch", current)
    if case in ("not_due", "already_open"):
        assert result is None
    else:
        assert result["state"] == "waiting"


@pytest.mark.parametrize("phase", ["prepared", "uncertain", "aborted"])
def test_automatic_prepare_never_replaces_held_request(qualified, monkeypatch, phase):
    x = qualified
    key = "booth-open1078-held-request"
    x.j.begin(key, "Dutch", panel.KIND, {"request": {}})
    if phase != "prepared":
        x.j.transition(key, phase, {})
    x.j.set("Dutch", panel.PENDING, key)
    monkeypatch.setattr(
        panel, "prepare", lambda *a: pytest.fail("Existing exact request stays held")
    )
    assert panel.prepare_due(x.ui, "Dutch", x.snap)["request_id"] == key


def test_global_stop_blocks_real_prepare_before_journal_mutation(qualified):
    x = qualified
    x.ui.closed = False
    x.ui.app = NS(closing=False)
    x.ui.runtime.stop_event = NS(is_set=lambda: False)
    x.ui.coordinator = NS(stopped=True)
    before = x.j.trace(x.key)
    with pytest.raises(panel.CaptureUnavailable, match="stopped"):
        panel.prepare_due(x.ui, "Dutch", x.snap)
    assert x.j.get("Dutch", panel.PENDING) == x.key and x.j.trace(x.key) == before


def test_grant_activated_during_preflight_prevents_new_panel_request(
    state, monkeypatch
):
    x = state
    x.ui.grant = None
    x.ui.runtime.lock = threading.RLock()
    x.ui.runtime.observers = {
        "Dutch": NS(adapter=NS(identity=x.snap["identity"]), hwnd=12)
    }
    monkeypatch.setattr(panel, "_idle", lambda *a, **k: None)
    monkeypatch.setattr(
        "conquest.merchants.listing_capability_1078.require", lambda *a: None
    )
    calls = []

    def observe(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            x.ui.grant = {"request_id": "another-existing-grant"}
        return {**x.snap, "timestamp": time.time(), "booth_target_preflight": {}}

    monkeypatch.setattr("conquest.merchants.observe_1078.observe", observe)
    body = {**x.body, "request_id": "booth-open1078-concurrent-grant"}
    with pytest.raises(ValueError, match="granted during"):
        panel.prepare(x.ui, body)
    assert panel._row(x.j, body["request_id"]) is None


def test_panel_scope_never_admits_listing_or_cancel(state):
    x = state
    x.ui.grant = {
        "scope": "listing_1078",
        "character": "Dutch",
        "listing_authority": {
            "mode": "open_panel",
            "panel_request_id": x.key,
            "profile_id": x.profile.id,
        },
    }
    assert panel.panel_scope_allows(x.ui, "Dutch", x.key)
    assert not panel.panel_scope_allows(x.ui, "Dutch", "another-request")
    for kwargs in ({}, {"scheduled": True}, {"cleanup": True}):
        assert handoff.scope_allows(x.ui, "Dutch", **kwargs) is False


def test_native_selected_actor_must_be_exact_and_gui_unclaimed():
    base = 0x10000000
    actor = 0x30000000
    manager = 0x40000000
    context = 0x50000000
    memory = {
        base + 0x6C4F08: struct.pack("<Q", manager),
        manager: struct.pack("<Q", base + 0x5EAB40),
        manager + 0xD8: struct.pack("<Q", actor + 0x10),
        actor + 0x78: struct.pack("<I", 77),
        actor + 0x10: struct.pack("<Q", base + 0x5EB2D0),
        base + 0x6B5EF0: struct.pack("<Q", context),
        context + 0xD0: b"\0",
        context + 0x3F04: b"\0" * 4,
    }
    session = NS(read=lambda a, n: memory[a], assert_identity=lambda: None)
    gui = NS(context_rva=0x6B5EF0)
    candidate = {"actor_address": hex(actor), "owned_booth_uid": 77}
    assert hover.assert_selected(session, gui, base, candidate)[
        "selected_actor"
    ] == hex(actor + 0x10)
    memory[manager + 0xD8] = struct.pack("<Q", actor + 0x20)
    with pytest.raises(hover.HoverNotReady):
        hover.assert_selected(session, gui, base, candidate)
    memory[manager + 0xD8] = struct.pack("<Q", actor + 0x10)
    memory[context + 0xD0] = b"\1"
    with pytest.raises(hover.HoverNotReady):
        hover.assert_selected(session, gui, base, candidate)


@contextmanager
def nothing(*args, **kwargs):
    yield


def test_durable_marker_precedes_click_and_uncertain_never_replays(state, monkeypatch):
    x = state
    grant = {
        "scope": "listing_1078",
        "character": "Dutch",
        "request_id": "merchant-refill:Dutch:1",
        "expires_at": time.time() + 45,
        "listing_authority": {
            "mode": "open_panel",
            "panel_request_id": x.key,
            "profile_id": x.profile.id,
            "farmer_target": {"pid": 10},
        },
    }
    x.ui.grant = grant
    x.ui.app = NS(control=NS(snapshot=lambda: {"enabled": False, "revision": 1}))
    x.ui.safe_to_yield = lambda: True
    x.ui.coordinator = NS(
        fence=NS(bind_worker=nothing), owned_panel_scope=nothing, lease=nothing
    )
    monkeypatch.setattr(panel, "_idle", lambda *a, **k: None)
    monkeypatch.setattr(handoff, "farmer_safe", lambda *a, **k: None)

    class Session:
        identity = x.snap["identity"]

        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def assert_identity(self):
            pass

    monkeypatch.setattr("conquest.memory.MemorySession", Session)
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nothing)
    gui = NS(windows=lambda: [], viewport_size=lambda: (1200, 900), session=None)
    monkeypatch.setattr(
        "conquest.merchants.memory.GuiReader.for_session", lambda s: gui
    )
    monkeypatch.setattr(
        "conquest.merchants.reader_1078.open_read_only_1078",
        lambda *a: NS(
            read_manual_ownership=lambda: {**x.snap, "timestamp": time.time()}
        ),
    )
    candidate = {
        "actor_address": "0x123456",
        "owned_booth_uid": 77,
        "projection_candidate": {"candidate_point": [500, 400]},
    }
    monkeypatch.setattr(
        "conquest.merchants.booth_target_1078.collect", lambda *a: deepcopy(candidate)
    )
    monkeypatch.setattr(hover, "qualify", lambda s: 0x10000000)
    monkeypatch.setattr(hover, "assert_selected", lambda *a: {"owned_booth_uid": 77})
    monkeypatch.setattr("conquest.scene_pointer.wait_scene_pointer", lambda *a: None)
    monkeypatch.setattr("conquest.focus_recovery.activate_client", lambda *a: True)
    target = NS(pid=2, hwnd=12, snapshot=lambda: {"root_hwnd": 12, "foreground": 12})
    monkeypatch.setattr("conquest.input_probe.MessageTarget", lambda *a: target)
    revision = NS(client_size=(1200, 900), client_origin=(0, 0))
    monkeypatch.setattr(
        "conquest.layout_revision.SharedLayoutRevision",
        lambda *a, **k: NS(stable=lambda: revision, assert_current=lambda r: None),
    )
    calls = []

    def click(*args, **kwargs):
        kwargs["before_press"]()
        kwargs["before_mouse_down"]()
        with x.j.db() as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM transaction_steps WHERE stage='panel_click'"
                ).fetchone()[0]
                == 1
            )
        calls.append("mouse-down-boundary")
        raise OSError("unknown native outcome")

    monkeypatch.setattr("conquest.foreground.foreground_click", click)
    panel._run(x.ui, "Dutch", x.before, grant, object())
    assert calls == ["mouse-down-boundary"]
    assert panel.status(x.j, "Dutch", x.key)["phase"] == "uncertain"
    monkeypatch.setattr(
        "conquest.merchants.observe_1078.observe", lambda *a, **k: opened(x)
    )
    panel.step(x.ui, "Dutch", opened(x))
    panel.step(x.ui, "Dutch", opened(x))
    assert (
        calls == ["mouse-down-boundary"]
        and panel.status(x.j, "Dutch", x.key)["phase"] == "verified"
    )
