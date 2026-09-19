import copy
import time
import pytest
from conquest.merchants.delivery_probe import unchanged
from conquest.merchants.delivery import prepare
from conquest.merchants import delivery_probe as probe


def pair_with_stock():
    def item(uid,kind=410008,**fields):
        return dict(uid=uid,type_id=kind,plus=1,gem1=0,gem2=0,quantity=1,
                    bound=False,slot=uid-10,**fields)
    def account(name,uid,inventory):
        return dict(character=name,character_uid=uid,identity={'pid':uid},server='America',
            timestamp=time.time(),map_id=1036,hp=100,silver=200,capacity=40,
            position=[10,10],inventory=inventory,booth=[],trade=None,request=None)
    stock=[item(10),item(11,111008),item(12,1050002)]
    stock[2]['plus']=0
    return account('Parasite',1,stock),account('Spiritual',2,[])


@pytest.mark.parametrize('uids',[None,[],[10,11],[10,10],[True],[0],['10'],10])
def test_supervised_probe_requires_one_explicit_uid(uids):
    with pytest.raises(ValueError,match='exactly one'):
        probe.selected_intent(*pair_with_stock(),uids)


def test_supervised_probe_cannot_expand_selection_to_other_gear_or_supplies():
    farmer,merchant=pair_with_stock()
    intent=probe.selected_intent(farmer,merchant,[11])
    assert [item['uid'] for item in intent['items']]==[11]
    assert [item['uid'] for item in intent['farmer']['inventory']]==[10,11,12]


@pytest.mark.parametrize('change',[
    {'bound':True},{'bound':None},{'plus':0},{'plus':2},{'plus':True},
    {'type_id':410009},{'type_id':1050002},{'type_id':720027},
    {'gem1':1},{'gem2':1},{'quantity':2},{'slot':None},
])
def test_supervised_probe_rejects_out_of_scope_selected_item(change):
    farmer,merchant=pair_with_stock();farmer['inventory'][0].update(change)
    with pytest.raises(ValueError,match='Qualification requires'):
        probe.selected_intent(farmer,merchant,[10])


def test_supervised_probe_requires_loose_meteors_banked_even_when_not_selected():
    farmer,merchant=pair_with_stock()
    farmer['inventory'].append({**farmer['inventory'][0],'uid':13,'type_id':1088001,'plus':0,'slot':3})
    with pytest.raises(ValueError,match='Bank loose Meteors'):
        probe.selected_intent(farmer,merchant,[11])


@pytest.mark.parametrize('change',['missing','duplicate','full','request','trade','map','stale'])
def test_supervised_selection_still_requires_fresh_complete_bilateral_preflight(change):
    farmer,merchant=pair_with_stock()
    if change=='missing':farmer['inventory']=farmer['inventory'][1:]
    if change=='duplicate':farmer['inventory'].append(copy.deepcopy(farmer['inventory'][0]))
    if change=='full':merchant['capacity']=0
    if change=='request':merchant['request']={'participant':'Other'}
    if change=='trade':farmer['trade']={'participant':'Spiritual'}
    if change=='map':merchant['map_id']=1002
    if change=='stale':farmer['timestamp']-=6
    with pytest.raises(ValueError):probe.selected_intent(farmer,merchant,[10])


@pytest.mark.parametrize('phase',[
    'prepared','targeting_submitted','targeting_verified','request_submitted','request_verified',
    'accept_submitted','trade_open_verified','placement_submitted','offer_verified',
    'farmer_confirm_submitted','farmer_confirm_verified','merchant_confirm_submitted',
    'cancel_submitted','unknown',None,
])
def test_restart_cannot_replace_any_nonterminal_or_unknown_probe(tmp_path,monkeypatch,phase):
    path=tmp_path/'probe.json';monkeypatch.setattr(probe,'JOURNAL',path)
    probe.write_json(path,{'phase':phase})
    with pytest.raises(ValueError,match='Reconcile existing'):
        probe.previous_probe()


@pytest.mark.parametrize('contents',['{','[]','null','{}'])
def test_corrupt_existing_probe_is_not_treated_as_no_previous_input(tmp_path,monkeypatch,contents):
    path=tmp_path/'probe.json';path.write_text(contents);monkeypatch.setattr(probe,'JOURNAL',path)
    with pytest.raises(ValueError):probe.previous_probe()


def test_terminal_probe_archival_is_complete_and_retryable(tmp_path,monkeypatch):
    path=tmp_path/'probe.json';monkeypatch.setattr(probe,'JOURNAL',path)
    assert probe.previous_probe() is None
    previous={'phase':'delivery_verified','intent':{'items':[{'uid':10}]},'farmer_after':{'silver':123}}
    probe.write_json(path,previous)
    for _ in range(2):probe.archive_probe(probe.previous_probe())
    archived=list((tmp_path/'delivery-request-probe-audit').glob('*.json'))
    assert len(archived)==1
    assert probe.read_json(archived[0])==previous
    probe.write_probe(path,{'phase':'prepared'})
    assert probe.read_json(archived[0])==previous
    with pytest.raises(ValueError):probe.previous_probe()


def start_ui(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from conquest.merchants import farmer_preferences
    monkeypatch.setattr(farmer_preferences,'permits_new_delivery',lambda _:None)
    monkeypatch.setattr(probe,'JOURNAL',tmp_path/'probe.json')
    monkeypatch.setattr(probe,'pair',lambda *args:pair_with_stock())
    started=[]
    class NoInputThread:
        def __init__(self,**kwargs):self.work=kwargs['target']
        def is_alive(self):return False
        def start(self):started.append(probe.read_json(probe.JOURNAL))
    monkeypatch.setattr(probe.threading,'Thread',NoInputThread)
    control={'enabled':False,'paused':False,'revision':1}
    ui=SimpleNamespace(coordinator=SimpleNamespace(check=lambda:None),safe_to_yield=lambda:True,
        app=SimpleNamespace(control=SimpleNamespace(snapshot=lambda:control)))
    return ui,control,started


def test_start_persists_exact_authorized_item_before_worker_and_preserves_terminal_record(tmp_path,monkeypatch):
    ui,_,started=start_ui(monkeypatch,tmp_path)
    old={'phase':'operator_overridden','historical_uid':999}
    probe.write_json(probe.JOURNAL,old)
    assert probe.start(ui,'Spiritual',uids=[11])['uids']==[11]
    assert started[0]['selected_uids']==[11]
    assert [item['uid'] for item in started[0]['intent']['items']]==[11]
    assert probe.read_json(next((tmp_path/'delivery-request-probe-audit').glob('*.json')))==old
    with pytest.raises(ValueError,match='Reconcile existing'):
        probe.start(ui,'Spiritual',uids=[10])
    assert len(started)==1


def test_persistence_failure_never_starts_probe_worker(tmp_path,monkeypatch):
    ui,_,started=start_ui(monkeypatch,tmp_path)
    def fail(_):raise OSError('disk flush failed')
    monkeypatch.setattr(probe.os,'fsync',fail)
    with pytest.raises(OSError,match='disk flush failed'):
        probe.start(ui,'Spiritual',uids=[10])
    assert not started
    with pytest.raises(ValueError,match='Reconcile existing'):
        probe.start(ui,'Spiritual',uids=[10])


def test_stopped_coordinator_denies_probe_without_new_journal(tmp_path,monkeypatch):
    ui,_,started=start_ui(monkeypatch,tmp_path)
    def stop():raise ValueError('Global Stop')
    ui.coordinator.check=stop
    with pytest.raises(ValueError,match='Global Stop'):
        probe.start(ui,'Spiritual',uids=[10])
    assert not started and not probe.JOURNAL.exists()


def legacy_probe_setup(monkeypatch,tmp_path):
    from types import SimpleNamespace
    farmer,merchant=pair_with_stock()
    for snapshot in (farmer,merchant):
        snapshot.update(booth_open=False,own_booth_uid=0)
        snapshot['identity'].update(creation_time_100ns=123,path='game.exe')
    state={'phase':'aborted_no_trade_observed','character':'Spiritual',
           'intent':copy.deepcopy(prepare(farmer,merchant,[farmer['inventory'][0]])),
           'recovery_note':'Expired request; exact stock and currency unchanged'}
    # The original game processes are gone. Explicit override can release the
    # historical hold, but cannot claim anything about their transfer outcome.
    farmer['identity']['creation_time_100ns']=456
    merchant['identity']['creation_time_100ns']=789
    farmer['map_id']=1011
    monkeypatch.setattr(probe,'JOURNAL',tmp_path/'probe.json')
    probe.write_json(probe.JOURNAL,state)
    monkeypatch.setattr(probe,'pair',lambda *args,**kwargs:(farmer,merchant))
    return SimpleNamespace(),state,farmer,merchant


def test_legacy_abort_text_never_implicitly_qualifies_as_terminal(tmp_path,monkeypatch):
    legacy_probe_setup(monkeypatch,tmp_path)
    with pytest.raises(ValueError,match='Reconcile existing'):
        probe.previous_probe()


def test_digest_bound_legacy_override_preserves_complete_original_without_transfer_claim(tmp_path,monkeypatch):
    ui,old,farmer,merchant=legacy_probe_setup(monkeypatch,tmp_path)
    preview=probe.recheck(ui)
    assert probe.read_json(probe.JOURNAL)==old
    assert preview['fresh_evidence']['farmer']['map_id']==1011
    digest=preview['incident_digest']
    result=probe.operator_override(ui,operator_confirmed=True,
        confirmation_reference=digest,incident_digest=digest,operator='supervisor')
    assert result=={'phase':'operator_overridden','historical_outcome':'unknown',
                   'incident_digest':digest,'replan_required':True}
    state=probe.previous_probe()
    assert state['operator_override']['original_state']==old
    assert state['operator_override']['fresh_evidence']['historical_outcome']=='unknown'
    archives=list((tmp_path/'delivery-request-probe-audit').glob('*.json'))
    assert len(archives)==1 and probe.read_json(archives[0])==old
    assert probe.operator_override(ui,operator_confirmed=True,
        confirmation_reference=digest,incident_digest=digest)==result


@pytest.mark.parametrize('change',['incident','no_preview','expired','silver','inventory','process',
                                  'trade','request','wrong_character','wrong_uid','server','stale'])
def test_operator_override_rejects_changed_or_missing_bound_evidence(tmp_path,monkeypatch,change):
    ui,old,farmer,merchant=legacy_probe_setup(monkeypatch,tmp_path)
    preview=probe.recheck(ui);digest=preview['incident_digest']
    if change=='incident':probe.write_json(probe.JOURNAL,{**old,'new_evidence':True})
    if change=='no_preview':probe.Path(str(probe.JOURNAL)+'.recheck.json').unlink()
    if change=='expired':
        preview['fresh_evidence']['observed_at']-=31
        probe.write_json(probe.Path(str(probe.JOURNAL)+'.recheck.json'),preview)
    if change=='silver':merchant['silver']+=1
    if change=='inventory':farmer['inventory'].pop()
    if change=='process':farmer['identity']['creation_time_100ns']+=1
    if change=='trade':merchant['trade']={'participant':'Other'}
    if change=='request':farmer['request']={'participant':'Other'}
    if change=='wrong_character':merchant['character']='Dutch'
    if change=='wrong_uid':farmer['character_uid']+=1
    if change=='server':merchant['server']='Other'
    if change=='stale':farmer['timestamp']-=6
    with pytest.raises(ValueError):
        probe.operator_override(ui,operator_confirmed=True,
            confirmation_reference=digest,incident_digest=digest)
    assert probe.read_json(probe.JOURNAL)['phase']=='aborted_no_trade_observed'
    assert not (tmp_path/'delivery-request-probe-audit').exists()


@pytest.mark.parametrize('arguments',[{}, {'operator_confirmed':True},
    {'operator_confirmed':False,'confirmation_reference':'digest','incident_digest':'digest'},
    {'operator_confirmed':True,'confirmation_reference':'other','incident_digest':'digest'}])
def test_operator_override_requires_explicit_exact_digest_confirmation(tmp_path,monkeypatch,arguments):
    ui,_,_,_=legacy_probe_setup(monkeypatch,tmp_path)
    probe.recheck(ui)
    with pytest.raises(ValueError,match='exact previewed'):
        probe.operator_override(ui,**arguments)
    assert probe.read_json(probe.JOURNAL)['phase']=='aborted_no_trade_observed'


def test_unrecognized_legacy_phase_cannot_enter_override_path(tmp_path,monkeypatch):
    ui,old,_,_=legacy_probe_setup(monkeypatch,tmp_path)
    probe.write_json(probe.JOURNAL,{**old,'phase':'unknown_history'})
    with pytest.raises(ValueError,match='No known unfinished'):
        probe.recheck(ui)


def test_probe_recheck_and_override_refuse_running_probe(tmp_path,monkeypatch):
    from types import SimpleNamespace
    ui,_,_,_=legacy_probe_setup(monkeypatch,tmp_path)
    preview=probe.recheck(ui);digest=preview['incident_digest']
    ui.delivery_probe_thread=SimpleNamespace(is_alive=lambda:True)
    with pytest.raises(ValueError,match='Wait for delivery input'):probe.recheck(ui)
    with pytest.raises(ValueError,match='Wait for delivery input'):
        probe.operator_override(ui,operator_confirmed=True,
            confirmation_reference=digest,incident_digest=digest)


@pytest.mark.parametrize('change',[None,'position','currency','identity','inventory','request'])
def test_request_probe_rechecks_both_participants_before_input(change):
    item=dict(uid=10,type_id=720027,plus=0,gem1=0,gem2=0,quantity=1,bound=False,slot=0)
    def snapshot(name,uid,items):
        return dict(character=name,character_uid=uid,identity={'pid':uid},server='America',
            timestamp=time.time(),map_id=1036,hp=100,silver=200,capacity=40,
            position=[10,10],inventory=items,booth=[],trade=None,request=None)
    f=snapshot('Parasite',1,[item]);m=snapshot('Spiritual',2,[])
    intent=copy.deepcopy(prepare(f,m,[item]))
    if change=='position':m['position']=[11,10]
    if change=='currency':f['silver']=199
    if change=='identity':m['identity']={'pid':3}
    if change=='inventory':f['inventory']=[]
    if change=='request':m['request']={'participant':'SomeoneElse'}
    if change:
        with pytest.raises(ValueError):unchanged(intent,f,m)
    else:unchanged(intent,f,m)
