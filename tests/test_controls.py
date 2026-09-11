import threading

import pytest

from conquest.control import FarmingControl
from conquest.control_runtime import ControlRuntime
from conquest.kill_loot import LootObservation
from conquest.memory_inventory import InventorySnapshot


def observed_loot():
    return LootObservation('test-client/map',10,(),(),InventorySnapshot(10,10,(),None,0,40))


def test_focus_changes_never_change_user_switch_or_selection():
    control = FarmingControl()
    control.update({"enabled": True, "target_ids": [77], "input_mode": "foreground"})
    assert control.reconcile(focused=False, minimized=False, observed_ids=[77])["execution_state"] == "waiting_for_focus"
    assert control.snapshot()["enabled"]
    assert control.reconcile(focused=True, minimized=False, observed_ids=[77])["execution_state"] == "ready"
    assert control.snapshot()["target_ids"] == [77]


def test_background_readiness_does_not_require_focus():
    control = FarmingControl()
    control.update({"enabled": True, "target_ids": [77]})
    assert control.reconcile(focused=False, minimized=False, observed_ids=[77])["execution_state"] == "ready"


def test_group_selection_resolves_new_ids_without_turning_off(tmp_path):
    control = FarmingControl(tmp_path/'groups.json')
    control.update({'target_type_ids':[1],'enabled':True})
    first = control.reconcile(focused=False,minimized=False,observed_ids=[77,88],observed_types={77:1,88:2})
    assert first['resolved_target_ids']==[77] and first['execution_state']=='ready'
    second = control.reconcile(focused=False,minimized=False,observed_ids=[99,100,88],observed_types={99:1,100:1,88:2})
    assert second['resolved_target_ids']==[99,100] and second['revision']==first['revision']
    assert second['enabled']
    restored = FarmingControl(tmp_path/'groups.json').snapshot()
    assert restored['target_type_ids']==[1] and not restored['enabled']


def test_group_off_rejects_pending_attack_and_other_species():
    control = FarmingControl()
    current = control.update({'target_type_ids':[1],'enabled':True})
    calls = []
    assert control.dispatch(current['revision'],99,lambda:calls.append(99),type_id=1)
    assert not control.dispatch(current['revision'],88,lambda:calls.append(88),type_id=2)
    control.update({'target_type_ids':[]})
    assert not control.dispatch(current['revision'],99,lambda:calls.append(99),type_id=1)
    assert calls==[99]


def test_runtime_expands_group_but_does_not_abandon_attacked_id_without_kill_evidence():
    control = FarmingControl()
    control.update({'target_type_ids':[1],'enabled':True})
    calls = []
    monsters = [{'entity_id':77,'type_id':1,'alive':True,'object_address':177},{'entity_id':88,'type_id':2}]
    runtime = ControlRuntime(control,None,None,None,'Parasite',observer=lambda:{
        'monsters':list(monsters),'focused':False,'loot_observation':observed_loot()},
        dispatcher=lambda monster,*args:calls.append(monster['entity_id']),pickup_dispatcher=lambda *_:None,clock=lambda:10)
    runtime.step()
    monsters[0] = {'entity_id':99,'type_id':1}
    runtime.step()
    assert calls==[77]
    assert control.snapshot()['resolved_target_ids']==[99]
    assert runtime.snapshot()['encounter']['monster_id']==77


def test_no_ids_never_means_attack_everything():
    control = FarmingControl()
    control.update({"enabled": True})
    assert control.reconcile(focused=True, minimized=False, observed_ids=[77])["execution_state"] == "waiting_for_targets"
    assert not control.dispatch(control.revision, 77, lambda: pytest.fail("Unselected attack"))


@pytest.mark.parametrize("change", [{"enabled": False}, {"target_ids": [88]}, {"input_mode": "foreground"}])
def test_settings_change_invalidates_pending_attack(change):
    control = FarmingControl()
    old = control.update({"enabled": True, "target_ids": [77]})
    control.update(change)
    assert not control.dispatch(old["revision"], 77, lambda: pytest.fail("Stale attack"))


def test_stale_observation_cannot_overwrite_off():
    control = FarmingControl()
    old = control.update({"enabled": True, "target_ids": [77]})
    control.update({"enabled": False})
    control.publish(old["revision"], "ready", "Old observation")
    assert control.snapshot()["execution_state"] == "off"


def test_targets_survive_service_restart_but_enabled_does_not(tmp_path):
    path = tmp_path / "controls.json"
    first = FarmingControl(path)
    first.update({"enabled": True, "target_ids": [77,88], "input_mode": "foreground"})
    restored = FarmingControl(path).snapshot()
    assert restored["target_ids"] == [77,88]
    assert restored["input_mode"] == "foreground"
    assert not restored["enabled"]


@pytest.mark.parametrize("body", [{"enabled": "false"}, {"enabled": 1}, {"target_ids": [True]},
    {"target_ids": [-1]}, {"target_ids": [0]}, {"target_ids": [2**32]}, {"target_ids": ["77"]},
    {"input_mode": "steal_focus"}, {"script": "execute"}, {"target_ids": list(range(1,130))}])
def test_invalid_update_is_atomic(body):
    control = FarmingControl()
    before = control.snapshot()
    with pytest.raises(ValueError):
        control.update({"enabled": True, **body})
    assert control.snapshot() == before


def test_off_serializes_with_in_flight_input():
    control = FarmingControl()
    settings = control.update({"enabled": True, "target_ids": [77]})
    started, release, stopped = threading.Event(), threading.Event(), threading.Event()
    def send():
        started.set()
        assert release.wait(2)
    attack = threading.Thread(target=lambda: control.dispatch(settings["revision"], 77, send))
    attack.start()
    assert started.wait(2)
    off = threading.Thread(target=lambda: (control.update({"enabled": False}), stopped.set()))
    off.start()
    assert not stopped.wait(.03)
    release.set()
    attack.join(2)
    off.join(2)
    assert stopped.is_set()
    assert not control.dispatch(settings["revision"], 77, lambda: pytest.fail("Attack after off"))


def test_runtime_dispatches_only_selected_id_while_unfocused():
    control = FarmingControl()
    calls = []
    observation = {"monsters": [{"entity_id": 66}, {"entity_id": 77,'object_address':177,'alive':True}], "focused": False,
                   "minimized": False, "blockers": [],'loot_observation':observed_loot()}
    runtime = ControlRuntime(control, None, None, None, "Parasite", observer=lambda: observation,
                             dispatcher=lambda monster, data, mode: calls.append((monster["entity_id"], mode)),
                             pickup_dispatcher=lambda *_:None, clock=lambda:10)
    control.update({"enabled": True, "target_ids": [77]})
    runtime.step()
    assert calls == [(77, "background")]
    control.update({"enabled": False})
    runtime.step()
    assert calls == [(77, "background")]


def test_validation_blocker_preserves_on_without_dispatching():
    control = FarmingControl()
    runtime = ControlRuntime(control, None, None, None, "Parasite",
        observer=lambda: {"monsters": [{"entity_id":77}], "focused": False, "blockers": ["HP unvalidated"]},
        dispatcher=lambda *_: pytest.fail("Unqualified attack"))
    control.update({"enabled": True, "target_ids": [77]})
    runtime.step()
    assert control.snapshot()["enabled"]
    assert control.snapshot()["execution_state"] == "blocked"


def test_external_runner_failure_preserves_real_reason_instead_of_generic_blockers():
    from conquest.control_runtime import ControlRuntime
    control=FarmingControl()
    state=control.update({'enabled':True,'target_type_ids':[2]})
    runtime=ControlRuntime(control,None,None,None,'Parasite',observer=lambda:{
        'monsters':[],'focused':True,'blockers':['Old diagnostic blocker']})
    runtime.external_failure=(state['revision'],'outside_trial_boundary')
    runtime.step()
    assert control.snapshot()['execution_state']=='runner_stopped'
    assert control.snapshot()['note']=='Farm runner stopped: outside_trial_boundary'


def test_old_runner_cleanup_preserves_new_enabled_selection():
    control=FarmingControl()
    control.update({'enabled':True,'target_type_ids':[6]})
    revision=control.snapshot()['revision']
    control.update({'target_type_ids':[7]})
    assert control.finish_session(revision,'control_changed')
    assert control.snapshot()['enabled'] and control.snapshot()['target_type_ids']==[7]
    control.update({'enabled':False})
    assert not control.finish_session(revision,'control_changed')
    assert not control.snapshot()['enabled']


def test_old_stop_cannot_cancel_new_on_but_emergency_still_stops():
    control=FarmingControl();control.update({'enabled':True})
    revision=control.snapshot()['revision']
    control.update({'enabled':False});control.update({'enabled':True})
    assert control.finish_session(revision,'requested_stop')
    assert not control.finish_session(revision,'emergency_stop')
    assert not control.snapshot()['enabled']
