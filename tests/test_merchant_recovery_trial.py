from contextlib import contextmanager
from types import SimpleNamespace as NS
import threading
import pytest
from conquest.merchants.recovery_trial import validate_before
from conquest.merchants.runtime import MerchantRuntime


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "trade",
        "request",
        "no_owner",
        "closed",
        "dead",
        "capacity",
        "dialog",
        "map",
    ],
)
def test_trial_requires_idle_market_and_capacity_for_returned_stock(case):
    s = dict(
        map_id=1036,
        own_booth_uid=12,
        booth_open=True,
        hp=90,
        capacity=40,
        inventory=[{}] * 2,
        booth=[{}] * 30,
        windows=[],
        trade=None,
        request=None,
    )
    if case in ("trade", "request"):
        s[case] = {"open": True}
    if case == "no_owner":
        s["own_booth_uid"] = 0
    if case == "closed":
        s["booth_open"] = False
    if case == "dead":
        s["hp"] = 0
    if case == "capacity":
        s["inventory"] = [{}] * 11
    if case == "dialog":
        s["windows"] = [{"name": "Add Item to Booth"}]
    if case == "map":
        s["map_id"] = 1002
    if case == "valid":
        validate_before(s)
    else:
        with pytest.raises(ValueError):
            validate_before(s)


@pytest.mark.parametrize("crashed", [True, False])
def test_automatic_recovery_uses_launch_or_login_surface_not_embedded_booth(
    monkeypatch, tmp_path, crashed
):
    from conquest.merchants import runtime

    calls = []

    @contextmanager
    def lease(character, *, purpose=None):
        calls.append(purpose)
        yield

    path = tmp_path / "credentials"
    path.write_bytes(b"encrypted fixture")
    monkeypatch.setattr(runtime, "credential_path", lambda c: path)
    monkeypatch.setattr(
        "conquest.reconnect.submit_login", lambda *a, **k: calls.append("login")
    )

    class Watch:
        def __init__(self, *a, **k):
            pass

        def start(self):
            calls.append("launch")

    monkeypatch.setattr("conquest.client_wrapper.LaunchWatch", Watch)
    monkeypatch.setattr(
        "conquest.character_context.merchant_installation",
        lambda character: (
            tmp_path if character == "Dutch" else pytest.fail("Wrong profile")
        ),
    )

    def installed(root):
        assert root == tmp_path
        return ["client.exe"], root

    monkeypatch.setattr("conquest.merchants.client_launch.installed_client", installed)
    driver = NS(
        require_qualified=lambda c: None, target=object(), observer=NS(adapter=object())
    )
    r = NS(
        enabled=lambda c: True,
        coordinator=NS(lease=lease, safe_to_yield=lambda: True),
        discovery_lock=threading.Lock(),
        launch_owner=None,
        launches={},
        catalog=object(),
        # No exact 1078 client is present, so the input-capable path is open.
        read_only_1078=lambda character, force=False: False,
        controllers={"Dutch": NS(driver=driver)},
        recoveries={"Dutch": NS(attempt=lambda action: action() or True)},
    )
    from conquest.merchants.journal import Journal

    r.journal = Journal(tmp_path / "recovery.sqlite3")
    MerchantRuntime.recover(r, "Dutch", crashed=crashed)
    assert calls == (["connect_launch", "launch"] if crashed else ["connect", "login"])


def test_dead_embedded_window_does_not_pause_reconnect(monkeypatch):
    from conquest.merchants.ui import UnifiedUI

    calls = []
    monkeypatch.setattr("conquest.merchants.ui.probe_busy", lambda ui: False)
    host = NS(
        saved=object(),
        is_alive=lambda: False,
        detach=lambda: calls.append("detach_stale"),
    )
    ui = NS(
        resize_jobs={},
        closed=False,
        hosts={"Dutch": host},
        runtime=NS(observers={}),
        render_sizes={"Dutch": (1888, 665)},
        layout_status={},
        calibration_results={},
        resize_merchant=lambda c: calls.append("resize"),
        pause=lambda c: calls.append("pause"),
    )
    UnifiedUI.finish_resize(ui, "Dutch")
    assert calls == ["detach_stale"]
    assert "Dutch" not in ui.render_sizes
    assert (
        ui.calibration_results["Dutch"]["note"]
        == "Client closed; waiting for reconnect"
    )


def test_client_launch_rejects_unqualified_binary(tmp_path):
    from conquest.merchants.client_launch import installed_client

    client = tmp_path / "bin" / "64" / "ImConquer.exe"
    client.parent.mkdir(parents=True)
    client.write_bytes(b"changed game")
    with pytest.raises(ValueError, match="changed"):
        installed_client(tmp_path)


@pytest.mark.parametrize("same_identity", [True, False])
def test_login_reattachment_requires_exact_previously_assigned_process(
    monkeypatch, same_identity
):
    from conquest.merchants import runtime

    identity = {"pid": 12, "creation_time_100ns": 34}
    bound = []
    candidate = NS(identity=identity, hwnd=56)
    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: True)
    monkeypatch.setattr(
        "conquest.memory_life.read_life",
        lambda *a: (_ for _ in ()).throw(
            AssertionError("No stale player read at login")
        ),
    )
    observer = NS(
        adapter=NS(identity=identity),
        health_layout=None,
        close=lambda: None,
        operations=NS(target=NS(hwnd=56)),
    )
    from conquest.client_attachment import AttachmentStatus

    requested = []

    def windows(*, include_hidden=False):
        requested.append(include_hidden)
        return [candidate] if include_hidden else []

    r = NS(
        attachments={"Dutch": AttachmentStatus()},
        discovery_lock=threading.Lock(),
        catalog=NS(windows=windows),
        read_only_1078=lambda character, force=False: False,
        observers={},
        observer_factory=lambda *a: observer,
        journal=NS(
            get=lambda *a: (
                identity if same_identity else {"pid": 12, "creation_time_100ns": 1}
            )
        ),
        bind=lambda c, o: bound.append(o),
    )
    r.merchant_windows = lambda: MerchantRuntime.merchant_windows(r)
    if same_identity:
        # bind() already schedules the saved itinerary. The login shell has no map.
        MerchantRuntime.attach(r, "Dutch")
        assert bound == [observer] and requested == [True]
    else:
        with pytest.raises(ValueError):
            MerchantRuntime.attach(r, "Dutch")
        assert not bound and requested == [True]


def test_hidden_logged_in_merchants_are_memory_verified_before_reattachment(
    monkeypatch,
):
    """An embedded pair is discoverable without admitting the visible farmer."""
    from conquest.client_wrapper import ClientCatalog
    from conquest.client_attachment import AttachmentStatus

    identities = {
        1: {"pid": 1, "creation_time_100ns": 10, "path": "ImConquer.exe"},
        2: {"pid": 2, "creation_time_100ns": 20, "path": "ImConquer.exe"},
        3: {"pid": 3, "creation_time_100ns": 30, "path": "ImConquer.exe"},
    }
    names = {1: "Parasite", 2: "Spiritual", 3: "Dutch"}
    backend = NS(
        processes=lambda exe: [{"pid": pid} for pid in identities],
        identity=lambda pid: identities[pid],
        windows=lambda pid: [
            dict(
                hwnd=pid + 100,
                title="ClassicConquer",
                visible=pid == 1,
                client_size=[1024, 768],
            )
        ],
    )
    catalog = ClientCatalog(backend)
    assert [client.identity["pid"] for client in catalog.windows()] == [1]
    closed, bound = [], []

    def observer_factory(client, character):
        return NS(
            adapter=NS(identity=client.identity),
            health_layout=None,
            operations=NS(target=NS(hwnd=client.hwnd)),
            close=lambda: closed.append(client.identity["pid"]),
        )

    def read_life(adapter, _layout, expected):
        if names[adapter.identity["pid"]] != expected:
            raise ValueError("Wrong character in memory")
        return NS(character=expected, map_id=1036)

    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: False)
    monkeypatch.setattr("conquest.memory_life.read_life", read_life)
    r = NS(
        attachments={name: AttachmentStatus() for name in ("Spiritual", "Dutch")},
        discovery_lock=threading.Lock(),
        catalog=catalog,
        read_only_1078=lambda character, force=False: False,
        observers={},
        observer_factory=observer_factory,
        journal=NS(get=lambda *args: None),
    )
    r.merchant_windows = lambda: MerchantRuntime.merchant_windows(r)

    def bind(character, observer):
        bound.append((character, observer.adapter.identity))
        r.observers[character] = observer

    r.bind = bind
    MerchantRuntime.attach(r, "Spiritual")
    MerchantRuntime.attach(r, "Dutch")
    assert bound == [("Spiritual", identities[2]), ("Dutch", identities[3])]
    # The farmer and the other merchant are never retained as a match.
    assert closed == [1, 3, 1]


def test_hidden_merchant_ambiguity_closes_every_candidate_and_fails_closed(monkeypatch):
    from conquest.client_wrapper import ClientCatalog
    from conquest.client_attachment import AttachmentStatus

    identities = {
        pid: {"pid": pid, "creation_time_100ns": pid * 10, "path": "ImConquer.exe"}
        for pid in (1, 2, 3)
    }
    backend = NS(
        processes=lambda exe: [{"pid": pid} for pid in identities],
        identity=lambda pid: identities[pid],
        windows=lambda pid: [
            dict(
                hwnd=pid + 100,
                title="ClassicConquer",
                visible=pid == 1,
                client_size=[1024, 768],
            )
        ],
    )
    closed, bound = [], []

    def observer_factory(client, character):
        return NS(
            adapter=NS(identity=client.identity),
            health_layout=None,
            operations=NS(target=NS(hwnd=client.hwnd)),
            close=lambda: closed.append(client.identity["pid"]),
        )

    def read_life(adapter, _layout, expected):
        if adapter.identity["pid"] == 1:
            raise ValueError("Wrong character in memory")
        return NS(character="Spiritual", map_id=1036)

    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: False)
    monkeypatch.setattr("conquest.memory_life.read_life", read_life)
    r = NS(
        attachments={"Spiritual": AttachmentStatus()},
        discovery_lock=threading.Lock(),
        catalog=ClientCatalog(backend),
        read_only_1078=lambda character, force=False: False,
        observers={},
        observer_factory=observer_factory,
        journal=NS(get=lambda *args: None),
        bind=lambda *args: bound.append(args),
    )
    r.merchant_windows = lambda: MerchantRuntime.merchant_windows(r)
    with pytest.raises(ValueError, match="found 2"):
        MerchantRuntime.attach(r, "Spiritual")
    assert not bound and sorted(closed) == [1, 2, 3]


def test_market_guard_uses_hidden_merchant_lookup_and_exact_saved_identity(monkeypatch):
    from conquest.merchants.market_guard import MarketGuard

    identity = {"pid": 2, "creation_time_100ns": 20, "path": "ImConquer.exe"}
    candidate = NS(identity=identity, hwnd=102)
    observed, closed = [], []

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, _seconds):
            self.stopped = True

    stop = Stop()
    observer = NS(adapter=NS(identity=identity), close=lambda: closed.append(True))
    r = NS(
        stop_event=stop,
        merchant_windows=lambda: [candidate],
        journal=NS(
            get=lambda character, name: (
                identity
                if (character, name) == ("Spiritual", "last_identity")
                else None
            )
        ),
        observer_factory=lambda client, character: (
            observed.append((character, client.identity)) or observer
        ),
    )
    monkeypatch.setattr("conquest.discord_notify.write_json", lambda *args: None)
    guard = MarketGuard(r)
    guard.check = lambda character, reader: observed.append(
        ("checked", character, reader.adapter.identity)
    )
    guard.run()
    assert observed == [("Spiritual", identity), ("checked", "Spiritual", identity)]
    assert closed == [True]


def test_login_focus_recovery_uses_verified_wrapper_caption(monkeypatch):
    from conquest.merchants.ui import UnifiedUI

    calls = []
    farmer = NS(operations=NS(target=NS(hwnd=1)), adapter=NS(identity={"pid": 1}))
    merchant = NS(operations=NS(target=NS(hwnd=2)), adapter=NS(identity={"pid": 2}))
    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: True)
    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client",
        lambda h, i: calls.append(("focus", h)) or len(calls) == 3,
    )
    monkeypatch.setattr(
        "conquest.window_host.HostApi",
        lambda: NS(activate_owned_caption=lambda h, i: calls.append(("caption", h))),
    )
    ui = NS(
        coordinator=NS(purpose="connect", check=lambda: None),
        app=NS(observer=farmer),
        runtime=NS(observers={"Dutch": merchant}),
        safe_to_yield=lambda: True,
    )
    UnifiedUI.prepare_input(ui, "Dutch")
    assert calls == [("focus", 2), ("caption", 1), ("focus", 2)]


@pytest.mark.parametrize(
    "error", ["Life state changed during observation", "Wrong character"]
)
def test_only_torn_life_observations_are_retried(monkeypatch, error):
    from conquest.merchants.transit_life import stable_life

    calls = []

    def read(*args):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError(error)
        return "fresh"

    monkeypatch.setattr("conquest.memory_life.read_life", read)
    if error.startswith("Life state"):
        assert stable_life() == "fresh" and len(calls) == 2
    else:
        with pytest.raises(ValueError, match="Wrong character"):
            stable_life()
        assert len(calls) == 1


@pytest.mark.parametrize("submitted", [False, True])
def test_replans_only_before_press_never_after_uncertain_input(monkeypatch, submitted):
    from conquest.merchants.return_driver import ReturnDriver, TravelPlanChanged

    calls = []
    s = {"identity": {"pid": 1}, "map_id": 1002, "position": [1, 1]}

    def move(*args):
        calls.append(1)
        if len(calls) == 1:
            raise (
                ValueError("Submitted movement result unknown")
                if submitted
                else TravelPlanChanged("Projection moved")
            )
        return "arrived"

    r = NS(_move=move, read=lambda: s)
    if submitted:
        with pytest.raises(ValueError):
            ReturnDriver.move(r, s, (2, 2), lambda: None)
        assert len(calls) == 1
    else:
        assert (
            ReturnDriver.move(r, s, (2, 2), lambda: None) == "arrived"
            and len(calls) == 2
        )


@pytest.mark.parametrize("confirmed", [False, True])
def test_conductress_waits_for_native_pointer_before_press(monkeypatch, confirmed):
    from types import SimpleNamespace as NS
    from conquest.merchants.return_driver import ReturnDriver
    from conquest.capture import CaptureUnavailable

    snapshot = {
        "identity": {"pid": 1},
        "map_id": 1002,
        "position": [438, 444],
        "silver": 1000,
    }
    npc = NS(draw_position=(500, 300))
    events = []
    records = [{"kind": 0, "text": "100 silver"}, {"kind": 1, "text": "Market"}]
    monkeypatch.setattr("conquest.conductress.read_conductress", lambda o: npc)
    monkeypatch.setattr(
        "conquest.conductress.read_dialog", lambda o: {"records": records}
    )
    monkeypatch.setattr("conquest.market_services.dialog_point", lambda *a: (1, 2))

    def pointer(session, point, check):
        check()
        events.append(("pointer", point))
        if not confirmed:
            raise CaptureUnavailable("No pointer acknowledgement")

    monkeypatch.setattr("conquest.scene_pointer.wait_scene_pointer", pointer)
    driver = NS(observer=NS(adapter=object()), memory=NS(gui=NS(windows=lambda: [])))
    travel = ReturnDriver(driver)
    travel.qualify_movement = lambda: None
    travel.read = lambda: snapshot

    def click(point, check, before_press):
        before_press()
        events.append("press")

    travel.click = click
    if confirmed:
        assert travel.prepare_transfer(snapshot, lambda: None) == records
        assert events == [("pointer", (500, 268)), "press"]
    else:
        with pytest.raises(CaptureUnavailable, match="acknowledgement"):
            travel.prepare_transfer(snapshot, lambda: None)
        assert events == [("pointer", (500, 268))]


def test_periodic_ui_poll_does_not_pause_a_dead_client(monkeypatch):
    from unittest.mock import Mock
    import time
    from conquest.merchants.ui import UnifiedUI

    monkeypatch.setattr(
        "conquest.merchants.dashboard.merchant_text", lambda *a, **k: "Recovering"
    )
    calls = []
    host = NS(saved=object(), is_alive=lambda: False)

    def detach():
        calls.append("detach")
        host.saved = None

    host.detach = detach
    state = {"snapshot": None, "scan": {}, "enabled": True, "refill": {"enabled": True}}
    text = Mock()
    u = NS(
        closed=False,
        presentation=NS(
            latest={
                "characters": {"Spiritual": state},
                "events": [],
                "tables": {"Spiritual": {"deferred": []}},
                "collected_at": time.monotonic(),
            }
        ),
        update_header=lambda data: None,
        app=NS(
            closing=False,
            control=NS(snapshot=lambda: {}),
            state_text=text,
            activity_text=text,
            stats_text=text,
        ),
        rows={"Farmer": text, "Spiritual": text},
        input_note=text,
        coordinator=NS(owner=None, stopped=False),
        safe_to_yield=lambda: True,
        background_surfaces={},
        batch_buttons={"Spiritual": Mock()},
        manage_buttons={"Spiritual": Mock()},
        refill_buttons={"Spiritual": Mock()},
        merchant_buttons={"Spiritual": Mock()},
        hosts={"Spiritual": host},
        runtime=NS(observers={}),
        labels={"Spiritual": text},
        notebook=NS(select=lambda: "Other"),
        frames={"Spiritual": "Spiritual"},
        root=Mock(),
        resize_jobs={},
        background_probe={},
        render_sizes={},
        layout_status={},
        calibration_results={},
        pause=lambda c: calls.append("pause"),
        resize_merchant=lambda c: calls.append("resize"),
    )
    monkeypatch.setattr("conquest.merchants.restore_hosts.restore", lambda ui: None)
    u.auto_show_selected = lambda: None
    u.poll = lambda: UnifiedUI.poll(u)
    u.finish_resize = lambda c: UnifiedUI.finish_resize(u, c)
    UnifiedUI.poll(u)
    assert calls == ["detach"]
    assert state["enabled"] is True


def test_disconnected_recovery_recreates_one_handoff_before_input(
    monkeypatch, tmp_path
):
    from conquest.merchants import runtime
    from conquest.capture import CaptureUnavailable

    path = tmp_path / "credentials"
    path.write_bytes(b"encrypted fixture")
    monkeypatch.setattr(runtime, "credential_path", lambda c: path)
    r = NS(
        enabled=lambda c: True,
        coordinator=NS(safe_to_yield=lambda: False),
        read_only_1078=lambda character, force=False: False,
        lock=threading.Lock(),
        handoff=None,
    )
    from conquest.merchants.journal import Journal

    r.journal = Journal(tmp_path / "recovery.sqlite3")
    with pytest.raises(CaptureUnavailable, match="safe farmer handoff"):
        MerchantRuntime.recover(r, "Dutch")
    key = r.handoff
    assert key.startswith("merchant-recovery:Dutch:")
    with pytest.raises(CaptureUnavailable):
        MerchantRuntime.recover(r, "Spiritual")
    assert r.handoff == key
    r.enabled = lambda c: False
    r.handoff = None
    with pytest.raises(CaptureUnavailable, match="Paused"):
        MerchantRuntime.recover(r, "Dutch")
    assert r.handoff is None
