from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from conquest.scroll_withdrawal import ScrollWithdrawal
from conquest.protected_withdrawal import ProtectedWithdrawalJournal, pending
from test_protected_withdrawal import Town, item, observation, IDENTITY


def scroll(uid=99, **changes):
    return item(uid, 720027, plus=0, **changes)


def sample(*, bag=(), stash=None, **changes):
    value=observation(bag, (scroll(),) if stash is None else stash)
    value['source'].update(timestamp=200,capacity=40,**changes)
    return value


def work(tmp_path,monkeypatch,observations,*,click='after'):
    town=Town()
    journal=ProtectedWithdrawalJournal(tmp_path/'withdraw.sqlite3',clock=lambda:200)
    task=ScrollWithdrawal(town,journal=journal,clock=lambda:200,sleep=lambda _:None)
    profile=NS(id='b3a6b949-d20e-49c6-9337-e82b1876159d',role='Farmer',character_uid=1173490)
    task._profile=lambda:NS(profile=profile,verify=lambda _:None)
    values=iter(observations);last=[observations[-1]]
    def observe():
        try:last[0]=next(values)
        except StopIteration:pass
        return deepcopy(last[0])
    task._observe=observe
    calls=[]
    def foreground(*args,**kwargs):
        calls.append('click')
        if click=='before':raise ValueError('manual input hold')
        kwargs['before_press']()
        if click=='after':raise OSError('lost acknowledgement')
    monkeypatch.setattr('conquest.merchants.driver.wait_hover_validation',lambda validate,check:None)
    monkeypatch.setattr('conquest.foreground.foreground_click',foreground)
    return task,journal,calls,profile


def run(task,operation='scroll-one',uid=99):
    return task._run(task.plan_id(operation),operation,uid)


def test_banked_scroll_has_exact_receipt_and_lost_ack_never_replays(tmp_path,monkeypatch):
    before=sample();after=sample(bag=(scroll(),),stash=())
    task,journal,calls,_=work(tmp_path,monkeypatch,[before,before,after])
    result=run(task)
    assert result['phase']=='withdrawn'
    assert result['next_action']=='prepare_exact_bilateral_delivery'
    assert result['receipt']['item']==scroll()
    assert result['receipt']['verified_absent_from_warehouse']
    assert result['receipt']['verified_in_inventory']
    assert pending(journal.path)==[]
    assert [row['phase'] for row in journal.history('scroll-one')]==[
        'prepared','input_maybe_sent','withdrawn']
    assert run(task)==result
    assert calls==['click']


def test_normal_click_reconciles_before_terminal_receipt(tmp_path,monkeypatch):
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample(),sample(),sample(bag=(scroll(),),stash=())],click='success')
    assert run(task)['phase']=='withdrawn'
    assert [row['phase'] for row in journal.history('scroll-one')]==[
        'prepared','input_maybe_sent','reconciling','withdrawn']


def test_snapshot_clock_advancing_does_not_break_durable_operation_binding(tmp_path,monkeypatch):
    first=sample();first['source']['timestamp']=199
    task,journal,calls,_=work(tmp_path,monkeypatch,[first,sample(),sample(bag=(scroll(),),stash=())])
    assert run(task)['phase']=='withdrawn'
    assert run(task)['phase']=='withdrawn'
    assert calls==['click']


def test_possible_click_unchanged_ownership_blocks_every_new_operation(tmp_path,monkeypatch):
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample()])
    assert run(task)['phase']=='blocked'
    assert run(task)['phase']=='blocked'
    with pytest.raises(ValueError,match='Another protected withdrawal'):
        run(task,'different-operation')
    assert calls==['click']
    assert pending(journal.path)[0]['uid']==99
    task._observe=lambda:sample(bag=(scroll(),),stash=())
    assert run(task)['phase']=='withdrawn'
    assert calls==['click']


def test_preinput_refusal_proves_no_transfer(tmp_path,monkeypatch):
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample()],click='before')
    result=run(task)
    assert result['phase']=='no_transfer' and not result['input_attempted']
    assert result['receipt']['input_attempted'] is False
    assert run(task)==result and calls==['click']


@pytest.mark.parametrize('kind',[1088001,1088000,114643])
def test_other_stored_types_cannot_use_scroll_withdrawal(tmp_path,monkeypatch,kind):
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample(stash=(item(99,kind,plus=0),))])
    with pytest.raises(ValueError,match='MeteorScroll'):
        run(task)
    assert calls==[] and journal.get('scroll-one') is None


@pytest.mark.parametrize('change',[
    {'bound':True},{'quantity':2},{'plus':1},{'gem1':1},{'gem2':1},
])
def test_unqualified_scroll_attributes_are_rejected_before_admission(tmp_path,monkeypatch,change):
    row={**scroll(),**change}
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample(stash=(row,))])
    with pytest.raises(ValueError,match='MeteorScroll'):
        run(task)
    assert calls==[] and journal.get('scroll-one') is None


@pytest.mark.parametrize('mutation',[
    lambda v:v['source'].update(map_id=1011),
    lambda v:v['source'].update(hp=0),
    lambda v:v['source'].update(identity={**IDENTITY,'creation_time_100ns':100}),
    lambda v:v['source'].update(timestamp=190),
    lambda v:v['source'].update(capacity=1,inventory=[item(4)]),
    lambda v:v['source'].update(inventory=[item(4,1088001,plus=0)]),
    lambda v:v['source'].update(trade={'participant':'Dutch'}),
    lambda v:v['source'].update(windows=[{'name':'Trade###Confirm'}]),
])
def test_preflight_safety_rejections_never_admit_input(tmp_path,monkeypatch,mutation):
    value=sample();mutation(value)
    task,journal,calls,_=work(tmp_path,monkeypatch,[value])
    with pytest.raises(ValueError):run(task)
    assert calls==[] and journal.get('scroll-one') is None


def test_manual_guard_rejection_never_admits_click(tmp_path,monkeypatch):
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample()])
    def stop():raise ValueError('Global Stop')
    task.town.check_input=stop
    with pytest.raises(ValueError,match='Global Stop'):run(task)
    assert calls==[] and journal.get('scroll-one') is None


def test_restart_profile_mismatch_cannot_reconcile_another_profile(tmp_path,monkeypatch):
    task,journal,calls,profile=work(tmp_path,monkeypatch,[sample()])
    assert run(task)['phase']=='blocked'
    profile.id='another-profile'
    with pytest.raises(ValueError,match='another farmer profile'):run(task)
    assert calls==['click']


def test_silver_change_cannot_be_reported_as_exact_withdrawal(tmp_path,monkeypatch):
    after=sample(bag=(scroll(),),stash=(),silver=998)
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample(),sample(),after])
    assert run(task)['phase']=='blocked'
    assert calls==['click']


def test_dispatch_is_narrow_and_reconcile_is_a_separate_read_only_endpoint(monkeypatch):
    from conquest import scroll_withdrawal
    from conquest.town_trade import TownTrade
    town=TownTrade.__new__(TownTrade);calls=[]
    monkeypatch.setattr(scroll_withdrawal,'withdraw',lambda *args:calls.append(('withdraw',args)) or {})
    monkeypatch.setattr(scroll_withdrawal,'reconcile',lambda *args:calls.append(('reconcile',args)) or {})
    for verb in ('withdraw','reconcile'):
        assert town({'action':'warehouse-'+verb+'-scroll','operation_id':'op','uid':99})=={}
    assert calls==[('withdraw',(town,'op',99)),('reconcile',(town,'op',99))]
    with pytest.raises((ValueError,AttributeError)):
        town({'action':'warehouse-withdraw-scroll','operation_id':'op','uid':99,'type_id':1088001})


def test_read_only_reconcile_closes_missing_admission_and_blocks_late_submission(tmp_path,monkeypatch):
    from conquest import scroll_withdrawal
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample()])
    monkeypatch.setattr(scroll_withdrawal,'ScrollWithdrawal',lambda *a,**kw:task)
    result=scroll_withdrawal.reconcile(task.town,'not-admitted',99,journal=journal)
    assert result['phase']=='no_transfer' and result['no_input_proven']
    assert result['receipt']['admission_closed']
    assert run(task,'not-admitted')['phase']=='no_transfer'
    assert calls==[]


def test_missing_admission_cannot_close_when_exact_item_is_not_in_warehouse(tmp_path,monkeypatch):
    from conquest import scroll_withdrawal
    task,journal,calls,_=work(tmp_path,monkeypatch,[sample(bag=(scroll(),),stash=())])
    monkeypatch.setattr(scroll_withdrawal,'ScrollWithdrawal',lambda *a,**kw:task)
    with pytest.raises(ValueError,match='stored MeteorScroll'):
        scroll_withdrawal.reconcile(task.town,'not-admitted',99,journal=journal)
    assert journal.get('not-admitted') is None and calls==[]
