import pytest
from conquest.travel_progress import ProgressDeadline,TravelStalled


def test_clicks_and_oscillation_do_not_renew_recovery():
    now=[0.0];watch=ProgressDeadline(clock=lambda:now[0])
    assert not watch.observe(20)
    now[0]=5;assert watch.observe(20)
    now[0]=7;assert not watch.observe(22)
    now[0]=10;assert watch.observe(20)
    now[0]=15
    with pytest.raises(TravelStalled):watch.observe(21)


def test_verified_improving_distance_ends_recovery():
    now=[0.0];watch=ProgressDeadline(clock=lambda:now[0])
    watch.observe(20);now[0]=5;assert watch.observe(20)
    now[0]=6;assert not watch.observe(19)
    assert watch.attempts==0 and watch.recovery_started is None
    now[0]=10;assert not watch.observe(19)
    now[0]=11;assert watch.observe(19)


def test_travel_stall_reason_is_typed():
    stalled=TravelStalled('budget ended',code='service_deadline')
    assert stalled.code=='service_deadline' and str(stalled)=='budget ended'
