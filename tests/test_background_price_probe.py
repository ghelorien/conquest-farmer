from copy import deepcopy
import struct
from types import SimpleNamespace
import zlib

import pytest

from conquest.merchants import background_price_probe as module


def fixture(monkeypatch, failure=None):
    clock = [0.]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0]+delay))
    item = dict(uid=123, slot=0, bound=False, type_id=410009, plus=1, gem1=0, gem2=0, quantity=1)
    inventory = dict(name='Inventory/##ItemGrid_A800F95C', address=0x100000,
                     geometry=[0., 0., 400., 400.], scroll=[0., 0.])
    booth = dict(name='Booth', address=0x200000, geometry=[420., 20., 200., 200.], scroll=[0., 0.])
    modal = dict(name=module.DIALOG, address=0x300000, geometry=[200., 200., 264., 92.], scroll=[0., 0.])
    current = dict(identity={'pid':7}, inventory=[item], booth=[], booth_open=True, silver=0,
                   position=[10,10], windows=[inventory,booth], trade=None, request=None)
    before = deepcopy(current)
    controls = {name:dict(offset=point, size=[264.,92.], window=module.DIALOG) for name,point in
                [('price_field',[72.,53.]), ('cancel_listing',[196.,75.]), ('confirm_listing',[68.,75.])]}
    monkeypatch.setattr(module, 'modal_controls', lambda session,snapshot: deepcopy(controls))
    geometry = dict(window=inventory['address'], points=[(40,40),(54,40)])
    monkeypatch.setattr(module, '_geometry', lambda *args: deepcopy(geometry))
    price, uid = [b''], [0]
    seed = 777
    field = zlib.crc32(b'##Amount',seed)
    cancel = zlib.crc32(b'Cancel',seed)
    neutral = dict(active={'id':0}, drag={'active':False}, queue={'size':0},
                   backend={'buttons_down':0}, mouse_down=[False]*5, modifiers={'ctrl':False},
                   want_capture_mouse=True, hover={'window':inventory['address'],'id':888},
                   mouse_position=[520.,120.])
    state = deepcopy(neutral)
    def read(address,size):
        if address == 0x400000+0x50 and size == 16:
            return struct.pack('<I',uid[0])+price[0].ljust(12,b'\0')
        if address == modal['address']+8 and size == 4:
            return struct.pack('<I',seed)
        raise AssertionError((address,size))
    session = SimpleNamespace(identity={'pid':7},assert_identity=lambda:None,read_block=read)
    posts,stages = [],[]
    def post(message,flags,packed):
        posts.append((message,flags,packed))
        if message == 0x102 and 48 <= flags <= 57:
            if failure == 'text':
                price[0] = b'9'
            else:
                price[0] += bytes([flags])
    target = SimpleNamespace(snapshot=lambda:{'client_size':[800,600]},post=post)
    gui = SimpleNamespace(viewport_size=lambda:[800,600],model=lambda *args:0x400000)
    driver = SimpleNamespace(target=target,memory=SimpleNamespace(gui=gui),read=lambda:deepcopy(current))
    reader = SimpleNamespace(session=session,snapshot=lambda:deepcopy(state))
    report = {'samples':[]}
    def sample(stage):
        stages.append(stage)
        state.clear();state.update(deepcopy(neutral))
        if stage in ('price-source-press','price-drag-payload','price-drop-hover'):
            state['active']['id']=888;state['mouse_down'][0]=True;state['backend']['buttons_down']=1
        if stage in ('price-drag-payload','price-drop-hover'):
            state['drag']=dict(active=True,payload_type='CQITEM',source_item_uid=123,source_id=888,delivery=False)
            if failure == 'payload':state['drag']['source_item_uid']=456
        if stage == 'price-drop-hover':
            state['hover']['window']=0x900000 if failure == 'drop-window' else booth['address']
        if stage == 'price-dialog-open':
            if modal not in current['windows']:current['windows'].append(modal)
            uid[0] = 456 if failure == 'uid' else 123
            if failure == 'nonblank':price[0]=b'42'
        if stage.startswith('price_field'):
            state['hover']={'window':modal['address'],'id':field}
            if stage != 'price_field-hover':state['active']['id']=field
        if stage.startswith('cancel_listing'):
            state['hover']={'window':modal['address'],'id':cancel}
            if stage != 'cancel_listing-hover':state['active']['id']=cancel
        if stage.endswith('-press'):
            state['mouse_down'][0]=True;state['backend']['buttons_down']=1
        if stage == 'cancel_listing-release' and failure != 'cancel':
            current['windows']=[w for w in current['windows'] if w['name']!=module.DIALOG]
            uid[0]=0
        if stage == 'price-text-verified':state['active']['id']=field
        report['samples'].append({'stage':stage,'gui':deepcopy(state)})
    return (driver,before,reader,report,sample,lambda:None),posts,stages,current,price,uid,clock


def test_exact_uid_price_text_and_cancel_without_confirm_or_enter(monkeypatch):
    args,posts,stages,current,price,uid,clock = fixture(monkeypatch)
    report = module.run_price_probe(*args)
    assert report['price_probe_verified'] and report['price_cancel_verified']
    assert report['price_text_verified']==123456 and report['listing_submitted'] is False
    assert uid[0]==0 and all(w['name']!=module.DIALOG for w in current['windows'])
    assert bytes(p[1] for p in posts if p[0]==0x102 and p[1]!=1)==b'123456'
    assert all(p[0] in (0x200,0x201,0x202,0x102) for p in posts)
    confirm_point=268 | 275<<16
    assert all(p[2]!=confirm_point for p in posts if p[0] in (0x201,0x202))


@pytest.mark.parametrize('failure',['uid','nonblank','payload','drop-window','text','cancel'])
def test_failures_never_confirm_and_only_cancel_same_uid(monkeypatch,failure):
    args,posts,stages,current,price,uid,clock=fixture(monkeypatch,failure)
    with pytest.raises(ValueError):module.run_price_probe(*args)
    report=args[3]
    assert report['listing_submitted'] is False and not report.get('price_probe_verified')
    assert clock[0]<=12.01
    if failure=='uid':
        assert report['price_requires_attention'] and uid[0]==456
        assert 'cancel_listing-hover' not in stages
    elif failure in ('nonblank','text'):
        assert report['price_failure_cancel_verified'] and uid[0]==0
    elif failure in ('payload','drop-window'):
        assert report['price_emergency_release_sent']
        assert not any(w['name']==module.DIALOG for w in current['windows'])
    else:
        assert report['price_requires_attention'] and uid[0]==123


def test_full_booth_rejected_before_input(monkeypatch):
    args,posts,*_=fixture(monkeypatch)
    args[0].read=lambda:{**deepcopy(args[1]),'booth':[{}]*32}
    # Retain the capacity guard as the first action after the stock guard.
    args[1]['booth']=[dict(args[1]['inventory'][0],uid=200+i,price=1) for i in range(32)]
    args[0].read=lambda:deepcopy(args[1])
    with pytest.raises(ValueError,match='space'):module.run_price_probe(*args)
    assert posts==[]
