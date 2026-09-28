"""The farmer's "no manual hold" journal answer is reused for a second while no
trade window is visible; anything else is read from the journal at once.

Toxic 2026-09-28: the SQLite reads behind it cost 71 ms of a 175 ms combat
observation, under the observer lock.
"""

from types import SimpleNamespace

from conquest.merchants.manual_farmer import MANUAL_HELD_TTL, manual_held


def runtime(held=False):
    calls = []
    state = {"held": held}

    def status(character):
        calls.append("status")
        return {"id": "hold"} if state["held"] else None

    def handoff():
        calls.append("handoff")
        return None

    return SimpleNamespace(manual_status=status, manual_handoff_status=handoff), calls, state


def test_no_window_reuses_the_not_held_answer_within_the_ttl():
    rt, calls, _ = runtime()
    assert manual_held(rt, False, now=100.0) is False
    assert manual_held(rt, False, now=100.0 + MANUAL_HELD_TTL / 2) is False
    assert calls == ["status", "handoff"]  # one journal read
    assert manual_held(rt, False, now=100.0 + MANUAL_HELD_TTL + 0.01) is False
    assert calls == ["status", "handoff"] * 2  # re-read once stale


def test_a_session_started_by_a_visible_window_is_seen_when_it_closes():
    rt, calls, state = runtime()
    manual_held(rt, False, now=100.0)
    assert manual_held(rt, True, now=100.1) is False  # visible: always read
    state["held"] = True  # the trade opened a manual session
    assert manual_held(rt, False, now=100.2) is True  # window closed: read again
    assert manual_held(rt, False, now=100.3) is True  # a hold is never reused


def test_a_cleared_cache_reads_again():
    rt, calls, state = runtime()
    manual_held(rt, False, now=5.0)
    rt.manual_held_cache = None  # observe() on a reader failure
    state["held"] = True
    assert manual_held(rt, False, now=5.1) is True
