"""Exact asymmetric confirmation receipts resume only the unsubmitted peer."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace as NS
import threading

import pytest

from conquest.merchants import (
    delivery_confirm_probe as confirm,
    delivery_probe as probe,
)
from conquest.merchants.delivery_probe_ownership import ownership
from test_delivery_probe_manual_ownership import supervised, open_trade
from test_manual_runtime import rig


@pytest.mark.parametrize(
    "blocked", [None, "delivery_window", "refill_window", "refilling", "paused"]
)
def test_receiver_lease_optional_windows_and_real_pause_guards(midpoint, blocked):
    x = midpoint
    x.guard.purpose = "delivery_confirm_probe"
    x.ui.calibrating.add("Dutch")
    x.ui.calibration_cancel["Dutch"] = threading.Event()
    # Fresh runtimes do not initialize either window until a handoff exists.
    for name in ("delivery_window", "refill_window"):
        if hasattr(x.runtime, name):
            delattr(x.runtime, name)
    assert x.farmer["trade"]["accepted"] is True
    if blocked == "paused":
        x.control["paused"] = True
    elif blocked:
        setattr(x.runtime, blocked, {"Dutch": True})
    assert confirm.lease_authorized(x.ui, "Dutch") is (blocked is None)
    assert x.events == []  # Qualification never repeats the Farmer confirmation.


@pytest.fixture
def midpoint(supervised, monkeypatch):
    x = supervised
    open_trade(x, phase="farmer_confirm_verified", offered=True)
    # Production receipt: the peer acknowledgement has not caught up, while
    # each client's own accepted flag is exact and independently observed.
    x.state["trade"]["other_accepted"] = False
    x.probe["merchant_after"] = x.read()
    x.save()
    x.control = {"revision": 3, "enabled": False, "paused": False}
    x.ui = NS(
        coordinator=x.guard,
        runtime=x.runtime,
        safe_to_yield=lambda: True,
        closed=False,
        app=NS(control=NS(snapshot=lambda: deepcopy(x.control)), closing=False),
        calibrating=set(),
        calibration_cancel={},
        delivery_probe_thread=threading.current_thread(),
    )
    x.target = NS(hwnd=77, snapshot=lambda: {"client_size": [1200, 900]})
    x.driver.observer = x.runtime.observers["Dutch"]
    x.driver.observer.operations = NS(target=x.target)
    x.driver.target = x.target
    x.driver.memory = NS(
        gui=NS(viewport_size=lambda: [1200, 900], assert_hovered=lambda *a, **k: None)
    )
    x.ui.hosts = {
        "Dutch": NS(
            mode="owned",
            saved=NS(hwnd=77, identity=deepcopy(x.state["identity"])),
            api=NS(assert_owner=lambda *a: None),
        )
    }
    monkeypatch.setattr(confirm, "JOURNAL", x.path)
    monkeypatch.setattr(confirm, "pair", lambda *a: (x.farmer_read(), x.read()))
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr(
        "conquest.merchants.farmer_preferences.permits_new_delivery", lambda *a: None
    )
    monkeypatch.setattr("ctypes.windll.user32.GetAsyncKeyState", lambda *a: 0)
    monkeypatch.setattr(
        "conquest.merchants.delivery_confirm_controls.confirm_control",
        lambda *a: ({}, (50, 50), 10),
    )
    monkeypatch.setattr(
        "conquest.merchants.driver.wait_hover_validation",
        lambda before, check: (check(), before()),
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_farmer_surface.prepare",
        lambda *a, **k: pytest.fail("Resume must not present Farmer"),
    )
    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client",
        lambda *a, **k: pytest.fail("Resume must not activate Farmer"),
    )
    monkeypatch.setattr(
        "conquest.merchants.memory.MerchantMemory",
        lambda *a, **k: pytest.fail("Resume must not create Farmer input memory"),
    )
    x.events = []
    x.before_press = lambda: None
    x.guard.on_acquire = lambda owner: x.events.append(("lease", owner))

    def click(target, *a, before_press, **kw):
        assert target is x.target and x.guard.owner == "Dutch"
        assert probe.read_probe()["phase"] == "merchant_confirm_submitted"
        x.before_press()
        before_press()
        x.events.append(("press", "Dutch"))
        x.farmer["trade"] = x.state["trade"] = None
        x.farmer["inventory"] = []
        x.state["inventory"] += deepcopy(x.probe["intent"]["items"])

    monkeypatch.setattr("conquest.foreground.foreground_click", click)
    return x


@pytest.fixture
def managed_midpoint(midpoint, monkeypatch, tmp_path):
    from conquest.character_context import ProfileMap, ProfileName
    from conquest.character_profiles import ProfileRegistry
    from conquest.merchants.delivery_bridge import pair as read_pair
    from conquest.merchants.ui import UnifiedUI

    x = midpoint
    x.profiles = ProfileRegistry(tmp_path / "managed")
    x.profile = x.profiles.add("Dutch", role="Merchant", local_enabled=True)
    x.profile_key = ProfileName(x.profile.name, x.profile.id)
    monkeypatch.setattr("conquest.character_context.registry", lambda: x.profiles)
    x.probe["target_profile_id"] = x.profile.id
    x.save()
    host = x.ui.hosts["Dutch"]
    observer = x.runtime.observers["Dutch"]
    x.runtime.controllers = ProfileMap()
    x.runtime.controllers[x.profile_key] = x.controller
    x.runtime.observers = ProfileMap()
    x.runtime.observers[x.profile_key] = observer
    x.ui.hosts = ProfileMap()
    x.ui.hosts[x.profile_key] = host
    x.ui.calibration_cancel = ProfileMap()
    x.ui.app.observer = x.source
    x.journal.set(x.profile_key, "enabled", True)
    observer.character = x.profile_key
    x.driver.read = lambda: {**x.read(), "character": x.profile_key}
    # Use the real bridge to cover its own ProfileMap lookup; Farmer memory
    # remains observation-only. Existing resume traps still forbid its surface
    # preparation and activation, and the click stub accepts only Merchant.
    monkeypatch.setattr(
        "conquest.merchants.memory.MerchantMemory",
        lambda source: NS(read=lambda **kw: x.farmer_read()),
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.MerchantMemory",
        lambda source: NS(read=lambda **kw: x.farmer_read()),
    )
    monkeypatch.setattr(confirm, "pair", read_pair)

    def present(owner):
        assert type(owner) is ProfileName and owner.profile_id == x.profile.id
        assert (
            x.runtime.controllers[owner] is x.controller
            and x.runtime.observers[owner] is observer
        )
        assert x.ui.hosts[owner] is host and owner in x.ui.calibrating
        assert isinstance(x.ui.calibration_cancel[owner], threading.Event)
        x.events.append(("present", str(owner)))

    x.ui.show_merchant = present

    class Queue:
        def put(self, request):
            callback, done, result = request
            try:
                callback()
            except Exception as error:
                result["error"] = str(error)
            finally:
                done.set()

    x.ui.ui_requests = Queue()
    monkeypatch.setattr(
        "conquest.merchants.ui.wait_for_merchant_surface",
        lambda owner, others, check: check(),
    )

    def acquired(owner):
        x.events.append(("lease", owner))
        UnifiedUI.prepare_input(x.ui, owner)

    x.guard.on_acquire = acquired
    return x


def test_managed_uuid_resolves_one_exact_runtime_key_for_merchant_only_resume(
    managed_midpoint,
):
    from conquest.character_context import ProfileName

    x = managed_midpoint
    assert (
        x.runtime.controllers.get("Dutch") is None
    )  # A display-name lookup cannot work.
    confirm.run(x.ui, deepcopy(x.probe))
    assert [(event, str(owner)) for event, owner in x.events] == [
        ("lease", "Dutch"),
        ("present", "Dutch"),
        ("press", "Dutch"),
    ]
    assert (
        type(x.events[0][1]) is ProfileName
        and x.events[0][1].profile_id == x.profile.id
    )
    saved = probe.read_probe()
    assert (
        saved["phase"] == "delivery_verified"
        and saved["character"] == "Dutch"
        and type(saved["character"]) is str
    )
    assert (
        saved["target_profile_id"] == x.profile.id
        and saved["confirming_role"] == "merchant"
    )
    assert not x.ui.calibrating and not any(
        str(owner) == "Farmer" for _, owner in x.events
    )


def test_managed_full_confirmation_preserves_farmer_then_exact_merchant_profile_key(
    managed_midpoint, monkeypatch
):
    from conquest.character_context import ProfileName

    x = managed_midpoint
    open_trade(x, phase="offer_verified", offered=True)
    farmer_target = NS(hwnd=88, snapshot=lambda: {"client_size": [1200, 900]})
    x.source.operations = NS(target=farmer_target)
    identity = deepcopy(x.farmer["identity"])
    x.ui.app.client = (identity["pid"], 88, identity)
    x.ui.app.host = NS(
        mode="owned",
        saved=NS(hwnd=88, identity=identity),
        api=NS(assert_owner=lambda *a: None),
    )

    def prepare(ui, state, **kw):
        assert ui.coordinator.owner == "Farmer" and type(ui.coordinator.owner) is str
        assert (
            state["phase"] == "offer_verified"
            and state["target_profile_id"] == x.profile.id
        )
        assert kw["purpose"] == "delivery_confirm_probe"
        return lambda: x.events.append(("present", "Farmer"))

    monkeypatch.setattr("conquest.merchants.delivery_farmer_surface.prepare", prepare)
    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client",
        lambda *a: x.events.append(("activate", "Farmer")) or True,
    )
    monkeypatch.setattr(
        "conquest.merchants.memory.MerchantMemory", lambda *a: x.driver.memory
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.source_memory",
        lambda *a: NS(gui=x.driver.memory.gui, read=lambda **kw: x.farmer_read()),
    )

    def click(target, *a, before_press, **kw):
        role = "Farmer" if target is farmer_target else "Dutch"
        if role == "Farmer":
            assert type(x.guard.owner) is str and x.guard.owner == "Farmer"
        else:
            assert (
                type(x.guard.owner) is ProfileName
                and x.guard.owner.profile_id == x.profile.id
            )
        assert (
            probe.read_probe()["phase"]
            == ("farmer" if role == "Farmer" else "merchant") + "_confirm_submitted"
        )
        before_press()
        x.events.append(("press", role))
        if role == "Farmer":
            x.farmer["trade"]["accepted"] = True
        else:
            x.farmer["trade"] = x.state["trade"] = None
            x.farmer["inventory"] = []
            x.state["inventory"] += deepcopy(x.probe["intent"]["items"])

    monkeypatch.setattr("conquest.foreground.foreground_click", click)
    confirm.run(x.ui, deepcopy(x.probe))
    assert [(event, str(owner)) for event, owner in x.events] == [
        ("lease", "Farmer"),
        ("present", "Farmer"),
        ("activate", "Farmer"),
        ("press", "Farmer"),
        ("lease", "Dutch"),
        ("present", "Dutch"),
        ("press", "Dutch"),
    ]
    saved = probe.read_probe()
    assert (
        saved["phase"] == "delivery_verified"
        and type(saved["character"]) is str
        and saved["character"] == "Dutch"
    )
    assert (
        saved["target_profile_id"] == x.profile.id
        and type(saved["merchant_after"]["character"]) is str
    )
    assert x.runtime.controllers.get("Dutch") is None and not x.ui.calibrating


@pytest.mark.parametrize("timing", ["before_run", "before_press"])
@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "disabled",
        "renamed",
        "role",
        "server",
        "id",
        "name_fallback",
        "registry_missing",
    ],
)
def test_managed_profile_changes_never_authorize_merchant_confirmation(
    managed_midpoint, monkeypatch, timing, fault
):
    x = managed_midpoint

    def change():
        if fault == "registry_missing":
            monkeypatch.setattr("conquest.character_context.registry", lambda: None)
            return
        if fault == "name_fallback":
            # A UUID-shaped *name* must never stand in for the saved UUID.
            with x.profiles.edit() as data:
                data["profiles"][0]["name"] = x.profile.id
                data["profiles"][0]["id"] = "164bfcf7-eae4-481d-8518-41c7b614283b"
            return
        with x.profiles.edit() as data:
            if fault == "missing":
                data["profiles"] = []
                return
            field, value = {
                "disabled": ("local_enabled", False),
                "renamed": ("name", "Other"),
                "role": ("role", "Farmer"),
                "server": ("server", "Europe"),
                "id": ("id", "164bfcf7-eae4-481d-8518-41c7b614283b"),
            }[fault]
            data["profiles"][0][field] = value

    saved = deepcopy(x.probe)
    if timing == "before_run":
        change()
    else:
        x.before_press = change
    with pytest.raises((ValueError, OSError)):
        confirm.run(x.ui, saved)
    assert not any(event == "press" for event, _ in x.events)
    assert not any(str(owner) == "Farmer" for _, owner in x.events)
    if timing == "before_run":
        assert not x.events and not x.ui.calibrating


@pytest.mark.parametrize(
    "field,value",
    [("target_profile_id", "Dutch"), ("target_profile_id", ""), ("character", "Other")],
)
def test_managed_receipt_cannot_use_name_or_missing_profile_binding(
    managed_midpoint, field, value
):
    x = managed_midpoint
    x.probe[field] = value
    x.save()
    with pytest.raises(ValueError):
        confirm.run(x.ui, deepcopy(x.probe))
    assert x.events == [] and not x.ui.calibrating


@pytest.mark.parametrize(
    "saved_remote,current_remote",
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_resume_exact_midpoint_uses_local_flags_and_only_merchant_input(
    midpoint, saved_remote, current_remote
):
    x = midpoint
    x.probe["merchant_after"]["trade"]["other_accepted"] = saved_remote
    x.state["trade"]["other_accepted"] = current_remote
    x.save()
    confirm.run(x.ui, deepcopy(x.probe))
    assert x.events == [("lease", "Dutch"), ("press", "Dutch")]
    saved = probe.read_probe()
    assert (
        saved["phase"] == "delivery_verified" and saved["confirming_role"] == "merchant"
    )
    assert saved["farmer_before_confirm"]["trade"]["accepted"] is True
    assert saved["merchant_before_confirm"]["trade"]["accepted"] is False
    assert x.runtime.manual_status() == []


def test_saved_json_delivery_receipt_validates_at_its_authority_time_not_as_fresh_input(
    midpoint,
):
    from conquest.merchants.delivery import (
        reconcile,
        reconciliation_outcome,
        ReconciliationBlocked,
    )

    x = midpoint
    confirm.run(x.ui, deepcopy(x.probe))
    saved = probe.read_probe()
    farmer, merchant = saved["farmer_after"], saved["merchant_after"]
    events = list(x.events)
    authority = saved["verified_at"]
    assert saved["phase"] == "delivery_verified" and reconcile(
        saved["intent"], farmer, merchant, now=authority
    )
    x.now += 150
    assert not reconcile(
        saved["intent"], farmer, merchant
    )  # No historical input authority.
    with pytest.raises(ReconciliationBlocked, match="fresh, living, identified"):
        reconciliation_outcome(saved["intent"], farmer, merchant)
    result = reconciliation_outcome(saved["intent"], farmer, merchant, now=authority)
    assert (
        result["outcome"] == "delivered"
        and x.events == events
        and probe.read_probe() == saved
    )


@pytest.mark.parametrize("fault", ["uid", "quantity", "silver", "identity", "trade"])
def test_historical_receipt_clock_never_bypasses_exact_ownership_checks(
    midpoint, fault
):
    from conquest.merchants.delivery import reconcile

    x = midpoint
    confirm.run(x.ui, deepcopy(x.probe))
    saved = probe.read_probe()
    farmer, merchant = saved["farmer_after"], saved["merchant_after"]
    events = list(x.events)
    if fault == "uid":
        merchant["inventory"][-1]["uid"] += 1
    if fault == "quantity":
        merchant["inventory"][-1]["quantity"] += 1
    if fault == "silver":
        merchant["silver"] += 1
    if fault == "identity":
        merchant["identity"]["creation_time_100ns"] += 1
    if fault == "trade":
        farmer["trade"] = {"participant": "Other", "participant_uid": 999}
    assert not reconcile(saved["intent"], farmer, merchant, now=saved["verified_at"])
    assert x.events == events


@pytest.mark.parametrize("role", ["farmer", "merchant"])
def test_asymmetric_midpoint_keeps_bot_precedence_without_manual_admission(
    midpoint, monkeypatch, role
):
    x = midpoint
    monkeypatch.setattr(
        "conquest.merchants.memory.MerchantMemory",
        lambda observer: NS(read=lambda **k: x.farmer_read()),
    )
    ownership(x.probe, "Dutch", "Dutch", "Farmer", x.farmer_read(), x.read(), now=x.now)
    assert x.runtime.process_manual(
        "Farmer" if role == "farmer" else "Dutch",
        x.farmer_read() if role == "farmer" else x.read(),
        now=x.now,
    )
    assert x.runtime.manual_status() == [] and x.events == []


@pytest.mark.parametrize(
    "phase",
    ["farmer_confirm_submitted", "merchant_confirm_submitted", "delivery_verified"],
)
def test_submitted_or_terminal_confirmation_is_never_replayed(midpoint, phase):
    x = midpoint
    x.probe["phase"] = phase
    x.save()
    with pytest.raises(ValueError, match="never repeat"):
        confirm.run(x.ui, deepcopy(x.probe))
    assert x.events == []


def mutate(x, fault):
    if fault == "farmer_acceptance":
        x.farmer["trade"]["accepted"] = False
    elif fault == "farmer_remote":
        x.farmer["trade"]["other_accepted"] = True
    elif fault == "merchant_acceptance":
        x.state["trade"]["accepted"] = True
    elif fault == "process":
        x.farmer["identity"]["creation_time_100ns"] += 1
    elif fault == "merchant_process":
        x.state["identity"]["pid"] += 1
    elif fault == "uid":
        x.state["trade"]["participant_uid"] += 1
    elif fault == "name":
        x.farmer["trade"]["participant"] = "Other"
    elif fault == "server":
        x.state["server"] = "Other"
    elif fault == "position":
        x.farmer["position"][0] += 1
    elif fault == "silver":
        x.state["silver"] += 1
    elif fault == "trade_silver":
        x.state["trade"]["other_silver"] = 1
    elif fault == "offer":
        x.state["trade"]["items"][0]["plus"] += 1
    elif fault == "missing_offer":
        x.farmer["trade"]["own_items"] = []
    elif fault == "reverse_offer":
        x.state["trade"]["own_items"] = deepcopy(x.state["inventory"])
    elif fault == "inventory":
        x.state["inventory"] = []
    elif fault == "booth":
        x.state["booth"][0]["price"] += 1
    elif fault == "capacity":
        x.state["capacity"] -= 1
    elif fault == "request":
        x.state["request"] = {"participant": "Visitor", "participant_uid": 56}
    elif fault == "stop":
        x.guard.stop()
    elif fault == "pause":
        x.control["paused"] = True
    elif fault == "revision":
        x.control["revision"] += 1
    elif fault == "mouse":
        x.guard.manual_active = lambda: True
    elif fault == "merchant_pause":
        x.runtime.enable("Dutch", False)
    elif fault == "profile":
        x.runtime.manual_target = lambda owner: "different-profile"
    elif fault == "hold":
        x.runtime.manual_reader_failure(
            "Farmer", {"reader_error": "unavailable"}, "unavailable", now=x.now
        )
    elif fault == "digest":
        receipt = probe.read_probe()
        receipt["operator_note"] = "changed"
        probe.write_probe(x.path, receipt)


@pytest.mark.parametrize("timing", ["before_lease", "before_press"])
@pytest.mark.parametrize(
    "fault",
    [
        "farmer_acceptance",
        "farmer_remote",
        "merchant_acceptance",
        "process",
        "merchant_process",
        "uid",
        "name",
        "server",
        "position",
        "silver",
        "trade_silver",
        "offer",
        "missing_offer",
        "reverse_offer",
        "inventory",
        "booth",
        "capacity",
        "request",
        "stop",
        "pause",
        "revision",
        "mouse",
        "merchant_pause",
        "profile",
        "hold",
        "digest",
    ],
)
def test_midpoint_mutations_never_press_merchant(midpoint, timing, fault):
    x = midpoint
    saved = deepcopy(x.probe)
    if timing == "before_lease":
        mutate(x, fault)
    else:
        x.before_press = lambda: mutate(x, fault)
    with pytest.raises((ValueError, OSError)):
        confirm.run(x.ui, saved, revision=3)
    assert ("press", "Dutch") not in x.events
    assert not any(owner == "Farmer" for _, owner in x.events)


def test_uncertain_merchant_press_cannot_resume_again(midpoint, monkeypatch):
    x = midpoint

    def uncertain(*a, before_press, **k):
        before_press()
        x.events.append(("press", "Dutch"))
        raise OSError("uncertain native submission")

    monkeypatch.setattr("conquest.foreground.foreground_click", uncertain)
    with pytest.raises(OSError, match="uncertain"):
        confirm.run(x.ui, deepcopy(x.probe))
    saved = probe.read_probe()
    assert saved["phase"] == "merchant_confirm_submitted"
    with pytest.raises(ValueError, match="never repeat"):
        confirm.run(x.ui, saved)
    assert x.events.count(("press", "Dutch")) == 1


@pytest.mark.parametrize("remote_changes", [False, True])
def test_first_confirmation_requires_no_prior_flags_then_accepts_lagging_peer(
    midpoint, monkeypatch, remote_changes
):
    x = midpoint
    open_trade(x, phase="offer_verified", offered=True)
    farmer_target = NS(hwnd=88)
    x.ui.app.observer = x.source
    x.source.operations = NS(target=farmer_target)
    identity = deepcopy(x.farmer["identity"])
    x.ui.app.client = (identity["pid"], 88, identity)
    x.ui.app.host = NS(
        mode="owned",
        saved=NS(hwnd=88, identity=identity),
        api=NS(assert_owner=lambda *a: None),
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_farmer_surface.prepare",
        lambda *a, **k: lambda: None,
    )
    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client",
        lambda *a: x.events.append(("activate", "Farmer")) or True,
    )
    monkeypatch.setattr(
        "conquest.merchants.memory.MerchantMemory", lambda *a: x.driver.memory
    )
    farmer_target.snapshot = lambda: {"client_size": [1200, 900]}
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.source_memory", lambda *a: x.driver.memory
    )

    def click(target, *a, before_press, **k):
        role = "Farmer" if target is farmer_target else "Dutch"
        assert (
            probe.read_probe()["phase"]
            == ("farmer" if role == "Farmer" else "merchant") + "_confirm_submitted"
        )
        if role == "Farmer" and remote_changes:
            x.state["trade"]["other_accepted"] = True
        before_press()
        x.events.append(("press", role))
        if role == "Farmer":
            x.farmer["trade"]["accepted"] = True
            assert x.state["trade"]["other_accepted"] is False  # Real remote lag.
        else:
            x.farmer["trade"] = x.state["trade"] = None
            x.farmer["inventory"] = []
            x.state["inventory"] += deepcopy(x.probe["intent"]["items"])

    monkeypatch.setattr("conquest.foreground.foreground_click", click)
    if remote_changes:
        with pytest.raises(ValueError, match="Local trade confirmation"):
            confirm.run(x.ui, deepcopy(x.probe))
        assert not any(event == "press" for event, role in x.events)
        assert probe.read_probe()["phase"] == "farmer_confirm_submitted"
    else:
        confirm.run(x.ui, deepcopy(x.probe))
        assert x.events == [
            ("lease", "Farmer"),
            ("activate", "Farmer"),
            ("press", "Farmer"),
            ("lease", "Dutch"),
            ("press", "Dutch"),
        ]
        assert probe.read_probe()["phase"] == "delivery_verified"


@pytest.mark.parametrize("key", [0x7A, 0x7B])
def test_hotkey_after_midpoint_submission_blocks_merchant_press(
    midpoint, monkeypatch, key
):
    x = midpoint
    x.before_press = lambda: monkeypatch.setattr(
        "ctypes.windll.user32.GetAsyncKeyState", lambda k: 0x8000 if k == key else 0
    )
    with pytest.raises(ValueError, match="stopped or expired"):
        confirm.run(x.ui, deepcopy(x.probe))
    assert x.events == [("lease", "Dutch")]


@pytest.mark.parametrize(
    "fault",
    [
        "saved_farmer_acceptance",
        "saved_merchant_acceptance",
        "saved_silver",
        "saved_item",
        "saved_process",
    ],
)
def test_resume_requires_exact_saved_midpoint_not_only_current_pair(midpoint, fault):
    x = midpoint
    if fault == "saved_farmer_acceptance":
        x.probe["farmer_after"]["trade"]["accepted"] = False
    if fault == "saved_merchant_acceptance":
        x.probe["merchant_after"]["trade"]["accepted"] = True
    if fault == "saved_silver":
        x.probe["merchant_after"]["silver"] += 1
    if fault == "saved_item":
        x.probe["farmer_after"]["trade"]["own_items"][0]["plus"] += 1
    if fault == "saved_process":
        x.probe["farmer_after"]["identity"]["pid"] += 1
    x.save()
    with pytest.raises(ValueError):
        confirm.run(x.ui, deepcopy(x.probe))
    assert x.events == []


@pytest.mark.parametrize(
    "phase,allowed",
    [
        ("offer_verified", True),
        ("farmer_confirm_verified", True),
        ("farmer_confirm_submitted", False),
        ("merchant_confirm_submitted", False),
    ],
)
def test_explicit_confirm_stage_can_only_start_unsubmitted_phase(
    midpoint, monkeypatch, phase, allowed
):
    from conquest.merchants import delivery_live

    x = midpoint
    x.probe["phase"] = phase
    x.save()
    x.ui.delivery_probe_thread = None
    monkeypatch.setattr(delivery_live, "JOURNAL", x.path)
    calls = []

    class Worker:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def start(self):
            calls.append("started")

    monkeypatch.setattr(delivery_live.threading, "Thread", Worker)
    monkeypatch.setattr(
        delivery_live.importlib, "import_module", lambda name: NS(run=lambda *a: None)
    )
    if allowed:
        assert delivery_live.start(x.ui, "confirm")["started"]
    else:
        with pytest.raises(ValueError, match="Reconcile"):
            delivery_live.start(x.ui, "confirm")
    assert ("started" in calls) == allowed


@pytest.mark.parametrize(
    "fault",
    [
        "controller",
        "driver",
        "observer",
        "adapter",
        "target",
        "hwnd",
        "host",
        "saved",
        "host_identity",
        "memory",
        "worker",
    ],
)
def test_replacement_native_chain_never_uses_old_target(midpoint, fault):
    x = midpoint

    def replace():
        if fault == "controller":
            x.runtime.controllers["Dutch"] = NS(driver=x.driver)
        if fault == "driver":
            x.controller.driver = NS(
                observer=x.driver.observer, target=x.target, memory=x.driver.memory
            )
        if fault == "observer":
            x.runtime.observers["Dutch"] = NS(adapter=x.driver.observer.adapter)
        if fault == "adapter":
            x.driver.observer.adapter = NS(
                identity=x.state["identity"], assert_identity=lambda: None
            )
        if fault == "target":
            x.driver.observer.operations.target = NS(hwnd=77)
        if fault == "hwnd":
            x.target.hwnd += 1
        if fault == "host":
            x.ui.hosts["Dutch"] = deepcopy(x.ui.hosts["Dutch"])
        if fault == "saved":
            x.ui.hosts["Dutch"].saved = deepcopy(x.ui.hosts["Dutch"].saved)
        if fault == "host_identity":
            x.ui.hosts["Dutch"].saved.identity["pid"] += 1
        if fault == "memory":
            x.driver.memory = NS(gui=x.driver.memory.gui)
        if fault == "worker":
            x.ui.delivery_probe_thread = NS()

    x.before_press = replace
    with pytest.raises((ValueError, OSError)):
        confirm.run(x.ui, deepcopy(x.probe))
    assert x.events == [("lease", "Dutch")]


@pytest.mark.parametrize("changed_receipt", [False, True])
def test_stage_worker_pins_revision_and_never_clobbers_replaced_receipt(
    midpoint, monkeypatch, changed_receipt
):
    from conquest.merchants import delivery_live

    x = midpoint
    x.ui.delivery_probe_thread = None
    monkeypatch.setattr(delivery_live, "JOURNAL", x.path)
    callbacks = []
    seen = []

    class Worker:
        def __init__(self, **kwargs):
            callbacks.append(kwargs["target"])

        def start(self):
            pass

    monkeypatch.setattr(delivery_live.threading, "Thread", Worker)

    def run(ui, state, *, revision):
        seen.append(revision)
        if changed_receipt:
            other = {
                **state,
                "phase": "merchant_confirm_submitted",
                "newer_worker": "preserve",
            }
            probe.write_probe(x.path, other)
        raise ValueError("delayed worker denied")

    monkeypatch.setattr(
        delivery_live.importlib, "import_module", lambda name: NS(run=run)
    )
    delivery_live.start(x.ui, "confirm")
    x.control["revision"] = 4
    callbacks[0]()
    assert seen == [3]
    saved = probe.read_probe()
    if changed_receipt:
        assert saved["newer_worker"] == "preserve" and "error" not in saved
    else:
        assert saved["error"] == "delayed worker denied"
