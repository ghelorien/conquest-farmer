from types import SimpleNamespace as NS
import pytest
from conquest import overnight
from conquest.overnight import OvernightLoop, OvernightStopped
from conquest.travel_care import TravelStateChanged


@pytest.mark.parametrize(
    "failure,retries,event",
    [
        (ValueError("Town route remains obstructed"), 2, None),
        (
            ValueError("Town travel has made no position progress for 90 seconds"),
            2,
            None,
        ),
        (ValueError("Market deposit receipt missing; no return issued"), 1, "failed"),
        (OvernightStopped("Stopped by user"), 1, "stopped"),
    ],
)
def test_movement_failure_keeps_care_running_but_does_not_retry_transactions(
    monkeypatch, failure, retries, event
):
    clock = [0.0]
    calls = []
    care = []
    events = []
    monkeypatch.setattr(overnight.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        overnight.time, "sleep", lambda t: clock.__setitem__(0, clock[0] + t)
    )
    monkeypatch.setattr(
        overnight.ctypes.windll.kernel32, "SetThreadExecutionState", lambda flags: None
    )
    monkeypatch.setattr(overnight, "request", lambda *a: None)
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.info = "unused"
    loop.check_stop = loop.refresh = lambda: None
    loop.record = lambda e, **kw: events.append(e)
    loop.living = lambda: {"embedded_controls": {"control": {"enabled": False}}}
    # run() establishes the controller identity from one fresh health read.
    loop.health = lambda: {"embedded_controls": {"control": {"enabled": False}}}

    def care_check(health):
        care.append(health)
        if len(care) == 1:
            raise TravelStateChanged("Revive submitted; reobserve")

    loop.care = NS(check=care_check)

    def run_route():
        calls.append(1)
        if len(calls) == 1:
            raise failure

    loop._run_route = run_route
    loop.run()
    assert len(calls) == retries
    if retries == 2:
        assert (
            len(care) > 1
            and "route_movement_retry" in events
            and "failed" not in events
        )
        assert loop.walk_after_obstruction
    else:
        assert not care and event in events


def test_manual_stop_cancels_movement_retry_before_more_input(monkeypatch):
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.record = lambda *a, **kw: None

    def stop():
        raise OvernightStopped("Stopped by user")

    loop.check_stop = stop
    loop.living = lambda: pytest.fail(
        "Stop must be checked before obtaining more input"
    )
    with pytest.raises(OvernightStopped):
        loop.protect_during_movement_retry()
