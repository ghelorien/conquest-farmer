import copy
import threading
import time
from types import SimpleNamespace
import pytest
from conquest.capture import CaptureUnavailable
from conquest.merchants.controller import MerchantController
from conquest.merchants.coordination import InputCoordinator
from conquest.merchants.journal import Journal
from conquest.merchants.market import MarketSnapshot
from conquest.merchants.price_history import PriceHistory
from conquest.merchants.refill import RefillSchedule, HistoricalComparisons
from conquest.merchants.runtime import MerchantRuntime


def stock(uid, type_id=130805, *, price=None, gem1=0):
    return dict(uid=uid,type_id=type_id,name='Valuable',plus=2,gem1=gem1,gem2=0,
                quantity=1,bound=False,price=price)


@pytest.fixture
def setup(tmp_path):
    j=Journal(tmp_path/'journal.sqlite3')
    guard=InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    runtime=MerchantRuntime(object(),guard,journal=j,market_path=tmp_path/'missing-market.json')
    guard.owner_allowed=runtime.input_allowed
    rows=[dict(name='Coat',category='Trojan Armor',quality='Normal',plus=2,sockets=['No socket','No socket'],
               seller='Outside',price=130000,quantity=1,server='America'),
          dict(name='Necklace',category='Necklace',quality='Normal',plus=2,sockets=['No socket','No socket'],
               seller='Outside',price=900000,quantity=1,server='America')]
    history=PriceHistory(tmp_path/'price-history.sqlite3')
    history.remember(MarketSnapshot(dict(source='https://conqueronline.net/market',server='America',complete=True,
        observed_at=100,total=2,listings=rows,equipment_categories={'130':'Trojan Armor','120':'Necklace'}),now=100))
    state=dict(character='Dutch',identity={'pid':1,'creation_time_100ns':2},timestamp=time.time(),
        map_id=1036,position=[264,214],server='America',hp=100,capacity=40,silver=100,
        inventory=[stock(1),stock(2,120005)],booth=[],booth_open=True,request=None,trade=None)
    def read():return {**copy.deepcopy(state),'timestamp':time.time()}
    calls=[]
    def post(before,item,price,check):
        check();calls.append((item['uid'],price))
        state['inventory']=[i for i in state['inventory'] if i['uid']!=item['uid']]
        state['booth'].append({**item,'price':price})
    driver=SimpleNamespace(read=read,require_qualified=lambda c:None,prepare_listing=lambda *args:None,
                           list_item=post,wait_for=lambda predicate,check:read() if predicate(read()) else None)
    runtime.controllers['Dutch']=MerchantController('Dutch',j,driver,guard)
    runtime.observers['Dutch']=SimpleNamespace(adapter=SimpleNamespace(identity=state['identity'],assert_identity=lambda:None),
                                               lock=threading.RLock())
    runtime.disconnected=lambda c:False
    now=[1000]
    refill=RefillSchedule('Dutch',j,clock=lambda:now[0]);runtime.refills['Dutch']=refill
    j.set('Dutch','refill',{'next_check':1000,'last_checked':None,'pending':False,'status':'waiting','interval_seconds':900})
    j.set('Dutch','enabled',True)
    def mark():
        j.set('Dutch','stock_marker',{'inventory':[i['uid'] for i in state['inventory']],
                                     'booth_count':len(state['booth'])})
    mark()
    return SimpleNamespace(runtime=runtime,j=j,guard=guard,state=state,calls=calls,mark=mark,refill=refill,
                           now=now,history=history,read=read)


def test_free_slot_fills_highest_value_from_old_history_without_web_scan(setup):
    x=setup;x.state['booth']=[stock(100+i,500009,price=100) for i in range(31)];x.mark()
    x.runtime.step('Dutch')
    assert x.calls==[(2,900000)]
    assert len(x.state['booth'])==32
    assert x.j.get('Dutch','inventory_queue')==[1]
    assert x.refill.state()['next_check']==1900 and x.refill.state()['listed']==1
    assert not x.runtime.market_path.exists()
    assert all(q['observed_at']==100 for q in x.history.quotes().values())
    x.runtime.step('Dutch')
    assert len(x.calls)==1


def test_full_booth_keeps_checking_while_operations_are_paused_without_input(setup):
    x=setup;x.state['booth']=[stock(100+i,500009,price=100) for i in range(32)];x.mark()
    x.guard.on_acquire=lambda c:pytest.fail('No input needed for a full booth')
    x.runtime.step('Dutch')
    assert x.refill.state()['status']=='booth_full'
    x.j.set('Dutch','enabled',False);x.now[0]=2000
    x.runtime.step('Dutch')
    assert x.refill.state()['next_check']==2900 and not x.calls


def test_refill_posts_while_operations_remain_paused(setup):
    x=setup;x.runtime.enable('Dutch',False)
    assert not x.runtime.input_allowed('Dutch')  # Permission exists only inside a refill operation.
    x.runtime.step('Dutch')
    assert x.calls==[(2,900000),(1,130000)]
    assert not x.runtime.enabled('Dutch') and not x.runtime.input_allowed('Dutch')
    assert not x.runtime.refilling


def test_refined_inventory_refills_from_normal_history_while_paused(setup):
    x=setup;x.runtime.enable('Dutch',False)
    x.state['inventory']=[stock(1,130806),stock(2,120006)];x.mark()
    x.runtime.step('Dutch')
    assert x.calls==[(2,900000),(1,130000)]
    assert not x.runtime.enabled('Dutch')


def test_explicit_refill_pause_and_global_stop_persist(setup):
    x=setup;x.runtime.enable('Dutch',False)
    x.runtime.set_refill_enabled('Dutch',False)
    x.runtime.step('Dutch')
    assert not x.calls and x.refill.state()['last_checked'] is None
    x.runtime.set_refill_enabled('Dutch',True)
    x.runtime.global_stop()
    x.guard.resume()
    assert not x.runtime.refill_enabled('Dutch') and not x.runtime.refill_enabled('Spiritual')
    x.runtime.step('Dutch');assert not x.calls
    restarted=MerchantRuntime(object(),InputCoordinator(),journal=x.j)
    assert not restarted.refill_enabled('Dutch')


def test_refill_pause_revokes_input_before_first_action(setup):
    x=setup;x.runtime.enable('Dutch',False)
    x.guard.on_acquire=lambda c:x.runtime.set_refill_enabled(c,False)
    with pytest.raises(CaptureUnavailable):x.runtime.step('Dutch')
    assert not x.calls and not x.j.pending('Dutch') and not x.runtime.refilling


def test_surface_change_revokes_refill_even_with_operations_enabled_then_retries(setup):
    x=setup
    x.guard.on_acquire=x.runtime.invalidate_refill
    with pytest.raises(CaptureUnavailable):x.runtime.step('Dutch')
    assert not x.calls and not x.j.pending('Dutch') and not x.runtime.refilling
    assert x.runtime.enabled('Dutch') and x.runtime.refill_enabled('Dutch')
    x.guard.on_acquire=lambda c:None
    x.runtime.step('Dutch')
    assert x.calls==[(2,900000),(1,130000)]


@pytest.mark.parametrize('field',['request','trade'])
def test_refill_never_accepts_incoming_trades_while_operations_paused(setup,field):
    x=setup;x.runtime.enable('Dutch',False);x.state[field]={'participant':'Parasite'}
    x.runtime.controllers['Dutch'].accept_request=lambda *args:pytest.fail('Trading is paused')
    x.runtime.controllers['Dutch'].accept_delivery=lambda *args:pytest.fail('Trading is paused')
    with pytest.raises(CaptureUnavailable,match='Inventory refill waits'):x.runtime.step('Dutch')
    assert not x.calls


def test_refill_controller_rejects_trades_and_reprices(setup):
    from conquest.merchants.refill import RefillController
    x=setup;driver=x.runtime.controllers['Dutch'].driver
    c=RefillController('Dutch',x.j,driver,x.guard)
    with pytest.raises(ValueError,match='cannot reprice'):c.apply_price({'old_price':100,'price':99})
    with pytest.raises(ValueError,match='cannot accept'):c.accept_request({})
    with pytest.raises(ValueError,match='cannot accept'):c.accept_delivery()


def test_empty_inventory_check_is_quiet_and_durable(setup):
    x=setup;x.state['inventory']=[];x.mark();x.runtime.step('Dutch')
    restarted=RefillSchedule('Dutch',x.j,clock=lambda:1001)
    assert not restarted.due() and restarted.state()['status']=='no_stock'
    assert not x.calls


def test_farmer_handoff_wait_remains_pending_then_replans(setup):
    x=setup;x.guard.safe_to_yield=lambda:False
    with pytest.raises(CaptureUnavailable):x.runtime.step('Dutch')
    assert x.refill.state()['pending'] and x.runtime.handoff and not x.calls
    # Another action removes one inventory item while input is unavailable.
    x.state['inventory']=[stock(1)]
    x.guard.safe_to_yield=lambda:True;x.runtime.step('Dutch')
    assert x.calls==[(1,130000)] and not x.refill.state()['pending']


def test_socket_mismatch_and_unknown_type_stay_deferred(setup):
    x=setup;x.state['inventory']=[stock(1,gem1=255),stock(2,999999)];x.mark()
    x.runtime.step('Dutch')
    assert not x.calls and len(x.j.get('Dutch','deferred'))==2
    assert x.refill.state()['deferred']==2


def test_owned_shop_price_is_matched_without_repeated_undercutting(setup):
    x=setup;x.state['inventory']=[stock(1)]
    x.state['booth']=[stock(50,price=100000)];x.mark()
    x.runtime.step('Dutch')
    assert x.calls==[(1,100000)]
    assert next(i['price'] for i in x.state['booth'] if i['uid']==50)==100000


def test_refill_does_not_consume_pending_reprice_request(setup):
    x=setup;x.j.request_scan('Dutch','scheduled:one')
    x.runtime.step('Dutch')
    assert x.j.get('Dutch','scan')['pending'] is True
    assert x.calls==[(2,900000),(1,130000)]


def test_delivery_remainder_only_refills_and_preserves_one_time_scan(setup):
    x=setup;x.runtime.refill_window='delivery'
    x.runtime.recoveries['Dutch'].verified()
    x.j.request_once('Dutch','one-time')
    x.runtime.step('Dutch')
    assert x.calls==[(2,900000),(1,130000)]
    assert x.j.get('Dutch','scan')['pending']


def test_delivery_refill_window_cannot_reconnect_or_accept_trade(setup):
    x=setup;x.runtime.refill_window='delivery'
    x.runtime.recoveries['Dutch'].verified()
    x.state['request']={'participant':'Parasite'}
    x.runtime.step('Dutch')
    assert not x.calls
    x.runtime.disconnected=lambda c:True
    x.runtime.recover=lambda *a,**kw:pytest.fail('Refill may not reconnect')
    x.runtime.step('Dutch')
    assert not x.calls


@pytest.mark.parametrize('window',['delivery_window','refill_window'])
def test_preplanned_listing_cannot_take_focus_during_dedicated_window(setup,monkeypatch,window):
    from conquest.merchants import delivery_reservation
    x=setup
    plan={'price':130000,'old_price':None}
    # The ordinary listing was planned before the protected window began.
    setattr(x.runtime,window,'reserved-delivery')
    monkeypatch.setattr(delivery_reservation,'active',
                        lambda *a:{'request_id':'reserved-delivery'})
    focus=[]
    x.guard.on_acquire=lambda c:focus.append(c)
    x.guard.on_release=lambda c:focus.append('restore')
    with pytest.raises(CaptureUnavailable,match='paused'):
        x.runtime.controllers['Dutch'].apply_price(plan)
    assert not focus and not x.calls and not x.j.pending('Dutch')
    assert x.guard.owner is x.guard.purpose is None


@pytest.mark.parametrize('purpose',[None,'listing','refill','trade'])
def test_delivery_permission_is_only_for_reserved_trade(setup,monkeypatch,purpose):
    from conquest.merchants import delivery_reservation
    x=setup;x.runtime.delivery_window='reserved-delivery'
    monkeypatch.setattr(delivery_reservation,'active',
                        lambda *a:{'request_id':'reserved-delivery'})
    if purpose=='trade':
        with x.guard.lease('Dutch',purpose=purpose):
            x.guard.check()
    else:
        with pytest.raises(CaptureUnavailable,match='paused'):
            with x.guard.lease('Dutch',purpose=purpose):
                pytest.fail('Non-trade work acquired delivery input')


def test_refill_permission_cannot_be_borrowed_by_another_thread(setup):
    x=setup;x.runtime.enable('Dutch',False)
    observed=[]
    def acquire(character):
        assert x.guard.purpose=='refill'
        assert x.runtime.input_allowed(character)
        thread=threading.Thread(target=lambda:observed.append(x.runtime.input_allowed(character)))
        thread.start();thread.join(timeout=2)
        assert not thread.is_alive()
    x.guard.on_acquire=acquire
    x.runtime.step('Dutch')
    assert x.calls==[(2,900000),(1,130000)]
    assert observed==[False,False]
    assert not x.runtime.refill_threads and not x.runtime.refilling


def test_new_stock_can_use_history_when_market_file_is_missing(setup):
    x=setup;x.j.set('Dutch','new_stock',True)
    x.j.set('Dutch','refill',{**x.refill.state(),'next_check':1300})
    x.runtime.step('Dutch')
    assert x.calls==[(2,900000),(1,130000)]
    assert x.refill.state()['last_checked'] is None


def test_old_timer_migrates_once_and_completion_has_no_catchup(tmp_path):
    j=Journal(tmp_path/'journal.sqlite3');now=[1100]
    j.set('Dutch','refill',dict(next_check=1200,last_checked=900,pending=True,status='checking'))
    timer=RefillSchedule('Dutch',j,clock=lambda:now[0])
    assert timer.state()['next_check']==1800
    assert not timer.due()
    now[0]=10000
    assert timer.due()
    timer.complete('no_stock')
    assert timer.state()['next_check']==10900
    restarted=RefillSchedule('Dutch',j,clock=lambda:10001)
    assert not restarted.due()
    assert restarted.state()['next_check']==10900


def test_refill_at_town_resets_full_interval(tmp_path):
    j=Journal(tmp_path/'journal.sqlite3');now=[1000]
    timer=RefillSchedule('Dutch',j,clock=lambda:now[0])
    assert timer.state()['next_check']==1900
    now[0]=1100;timer.start();timer.complete('town_visit',listed=1)
    assert timer.state()['next_check']==2000


@pytest.mark.parametrize('kind',[2000031,2000032,2000033,2000034,2000035,2000036,2000037,2000038])
def test_rare_dragonball_never_refills_or_reprices(setup,kind):
    from conquest.valuables import DRAGONBALL_NAMES
    x=setup;x.state['inventory']=[{**stock(1,kind),'name':DRAGONBALL_NAMES[kind],'plus':0}]
    x.mark();x.runtime.step('Dutch')
    assert not x.calls
    deferred=x.j.get('Dutch','deferred')
    assert deferred[0]['price'] is None and 'storage-only' in deferred[0]['reason']


def test_full_combined_ownership_still_refills_an_existing_empty_booth_slot(setup):
    from conquest.merchants.capacity import available_slots
    x=setup
    x.state['booth']=[stock(100+i,500009,price=100) for i in range(31)]
    x.state['inventory'] += [stock(300+i,500009) for i in range(7)]
    x.mark()
    assert available_slots(x.state)==0
    before={i['uid'] for k in ('inventory','booth') for i in x.state[k]}
    x.runtime.step('Dutch')
    assert x.calls==[(2,900000)]
    assert len(x.state['booth'])==32 and len(x.state['inventory'])==8
    assert available_slots(x.state)==0
    assert {i['uid'] for k in ('inventory','booth') for i in x.state[k]}==before
    assert x.refill.state()['listed']==1


@pytest.mark.parametrize('note,ready',[
    ('Waiting for a safe farmer handoff',True),
    ('Delivery result is uncertain',False),
    ('Another trade needs reconciliation',False)])
def test_delivery_readiness_distinguishes_handoff_wait_from_transaction_errors(setup,note,ready):
    x=setup;x.runtime.latest['Dutch']=x.read()
    x.runtime.errors['Dutch']={'note':note}
    status=x.runtime.status()['Dutch']
    assert status['ready'] is ready
    assert not x.calls  # Readiness never grants input or posts an item.
    x.runtime.enable('Dutch',False)
    assert x.runtime.status()['Dutch']['ready'] is False
