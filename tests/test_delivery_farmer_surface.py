from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock
import threading
import time

import pytest

from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.coordination import InputCoordinator, install
from conquest.merchants import delivery_farmer_surface as surface


@pytest.fixture
def farmer_surface(tmp_path, monkeypatch):
    identity = {"pid": 7, "creation_time_100ns": 12, "path": "C:/Game/ImConquer.exe"}
    state = {
        "phase": "trade_open_verified",
        "farmer_profile_id": "Farmer",
        "target_profile_id": "Dutch",
        "character": "Dutch",
        "intent": {
            "farmer": {
                "character": "Parasite",
                "server": "America",
                "identity": identity,
            },
            "merchant": {"character": "Dutch", "server": "America"},
        },
    }
    x = NS(
        state=state,
        control={"enabled": False, "paused": False, "revision": 3},
        calls=[],
        selected="merchant",
        visible=False,
    )
    x.coordinator = InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    install(x.coordinator)
    target = NS(hwnd=77)
    observer = NS(
        character="Parasite",
        operations=NS(target=target),
        # Pre-1078 client: source_memory keeps the MerchantMemory path.
        adapter=NS(
            identity=deepcopy(identity),
            assert_identity=Mock(),
            expected_sha256=CLIENT_SHA256,
        ),
    )
    pane = NS(
        winfo_width=lambda: 1200,
        winfo_height=lambda: 900,
        winfo_ismapped=lambda: x.selected == "farmer",
    )
    native = NS(
        GetClientRect=lambda hwnd: (0, 0, 1200, 900),
        ClientToScreen=lambda *args: (10, 20),
        IsWindowVisible=lambda hwnd: (
            x.visible if hwnd == 77 else x.selected == "farmer" if hwnd == 55 else False
        ),
        IsIconic=lambda hwnd: False,
        GetAncestor=lambda *a: 55,
        GetWindowRect=lambda hwnd: (10, 20, 1210, 920),
    )
    host = NS(
        saved=NS(hwnd=77, identity=deepcopy(identity)),
        mode="owned",
        parent=55,
        api=NS(assert_owner=Mock(), gui=native, is_above=lambda *a: True),
    )

    def resize(*size):
        x.calls.append(("resize", size))
        x.visible = x.selected == "farmer"

    host.resize = Mock(side_effect=resize)
    sibling = NS(
        saved=NS(hwnd=88, identity={"pid": 8}),
        api=NS(
            assert_owner=Mock(),
            show_async=Mock(side_effect=lambda *a: x.calls.append(("hide", 88))),
            gui=native,
        ),
    )

    def select(frame):
        x.calls.append(("tab", frame))
        x.selected = frame

    app = NS(
        observer=observer,
        host=host,
        client=(7, 77, deepcopy(identity)),
        pane=pane,
        closing=False,
        control=NS(snapshot=lambda: dict(x.control)),
        state_text=NS(set=lambda value: x.calls.append(("error", value))),
    )
    ui = NS(
        app=app,
        coordinator=x.coordinator,
        closed=False,
        safe_to_yield=lambda: True,
        delivery_probe_thread=threading.current_thread(),
        hosts={"Dutch": sibling},
        runtime=NS(manual_target=lambda owner: owner),
        frames={"Farmer": "farmer"},
        notebook=NS(select=select),
        root=NS(update_idletasks=lambda: x.calls.append("idle")),
        apply_client_compact_layout=lambda: x.calls.append("layout"),
    )
    app.unified = ui
    x.ui = ui
    x.host = host
    x.sibling = sibling
    x.threads = []
    x.hook = lambda: None

    class Queue:
        def put(self, task):
            callback, done, result = task

            def work():
                try:
                    x.hook()
                    callback()
                except Exception as error:
                    result["error"] = str(error)
                finally:
                    done.set()

            thread = threading.Thread(target=work)
            x.threads.append(thread)
            thread.start()

    ui.ui_requests = Queue()
    monkeypatch.setattr(surface, "read_probe", lambda: deepcopy(x.state))
    monkeypatch.setattr("conquest.character_context.registry", lambda: None)
    monkeypatch.setattr("conquest.character_context.current", lambda: None)
    monkeypatch.setattr("ctypes.windll.user32.GetAsyncKeyState", lambda key: 0)
    monkeypatch.setattr(
        "conquest.merchants.farmer_preferences.permits_new_delivery", lambda name: None
    )

    def prepare(purpose="delivery_offer_probe"):
        with x.coordinator.lease("Farmer", purpose=purpose):
            return surface.prepare(
                ui,
                deepcopy(x.state),
                purpose=purpose,
                revision=3,
                deadline=time.monotonic() + 15,
            )

    x.prepare = prepare
    yield x
    for thread in x.threads:
        thread.join(3)
    install(None)


@pytest.mark.parametrize("purpose,phase", list(surface.PHASES.items()))
def test_receipt_bound_cross_thread_presentation_shows_hidden_farmer_without_input(
    farmer_surface, purpose, phase
):
    x = farmer_surface
    x.state["phase"] = phase
    x.prepare(purpose)
    assert x.visible and x.selected == "farmer"
    assert x.calls == [
        ("tab", "farmer"),
        "layout",
        "idle",
        ("hide", 88),
        ("resize", (1200, 900)),
    ]
    assert x.control == {"enabled": False, "paused": False, "revision": 3}
    assert x.threads[0].ident != threading.get_ident()


def test_ordinary_show_game_is_denied_on_ui_thread_under_worker_lease(farmer_surface):
    from conquest.desktop_app import DesktopApp

    x = farmer_surface
    done = threading.Event()
    result = {}
    with x.coordinator.lease("Farmer", purpose="delivery_offer_probe"):
        x.ui.ui_requests.put(
            (lambda: result.update(shown=DesktopApp.show_game(x.ui.app)), done, result)
        )
        assert done.wait(3)
    assert result == {"shown": False}
    assert x.calls == [("error", "Another character owns game input")]
    assert not x.visible and x.selected == "merchant"


@pytest.mark.parametrize(
    "fault",
    [
        "purpose",
        "owner",
        "thread",
        "worker",
        "profile",
        "merchant_profile",
        "identity",
        "observer",
        "target",
        "hwnd",
        "host",
        "host_identity",
        "journal",
        "phase",
        "revision",
        "pause",
        "enabled",
        "stop",
        "mouse",
        "F11",
        "F12",
        "viewport",
        "sibling",
        "farmer_hold",
        "merchant_hold",
        "closed",
    ],
)
def test_changed_presentation_authority_never_mutates_native_surface(
    farmer_surface, monkeypatch, fault
):
    x = farmer_surface

    def change():
        if fault == "purpose":
            x.coordinator.purpose = "other"
        if fault == "owner":
            x.coordinator.owner = "Dutch"
        if fault == "thread":
            x.coordinator.thread = -1
        if fault == "worker":
            x.ui.delivery_probe_thread = NS(ident=-1, is_alive=lambda: True)
        if fault in ("profile", "merchant_profile"):
            owner = "Farmer" if fault == "profile" else "Dutch"
            x.ui.runtime.manual_target = lambda value: (
                "different" if value == owner else value
            )
        if fault == "identity":
            x.ui.app.observer.adapter.identity["creation_time_100ns"] += 1
        if fault == "observer":
            x.ui.app.observer = deepcopy(x.ui.app.observer)
        if fault == "target":
            x.ui.app.observer.operations.target = NS(hwnd=77)
        if fault == "hwnd":
            x.ui.app.observer.operations.target.hwnd += 1
        if fault == "host":
            x.ui.app.host = NS(saved=x.host.saved, mode="owned")
        if fault == "host_identity":
            x.host.saved.identity["creation_time_100ns"] += 1
        if fault == "journal":
            x.state["changed"] = True
        if fault == "phase":
            x.state["phase"] = "placement_submitted"
        if fault == "revision":
            x.control["revision"] += 1
        if fault == "pause":
            x.control["paused"] = True
        if fault == "enabled":
            x.control["enabled"] = True
        if fault == "stop":
            x.coordinator.stop()
        if fault == "mouse":
            x.coordinator.manual_active = lambda: True
        if fault in ("F11", "F12"):
            monkeypatch.setattr(
                "ctypes.windll.user32.GetAsyncKeyState",
                lambda key: 0x8000 if key == (0x7A if fault == "F11" else 0x7B) else 0,
            )
        if fault == "viewport":
            x.ui.app.pane.winfo_height = lambda: 300
        if fault == "sibling":
            x.ui.hosts["Other"] = NS(
                saved=NS(hwnd=99, identity={"pid": 9}),
                api=NS(
                    assert_owner=Mock(side_effect=ValueError("Other sibling changed"))
                ),
            )
        if fault in ("farmer_hold", "merchant_hold"):
            target = "Farmer" if fault == "farmer_hold" else "Dutch"
            x.coordinator.set_manual_sessions(
                [{"target_profile_id": target, "holds_automation": True}]
            )
        if fault == "closed":
            x.ui.closed = True

    x.hook = change
    with pytest.raises(ValueError):
        x.prepare()
    x.host.resize.assert_not_called()
    x.sibling.api.show_async.assert_not_called()
    assert x.calls == [] and not x.visible


@pytest.mark.parametrize("boundary", ["tab", "layout", "idle", "hide", "resize"])
def test_receipt_is_rechecked_after_each_presentation_mutation(
    farmer_surface, boundary
):
    x = farmer_surface
    if boundary == "tab":
        prior = x.ui.notebook.select

        def changed(*args):
            prior(*args)
            x.state["changed"] = True

        x.ui.notebook.select = changed
    elif boundary in ("layout", "idle"):

        def changed():
            x.calls.append(boundary)
            x.state["changed"] = True

        if boundary == "layout":
            x.ui.apply_client_compact_layout = changed
        else:
            x.ui.root.update_idletasks = changed
    else:
        mutation = x.sibling.api.show_async if boundary == "hide" else x.host.resize
        prior = mutation.side_effect

        def changed(*args):
            prior(*args)
            x.state["changed"] = True

        mutation.side_effect = changed
    with pytest.raises(ValueError, match="receipt"):
        x.prepare()
    assert (
        x.calls[-1]
        == {
            "tab": ("tab", "farmer"),
            "layout": "layout",
            "idle": "idle",
            "hide": ("hide", 88),
            "resize": ("resize", (1200, 900)),
        }[boundary]
    )


def test_false_queue_completion_is_not_presentation_authority(farmer_surface):
    x = farmer_surface
    x.ui.ui_requests = NS(put=lambda task: task[1].set())
    with pytest.raises(ValueError, match="not verified"):
        x.prepare()
    assert x.calls == []


def test_timed_out_callback_cannot_present_later(farmer_surface):
    x = farmer_surface
    queued = []
    x.ui.ui_requests = NS(put=lambda task: queued.append(task))
    with x.coordinator.lease("Farmer", purpose="delivery_offer_probe"):
        with pytest.raises(ValueError, match="timed out"):
            surface.prepare(
                x.ui,
                deepcopy(x.state),
                purpose="delivery_offer_probe",
                revision=3,
                deadline=time.monotonic() + 0.02,
            )
        with pytest.raises(ValueError, match="authority"):
            queued[0][0]()
    assert x.calls == []


@pytest.mark.parametrize(
    "mode", ["ready", "false_completion", "stopped", "receipt_changed"]
)
def test_offer_stage_presents_hidden_farmer_before_activation_and_durable_drag(
    farmer_surface, monkeypatch, mode
):
    from contextlib import nullcontext
    from conquest.merchants import delivery_offer_probe as offer

    x = farmer_surface
    item = dict(
        uid=99, type_id=410009, plus=1, gem1=0, gem2=0, quantity=1, bound=False, slot=0
    )
    farmer = {
        **deepcopy(x.state["intent"]["farmer"]),
        "inventory": [item],
        "trade": {"accepted": False, "other_accepted": False},
    }
    merchant = {
        "character": "Dutch",
        "trade": {"accepted": False, "other_accepted": False},
    }
    x.state["intent"].update(items=[item], farmer=farmer, merchant=merchant)
    x.ui.runtime.reconcile_probe_pair = lambda *a: True
    target = x.ui.app.observer.operations.target
    target.snapshot = lambda: {"client_size": [1200, 900]}
    # The GUI reader now carries its build's context RVA (1074 value here).
    memory = NS(gui=NS(base=1, context_rva=0x6966F0, viewport_size=lambda: [1200, 900]))
    monkeypatch.setattr(
        "conquest.merchants.memory.MerchantMemory", lambda observer: memory
    )
    # delivery_bridge.source_memory binds MerchantMemory at import.
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.MerchantMemory", lambda observer: memory
    )
    monkeypatch.setattr("conquest.merchants.memory.unpack", lambda *a: [10])
    monkeypatch.setattr(
        "conquest.merchants.farmer_preferences.permits_new_delivery", lambda name: None
    )
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr(
        "conquest.merchants.driver.wait_hover_validation",
        lambda callback, check: callback(),
    )
    offered = []
    reads = []

    def pair(*args):
        reads.append(True)
        if mode == "receipt_changed" and len(reads) == 2:
            x.state["changed"] = True
        return deepcopy(farmer), deepcopy(merchant)

    monkeypatch.setattr(offer, "pair", pair)
    monkeypatch.setattr(offer, "partial_offer", lambda *a: deepcopy(offered))
    monkeypatch.setattr(offer, "validate_offers", lambda *a: None)
    monkeypatch.setattr(
        offer,
        "endpoints",
        lambda *a: ({"address": 10}, {"address": 20}, (50, 50), (200, 200)),
    )
    phases = []
    monkeypatch.setattr(
        offer, "write_json", lambda path, state: phases.append(state["phase"])
    )

    def activate(*args):
        assert x.visible and x.selected == "farmer"
        x.calls.append("activate")
        return True

    monkeypatch.setattr("conquest.focus_recovery.activate_client", activate)

    def drag(*args, **kwargs):
        assert phases == ["placement_submitted"] and x.visible
        # The same owned-host refresh that formerly hid the unselected farmer
        # now keeps it visible throughout the worker's drag.
        x.host.resize(1200, 900)
        assert x.visible
        kwargs["before_press"]()
        x.calls.append("drag")
        offered.append(deepcopy(item))
        farmer["inventory"] = []

    monkeypatch.setattr("conquest.foreground.foreground_drag", drag)
    if mode == "false_completion":
        x.ui.ui_requests = NS(put=lambda task: task[1].set())
    if mode == "stopped":
        x.hook = x.coordinator.stop
    if mode == "ready":
        offer.run(x.ui, x.state)
        assert phases == [
            "placement_submitted",
            "trade_open_verified",
            "offer_verified",
        ]
        assert (
            x.calls.index(("tab", "farmer"))
            < x.calls.index("activate")
            < x.calls.index("drag")
        )
    else:
        with pytest.raises(ValueError):
            offer.run(x.ui, x.state)
        assert phases == [] and "activate" not in x.calls and "drag" not in x.calls


def test_confirm_stage_requires_successful_farmer_presentation_before_any_confirmation(
    farmer_surface, monkeypatch
):
    from contextlib import nullcontext
    from conquest.merchants import delivery_confirm_probe as confirm

    x = farmer_surface
    x.state["phase"] = "offer_verified"
    x.state["intent"]["merchant"] = {"character": "Dutch", "server": "America"}
    x.ui.runtime.reconcile_probe_pair = lambda *a: True
    monkeypatch.setattr(confirm, "read_probe", lambda: deepcopy(x.state))
    monkeypatch.setattr(
        "conquest.merchants.farmer_preferences.permits_new_delivery", lambda name: None
    )
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr(confirm, "pair", lambda *a: ({}, {}))
    x.ui.ui_requests = NS(put=lambda task: task[1].set())
    monkeypatch.setattr(
        confirm, "write_json", lambda *a: pytest.fail("No submitted confirmation")
    )
    monkeypatch.setattr(
        "conquest.foreground.foreground_click", lambda *a, **k: pytest.fail("No click")
    )
    with pytest.raises(ValueError, match="not verified"):
        confirm.run(x.ui, x.state)
    assert x.calls == []


@pytest.fixture
def ordinary_surface(farmer_surface, tmp_path):
    from conquest.merchants.journal import Journal
    from conquest.merchants.grant_fence import GrantFence
    from conquest.merchants.delivery_reservation import reserve
    from conquest.merchants.farmer_trade import FarmerTradeDriver
    from test_manual_runtime import snapshot, item

    x = farmer_surface
    now = time.time()
    key = "delivery:surface-test"
    farmer = snapshot(
        at=now,
        character="Parasite",
        character_uid=55,
        identity=deepcopy(x.state["intent"]["farmer"]["identity"]),
        request=None,
        booth=[],
        own_booth_uid=0,
        booth_open=False,
        inventory=[item(99, slot=0)],
    )
    merchant = snapshot(
        at=now,
        request=None,
        identity={"pid": 8, "creation_time_100ns": 13, "path": "C:/Game/ImConquer.exe"},
    )
    origin = dict(
        operation_id=key,
        farmer_profile_id="Farmer",
        visit_id="visit-1",
        town_visit_id="town-1",
    )
    x.intent = dict(
        farmer=farmer, merchant=merchant, items=deepcopy(farmer["inventory"]), **origin
    )
    x.source_journal = Journal(tmp_path / "source.sqlite3")
    x.receiver_journal = Journal(tmp_path / "receiver.sqlite3")
    x.source_journal.begin(key, "Dutch", "farmer_delivery", x.intent)
    reserve(
        x.receiver_journal,
        key,
        deepcopy(farmer),
        deepcopy(merchant),
        deepcopy(farmer["inventory"]),
        origin=origin,
        now=now,
    )
    x.ui.runtime.journal = x.receiver_journal
    x.ui.runtime.enabled = lambda c: True
    x.ui.delivery_workers = {key: threading.current_thread()}
    fence = GrantFence()
    x.coordinator.fence = fence
    token = fence.activate(
        key, 3, now + 60, scope="market_visit", farmer_profile_id="Farmer"
    )
    x.ui.grant = dict(
        request_id=key,
        revision=3,
        expires_at=token.expires_at,
        scope="market_visit",
        farmer_profile_id="Farmer",
        visit_id="visit-1",
        town_visit_id="town-1",
    )
    driver = FarmerTradeDriver.__new__(FarmerTradeDriver)
    driver.ui = x.ui
    driver.revision = 3
    driver.operation = NS(key=key, journal=x.source_journal)
    driver.driver = NS(
        observer=x.ui.app.observer, target=x.ui.app.observer.operations.target
    )
    driver.check = x.coordinator.check
    x.driver = driver
    x.key = key
    x.token = token

    def prepare(stage="open"):
        with (
            fence.bind_worker(token),
            x.coordinator.lease("Farmer", purpose="farmer_delivery"),
        ):
            return surface.prepare_delivery(
                driver, x.intent, stage=stage, deadline=time.monotonic() + 15
            )

    x.prepare_delivery = prepare
    return x


@pytest.mark.parametrize("stage", ["open", "place", "confirm"])
def test_ordinary_transaction_factory_presents_only_its_exact_stage(
    ordinary_surface, stage
):
    x = ordinary_surface
    if stage == "confirm":
        x.source_journal.transition(x.key, "submitted")
    x.prepare_delivery(stage)
    assert x.selected == "farmer" and x.visible
    assert x.calls == [
        ("tab", "farmer"),
        "layout",
        "idle",
        ("hide", 88),
        ("resize", (1200, 900)),
    ]


@pytest.mark.parametrize(
    "fault",
    [
        "source_phase",
        "source_kind",
        "source_character",
        "source_intent",
        "source_origin",
        "operation_key",
        "operation_object",
        "worker",
        "reservation_phase",
        "reservation_key",
        "reservation_intent",
        "reservation_expired",
        "revoke",
        "grant_scope",
        "grant_key",
        "grant_revision",
        "grant_profile",
        "grant_expired",
        "grant_visit",
        "driver_observer",
        "driver_target",
        "merchant_paused",
        "read_only",
    ],
)
def test_ordinary_receipt_and_grant_changes_cannot_present(ordinary_surface, fault):
    x = ordinary_surface

    def change():
        if fault.startswith("source_"):
            with x.source_journal.db() as db:
                if fault == "source_phase":
                    db.execute(
                        "UPDATE transactions SET phase='submitted' WHERE id=?", (x.key,)
                    )
                elif fault == "source_kind":
                    db.execute(
                        "UPDATE transactions SET kind='other' WHERE id=?", (x.key,)
                    )
                elif fault == "source_character":
                    db.execute(
                        "UPDATE transactions SET character='Other' WHERE id=?", (x.key,)
                    )
                else:
                    import json

                    value = deepcopy(x.intent)
                    if fault == "source_intent":
                        value["items"][0]["quantity"] += 1
                    else:
                        value["visit_id"] = "other"
                    db.execute(
                        "UPDATE transactions SET before_json=? WHERE id=?",
                        (json.dumps(value), x.key),
                    )
        if fault == "operation_key":
            x.driver.operation.key = "other"
        if fault == "operation_object":
            x.driver.operation = NS(key=x.key, journal=x.source_journal)
        if fault == "worker":
            x.ui.delivery_workers[x.key] = NS(ident=-1, is_alive=lambda: True)
        if fault.startswith("reservation_"):
            value = x.receiver_journal.get("Dutch", "delivery_reservation")
            if fault == "reservation_phase":
                value["phase"] = "offer_ready"
            if fault == "reservation_key":
                value["request_id"] = "other"
            if fault == "reservation_intent":
                value["intent"]["items"][0]["quantity"] += 1
            if fault == "reservation_expired":
                value["expires_at"] = time.time() - 1
            x.receiver_journal.set("Dutch", "delivery_reservation", value)
        if fault == "revoke":
            x.coordinator.fence.revoke(x.token)
        if fault.startswith("grant_"):
            key, value = {
                "grant_scope": ("scope", "hunting"),
                "grant_key": ("request_id", "other"),
                "grant_revision": ("revision", 99),
                "grant_profile": ("farmer_profile_id", "Other"),
                "grant_expired": ("expires_at", time.time() - 1),
                "grant_visit": ("visit_id", "other"),
            }[fault]
            x.ui.grant[key] = value
        if fault == "driver_observer":
            x.driver.driver.observer = NS()
        if fault == "driver_target":
            x.driver.driver.target = NS(hwnd=77)
        if fault == "merchant_paused":
            x.ui.runtime.enabled = lambda c: False
        if fault == "read_only":
            with x.coordinator.fence.lock:
                for binding in x.coordinator.fence.workers.values():
                    binding["action_capable"] = False

    x.hook = change
    with pytest.raises(ValueError):
        x.prepare_delivery()
    assert x.calls == [] and not x.visible


@pytest.mark.parametrize(
    "fault",
    ["phase", "intent", "origin", "reservation", "unbound", "scope", "source_profile"],
)
def test_ordinary_factory_rejects_bad_initial_authority_before_queue(
    ordinary_surface, fault
):
    x = ordinary_surface
    if fault == "phase":
        x.source_journal.transition(x.key, "submitted")
    if fault == "intent":
        x.intent["items"][0]["quantity"] += 1
    if fault == "origin":
        x.intent["operation_id"] = "other"
    if fault == "source_profile":
        x.ui.runtime.manual_target = lambda name: (
            "replacement-uuid" if name == "Dutch" else name
        )
    if fault == "reservation":
        value = x.receiver_journal.get("Dutch", "delivery_reservation")
        value["intent"]["farmer"]["silver"] += 1
        x.receiver_journal.set("Dutch", "delivery_reservation", value)
    if fault == "scope":
        from conquest.merchants.grant_fence import GrantFence

        fence = x.coordinator.fence = GrantFence()
        token = fence.activate(
            x.key, 3, time.time() + 60, scope="hunting", farmer_profile_id="Farmer"
        )
        with (
            fence.bind_worker(token),
            x.coordinator.lease("Farmer", purpose="farmer_delivery"),
        ):
            with pytest.raises(ValueError):
                surface.prepare_delivery(
                    x.driver, x.intent, stage="open", deadline=time.monotonic() + 3
                )
    elif fault == "unbound":
        with x.coordinator.lease("Farmer", purpose="farmer_delivery"):
            with pytest.raises(ValueError):
                surface.prepare_delivery(
                    x.driver, x.intent, stage="open", deadline=time.monotonic() + 3
                )
    else:
        with pytest.raises(ValueError):
            x.prepare_delivery()
    assert x.calls == [] and x.threads == []


@pytest.mark.parametrize("stage", ["open", "place", "confirm"])
@pytest.mark.parametrize("mode", ["ready", "changed_pair", "revoked_after_pair"])
def test_ordinary_action_reobserves_exact_pair_before_worker_activation(
    ordinary_surface, monkeypatch, stage, mode
):
    from contextlib import nullcontext

    x = ordinary_surface
    driver = x.driver
    if stage == "confirm":
        x.source_journal.transition(x.key, "submitted")
    driver.require_qualified = lambda: {}
    farmer = deepcopy(x.intent["farmer"])
    merchant = deepcopy(x.intent["merchant"])
    if stage != "open":
        items = deepcopy(x.intent["items"]) if stage == "confirm" else []
        for current, other, own, received in (
            (farmer, merchant, items, []),
            (merchant, farmer, [], items),
        ):
            current["trade"] = dict(
                participant=other["character"],
                participant_uid=other["character_uid"],
                own_items=own,
                items=received,
                own_silver=0,
                other_silver=0,
                accepted=False,
                other_accepted=False,
            )

    def pair(character):
        assert x.visible and x.selected == "farmer"
        f, m = deepcopy(farmer), deepcopy(merchant)
        f["timestamp"] = m["timestamp"] = time.time()
        if mode == "changed_pair":
            f["silver"] += 1
        if mode == "revoked_after_pair":
            x.coordinator.fence.revoke(x.token)
        return f, m

    driver.read_pair = pair
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)

    def activate(*args):
        x.calls.append("activate")
        return True

    monkeypatch.setattr("conquest.focus_recovery.activate_client", activate)
    with x.coordinator.fence.bind_worker(x.token):
        if mode == "ready":
            with driver.action(x.intent, stage=stage):
                x.calls.append("body")
            assert x.calls[-2:] == ["activate", "body"]
        else:
            with pytest.raises(ValueError):
                with driver.action(x.intent, stage=stage):
                    pytest.fail("No action body")
            assert "activate" not in x.calls


@pytest.mark.parametrize(
    "fault", [None, "selected", "role", "name", "server", "uid", "disabled"]
)
def test_managed_farmer_profile_is_bound_before_presentation(
    farmer_surface, monkeypatch, fault
):
    x = farmer_surface
    profile_id = "farmer-uuid"
    x.state["farmer_profile_id"] = profile_id
    x.state["intent"]["farmer"]["character_uid"] = 55
    x.ui.runtime.manual_target = lambda name: profile_id if name == "Farmer" else name
    profile = NS(
        id=profile_id,
        role="Farmer",
        name="Parasite",
        server="America",
        character_uid=55,
        local_enabled=True,
    )
    selected = NS(profile=deepcopy(profile))
    if fault == "selected":
        selected.profile.id = "other"
    if fault == "role":
        profile.role = "Merchant"
    if fault == "name":
        profile.name = "Other"
    if fault == "server":
        profile.server = "Other"
    if fault == "uid":
        profile.character_uid += 1
    if fault == "disabled":
        profile.local_enabled = False
    monkeypatch.setattr(
        "conquest.character_context.registry",
        lambda: NS(resolve=lambda *a, **k: profile),
    )
    monkeypatch.setattr("conquest.character_context.current", lambda: selected)
    if fault:
        with pytest.raises(ValueError, match="profile"):
            x.prepare()
        assert x.calls == []
    else:
        x.prepare()
        assert x.visible


@pytest.mark.parametrize("ready", [False, True])
def test_initial_request_stage_uses_worker_activation_only_after_surface_and_pair(
    farmer_surface, monkeypatch, ready
):
    from contextlib import nullcontext
    from conquest.merchants import delivery_probe as probe

    x = farmer_surface
    x.state["phase"] = "prepared"
    x.state["intent"]["items"] = []
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr("conquest.merchants.memory.MerchantMemory", lambda *a: NS())
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.MerchantMemory", lambda *a: NS()
    )
    monkeypatch.setattr(
        probe,
        "pair",
        lambda *a: (x.state["intent"]["farmer"], x.state["intent"]["merchant"]),
    )
    monkeypatch.setattr(
        surface, "verify_stage_pair", lambda *a, **k: x.calls.append("pair_verified")
    )
    monkeypatch.setattr(
        probe,
        "write_probe",
        lambda *a: pytest.fail("No gameplay boundary before activation"),
    )

    class Activated(Exception):
        pass

    def activate(*args):
        assert x.visible and x.calls[-1] == "pair_verified"
        x.calls.append("activate")
        raise Activated()

    monkeypatch.setattr("conquest.focus_recovery.activate_client", activate)
    if not ready:
        x.ui.ui_requests = NS(put=lambda task: task[1].set())
    with pytest.raises(Activated if ready else ValueError):
        probe.run(x.ui, x.state["intent"], 3, x.state)
    assert ("activate" in x.calls) == ready
    if not ready:
        assert x.calls == []


def test_confirm_stage_prepares_visible_farmer_before_first_confirmation(
    farmer_surface, monkeypatch
):
    from contextlib import nullcontext
    from conquest.merchants import delivery_confirm_probe as confirm

    x = farmer_surface
    x.state["phase"] = "offer_verified"
    x.ui.runtime.reconcile_probe_pair = lambda *a: True
    monkeypatch.setattr(confirm, "read_probe", lambda: deepcopy(x.state))
    monkeypatch.setattr(
        "conquest.merchants.delivery_probe_ownership.ownership", lambda *a, **k: None
    )
    farmer = {
        **deepcopy(x.state["intent"]["farmer"]),
        "trade": {"accepted": False, "other_accepted": False},
    }
    merchant = {
        "character": "Dutch",
        "trade": {"accepted": False, "other_accepted": False},
    }
    memory = NS(gui=NS(viewport_size=lambda: [1200, 900]))
    x.ui.app.observer.operations.target.snapshot = lambda: {"client_size": [1200, 900]}
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr("conquest.merchants.memory.MerchantMemory", lambda *a: memory)
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.MerchantMemory", lambda *a: memory
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_confirm_controls.confirm_control",
        lambda *a: ({}, (50, 50), 10),
    )
    monkeypatch.setattr(confirm, "pair", lambda *a: (farmer, merchant))
    monkeypatch.setattr(confirm, "validate_offers", lambda *a: None)
    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client",
        lambda *a: x.calls.append("activate") or True,
    )
    phases = []
    monkeypatch.setattr(confirm, "write_json", lambda p, s: phases.append(s["phase"]))

    class FirstConfirmation(Exception):
        pass

    def click(*a, **k):
        assert x.visible and x.selected == "farmer"
        raise FirstConfirmation()

    monkeypatch.setattr("conquest.foreground.foreground_click", click)
    with pytest.raises(FirstConfirmation):
        confirm.run(x.ui, x.state)
    assert phases == ["farmer_confirm_submitted"]
