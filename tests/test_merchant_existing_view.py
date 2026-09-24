from types import SimpleNamespace as NS
import threading

import pytest

from conquest.merchants import restore_hosts as module
from conquest.merchants.ui import UnifiedUI
from conquest.memory_build_layout import CLIENT_SHA256_1078


@pytest.fixture
def rig(monkeypatch):
    calls = []
    selected = ["Farmer"]
    visible = {20: False, 30: True}
    owners = {20: 10, 30: 10}
    identity = {"pid": 2, "creation_time_100ns": 3, "path": "game.exe"}
    observer = NS(
        hwnd=20,
        merchant_observation_only=True,
        adapter=NS(
            expected_sha256=CLIENT_SHA256_1078,
            identity=identity,
            assert_identity=lambda: calls.append("identity"),
        ),
    )
    pane = NS(
        winfo_id=lambda: 9,
        winfo_ismapped=lambda: selected[0] == "Dutch",
        winfo_width=lambda: 1420,
        winfo_height=lambda: 1009,
    )

    def show(hwnd, flag):
        visible[hwnd] = bool(flag)
        calls.append(("show", hwnd, flag))

    gui = NS(
        GetAncestor=lambda hwnd, kind: hwnd if hwnd in (20, 30) else 10,
        GetWindow=lambda hwnd, kind: owners[hwnd],
        IsWindowVisible=lambda hwnd: visible[hwnd],
    )
    api = NS(
        gui=gui, assert_owner=lambda *a: calls.append(("owner", a[0])), show_async=show
    )

    def resize(*size):
        calls.append(("resize", size))
        show(20, 4)

    host = NS(
        mode="owned",
        saved=NS(hwnd=20, identity=identity),
        parent=9,
        api=api,
        resize=resize,
    )
    other = NS(mode="owned", saved=NS(hwnd=30, identity={"pid": 3}), parent=11, api=api)

    def select(value=None):
        if value is not None:
            selected[0] = value
            calls.append(("select", value))
        return selected[0]

    def forbidden(*a, **kw):
        pytest.fail("Display reached gameplay state, attach or input permission")

    ui = NS(
        closed=False,
        app=NS(closing=False),
        coordinator=NS(
            lock=threading.RLock(),
            owner=None,
            stopped=True,
            manual_active=lambda: True,
            surface_blocks={"Dutch": True},
        ),
        runtime=NS(observers={"Dutch": observer}, manual_handoff_status=forbidden),
        released_clients=set(),
        hosts={"Dutch": host, "Spiritual": other},
        client_panes={"Dutch": pane},
        frames={"Dutch": "Dutch", "Spiritual": "Spiritual"},
        notebook=NS(select=select),
        detail_tabs={"Dutch": NS(select=lambda n: calls.append(("detail", n)))},
        root=NS(update_idletasks=lambda: calls.append("layout")),
        layout_status={},
        resize_jobs={},
        calibration_results={},
        auto_embedding=False,
        calibrating=set(),
        auto_embed_retry={},
    )
    monkeypatch.setattr(
        "conquest.merchants.background_probe.probe_busy", lambda ui: False
    )
    monkeypatch.setattr("conquest.merchants.ui.probe_busy", lambda ui: False)
    monkeypatch.setattr(module, "_readonly_market_hosting_safe", forbidden)
    ui.embed_client = forbidden
    return ui, calls, selected, visible, owners


def test_explicit_existing_view_works_at_login_stop_and_manual_setup(rig):
    ui, calls, selected, visible, owners = rig
    result = module.present_existing(ui, "Dutch", select=True)
    assert result["display_only"] and result["native_visible"] and result["hwnd"] == 20
    assert selected[0] == "Dutch" and visible == {20: True, 30: False}
    assert ui.coordinator.stopped and ui.coordinator.surface_blocks == {"Dutch": True}
    assert ui.layout_status["Dutch"]["existing_host_view"] is True
    assert ("resize", (1420, 1009)) in calls


def test_selected_tab_and_resize_do_not_require_ready_handoff_or_game_memory(rig):
    ui, calls, selected, visible, owners = rig
    selected[0] = "Dutch"
    UnifiedUI.auto_show_selected(ui)
    assert visible[20]
    visible[20] = False
    calls.clear()
    UnifiedUI.finish_resize(ui, "Dutch")
    assert visible[20] and ("resize", (1420, 1009)) in calls


def test_manual_view_button_uses_existing_host_before_handoff_ready(rig):
    ui, calls, selected, visible, owners = rig
    UnifiedUI.show_manual_handoff_surface(ui, "Dutch")
    assert selected[0] == "Dutch" and visible[20]


def test_tab_change_hides_old_host_without_activating_any_client(rig):
    ui, calls, selected, visible, owners = rig
    visible[20] = True
    UnifiedUI.finish_resize(ui, "Dutch")
    assert visible[20] is False
    assert ("resize", (1420, 1009)) not in calls


@pytest.mark.parametrize(
    "change",
    ["owner", "released", "pid", "hwnd", "native_owner", "parent", "detached", "build"],
)
def test_existing_view_rejects_changed_binding_before_select_or_show(rig, change):
    ui, calls, selected, visible, owners = rig
    host = ui.hosts["Dutch"]
    observer = ui.runtime.observers["Dutch"]
    if change == "owner":
        ui.coordinator.owner = "Farmer"
    elif change == "released":
        ui.released_clients.add("Dutch")
    elif change == "pid":
        observer.adapter.identity = {**observer.adapter.identity, "pid": 99}
    elif change == "hwnd":
        observer.hwnd = 99
    elif change == "native_owner":
        owners[20] = 99
    elif change == "parent":
        host.parent = 99
    elif change == "detached":
        host.saved = None
    else:
        observer.adapter.expected_sha256 = "unknown"
    with pytest.raises(ValueError):
        module.present_existing(ui, "Dutch", select=True)
    assert selected[0] == "Farmer" and not visible[20]
    assert not any(
        isinstance(c, tuple) and c[0] in ("resize", "show", "select") for c in calls
    )


def test_busy_coordinator_does_not_wait_or_switch_tab(rig):
    ui, calls, selected, visible, owners = rig
    ui.coordinator.lock = NS(
        acquire=lambda **kw: False, release=lambda: pytest.fail("not acquired")
    )
    with pytest.raises(ValueError, match="active input"):
        module.present_existing(ui, "Dutch", select=True)
    assert calls == [] and selected[0] == "Farmer"


def test_queued_user_view_pins_exact_existing_host(rig):
    ui, calls, selected, visible, owners = rig

    def put(entry):
        callback, done, result = entry
        callback()
        done.set()

    ui.ui_requests = NS(put=put)
    result = UnifiedUI.dispatch(
        ui, {"action": "show-merchant-view", "character": "Dutch"}
    )
    assert result["display_only"] and result["native_visible"]
    assert selected[0] == "Dutch" and ui.coordinator.stopped


def test_stale_queued_view_cannot_show_replacement_host(rig):
    ui, calls, selected, visible, owners = rig

    def put(entry):
        callback, done, result = entry
        old = ui.hosts["Dutch"]
        ui.hosts["Dutch"] = NS(**vars(old))
        try:
            callback()
        except ValueError as error:
            result["error"] = str(error)
        done.set()

    ui.ui_requests = NS(put=put)
    with pytest.raises(ValueError, match="binding changed"):
        UnifiedUI.dispatch(ui, {"action": "show-merchant-view", "character": "Dutch"})
    assert selected[0] == "Farmer" and not visible[20]


@pytest.mark.parametrize(
    "bad", ["none", "foreign_owner", "minimized", "identity_changed"]
)
def test_explicit_view_can_attach_only_the_existing_identified_observer(rig, bad):
    ui, calls, selected, visible, owners = rig
    candidate = ui.hosts.pop("Dutch")
    candidate.saved = None
    candidate.parent = None
    owners[20] = 99 if bad == "foreign_owner" else 0
    candidate.api.gui.IsIconic = lambda hwnd: bad == "minimized"

    def attach(hwnd, identity, parent, *size):
        calls.append(("attach", hwnd))
        candidate.saved = NS(hwnd=hwnd, identity=identity)
        candidate.parent = parent
        owners[hwnd] = 10

    candidate.attach = attach
    if bad == "identity_changed":
        ui.root.update_idletasks = lambda: setattr(
            ui.runtime.observers["Dutch"], "hwnd", 99
        )
    if bad != "none":
        with pytest.raises(ValueError):
            module.present_user_view(ui, "Dutch", host_factory=lambda: candidate)
        assert not any(isinstance(c, tuple) and c[0] == "attach" for c in calls)
        assert "Dutch" not in ui.hosts
    else:
        result = module.present_user_view(ui, "Dutch", host_factory=lambda: candidate)
        assert result["display_only"] and visible[20]
        assert ("attach", 20) in calls and ui.coordinator.stopped
        assert ui.coordinator.surface_blocks == {"Dutch": True}


def test_automatic_tab_polling_never_uses_explicit_attach_path(rig, monkeypatch):
    ui, calls, selected, visible, owners = rig
    ui.hosts.pop("Dutch")
    selected[0] = "Dutch"
    ui.runtime.manual_handoff_status = lambda: None
    monkeypatch.setattr(
        module,
        "present_user_view",
        lambda *a, **k: pytest.fail("Automatic explicit attachment"),
    )
    monkeypatch.setattr(
        module,
        "restore_readonly",
        lambda *a, **k: calls.append("ordinary safe attachment gate"),
    )
    UnifiedUI.auto_show_selected(ui)
    assert calls == ["ordinary safe attachment gate"] and not visible[20]
