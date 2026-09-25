"""M1 login stage, login verification and stage limit: failure modes first.

This module was written before the code it tests (AGENTS.md testing rule).
Each row names a way automatic exact-1078 disconnect recovery could do harm,
replay input, or stall unattended; tests cite the rows they cover.

Fakes sit only at the game-process boundary: the merchant's process-memory
observer (identity and ownership reads), the native login code/GUI/error/form
readers, the Win32 login shell and focus calls, the SendInput login submitter,
and the farmer's safety observation. Profiles, credentials location, journal,
runtime, coordinator lease and alerts are the real implementations.

Login submission (part d)
  F1  Input while the client is not at a memory-proven login screen
      (title/class only, loading, or a rendered in-world frame).
  F2  A second login submission for one incident (at most one, ever).
  F3  Replaying a submission whose outcome is uncertain: an exception during
      submit_login, or an app restart after the durable journal write.
  F4  Sending any login input before login_attempted is durably journaled.
  F5  Loading credentials or sending input when the login error text is not
      the recognised disconnect text, or the error/form layout changed.
  F6  Missing encrypted credentials: no input; persistent block alerts.
  F7  Global Stop (coordinator stopped or app stop_event) ignored.
  F8  Operator manual handoff ignored.
  F9  A manual visitor session holding the merchant or Farmer ignored.
  F10 A pending merchant transaction ignored.
  F11 connect_hold (protective hold) ignored.
  F12 Physical mouse not idle (manual input priority) ignored.
  F13 Unsafe farmer: input without the farmer Off/parked/granted; unbounded
      handoff requests that could thrash a hunting farmer.
  F14 Another input owner active at the same time.
  F15 The purpose string "merchant_return_1078" alone authorizing input.
  F16 The native recovery capability leaking to another thread.
  F17 Submitting for a process whose identity differs from the incident.
  F18 Trading/refill Off stopping recovery (user decision: always runs).
  F19 Acting on a transient login frame (no settle time after arming).
  F20 A focus failure consuming the only attempt although nothing was sent.
  F21 The five-second protective-disconnect watchdog armed at login.
  F22 The hunting farmer granting input for anything but an exact pending
      native login request (submitted or blocked incidents never qualify).

Login verification and stage limit (part e)
  V1  Declaring login success on the login screen alone or one reading.
  V2  A different character in world treated as the lost merchant.
  V3  A different process in world treated as the lost merchant.
  V4  A stale first in-world reading combined with a much later one.
  V5  Returning to login between the two readings not resetting them.
  V6  Still at login long after the single submission: waits forever or
      retries, instead of stopping for attention.
  V7  Neither at login nor readable in world long after submission.
  V8  Travel, fare or stall input after verified login (M1 stops here).
  V9  Market arrival marked terminal "complete" without shop restoration.
  V10 A second disconnect after verified login causing another login.
  V11 The incident closing without evidence the shop was restored.
  V12 The login-verified #shops notice missing or repeated.
  V13 The pre-loss baseline overwritten while the incident is unresolved.

Detached process during an incident (found while building part e;
written before its code)
  D1  The merchant process exits or is replaced during an unresolved
      incident: the incident waits silently forever with no attention notice.
  D2  A brief detach (app restart and pinned rebind) wrongly escalating, or
      a detach time persisted across an app restart escalating on startup.
  D3  A stale "at the login screen" observation reported after the process
      is gone.

End to end
  E1  Healthy Market -> login detected -> incident armed -> one login
      submitted -> in-world verified -> stage limit reached, recorded as a
      repeatable JSON trace artifact that is re-read and checked.
"""

import json
import threading
from types import SimpleNamespace

import pytest

from conquest.capture import CaptureUnavailable
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants import return_1078
from conquest.merchants.coordination import InputCoordinator
from conquest.merchants.journal import Journal
from conquest.merchants.reader_1078 import ObservationUnavailable1078
from conquest.merchants.runtime import MerchantRuntime

HWND = 0x5150
IDENTITY = {
    "pid": 4100,
    "creation_time_100ns": 133000000000000000,
    "path": "C:/Co/ImConquer.exe",
}
FORM = ((114, 43), (114, 81), (114, 163))


def item(uid, price=None):
    return {
        "uid": uid,
        "type_id": 130805,
        "name": "Coat",
        "plus": 2,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
        "price": price,
    }


class Clock:
    def __init__(self):
        self.now = 1_900_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Game:
    """The merchant client process as seen through memory, Win32 and input."""

    def __init__(self, clock):
        self.clock = clock
        self.screen = "market"  # market | login | loading | world
        self.map_id = 1002  # In-world map after relog.
        self.character_uid = 1001
        self.identity = dict(IDENTITY)
        self.login_error = None  # None | unrecognized | layout | form
        self.login_gui = True  # Native Login window rendered this frame.
        self.focus = True
        self.after_submit = "world"
        self.submit_raises = None
        self.submits = []
        self.credentials_loaded = 0

    def snapshot(self):
        if self.screen in ("login", "loading"):
            raise ObservationUnavailable1078("1078 character identity is unavailable")
        market = self.screen == "market"
        return {
            "character": "Spiritual",
            "character_uid": self.character_uid,
            "identity": dict(self.identity),
            "server": "America",
            "inventory": [item(1)] if market else [item(1), item(2), item(3)],
            "booth": [item(2, 250000), item(3, 90000)] if market else [],
            "capacity": 40,
            "silver": 5000,
            "map_id": 1036 if market else self.map_id,
            "position": [180, 190] if market else [438, 380],
            "hp": 500,
            "health": {"current_hp_candidate": 500, "max_hp_candidate": 500},
            "own_booth_uid": 77001 if market else 0,
            "booth_open": market,
            "trade": None,
            "request": None,
            "timestamp": self.clock(),
        }


class Gui:
    def __init__(self, game):
        self.game = game

    def read(self, name):
        if name == "Login" and self.game.screen == "login" and self.game.login_gui:
            return SimpleNamespace(
                name="Login",
                address=0x7000,
                position=(10.0, 10.0),
                size=(208.0, 186.0),
                scroll=(0.0, 0.0),
            )
        raise ValueError("Requested GUI window is not active")


def boundary(monkeypatch, game, journal_state):
    """Patch only the game-process boundary (memory, Win32, SendInput)."""
    monkeypatch.setattr(
        "conquest.reconnect.login_screen",
        lambda hwnd: hwnd == HWND and game.screen == "login",
    )
    monkeypatch.setattr("conquest.login_1078.assert_code", lambda session: 0x1400)
    monkeypatch.setattr(
        "conquest.memory_shop.MemoryGui.for_session", lambda session: Gui(game)
    )

    class ErrorReader:
        def __init__(self, session):
            pass

        def read(self):
            if game.login_error == "unrecognized":
                raise ValueError("Unrecognized login error requires attention")
            if game.login_error == "layout":
                raise ValueError("Login error OK button layout changed")
            return None

    monkeypatch.setattr("conquest.reconnect.LoginErrorReader", ErrorReader)

    def form_points(session, window):
        if game.login_error == "form":
            raise ValueError("1078 login form layout changed")
        return FORM

    monkeypatch.setattr("conquest.login_1078.form_points", form_points)
    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client",
        lambda hwnd, identity: game.focus and hwnd == HWND,
    )

    def load_credentials(path):
        game.credentials_loaded += 1
        raise AssertionError("credentials must never be decrypted in these tests")

    monkeypatch.setattr("conquest.reconnect.load_credentials", load_credentials)

    def submit_login(target, credential_path, *, session=None):
        game.submits.append(
            {
                "at": game.clock(),
                "hwnd": target.hwnd,
                "credential_exists": credential_path.exists(),
                "journal": journal_state(),
                "identity": dict(session.identity),
            }
        )
        if game.submit_raises is not None:
            raise game.submit_raises
        game.screen = game.after_submit
        return {"submitted": True}

    monkeypatch.setattr("conquest.reconnect.submit_login", submit_login)


def make_observer(game):
    adapter = SimpleNamespace(
        identity=dict(IDENTITY),
        expected_sha256=CLIENT_SHA256_1078,
        assert_identity=lambda: None,
    )
    return SimpleNamespace(
        adapter=adapter,
        hwnd=HWND,
        lock=threading.RLock(),
        merchant_observation_only=True,
        character_context=None,
        operations=SimpleNamespace(target=SimpleNamespace(hwnd=HWND)),
        read_ownership=game.snapshot,
        close=lambda: None,
    )


@pytest.fixture
def x(tmp_path, monkeypatch):
    from conquest.character_context import merchant_context
    from conquest.character_profiles import ProfileRegistry

    registry = ProfileRegistry(tmp_path / "profiles")
    profile = registry.add("Spiritual", role="Merchant", character_uid=1001)
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(registry.root))
    context = merchant_context("Spiritual")
    context.credentials.parent.mkdir(parents=True, exist_ok=True)
    context.credentials.write_bytes(b"dpapi-ciphertext-never-decrypted-here")
    from conquest.merchants.journal import character_name

    character = character_name("Spiritual")
    clock = Clock()
    monkeypatch.setattr(return_1078, "_clock", clock)
    game = Game(clock)
    journal = Journal(tmp_path / "journal.sqlite3")
    farmer = {"safe": True, "mouse": False, "checks": 0, "error": None}
    coordinator = InputCoordinator(
        lambda: farmer["safe"], lambda: farmer["mouse"], path=tmp_path / "input.lock"
    )
    # The desktop UI's owner policy for this purpose (ui.owner_allowed).
    coordinator.owner_allowed = lambda c: (
        coordinator.purpose == return_1078.PURPOSE
        and coordinator.native_return1078_authorized(c)
    )
    runtime = MerchantRuntime(
        SimpleNamespace(identities=lambda: [], windows=lambda **_: []),
        coordinator,
        journal=journal,
        market_path=tmp_path / "market.json",
    )

    def farmer_check():
        farmer["checks"] += 1
        if farmer["error"]:
            raise CaptureUnavailable(farmer["error"])
        return {"pid": 1}

    runtime.native1078_farmer_check = farmer_check
    coordinator.surface_blocks[character] = True
    runtime.observers[character] = make_observer(game)
    boundary(monkeypatch, game, lambda: journal.get(character, return_1078.KEY) or {})
    return SimpleNamespace(
        rt=runtime,
        game=game,
        clock=clock,
        farmer=farmer,
        journal=journal,
        character=character,
        profile=profile,
        credentials=context.credentials,
        coordinator=coordinator,
    )


def tick(x, seconds=0):
    """One merchant-thread observation; returns the recorded wait/error text."""
    x.clock.advance(seconds)
    try:
        x.rt.step_observation_1078(x.character)
    except ValueError as error:  # CaptureUnavailable is a ValueError.
        return str(error)
    return None


def state(x):
    return x.journal.get(x.character, return_1078.KEY) or {}


def disconnect(x):
    """Healthy Market baseline, then the client falls back to its login."""
    assert tick(x) is None
    assert return_1078.baseline(x.rt, x.character) is not None
    x.game.screen = "login"
    tick(x, 1)
    assert state(x)["phase"] == "login"


def submit(x):
    disconnect(x)
    tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    assert len(x.game.submits) == 1


# --- Part d: one login submission --------------------------------------------


def test_login_title_without_native_login_gui_never_arms_or_submits(x):
    """F1"""
    assert tick(x) is None
    x.game.screen, x.game.login_gui = "login", False
    for _ in range(3):
        tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    assert state(x) == {} and x.game.submits == []
    x.game.login_gui = True
    tick(x, 1)
    assert state(x)["phase"] == "login"
    x.game.login_gui = False  # Loading frame: shell title, no Login window.
    for _ in range(3):
        tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    assert x.game.submits == [] and state(x)["login_attempted"] is False


def test_one_submission_after_settle_even_with_operations_off(x):
    """F2 F4 F18 F19 F21"""
    x.rt.enable(x.character, False)
    x.rt.set_refill_enabled(x.character, False)
    disconnect(x)
    tick(x, 1)
    assert x.game.submits == []  # F19: settle time after arming.
    tick(x, return_1078.LOGIN_SETTLE_SECONDS)
    [sent] = x.game.submits
    # F4: the durable journal already recorded the attempt before input.
    assert sent["journal"]["phase"] == "login_submitted"
    assert sent["journal"]["login_attempted"] is True
    assert sent["hwnd"] == HWND and sent["credential_exists"]
    assert sent["identity"] == IDENTITY
    assert x.game.credentials_loaded == 0
    x.game.screen = "login"  # Client is still (or again) at login.
    for _ in range(5):
        tick(x, 2)
    assert len(x.game.submits) == 1  # F2
    assert x.journal.get(x.character, "recovery_safety") is None  # F21
    assert not x.rt.enabled(x.character)  # F18: toggles unchanged.
    assert x.coordinator.owner is None


def test_still_at_login_after_the_submission_needs_attention(x):
    """F2 V6"""
    x.game.after_submit = "login"
    submit(x)
    tick(x, return_1078.LOGIN_OUTCOME_SECONDS - 5)
    assert state(x)["phase"] == "login_submitted"
    tick(x, 10)
    assert state(x)["phase"] == "needs_attention"
    for _ in range(3):
        tick(x, 30)
    assert len(x.game.submits) == 1


def test_interrupted_submission_is_never_replayed(x):
    """F3"""
    x.game.submit_raises = OSError("Login input was incomplete")
    x.game.after_submit = "login"
    disconnect(x)
    note = tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    assert "uncertain" in note
    saved = state(x)
    assert saved["phase"] == "login_submitted" and saved["login_attempted"] is True
    assert saved["login_submit_outcome"] == "interrupted_uncertain"
    x.game.submit_raises = None
    for _ in range(4):
        tick(x, 5)
    assert len(x.game.submits) == 1
    # An app restart after the durable write never replays either.
    restarted = MerchantRuntime(
        SimpleNamespace(identities=lambda: [], windows=lambda **_: []),
        x.coordinator,
        journal=x.journal,
        market_path=x.rt.market_path,
    )
    restarted.native1078_farmer_check = x.rt.native1078_farmer_check
    restarted.observers[x.character] = make_observer(x.game)
    x.rt = restarted
    for _ in range(3):
        tick(x, 5)
    assert len(x.game.submits) == 1


@pytest.mark.parametrize("fault", ["unrecognized", "layout", "form"])
def test_changed_login_error_or_layout_fails_closed_before_credentials(x, fault):
    """F5"""
    x.game.login_error = fault
    disconnect(x)
    for _ in range(3):
        note = tick(x, return_1078.LOGIN_SETTLE_SECONDS)
        assert note and ("changed" in note or "Unrecognized" in note)
    assert x.game.submits == [] and x.game.credentials_loaded == 0
    assert state(x)["login_attempted"] is False and state(x)["phase"] == "login"
    assert state(x)["blocker"]
    tick(x, return_1078.BLOCKED_ESCALATE_SECONDS)
    assert state(x)["phase"] == "needs_attention"
    assert x.game.submits == [] and x.game.credentials_loaded == 0


def test_missing_credentials_block_without_input(x):
    """F6"""
    x.credentials.unlink()
    disconnect(x)
    note = tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    assert "credentials" in note
    assert x.game.submits == [] and x.rt.handoff is None
    tick(x, return_1078.BLOCKED_ESCALATE_SECONDS + 1)
    assert state(x)["phase"] == "needs_attention"


def hold(x, name, active):
    from conquest.merchants.journal import character_name

    if name == "stop":
        x.coordinator.stop() if active else x.coordinator.resume()
    elif name == "stop_event":
        x.rt.stop_event.set() if active else x.rt.stop_event.clear()
    elif name == "manual_handoff":
        x.rt.manual_handoff_status = (
            (lambda: {"phase": "preparing"}) if active else (lambda: None)
        )
    elif name in ("manual_session", "farmer_session"):
        target = x.profile.id if name == "manual_session" else "Farmer"
        x.coordinator.set_manual_sessions(
            [{"target_profile_id": target, "holds_automation": True}] if active else []
        )
    elif name == "pending":
        if active:
            x.journal.begin("tx-hold", x.character, "listing", {"uid": 1})
        else:
            with x.journal.db() as db:
                db.execute("UPDATE transactions SET phase='aborted' WHERE id='tx-hold'")
    elif name == "connect_hold":
        x.journal.set(character_name("Spiritual"), "connect_hold", active)
    elif name == "mouse":
        x.farmer["mouse"] = active
    elif name == "owner":
        x.coordinator.owner = "Dutch" if active else None
    elif name == "farmer_unsafe":
        x.farmer["error"] = (
            "Farmer handoff rejected: nearby monster" if active else None
        )


@pytest.mark.parametrize(
    "name",
    [
        "stop",  # F7
        "stop_event",  # F7
        "manual_handoff",  # F8
        "manual_session",  # F9
        "farmer_session",  # F9
        "pending",  # F10
        "connect_hold",  # F11
        "mouse",  # F12
        "owner",  # F14
        "farmer_unsafe",  # F13
    ],
)
def test_holds_prevent_the_login_until_cleared(x, name):
    """F7 F8 F9 F10 F11 F12 F13 F14"""
    disconnect(x)
    hold(x, name, True)
    for _ in range(3):
        assert tick(x, return_1078.LOGIN_SETTLE_SECONDS) is not None
    assert x.game.submits == []
    assert state(x)["login_attempted"] is False and state(x)["phase"] == "login"
    hold(x, name, False)
    assert tick(x, 1) is None
    assert len(x.game.submits) == 1


def test_unsafe_farmer_gets_bounded_handoff_requests(x):
    """F13 F22"""
    from conquest.merchants.handoff import native_recovery_request

    x.farmer["safe"] = False
    disconnect(x)
    tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    first = x.rt.handoff
    assert first and first.startswith(f"merchant-recovery:{x.character}:")
    assert state(x)["handoff_request"] == first
    status = {
        "handoff_requested": first,
        "characters": {str(x.character): x.rt.status()[x.character]},
    }
    assert native_recovery_request(status) == str(x.character)
    tick(x, 2)
    assert x.rt.handoff == first and state(x)["handoff_requests"] == 1
    for count in (2, 3):
        x.rt.handoff = None  # The farmer released without a usable grant.
        tick(x, 2)
        assert state(x)["handoff_requests"] == count
    x.rt.handoff = None
    tick(x, 2)
    assert state(x)["phase"] == "needs_attention"
    assert x.rt.handoff is None and x.game.submits == []


def test_merchant_side_block_never_interrupts_the_farmer(x):
    """F13: a handoff is requested only once every merchant-side proof passes."""
    x.farmer["safe"] = False
    x.game.login_error = "unrecognized"
    disconnect(x)
    for _ in range(3):
        tick(x, return_1078.LOGIN_SETTLE_SECONDS)
    assert x.rt.handoff is None and "handoff_request" not in state(x)


def test_focus_failure_consumes_no_attempt(x):
    """F20"""
    x.game.focus = False
    disconnect(x)
    assert "focus" in tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    assert x.game.submits == [] and state(x)["login_attempted"] is False
    x.game.focus = True
    tick(x, 1)
    assert len(x.game.submits) == 1


def test_process_identity_change_blocks_the_login(x):
    """F17"""
    disconnect(x)
    x.rt.observers[x.character].adapter.identity = {**IDENTITY, "pid": 9999}
    tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    assert state(x)["phase"] == "needs_attention" and x.game.submits == []


def test_purpose_string_alone_never_authorizes_input(x):
    """F15 F16"""
    coordinator = x.coordinator
    disconnect(x)
    with pytest.raises(CaptureUnavailable):
        # A plain lease with the purpose string is refused by owner policy.
        with coordinator.lease(x.character, purpose=return_1078.PURPOSE):
            pytest.fail("purpose string alone entered the lease body")
    coordinator.purpose = return_1078.PURPOSE
    try:
        assert not coordinator.native_return1078_authorized(x.character)
        assert not coordinator.native_return1078_bound()
    finally:
        coordinator.purpose = None
    seen = {}
    with coordinator.native_return1078_scope(x.character, lambda: True):
        coordinator.purpose = return_1078.PURPOSE
        try:
            assert coordinator.native_return1078_bound(x.character)
            assert coordinator.native_return1078_authorized(x.character)
            assert not coordinator.native_return1078_bound("Dutch")

            def other_thread():
                seen["bound"] = coordinator.native_return1078_bound()
                seen["authorized"] = coordinator.native_return1078_authorized(
                    x.character
                )

            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join()
        finally:
            coordinator.purpose = None
    assert seen == {"bound": False, "authorized": False}
    # policy() itself also rejects an unbound caller.
    assert return_1078.policy(x.rt, x.character) is False


def test_farmer_safe_accepts_recovery_purpose_only_when_thread_bound(monkeypatch):
    """F15 F16: listing_handoff_1078.farmer_safe."""
    from conquest.merchants import listing_handoff_1078

    coordinator = InputCoordinator(lambda: True)
    ui = SimpleNamespace(
        grant=None,
        coordinator=coordinator,
        safe_to_yield=lambda: True,
        app=SimpleNamespace(control=SimpleNamespace(snapshot=lambda: {})),
    )
    monkeypatch.setattr(
        listing_handoff_1078,
        "_safe_health",
        lambda ui, control, expected, grant_check: (
            grant_check(),
            {"target": {"pid": 1}},
        )[1],
    )
    coordinator.purpose = return_1078.PURPOSE
    with pytest.raises(CaptureUnavailable):
        listing_handoff_1078.farmer_safe(ui)
    with coordinator.native_return1078_scope("Spiritual", lambda: True):
        assert listing_handoff_1078.farmer_safe(ui) == {"pid": 1}
    coordinator.purpose = None


def test_hunting_farmer_grants_only_exact_pending_native_login():
    """F22"""
    from conquest.merchants.handoff import (
        native_recovery_request,
        service_candidate,
        urgent_recovery,
    )

    native = {
        "id": "inc",
        "phase": "login",
        "login_attempted": False,
        "authorization": return_1078.AUTHORIZATION,
        "handoff_request": "merchant-recovery:Spiritual:123",
    }
    row = {"connected": False, "enabled": False, "native_return_1078": native}
    status = {
        "handoff_requested": "merchant-recovery:Spiritual:123",
        "characters": {"Spiritual": row},
    }
    assert native_recovery_request(status) == "Spiritual"
    assert urgent_recovery(status) and service_candidate(row)
    for change in (
        {"phase": "login_submitted", "login_attempted": True},
        {"phase": "needs_attention"},
        {"handoff_request": "merchant-recovery:Spiritual:999"},
        {"authorization": "explicit_one_time_relog_to_market"},
    ):
        changed = {"Spiritual": {**row, "native_return_1078": {**native, **change}}}
        other = {**status, "characters": changed}
        assert native_recovery_request(other) is None
        assert not urgent_recovery(other)
        # service_candidate sees one row, not the request key; the exact-key
        # gate is native_recovery_request/urgent_recovery above.
        if "handoff_request" not in change:
            assert not service_candidate(changed["Spiritual"])
    assert native_recovery_request({**status, "handoff_requested": "x"}) is None


# --- Part e: verified login and the M1 stage limit ---------------------------


def test_login_is_verified_only_by_two_stable_world_readings(x):
    """V1 V13"""
    submit(x)
    before = x.journal.get(x.character, return_1078.BASELINE)
    tick(x, 0.2)
    assert state(x)["phase"] == "login_submitted"
    tick(x, 0.5)
    assert state(x)["phase"] == "login_submitted"
    tick(x, 1.0)
    saved = state(x)
    assert saved["phase"] == "logged_in_awaiting_return"
    assert saved["stage_limit"] == "login" and saved["login_by"] == "automatic"
    first, second = saved["login_verified"]["first"], saved["login_verified"]["second"]
    assert first["character_uid"] == second["character_uid"] == 1001
    assert first["identity"] == second["identity"] == IDENTITY
    assert second["timestamp"] - first["timestamp"] >= 1
    assert x.journal.get(x.character, return_1078.BASELINE) == before  # V13


def test_other_character_in_world_is_not_the_lost_merchant(x):
    """V2"""
    x.game.after_submit = "world"
    submit(x)
    x.game.character_uid = 2002
    tick(x, 1)
    tick(x, 2)
    assert state(x)["phase"] == "needs_attention"


def test_other_process_in_world_is_not_the_lost_merchant(x):
    """V3"""
    submit(x)
    x.game.identity = {**IDENTITY, "pid": 9999}
    tick(x, 1)
    tick(x, 2)
    assert state(x)["phase"] == "needs_attention"


def test_stale_first_reading_restarts_verification(x):
    """V4"""
    submit(x)
    tick(x, 1)
    tick(x, return_1078.VERIFY_MAX_SECONDS + 5)
    assert state(x)["phase"] == "login_submitted"
    tick(x, 2)
    assert state(x)["phase"] == "logged_in_awaiting_return"


def test_login_between_readings_resets_verification(x):
    """V5"""
    submit(x)
    tick(x, 1)
    x.game.screen = "login"
    tick(x, 1)
    x.game.screen = "world"
    tick(x, 1)
    assert state(x)["phase"] == "login_submitted"
    tick(x, 2)
    assert state(x)["phase"] == "logged_in_awaiting_return"
    assert len(x.game.submits) == 1


def test_unreadable_after_submission_stops_for_attention(x):
    """V7"""
    x.game.after_submit = "loading"
    submit(x)
    tick(x, return_1078.LOADING_SECONDS - 10)
    assert state(x)["phase"] == "login_submitted"
    tick(x, 20)
    assert state(x)["phase"] == "needs_attention"
    assert len(x.game.submits) == 1


def verified(x):
    submit(x)
    tick(x, 1)
    tick(x, 2)
    assert state(x)["phase"] == "logged_in_awaiting_return"


def test_stage_limit_sends_no_travel_input_after_verified_login(x, monkeypatch):
    """V8"""
    verified(x)
    monkeypatch.setattr(
        return_1078,
        "ReturnDriver1078",
        lambda *a, **k: pytest.fail("M1 must not construct the travel driver"),
    )
    assert return_1078.step(x.rt, x.character) is False
    for _ in range(3):
        tick(x, 5)
    assert state(x)["phase"] == "logged_in_awaiting_return"
    assert x.coordinator.owner is None and len(x.game.submits) == 1


def test_market_arrival_is_not_terminal_without_shop_restoration(x):
    """V9"""
    verified(x)
    saved = state(x)
    return_1078.market_arrived(x.rt, x.character, saved, x.game.snapshot())
    after = state(x)
    assert after["phase"] == "market_arrived" and after["shop_restored"] is False
    assert "market_arrived" not in return_1078.TERMINAL
    assert return_1078.unresolved(x.rt, x.character)["id"] == saved["id"]


def test_second_disconnect_after_verified_login_does_not_log_in_again(x):
    """V10"""
    verified(x)
    x.game.screen = "login"
    for _ in range(3):
        tick(x, return_1078.LOGIN_SETTLE_SECONDS + 1)
    assert state(x)["phase"] == "needs_attention"
    assert len(x.game.submits) == 1


def test_incident_closes_only_on_two_healthy_owned_market_readings(x):
    """V11 V13"""
    verified(x)
    before = x.journal.get(x.character, return_1078.BASELINE)
    x.game.screen = "market"  # Operator walked back and reopened the shop.
    tick(x, 1)
    assert state(x)["phase"] == "logged_in_awaiting_return"
    assert x.journal.get(x.character, return_1078.BASELINE) == before
    tick(x, 2)
    assert state(x)["phase"] == "restored_observed"
    assert return_1078.unresolved(x.rt, x.character) is None
    tick(x, 2)
    after = x.journal.get(x.character, return_1078.BASELINE)
    assert after["recorded_at"] > before["recorded_at"]


def test_login_verified_notice_is_sent_once(x):
    """V12"""
    from conquest.merchants.alerts import Alerts

    verified(x)
    alerts = Alerts()
    for _ in range(3):
        row = x.rt.status()[x.character]
        alerts.poll({"characters": {"Spiritual": row}}, x.clock())
        x.clock.advance(5)
    notices = [m for m in alerts.state["queue"] if m["kind"] == "notice"]
    assert len(notices) == 1
    assert "not yet automated" in notices[0]["content"]


# --- E1: end-to-end scenario with a repeatable artifact ----------------------


def test_e2e_market_to_verified_login_stops_at_stage_limit(x, tmp_path, monkeypatch):
    """E1"""
    from conquest.merchants.alerts import Alerts

    monkeypatch.setattr(
        return_1078,
        "ReturnDriver1078",
        lambda *a, **k: pytest.fail("M1 must not construct the travel driver"),
    )
    alerts = Alerts()
    trace = []

    def record(label, seconds=0):
        note = tick(x, seconds)
        saved = state(x)
        row = x.rt.status()[x.character]
        alerts.poll({"characters": {"Spiritual": row}}, x.clock())
        trace.append(
            {
                "step": label,
                "screen": x.game.screen,
                "note": note,
                "phase": saved.get("phase"),
                "incident": bool(saved.get("id")),
                "login_attempted": saved.get("login_attempted"),
                "submits": len(x.game.submits),
                "credentials_loaded": x.game.credentials_loaded,
                "baseline_sha256": (
                    x.journal.get(x.character, return_1078.BASELINE) or {}
                ).get("sha256"),
                "recovery_safety": x.journal.get(x.character, "recovery_safety"),
                "input_owner": x.coordinator.owner,
                "shops": [
                    [m["kind"], m["subject"]]
                    for m in alerts.state["queue"]
                    if m["subject"].startswith("Spiritual")
                ],
            }
        )

    x.rt.enable(x.character, False)  # Recovery runs with trading Off.
    record("healthy_market")
    x.game.screen = "login"
    record("login_detected", 1)
    record("settling", 2)
    record("login_submitted", return_1078.LOGIN_SETTLE_SECONDS)
    record("first_world_reading", 1)
    record("second_world_reading", 1.5)
    record("stage_limit_hold", 5)
    assert return_1078.step(x.rt, x.character) is False
    record("stage_limit_hold_again", 5)
    events = [
        e["event"]
        for e in x.journal.events(0, 1000)
        if e["event"].startswith("native_")
    ]
    artifact = tmp_path / "m1-e2e-trace.json"
    artifact.write_text(
        json.dumps({"trace": trace, "events": events}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    replay = json.loads(artifact.read_text(encoding="utf-8"))
    assert replay == {"trace": trace, "events": events}
    assert [row["phase"] for row in replay["trace"]] == [
        None,
        "login",
        "login",
        "login_submitted",
        "login_submitted",
        "logged_in_awaiting_return",
        "logged_in_awaiting_return",
        "logged_in_awaiting_return",
    ]
    assert [row["submits"] for row in replay["trace"]] == [0, 0, 0, 1, 1, 1, 1, 1]
    assert all(row["credentials_loaded"] == 0 for row in replay["trace"])
    assert all(row["recovery_safety"] is None for row in replay["trace"])
    assert all(row["input_owner"] is None for row in replay["trace"])
    assert len({row["baseline_sha256"] for row in replay["trace"]}) == 1
    assert replay["events"] == [
        "native_return_started",
        "native_return_login_submitted",
        "native_return_login_verified",
    ]
    shops = replay["trace"][-1]["shops"]
    assert shops[0] == ["failure", "Spiritual"]
    assert ["notice", "Spiritual recovery"] in shops
    assert [kind for kind, _ in shops].count("notice") == 1


# --- D: detached process during an incident (failure modes first) -------------

detached_pending = pytest.mark.xfail(
    strict=True, reason="Failure modes written first; detach handling not built yet"
)


def process_gone(x):
    """The exact merchant process exited: its identity can no longer be proven."""

    def gone():
        raise OSError("Merchant process identity changed or exited")

    x.rt.observers[x.character].adapter.assert_identity = gone


@detached_pending
def test_detached_process_during_an_incident_stops_for_attention(x):
    """D1"""
    disconnect(x)
    process_gone(x)
    assert "found 0" in tick(x, 1)
    assert x.character not in x.rt.observers
    tick(x, return_1078.LOADING_SECONDS - 10)
    assert state(x)["phase"] == "login"
    tick(x, 20)
    assert state(x)["phase"] == "needs_attention"
    assert "no longer observable" in state(x)["note"]
    assert x.game.submits == []


@detached_pending
def test_brief_detach_or_app_restart_does_not_escalate(x):
    """D2"""
    disconnect(x)
    process_gone(x)
    tick(x, 1)
    tick(x, 30)
    x.rt.observers[x.character] = make_observer(x.game)  # Pinned rebind.
    tick(x, 1)
    assert state(x)["phase"] == "login_submitted" and len(x.game.submits) == 1
    tick(x, 1)
    tick(x, 2)
    assert state(x)["phase"] == "logged_in_awaiting_return"
    # A detach before an app restart is not carried into the new process.
    process_gone(x)
    tick(x, 1)
    restarted = MerchantRuntime(
        SimpleNamespace(identities=lambda: [], windows=lambda **_: []),
        x.coordinator,
        journal=x.journal,
        market_path=x.rt.market_path,
    )
    restarted.native1078_farmer_check = x.rt.native1078_farmer_check
    x.rt = restarted
    x.clock.advance(return_1078.LOADING_SECONDS * 3)
    tick(x, 1)
    assert state(x)["phase"] == "logged_in_awaiting_return"
    tick(x, return_1078.LOADING_SECONDS + 1)
    assert state(x)["phase"] == "needs_attention"


@detached_pending
def test_login_observation_is_dropped_with_the_process(x):
    """D3"""
    assert tick(x) is None
    x.journal.set(x.character, return_1078.BASELINE, None)
    x.game.screen = "login"
    tick(x, 1)
    assert x.rt.login1078_status[x.character]["reason"]
    assert x.rt.status()[x.character]["login_1078"]["at_login"] is True
    process_gone(x)
    tick(x, 1)
    assert x.character not in x.rt.login1078_status
    assert x.rt.status()[x.character]["login_1078"] is None
