import numpy as np
import pytest

from conquest.recovery import RecoveryConfig, DeathRecovery, RecoveryPhase, revive_button


def recovery(**kwargs):
    config = RecoveryConfig(enabled=True, return_route=((430,380),(434,384)), **kwargs)
    return DeathRecovery(config,(434,384),0,(433,383,440,390))


def tick(r, now, health=0, position=(434,384), button=(700,400), **kwargs):
    return r.decide(health=health,position=position,map_id=kwargs.get("map_id",1002),
                    timestamp=kwargs.get("timestamp",now),now=now,button=button)


def test_revive_waits_for_cooldown_fresh_button_and_living_spawn_evidence():
    r = recovery()
    assert tick(r,19.9) is None
    assert tick(r,20,timestamp=19) is None
    assert tick(r,20,button=None) is None
    action = tick(r,21)
    assert action.kind == "revive"
    assert not r.revival_verified
    assert tick(r,22) is None
    action = tick(r,23,health=1,position=(430,380))
    assert r.revival_verified and r.phase == RecoveryPhase.RETURNING
    assert action.kind == "return_walk"
    assert tick(r,25,health=1,position=(434,384)) is None
    assert r.phase == RecoveryPhase.COMPLETE


def test_unexpected_spawn_or_map_never_trigger_return_clicks():
    r = recovery()
    assert tick(r,21,health=1,position=(700,700)) is None
    assert r.reason == "unexpected_revival_position"
    r = recovery()
    assert tick(r,21,health=1,map_id=2000) is None
    assert r.reason == "unexpected_revival_map"


def test_missing_calibration_times_out_without_input_and_attempts_are_bounded():
    r = recovery()
    assert tick(r,120,button=None) is None
    assert r.reason == "revive_button_not_verified"
    r = recovery()
    for now in (20,28,36):
        assert tick(r,now).kind == "revive"
    assert tick(r,44) is None
    assert r.reason == "revive_attempt_limit"


def test_return_defers_to_healing_and_stops_on_repeated_failed_movement():
    r = recovery()
    assert tick(r,21,health=.39,position=(430,380)) is None
    assert r.phase == RecoveryPhase.RETURNING
    for now in (22,24,26):
        assert tick(r,now,health=.8,position=(430,380)).kind == "return_walk"
    assert tick(r,28,health=.8,position=(430,380)) is None
    assert r.reason == "return_movement_failure_limit"


def test_on_site_resurrection_completes_without_town_walk():
    r = recovery()
    assert tick(r,21,health=.8) is None
    assert r.revival_verified and r.phase == RecoveryPhase.COMPLETE


def test_revive_button_must_be_unique_and_within_its_calibrated_region():
    template = np.random.default_rng(42).integers(0,256,(20,70,3),dtype=np.uint8)
    frame = np.zeros((861,1584,3),np.uint8)
    frame[300:320,700:770] = template
    assert revive_button(frame,template,(650,250,950,450)) == (735,310)
    assert revive_button(frame,template,(100,100,400,300)) is None
    frame[350:370,800:870] = template
    assert revive_button(frame,template,(650,250,950,450)) is None
    with pytest.raises(ValueError):
        revive_button(frame,np.zeros((20,70,3),np.uint8),(650,250,950,450))
