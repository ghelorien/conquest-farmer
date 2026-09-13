from types import SimpleNamespace as NS
import pytest
from conquest.merchants.journal import Journal
from conquest.merchants.recovery_safety import arm, submitted, observe, KEY


def fixture(tmp_path):
    j=Journal(tmp_path/'j.db')
    r=NS(journal=j,connect_cancel={},enable=lambda c,v:j.set(c,'enabled',v),
         set_refill_enabled=lambda c,v:j.set(c,'refill_enabled',v))
    identity={'pid':123,'creation_time_100ns':456}
    return r,identity


def life(x=450,y=444,world=1002):
    return NS(map_id=world,position=(x,y),dead_candidate=False)


def test_normal_outside_market_never_authorizes_disconnect(tmp_path):
    r,i=fixture(tmp_path)
    assert observe(r,'Dutch',i,life(),now=100,close=lambda i:pytest.fail('unauthorized')) is False


def test_pending_trade_does_not_delay_authorized_failed_recovery(tmp_path):
    r,i=fixture(tmp_path); calls=[]
    r.journal.begin('unsettled','Dutch','delivery',{})
    arm(r,'Dutch',now=100); submitted(r,'Dutch',now=100)
    for t in [100,104.99]:
        observe(r,'Dutch',i,life(),now=t,close=lambda i:calls.append(i) or True)
    assert not calls
    observe(r,'Dutch',i,life(),now=105,close=lambda i:calls.append(i) or True)
    assert calls==[i]
    assert r.journal.pending('Dutch')
    assert r.journal.get('Dutch',KEY)['disconnected']
    assert r.journal.get('Dutch','connect_hold')


def test_only_real_forward_progress_resets_deadline(tmp_path):
    r,i=fixture(tmp_path); calls=[]; arm(r,'Dutch',now=100)
    for t,x in [(100,450),(104,449),(108,450),(109,449)]:
        observe(r,'Dutch',i,life(x),now=t,close=lambda i:calls.append(i) or True)
    assert calls==[i]  # Returning to a previously reached tile cannot hide a stall.


def test_market_arrival_disarms_even_if_booth_work_is_blocked(tmp_path):
    r,i=fixture(tmp_path); arm(r,'Dutch',now=100)
    observe(r,'Dutch',i,life(),now=100)
    observe(r,'Dutch',i,life(world=1036),now=104)
    assert r.journal.get('Dutch',KEY)['phase']=='market_arrived'
    assert not observe(r,'Dutch',i,life(world=1036),now=1000,close=lambda i:pytest.fail('safe Market'))


def test_failed_close_preserves_pending_state_and_never_claims_success(tmp_path):
    r,i=fixture(tmp_path); arm(r,'Dutch',now=100); submitted(r,'Dutch',now=100)
    def fail(i):raise ValueError('PID changed')
    with pytest.raises(ValueError):observe(r,'Dutch',i,now=105,close=fail)
    assert not r.journal.get('Dutch',KEY)['disconnected']
    assert r.journal.get('Dutch',KEY)['phase']=='disconnect_pending'


def test_explicit_hold_prevents_recovery_and_stale_restart_deadline_is_retained(tmp_path):
    r,i=fixture(tmp_path);r.journal.set('Dutch','connect_hold',True)
    arm(r,'Dutch',now=100);assert r.journal.get('Dutch',KEY) is None
    r.journal.set('Dutch','connect_hold',False)
    arm(r,'Dutch',now=100);submitted(r,'Dutch',now=100)
    arm(r,'Dutch',now=105);submitted(r,'Dutch',now=105)
    calls=[];observe(r,'Dutch',i,now=105,close=lambda i:calls.append(i) or True)
    assert calls==[i]
