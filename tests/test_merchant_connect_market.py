import threading
from types import SimpleNamespace as NS
import pytest
from conquest.capture import CaptureUnavailable
from conquest.discord_notify import read_json
from conquest.merchants import connect_market as connect
from conquest.merchants.coordination import InputCoordinator
from conquest.merchants.journal import Journal
from conquest.merchants.runtime import MerchantRuntime


@pytest.fixture
def setup(tmp_path, monkeypatch):
    guard = InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    runtime = MerchantRuntime(
        NS(identities=lambda: []), guard, journal=Journal(tmp_path / "state.sqlite3")
    )
    guard.owner_allowed = runtime.input_allowed
    path = tmp_path / "account.dpapi"
    path.write_bytes(b"encrypted-placeholder")
    monkeypatch.setattr(connect, "credential_path", lambda c: path)
    ui = NS(
        runtime=runtime,
        coordinator=guard,
        closed=False,
        connect_threads={},
        safe_to_yield=lambda: True,
        app=NS(control=NS(snapshot=lambda: {"revision": 0})),
    )
    return NS(runtime=runtime, guard=guard, ui=ui, path=path)


@pytest.mark.parametrize(
    "reason", ["missing_login", "busy", "stopped", "unsafe", "uncertain_fare"]
)
def test_start_refuses_missing_prerequisites_without_launch(setup, reason):
    x = setup
    if reason == "missing_login":
        x.path.unlink()
    if reason == "busy":
        x.runtime.connecting["Dutch"] = 7
    if reason == "stopped":
        x.guard.stop()
    if reason == "unsafe":
        x.ui.safe_to_yield = lambda: False
    if reason == "uncertain_fare":
        x.runtime.journal.set(
            "Spiritual", "connect_market", {"phase": "transfer_pending"}
        )
    with pytest.raises(ValueError):
        connect.start(x.ui, "Spiritual")
    assert not x.ui.connect_threads
    assert not x.runtime.enabled("Spiritual")


@pytest.mark.parametrize(
    "purpose", ["connect", "connect_launch", "listing", "trade", "refill", None]
)
def test_connection_has_scoped_permission_without_enabling_trades(setup, purpose):
    x = setup
    x.runtime.connecting["Dutch"] = threading.get_ident()
    x.runtime.connect_checks["Dutch"] = lambda: True
    if purpose in ("connect", "connect_launch"):
        with x.guard.lease("Dutch", purpose=purpose):
            x.guard.check()
    else:
        with pytest.raises(CaptureUnavailable):
            with x.guard.lease("Dutch", purpose=purpose):
                pytest.fail("Unrelated input")
    assert not x.runtime.enabled("Dutch")


def test_manual_stop_cancels_connection(setup):
    x = setup
    cancel = threading.Event()
    x.runtime.connect_cancel["Dutch"] = cancel
    x.runtime.global_stop()
    assert cancel.is_set() and x.guard.stopped


def test_movement_trial_cannot_launch_a_missing_client(setup):
    with pytest.raises(ValueError, match="attached merchant"):
        connect.start(setup.ui, "Dutch", market_trial=True)
    assert not setup.ui.connect_threads


@pytest.mark.parametrize(
    "owner", ["unassigned", "farmer", "other_merchant", "missing", "ambiguous"]
)
def test_existing_client_selection_never_steals_assigned_account(setup, owner):
    x = setup
    candidate = NS(identity={"pid": 20}, hwnd=30)
    windows = (
        []
        if owner == "missing"
        else [candidate, candidate]
        if owner == "ambiguous"
        else [candidate]
    )
    x.runtime.catalog = NS(windows=lambda: windows)
    observer = NS(adapter=NS(identity=candidate.identity))
    if owner == "farmer":
        x.ui.app.observer = observer
    if owner == "other_merchant":
        x.runtime.observers["Dutch"] = observer
    if owner == "unassigned":
        assert connect.select_client(x.ui, "Spiritual", 20) is candidate
    else:
        with pytest.raises(ValueError):
            connect.select_client(x.ui, "Spiritual", 20)


@pytest.mark.parametrize("progress", [False, True])
def test_movement_capability_is_written_only_after_verified_trial(
    tmp_path, monkeypatch, progress
):
    dimensions = {"client_size": [1000, 800], "gui_size": [1000, 800]}
    monkeypatch.setattr(connect, "geometry", lambda d: dimensions)
    driver = NS(
        qualification=tmp_path / "qualification.json",
        observer=NS(character="Dutch", adapter=NS(expected_sha256="pinned")),
    )
    original = lambda: None
    before = {"identity": {"pid": 2}, "map_id": 1002, "position": [10, 10]}
    after = {**before, "position": [18, 10] if progress else [10, 10]}
    travel = NS(qualify_movement=original, move=lambda *args: after)
    if progress:
        assert (
            connect.qualify_move(driver, travel, before, (438, 444), lambda: None)
            == after
        )
        assert read_json(driver.qualification)["capabilities"] == {
            "market_return": True
        }
    else:
        with pytest.raises(ValueError, match="progress"):
            connect.qualify_move(driver, travel, before, (438, 444), lambda: None)
        assert not driver.qualification.exists()
    assert travel.qualify_movement is original


def test_arriving_in_market_keeps_ordinary_work_held(setup, monkeypatch):
    x = setup
    observer = NS(adapter=NS(identity={"pid": 2}), health_layout=None)
    driver = NS(
        target=NS(hwnd=10),
        observer=observer,
        memory=NS(read_travel=lambda: {"map_id": 1036, "position": [100, 100]}),
    )
    x.runtime.observers["Dutch"] = observer
    x.runtime.controllers["Dutch"] = NS(driver=driver)
    x.runtime.return_drivers["Dutch"] = NS(
        read=lambda: {"map_id": 1036, "position": [100, 100]}
    )
    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: False)
    monkeypatch.setattr(
        "conquest.memory_life.read_life", lambda *a: NS(character="Dutch", map_id=1036)
    )
    monkeypatch.setattr("ctypes.windll.user32.GetAsyncKeyState", lambda key: 0)
    connect.run(x.ui, "Dutch", threading.Event(), 0)
    assert x.runtime.journal.get("Dutch", "connect_market")["phase"] == "market"
    assert x.runtime.journal.get("Dutch", "connect_hold") is True
    assert not x.runtime.enabled("Dutch") and not x.runtime.connecting


@pytest.mark.parametrize("logged_in", [False, True])
def test_market_movement_trial_never_logs_in_or_leaves_town(
    setup, monkeypatch, logged_in
):
    x = setup
    observer = NS(adapter=NS(identity={"pid": 2}), health_layout=None)
    driver = NS(target=NS(hwnd=10), observer=observer)
    x.runtime.observers["Dutch"] = observer
    x.runtime.controllers["Dutch"] = NS(driver=driver)
    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: not logged_in)
    monkeypatch.setattr(
        "conquest.reconnect.submit_login",
        lambda *a, **kw: pytest.fail("No trial login"),
    )
    monkeypatch.setattr(
        "conquest.memory_life.read_life",
        lambda *a: NS(character="Dutch", map_id=1002, dead_candidate=False),
    )
    monkeypatch.setattr("ctypes.windll.user32.GetAsyncKeyState", lambda key: 0)
    connect.run(x.ui, "Dutch", threading.Event(), 0, market_trial=True)
    result = x.runtime.journal.get("Dutch", "connect_market")
    assert result["phase"] == "failed"
    assert "Movement qualification" in result["note"]


def test_login_focus_failure_precedes_loading_credentials(monkeypatch):
    from conquest import reconnect

    calls = []
    monkeypatch.setattr(reconnect, "login_screen", lambda hwnd: True)
    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client",
        lambda hwnd, identity: calls.append((hwnd, identity)) or False,
    )
    monkeypatch.setattr(
        reconnect,
        "load_credentials",
        lambda *a: pytest.fail("No credential load before focus"),
    )
    session = NS(identity={"pid": 2}, assert_identity=lambda: calls.append("identity"))
    with pytest.raises(CaptureUnavailable, match="verified focus"):
        reconnect.submit_login(NS(hwnd=7), session=session)
    assert calls == ["identity", (7, {"pid": 2})]


@pytest.mark.parametrize("occupied", [False, True])
def test_stall_approach_rechecks_vacancy_before_moving(monkeypatch, tmp_path, occupied):
    s = {
        "identity": {"pid": 1},
        "map_id": 1036,
        "position": [200, 190],
        "silver": 1,
        "inventory": [],
        "booth": [],
        "booth_open": False,
        "own_booth_uid": 0,
    }
    flag = {"uid": 10, "position": [208, 190]}
    reads = [0]
    moves = []

    def flags(*args):
        reads[0] += 1
        return [] if occupied and reads[0] > 1 else [flag]

    monkeypatch.setattr("conquest.merchants.stalls.vacant_flags", flags)
    merchant_root = tmp_path / "Dutch installation"
    monkeypatch.setattr(
        "conquest.character_context.merchant_installation",
        lambda character: (
            merchant_root
            if character == "Dutch"
            else pytest.fail("Wrong character installation")
        ),
    )

    def terrain(root, map_id):
        assert root == merchant_root and map_id == 1036
        return object()

    monkeypatch.setattr("conquest.navigation.read_terrain", terrain)
    monkeypatch.setattr(
        "conquest.merchants.return_driver.stall_approach", lambda *a: (7, (206, 190))
    )
    driver = NS(
        memory=NS(read=lambda: dict(s)),
        observer=NS(character="Dutch"),
        require_qualified=lambda cap: {"shop_setup": {}},
    )

    def move(*a):
        moves.append(a[1])
        s["position"] = [206, 190]
        return s

    travel = NS(read=lambda: s, move=move)
    if occupied:
        with pytest.raises(CaptureUnavailable, match="occupied"):
            connect.approach_vacant_flag(driver, travel, lambda: None)
        assert not moves
    else:
        assert connect.approach_vacant_flag(driver, travel, lambda: None) == 10
        assert moves == [(206, 190)]


@pytest.mark.parametrize("near,changed", [(False, False), (True, False), (False, True)])
def test_stall_scouts_saved_area_without_claiming_or_ignoring_stock_change(
    monkeypatch, near, changed
):
    state = {
        "identity": {"pid": 1},
        "map_id": 1036,
        "position": [200, 190],
        "silver": 1,
        "inventory": [],
        "booth": [],
        "booth_open": False,
        "own_booth_uid": 0,
    }
    reads = [0]
    moves = []

    def read():
        reads[0] += 1
        return {**state, "silver": 2 if changed and reads[0] > 1 else 1}

    monkeypatch.setattr("conquest.merchants.stalls.vacant_flags", lambda *a: [])
    monkeypatch.setattr(
        "conquest.navigation.read_terrain", lambda *a: NS(travel_path=lambda *a: [])
    )
    driver = NS(
        memory=NS(read=read),
        observer=NS(character="Spiritual"),
        require_qualified=lambda cap: {"shop_setup": {}},
    )

    def move(before, target, check):
        moves.append(target)
        return {**state, "position": [212, 190]}

    travel = NS(read=lambda: state, move=move)
    with pytest.raises(ValueError):
        connect.approach_vacant_flag(
            driver, travel, lambda: None, preferred=[204 if near else 240, 190]
        )
    assert moves == ([] if near or changed else [[240, 190]])
