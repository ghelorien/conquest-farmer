"""The market guard looks for merchant clients once a second, not every tick.

Live 2026-09-28 (Suicide's app, thread_sampler): merchant-market-safety spent
21% of its samples in process enumeration, a whole-system walk every 0.25 s
tick, with no merchant client running on the PC. It also rewrote its status
file every tick.

Failure modes, written before the change:
1. Client discovery runs on every 0.25 s tick.
2. A bound reader stops being checked every tick.
3. A reader whose reads fail keeps a stale client list, so a closed merchant
   client outlives the two-second grace and trips a false safety pause.
4. A newly started merchant client waits more than a second to be bound.
5. The status file is rewritten on every tick.
"""

from types import SimpleNamespace as NS

import pytest

from conquest.merchants.market_guard import MarketGuard

IDENTITY = {"pid": 2, "creation_time_100ns": 20, "path": "ImConquer.exe"}


class Ticks:
    """A stop event whose waits advance a fake clock, for a fixed tick count."""

    def __init__(self, count, clock):
        self.left, self.clock = count, clock

    def is_set(self):
        return self.left <= 0

    def wait(self, seconds):
        self.left -= 1
        self.clock[0] += seconds


def rig(monkeypatch, ticks, clients):
    clock = [100.0]
    calls = NS(windows=0, checks=[], writes=0, created=[])

    def windows():
        calls.windows += 1
        return clients(clock[0])

    runtime = NS(
        stop_event=Ticks(ticks, clock),
        merchant_windows=windows,
        journal=NS(
            get=lambda character, name: (
                IDENTITY if (character, name) == ("Spiritual", "last_identity") else None
            )
        ),
        observer_factory=lambda client, character: (
            calls.created.append(clock[0])
            or NS(adapter=NS(identity=client.identity), close=lambda: None)
        ),
    )

    def write(*args):
        calls.writes += 1

    monkeypatch.setattr("conquest.discord_notify.write_json", write)
    guard = MarketGuard(runtime, clock=lambda: clock[0])
    guard.check = lambda character, reader: calls.checks.append(clock[0])
    return guard, calls, clock


def test_discovery_runs_once_a_second_and_checks_every_tick(monkeypatch):
    # 1, 2, 5
    client = NS(identity=IDENTITY, hwnd=7)
    guard, calls, _ = rig(monkeypatch, 8, lambda now: [client])
    guard.run()  # ticks at 100.00 .. 101.75
    assert calls.windows == 2
    assert calls.checks == [100 + 0.25 * i for i in range(8)]
    assert calls.writes == 2


def test_a_failing_reader_refreshes_the_list_on_the_next_tick(monkeypatch):
    # 3
    client = NS(identity=IDENTITY, hwnd=7)
    guard, calls, clock = rig(
        monkeypatch, 4, lambda now: [client] if now < 100.2 else []
    )

    def failing_check(character, reader):
        calls.checks.append(clock[0])
        guard.unknown_since.setdefault(character, clock[0])

    guard.check = failing_check
    guard.run()
    # Tick 1 binds and fails; tick 2 re-enumerates, finds the client gone and
    # drops the reader with its grace timer before any pause.
    assert calls.windows == 2
    assert calls.checks == [100.0]
    assert guard.readers == {} and guard.unknown_since == {}


@pytest.mark.parametrize("appears_at", [100.1, 100.6, 100.9])
def test_a_new_client_is_bound_within_a_second(monkeypatch, appears_at):
    # 4
    client = NS(identity=IDENTITY, hwnd=7)
    guard, calls, _ = rig(
        monkeypatch, 12, lambda now: [client] if now >= appears_at else []
    )
    guard.run()
    assert calls.created and calls.created[0] - appears_at <= 1.0
