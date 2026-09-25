"""End-to-end: one merchant's repeatedly failing refill check starved the other.

Live 2026-09-25 14:18:48-14:53: every merchant observer thread ends its tick
with refill_1078.step, admitted only by a non-blocking acquire of the shared
runtime.listing1078_lock. Spiritual's check was overdue and, on every tick,
ran the heavy owned-peer plan (fresh peer memory observations, saved prices,
receipt scans, restoration preview) before raising "Pre-disconnect merchant
ownership differs from current stock" -> refill_precondition_unavailable,
immediately due again on its next tick. Dutch's result was
('waiting', 'other_refill_check_active') in 60/60 one-second samples, so it
never reported eligible_backlog and each 15-minute merchant stop ended at
once; its 4 free slots and 6 queued items never listed. The bridge status
could not show who held the lock or since when Dutch had been refused.
Measured afterwards (14:53-14:54), the mirror image: Dutch's now-successful
but equally heavy waiting_farmer_handoff check (~3.5 s per tick) refused
Spiritual in 29/40 samples.

Real code under test: MerchantRuntime (its listing1078_lock and read-only
lock status), refill_1078.step (the whole admitted body: schedule, capacity,
backlog, request_handoff, exception handling, finally-release), Journal and
RefillSchedule on a real SQLite file, and real threads named like the
runtime's observer threads (merchant-<name>), each looping step -> record ->
wait like MerchantRuntime.run. Fakes only at boundaries: memory snapshots,
the capability receipt check, the owned-peer plan (a sleep standing in for
its peer memory reads, then the live ValueError for Spiritual; a two-item
priced queue for Dutch) and release_completed_refill (no grant exists).
The live observer ticks were phase-locked so Dutch's attempts always landed
inside Spiritual's hold; the scenario models that worst case directly:
Spiritual re-steps immediately after each release.

Failure modes, written before the fix:
 F1 Starvation: while one merchant's check holds the lock for nearly all of
    every tick, the other merchant is refused indefinitely. Required: it is
    admitted within a bounded wait (one holder check + one own tick), keeps
    getting turns, and reports its eligible_backlog; the busy merchant is not
    starved in return.
 F2 Invisible holder: a read-only status cannot show which merchant/thread
    holds the refill lock, since when, and at which stage inside step
    (for example "plan").
 F3 No refusal age: a refused result lacks blocked_since, or blocked_since
    changes between consecutive refusals / survives an admission.
 F4 A step that raises (an exception step() does not catch) leaves the lock
    held or the holder recorded, blocking every later check.
 F5 Two admitted steps ever run concurrently (fairness must only add
    refusals; the single lock stays the only admission).
 F6 Fairness deadlocks or waits forever for a waiter that stopped trying: a
    holder must yield to a fresh waiter, but a waiter that has not retried
    within WAIT_TTL no longer holds anyone back.
 F7 The merchant holding the live listing grant is made to yield its bounded
    listing window to the other merchant's check.
 F8 A refused/yielded check blocks or sleeps (it must return at once so trade
    and request handling in the same tick are not delayed).
 F9 The artifact is not repeatable.

The scenario writes <tmp>/<run>/refill-lock-fairness.json, re-reads it for
the assertions, and runs twice to prove the artifact is byte-identical.
"""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from conquest.merchants import listing_handoff_1078, listing_plan_1078, refill_1078
from conquest.merchants.coordination import InputCoordinator
from conquest.merchants.journal import Journal
from conquest.merchants.runtime import MerchantRuntime

NAMES = ("Spiritual", "Dutch")
HOLD = 0.25  # Spiritual's heavy failing check, per tick
DUTCH_GAP = 0.05  # Dutch's short tick between attempts
BOUND = 2.0  # generous bound on any refusal streak (expected ~HOLD + DUTCH_GAP)
PHASE_DEADLINE = 8.0
TURNS = 3
REFUSED = ("other_refill_check_active", "refill_turn_yielded")
LIVE_ERROR = "Pre-disconnect merchant ownership differs from current stock"


def item(uid):
    return {
        "uid": uid,
        "type_id": 130805,
        "name": "Coat",
        "plus": 2,
        "gem1": 0,
        "gem2": 0,
        "bound": False,
        "quantity": 1,
        "slot": 0,
        "price": None,
    }


def snapshot(character):
    return {
        "character": character,
        "identity": {"pid": 1, "creation_time_100ns": 2, "path": "C:/ImConquer.exe"},
        "character_uid": 8 if character == "Dutch" else 9,
        "map_id": 1036,
        "hp": 900,
        "own_booth_uid": 18,
        "booth_open": True,
        "booth": [],
        "inventory": [item(91), item(92)],
        "trade": None,
        "request": None,
        "timestamp": time.time(),
    }


class Plan:
    """Owned-peer plan stand-in; also detects two admitted bodies at once."""

    def __init__(self):
        self.inside = 0
        self.max_inside = 0
        self.guard = threading.Lock()
        self.modes = {"Spiritual": "hold_fail", "Dutch": "queue"}
        self.gates = {}

    def __call__(self, runtime, character, snapshot):
        with self.guard:
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
        try:
            mode = self.modes[character]
            gate = self.gates.get(character)
            if gate is not None:
                gate["entered"].set()
                assert gate["release"].wait(10)
            if mode == "hold_fail":
                time.sleep(HOLD)
                raise ValueError(LIVE_ERROR)
            if mode == "fail":
                raise ValueError(LIVE_ERROR)
            if mode == "crash":
                raise RuntimeError("unexpected failure inside the refill check")
            return [{"uid": 92, "price": 90000}, {"uid": 91, "price": 50000}]
        finally:
            with self.guard:
                self.inside -= 1


def lock_status(runtime):
    method = getattr(runtime, "refill1078_lock_status", None)
    return method() if method else None


def make_rig(root, monkeypatch, plan):
    root.mkdir(parents=True)
    journal = Journal(root / "journal.sqlite3")
    coordinator = InputCoordinator(lambda: True, path=root / "input.lock")
    catalog = SimpleNamespace(identities=list, windows=lambda **_: [])
    runtime = MerchantRuntime(catalog, coordinator, journal=journal)
    for name in NAMES:
        journal.set(
            name,
            "refill",
            {
                "pending": False,
                "next_check": 0,
                "interval_seconds": 900,
                "last_checked": None,
                "status": "waiting",
            },
        )
        journal.set(name, "refill_enabled", True)
    ui = SimpleNamespace(
        runtime=runtime,
        coordinator=coordinator,
        grant=None,
        safe_to_yield=lambda: True,
        app=SimpleNamespace(
            control=SimpleNamespace(
                snapshot=lambda: {"enabled": True, "paused": False, "revision": 1}
            )
        ),
    )
    # Exactly how MerchantUI binds the observer threads' refill step.
    runtime.refill1078_step = lambda character, snap: refill_1078.step(
        ui, character, snap
    )
    monkeypatch.setattr(listing_plan_1078, "plan", plan)
    monkeypatch.setattr(refill_1078, "require", lambda *a: None)
    monkeypatch.setattr(
        listing_handoff_1078, "release_completed_refill", lambda *a: False
    )
    return SimpleNamespace(runtime=runtime, ui=ui, journal=journal)


def refused(result):
    return result.get("blocker") in REFUSED


def streaks(log, end):
    """Seconds from each first refusal to the next admission (or the end)."""
    waits, start = [], None
    for wall, _, result in log:
        if refused(result):
            start = wall if start is None else start
        elif start is not None:
            waits.append(wall - start)
            start = None
    if start is not None:
        waits.append(end - start)
    return waits


def blocked_since_consistent(log):
    current = None
    for wall, duration, result in log:
        if not refused(result):
            current = None
            continue
        since = result.get("blocked_since")
        if not isinstance(since, float):
            return False
        if current is None:
            if not wall - 0.01 <= since <= wall + duration + 0.01:
                return False
            current = since
        elif since != current:
            return False
    return True


def starvation_phase(root, monkeypatch):
    """F1/F2/F3/F5/F8 with two real observer threads."""
    plan = Plan()
    rig = make_rig(root / "starvation", monkeypatch, plan)
    runtime = rig.runtime
    stop = threading.Event()
    logs = {name: [] for name in NAMES}
    gaps = {"Spiritual": 0.0, "Dutch": DUTCH_GAP}
    samples = {"holders": 0, "keys": set(), "seen": set(), "threads_match": True}

    def admitted(name):
        return sum(1 for _, _, result in logs[name] if not refused(result))

    def observer(name):
        while not stop.is_set():
            wall, began = time.time(), time.perf_counter()
            result = runtime.refill1078_step(name, snapshot(name))
            runtime.refill1078_status[name] = result
            logs[name].append((wall, time.perf_counter() - began, result))
            stop.wait(gaps[name])

    def reader():
        while not stop.is_set():
            status = lock_status(runtime)
            holder = (status or {}).get("holder")
            if holder:
                samples["holders"] += 1
                samples["keys"].add(tuple(sorted(holder)))
                samples["seen"].add((holder.get("character"), holder.get("stage")))
                if holder.get("thread") != "merchant-" + str(holder.get("character")):
                    samples["threads_match"] = False
            stop.wait(0.01)

    threads = [
        threading.Thread(target=observer, args=(name,), name=f"merchant-{name}")
        for name in NAMES
    ] + [threading.Thread(target=reader, name="bridge-status-reader")]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + PHASE_DEADLINE
    while time.monotonic() < deadline:
        if all(admitted(name) >= TURNS for name in NAMES):
            break
        time.sleep(0.02)
    stop.set()
    for thread in threads:
        thread.join(10)
    end = time.time()
    final = lock_status(runtime) or {}
    by_name = {}
    for name in NAMES:
        log = logs[name]
        kinds = sorted(
            {
                (result["state"], result.get("blocker"), result.get("eligible_backlog"))
                for _, _, result in log
                if not refused(result)
            },
            key=str,
        )
        waits = streaks(log, end)
        by_name[name] = {
            "reached_turns": admitted(name) >= TURNS,
            "admitted_results": [list(kind) for kind in kinds],
            "max_refusal_streak_within_bound": not waits or max(waits) <= BOUND,
            "refusal_blockers_known": all(
                result.get("blocker") in REFUSED
                for _, _, result in log
                if result.get("state") == "waiting" and refused(result)
            ),
            "refused_at_least_once": any(refused(result) for _, _, result in log),
            "blocked_since_consistent": blocked_since_consistent(log),
            # F8, structurally rather than by wall clock (full-suite CPU load
            # delays threads by >0.5 s): a lock refusal exists at all only
            # if admission never waits (a blocking acquire would eventually
            # succeed instead), and each one was returned while the OTHER
            # merchant held the lock, never this one.
            "refusals_nonblocking": all(
                (result.get("lock_holder") or {}).get("character") != name
                for _, _, result in log
                if result.get("blocker") == "other_refill_check_active"
            ),
        }
    return {
        "merchants": by_name,
        "concurrent_admitted_bodies": plan.max_inside > 1,
        "lock_refusal_seen": any(
            result.get("blocker") == "other_refill_check_active"
            for name in NAMES
            for _, _, result in logs[name]
        ),
        "status_showed_holder": samples["holders"] > 0,
        "status_holder_fields": [list(keys) for keys in sorted(samples["keys"])],
        "status_showed_spiritual_in_plan": ("Spiritual", "plan") in samples["seen"],
        "status_holder_thread_is_observer": samples["holders"] > 0
        and samples["threads_match"],
        "lock_free_after_threads_stop": not runtime.listing1078_lock.locked(),
        "status_holder_cleared_after_stop": final.get("holder", "missing") is None,
    }


def raise_phase(root, monkeypatch):
    """F4: an uncaught exception still releases the lock and clears the holder."""
    plan = Plan()
    rig = make_rig(root / "raise", monkeypatch, plan)
    runtime = rig.runtime
    plan.modes["Spiritual"] = "crash"
    try:
        runtime.refill1078_step("Spiritual", snapshot("Spiritual"))
        propagated = False
    except RuntimeError:
        propagated = True
    status = lock_status(runtime) or {}
    after = runtime.refill1078_step("Dutch", snapshot("Dutch"))
    return {
        "exception_propagates_to_observer_loop": propagated,
        "lock_released": not runtime.listing1078_lock.locked(),
        "holder_cleared": status.get("holder", "missing") is None,
        "next_check_admitted": [after["state"], after.get("blocker")],
    }


def hold_in_thread(runtime, plan, name):
    gate = {"entered": threading.Event(), "release": threading.Event()}
    plan.gates[name] = gate
    out = {}
    thread = threading.Thread(
        target=lambda: out.update(result=runtime.refill1078_step(name, snapshot(name))),
        name=f"merchant-{name}",
    )
    thread.start()
    assert gate["entered"].wait(10)
    return thread, gate, out


def yield_phase(root, monkeypatch):
    """F6: yield to a fresh waiter; a waiter that stopped trying expires."""
    with pytest.MonkeyPatch.context() as patch:
        try:
            from conquest.merchants import refill_turns_1078
        except ImportError:  # pre-fix code: no fairness module
            pass
        else:
            patch.setattr(refill_turns_1078, "WAIT_TTL", 0.3)
        return _yield_phase(root, monkeypatch)


def _yield_phase(root, monkeypatch):
    plan = Plan()
    plan.modes["Spiritual"] = "fail"
    rig = make_rig(root / "yield", monkeypatch, plan)
    runtime = rig.runtime
    thread, gate, _ = hold_in_thread(runtime, plan, "Spiritual")
    waiter = runtime.refill1078_step("Dutch", snapshot("Dutch"))
    status = lock_status(runtime) or {}
    gate["release"].set()
    thread.join(10)
    plan.gates.clear()
    again = runtime.refill1078_step("Spiritual", snapshot("Spiritual"))
    time.sleep(0.45)  # Dutch stopped trying: longer than WAIT_TTL
    later = runtime.refill1078_step("Spiritual", snapshot("Spiritual"))
    holder = status.get("holder") or {}
    waiting = (status.get("waiting") or {}).get("Dutch") or {}
    return {
        "waiter_refused_while_held": waiter.get("blocker"),
        "refusal_names_holder": (waiter.get("lock_holder") or {}).get("character"),
        "status_holder_during_hold": [holder.get("character"), holder.get("stage")],
        "status_lists_waiter_blocked_since": waiting.get("blocked_since")
        == waiter.get("blocked_since")
        and isinstance(waiter.get("blocked_since"), float),
        "holder_yields_to_fresh_waiter": again.get("blocker"),
        "stale_waiter_expires": later.get("blocker"),
    }


def grant_phase(root, monkeypatch):
    """F7: the live listing-grant holder is never made to yield its window."""
    plan = Plan()
    plan.modes["Spiritual"] = "fail"
    rig = make_rig(root / "grant", monkeypatch, plan)
    runtime, ui = rig.runtime, rig.ui
    ui.grant = {"character": "Dutch", "scope": "listing_1078"}
    thread, gate, _ = hold_in_thread(runtime, plan, "Dutch")
    other = runtime.refill1078_step("Spiritual", snapshot("Spiritual"))
    gate["release"].set()
    thread.join(10)
    plan.gates.clear()
    holder_again = runtime.refill1078_step("Dutch", snapshot("Dutch"))
    ui.grant = None
    other_next = runtime.refill1078_step("Spiritual", snapshot("Spiritual"))
    return {
        "other_refused_during_grant_check": other.get("blocker"),
        "grant_holder_next_check": [holder_again["state"], holder_again.get("blocker")],
        "other_admitted_after": [other_next["state"], other_next.get("blocker")],
    }


def run_scenario(root, monkeypatch):
    artifact = {
        "scenario": {
            "hold_seconds": HOLD,
            "dutch_gap_seconds": DUTCH_GAP,
            "bound_seconds": BOUND,
            "turns": TURNS,
            "live_error": LIVE_ERROR,
        },
        "starvation": starvation_phase(root, monkeypatch),
        "raise": raise_phase(root, monkeypatch),
        "yield": yield_phase(root, monkeypatch),
        "grant": grant_phase(root, monkeypatch),
    }
    path = root / "refill-lock-fairness.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_busy_refill_check_cannot_starve_the_other_merchant(tmp_path, monkeypatch):
    first = run_scenario(tmp_path / "first", monkeypatch)
    second = run_scenario(tmp_path / "second", monkeypatch)
    artifact = json.loads(first.read_text(encoding="utf-8"))
    starvation = artifact["starvation"]
    dutch = starvation["merchants"]["Dutch"]
    spiritual = starvation["merchants"]["Spiritual"]

    # F1: Dutch gets bounded, repeated turns and reports its backlog; the busy
    # merchant keeps checking too.
    assert dutch["refused_at_least_once"] is True
    assert dutch["max_refusal_streak_within_bound"] is True
    assert dutch["reached_turns"] is True
    assert dutch["admitted_results"] == [["waiting", "waiting_farmer_handoff", 2]]
    assert spiritual["reached_turns"] is True
    assert spiritual["max_refusal_streak_within_bound"] is True
    assert spiritual["admitted_results"] == [
        ["waiting", "refill_precondition_unavailable", None]
    ]
    # F2: read-only status names holder, observer thread and stage.
    assert starvation["status_showed_holder"] is True
    assert starvation["status_holder_fields"] == [
        ["acquired_at", "character", "stage", "stage_at", "thread"]
    ]
    assert starvation["status_showed_spiritual_in_plan"] is True
    assert starvation["status_holder_thread_is_observer"] is True
    assert starvation["status_holder_cleared_after_stop"] is True
    # F3 / F8: refusals carry a stable age and never block.
    for merchant in (dutch, spiritual):
        assert merchant["refusal_blockers_known"] is True
        assert merchant["blocked_since_consistent"] is True
        assert merchant["refusals_nonblocking"] is True
    # F8: admission never waits, so the contended lock produced refusals.
    assert starvation["lock_refusal_seen"] is True
    # F5: never two admitted checks at once.
    assert starvation["concurrent_admitted_bodies"] is False
    assert starvation["lock_free_after_threads_stop"] is True

    # F4: an uncaught exception releases and clears.
    assert artifact["raise"] == {
        "exception_propagates_to_observer_loop": True,
        "lock_released": True,
        "holder_cleared": True,
        "next_check_admitted": ["waiting", "waiting_farmer_handoff"],
    }
    # F6: the holder yields once to the fresh waiter, then the stale waiter
    # expires; the waiter's refusal names the holder and its blocked_since.
    assert artifact["yield"] == {
        "waiter_refused_while_held": "other_refill_check_active",
        "refusal_names_holder": "Spiritual",
        "status_holder_during_hold": ["Spiritual", "plan"],
        "status_lists_waiter_blocked_since": True,
        "holder_yields_to_fresh_waiter": "refill_turn_yielded",
        "stale_waiter_expires": "refill_precondition_unavailable",
    }
    # F7: the grant holder keeps its window; the other merchant follows.
    assert artifact["grant"] == {
        "other_refused_during_grant_check": "other_refill_check_active",
        "grant_holder_next_check": ["waiting", "waiting_farmer_handoff"],
        "other_admitted_after": ["waiting", "refill_precondition_unavailable"],
    }
    # F9: repeatable artifact.
    assert first.read_bytes() == second.read_bytes()
