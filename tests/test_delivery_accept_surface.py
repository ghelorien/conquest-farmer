import struct
import threading
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from conquest.merchants.delivery_accept_probe import control, lease_authorized, run
from conquest.merchants.ui import UnifiedUI, callback_failure


def test_accept_control_binds_distinct_rendered_confirm_to_raw_trade_model(monkeypatch):
    """The raw Trade model and rendered ImGui window have distinct pointers."""
    base,model,window=0x140000000,0x100000,0x200000
    handlers={base+0x95fd6:bytes.fromhex('e835dcfaff'),
              base+0x95fdf:bytes.fromhex('b201488bcbe857070000')}
    raw=bytearray(0x250)
    struct.pack_into('<4f',raw,0x18,844,282,200,100)
    struct.pack_into('<2f',raw,0xe8,1036,373)
    struct.pack_into('<f',raw,0x114,18)
    def read(address,size):
        if address==model+12:return b'\x01'
        if address==window:return bytes(raw[:size])
        return handlers[address][:size]
    labels=['Trade###Confirm','Parasite wishes to trade with you.','Accept','Cancel']
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.string',
        lambda _session,address:labels[(address-model-0x48)//0x20])
    driver=NS(observer=NS(adapter=NS(read_block=read)),
              memory=NS(gui=NS(base=base,model=lambda *_args:model)))
    snapshot={'windows':[{'name':'Open Booth###Confirm','address':window,
                          'geometry':[844,282,200,100]}]}
    assert control(driver,snapshot)==({**snapshot['windows'][0],'model_address':model},(944,360))


@pytest.mark.parametrize('address',[0,True])
def test_accept_control_rejects_invalid_rendered_confirmation_address(monkeypatch,address):
    """A rendered confirmation needs its own valid memory address."""
    base,model=0x140000000,0x100000
    handlers={base+0x95fd6:bytes.fromhex('e835dcfaff'),
              base+0x95fdf:bytes.fromhex('b201488bcbe857070000')}
    def read(pointer,size):
        if pointer==model+12:return b'\x01'
        return handlers[pointer][:size]
    labels=['Trade###Confirm','Parasite wishes to trade with you.','Accept','Cancel']
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.string',
        lambda _session,pointer:labels[(pointer-model-0x48)//0x20])
    driver=NS(observer=NS(adapter=NS(read_block=read)),
              memory=NS(gui=NS(base=base,model=lambda *_args:model)))
    snapshot={'windows':[{'name':'Open Booth###Confirm','address':address,
                          'geometry':[844,282,200,100]}]}
    with pytest.raises(ValueError,match='absent or ambiguous'):
        control(driver,snapshot)


def test_accept_control_rejects_trade_when_open_booth_confirmation_also_exists(monkeypatch):
    """Two native confirmation windows are ambiguous, even if Trade is present."""
    base,model,window=0x140000000,0x100000,0x200000
    handlers={base+0x95fd6:bytes.fromhex('e835dcfaff'),
              base+0x95fdf:bytes.fromhex('b201488bcbe857070000')}
    raw=bytearray(0x250)
    struct.pack_into('<4f',raw,0x18,844,282,200,100)
    struct.pack_into('<2f',raw,0xe8,1036,373);struct.pack_into('<f',raw,0x114,18)
    def read(address,size):
        if address==model+12:return b'\x01'
        if address==window:return bytes(raw[:size])
        return handlers[address][:size]
    labels=['Trade###Confirm','Parasite wishes to trade with you.','Accept','Cancel']
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.string',
        lambda _session,address:labels[(address-model-0x48)//0x20])
    driver=NS(observer=NS(adapter=NS(read_block=read)),
              memory=NS(gui=NS(base=base,model=lambda *_args:model)))
    snapshot={'windows':[{'name':'Open Booth###Confirm','address':window,'geometry':[844,282,200,100]},
                         {'name':'Open Booth###Confirm','address':window+0x400,'geometry':[1,1,200,100]}]}
    with pytest.raises(ValueError,match='ambiguous'):
        control(driver,snapshot)


@pytest.mark.parametrize('drift',[('geometry',[844,282,201,100]),('handler',None),('label',None)])
def test_accept_control_rejects_layout_signature_or_raw_button_drift(monkeypatch,drift):
    """All geometry and handler evidence remains exact despite a stale label."""
    base,model,window=0x140000000,0x100000,0x200000
    handlers={base+0x95fd6:bytes.fromhex('e835dcfaff'),
              base+0x95fdf:bytes.fromhex('b201488bcbe857070000')}
    raw=bytearray(0x250)
    struct.pack_into('<4f',raw,0x18,844,282,200,100)
    struct.pack_into('<2f',raw,0xe8,1036,373);struct.pack_into('<f',raw,0x114,18)
    kind,value=drift
    if kind=='handler':handlers[base+0x95fd6]=b'\0'*5
    labels=['Trade###Confirm','Parasite wishes to trade with you.','Accept','Cancel']
    if kind=='label':labels[2]='Yes'
    def read(pointer,size):
        if pointer==model+12:return b'\x01'
        if pointer in (window,window+0x400):return bytes(raw[:size])
        return handlers[pointer][:size]
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.string',
        lambda _session,pointer:labels[(pointer-model-0x48)//0x20])
    driver=NS(observer=NS(adapter=NS(read_block=read)),
              memory=NS(gui=NS(base=base,model=lambda *_args:model)))
    geometry=value if kind=='geometry' else [844,282,200,100]
    snapshot={'windows':[{'name':'Open Booth###Confirm','address':window,'geometry':geometry}]}
    with pytest.raises(ValueError):
        control(driver,snapshot)


def accept_run_fixture(monkeypatch):
    """A no-input staged accept harness; native controls are mocked only."""
    from contextlib import nullcontext
    from conquest import desktop_runtime,foreground
    from conquest.merchants import delivery_probe,delivery_probe_ownership,farmer_preferences,driver as driver_module
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    item={'uid':99,'type_id':720027,'plus':0,'gem1':0,'gem2':0,'quantity':1,'bound':False}
    farmer={'character':'Parasite','character_uid':55,'identity':{'pid':8,'creation_time_100ns':10,'path':'ImConquer.exe'},
            'server':'America','position':[10,10],'silver':200,'inventory':[item],'request':None,'trade':None}
    merchant={'character':'Spiritual','character_uid':123,'identity':identity,'server':'America','position':[20,20],
              'silver':200,'inventory':[],'trade':None,
              'request':{'participant':'Parasite','participant_uid':55,
                         'message':'Parasite wishes to trade with you.','server':'America'}}
    state={'phase':'request_verified','character':'Spiritual','started_at':1,'updated_at':1,
           'intent':{'farmer':deepcopy(farmer),'merchant':{**deepcopy(merchant),'request':None},'items':[item]}}
    target=NS(snapshot=lambda:{'client_size':[1000,800]})
    game_driver=NS(target=target,memory=NS(gui=NS(viewport_size=lambda:[1000,800],assert_hovered=Mock())))
    ui=NS(app=NS(control=NS(snapshot=lambda:{'revision':1,'enabled':False})),
          coordinator=NS(check=lambda:None,lease=lambda *_args,**_kwargs:nullcontext(),
                         manual_session_blocked=lambda *_args,**_kwargs:False,lock=threading.RLock()),
          runtime=NS(controllers={'Spiritual':NS(driver=game_driver)},
                     reconcile_probe_owned=lambda *_args,**_kwargs:True),
          calibrating=set(),calibration_cancel={})
    monkeypatch.setattr('conquest.character_context.registry',lambda:None)
    monkeypatch.setattr(delivery_probe,'read_probe',lambda:deepcopy(state))
    monkeypatch.setattr(delivery_probe_ownership,'ownership',lambda *_args,**_kwargs:{})
    monkeypatch.setattr(farmer_preferences,'permits_new_delivery',lambda *_args:None)
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.control',
        lambda *_args:({'model_address':2,'address':1,'geometry':[1,1,200,100]},(100,100)))
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.write_json',lambda *_args:None)
    monkeypatch.setattr(desktop_runtime,'physical_coordinates',nullcontext)
    monkeypatch.setattr(driver_module,'wait_hover_validation',lambda guard,_check:guard())
    return ui,state,farmer,merchant,foreground


def live_accept_control(ui,monkeypatch,*,stage,model=0x100000,window=0x200000):
    """Install a raw slot-15 control that can change only before press."""
    base=0x140000000
    raw=bytearray(0x250)
    struct.pack_into('<4f',raw,0x18,844,282,200,100)
    struct.pack_into('<2f',raw,0xe8,1036,373);struct.pack_into('<f',raw,0x114,18)
    handlers={base+0x95fd6:bytes.fromhex('e835dcfaff'),
              base+0x95fdf:bytes.fromhex('b201488bcbe857070000')}
    def active_model():return model+0x100 if stage[0]=='model' and stage[1] else model
    def read(pointer,size):
        if pointer==active_model()+12:return b'\x01'
        if pointer in (window,window+0x400):return bytes(raw[:size])
        if pointer in handlers:
            code=handlers[pointer]
            return (b'\0'*len(code) if stage[0]=='handler' and stage[1] else code)[:size]
        raise AssertionError(f'unexpected control read: {pointer:#x}')
    def strings(_session,pointer):
        labels=['Trade###Confirm','Parasite wishes to trade with you.','Accept','Cancel']
        if stage[0]=='raw_string' and stage[1]:labels[2]='Yes'
        offset=pointer-active_model()-0x48
        if offset not in (0,0x20,0x40,0x60):return 'changed'
        return labels[offset//0x20]
    gui=NS(base=base,model=lambda *_args:active_model(),viewport_size=lambda:[1000,800],
           assert_hovered=Mock())
    driver=NS(target=NS(snapshot=lambda:{'client_size':[1000,800]}),memory=NS(gui=gui),
              observer=NS(adapter=NS(read_block=read)))
    ui.runtime.controllers['Spiritual']=NS(driver=driver)
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.string',strings)
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.farmer_name',lambda:'Parasite')
    return gui,window


def live_confirm_snapshot(stage,window=0x200000):
    name='Trade###Confirm' if stage[0]=='label' and stage[1] else 'Open Booth###Confirm'
    address=window+0x400 if stage[0]=='address' and stage[1] else window
    geometry=[844,282,201,100] if stage[0]=='geometry' and stage[1] else [844,282,200,100]
    return {'name':name,'address':address,'geometry':geometry,
            'scroll':[99,7] if stage[0]=='label' and stage[1] else [0,0]}


def test_accept_prepress_ignores_stale_label_and_scroll_change_for_same_raw_control(monkeypatch):
    """Only canonical raw-bound control identity, not registry metadata, gates press."""
    from conquest.merchants import delivery_accept_probe
    ui,state,farmer,merchant,foreground=accept_run_fixture(monkeypatch)
    monkeypatch.setattr(delivery_accept_probe,'control',control)
    stage=['label',False];hovered,window=live_accept_control(ui,monkeypatch,stage=stage)
    merchant['windows']=[live_confirm_snapshot(stage,window)]
    def pairs(*_args):
        current=deepcopy(merchant);current['windows']=[live_confirm_snapshot(stage,window)]
        return deepcopy(farmer),current
    monkeypatch.setattr(delivery_accept_probe,'pair',pairs)
    monkeypatch.setattr(delivery_accept_probe,'validate_offers',lambda *_args:None)
    presses=[]
    def click(*_args,**kwargs):
        stage[1]=True
        kwargs['before_press']()
        farmer['trade']={'participant':'Spiritual'};merchant['trade']={'participant':'Parasite'}
        presses.append(True)
    monkeypatch.setattr(foreground,'foreground_click',click)
    run(ui,state)
    assert presses==[True]
    assert hovered.assert_hovered.call_args.args[0]['name']=='Trade###Confirm'


@pytest.mark.parametrize('kind',['address','model','geometry','raw_string','handler'])
def test_accept_prepress_control_drift_never_reaches_press(monkeypatch,kind):
    """Every authority-bearing control change still fails before foreground input."""
    from conquest.merchants import delivery_accept_probe
    ui,state,farmer,merchant,foreground=accept_run_fixture(monkeypatch)
    monkeypatch.setattr(delivery_accept_probe,'control',control)
    stage=[kind,False];_gui,window=live_accept_control(ui,monkeypatch,stage=stage)
    merchant['windows']=[live_confirm_snapshot(stage,window)]
    def pairs(*_args):
        current=deepcopy(merchant);current['windows']=[live_confirm_snapshot(stage,window)]
        return deepcopy(farmer),current
    monkeypatch.setattr(delivery_accept_probe,'pair',pairs)
    presses=[]
    def click(*_args,**kwargs):
        stage[1]=True
        kwargs['before_press']()
        presses.append(True)
    monkeypatch.setattr(foreground,'foreground_click',click)
    with pytest.raises(ValueError):run(ui,state)
    assert presses==[]


def test_accept_rejects_one_wrong_rendered_confirm_when_accept_hover_id_is_absent(monkeypatch):
    """A hidden/wrong confirmation cannot redirect an exact raw Trade model."""
    from conquest.merchants import delivery_accept_probe
    ui,state,farmer,merchant,foreground=accept_run_fixture(monkeypatch)
    monkeypatch.setattr(delivery_accept_probe,'control',control)
    stage=['label',False];gui,window=live_accept_control(ui,monkeypatch,stage=stage)
    # The raw model proves Trade, while this lone rendered dialog has the
    # stale Open Booth name and lacks the ImGui Accept control ID.
    gui.assert_hovered.side_effect=ValueError('Pointer is not over the memory-identified merchant control')
    merchant['windows']=[live_confirm_snapshot(stage,window)]
    def pairs(*_args):
        current=deepcopy(merchant);current['windows']=[live_confirm_snapshot(stage,window)]
        return deepcopy(farmer),current
    monkeypatch.setattr(delivery_accept_probe,'pair',pairs)
    presses=[]
    def click(*_args,**kwargs):
        kwargs['before_press']()
        presses.append(True)
    monkeypatch.setattr(foreground,'foreground_click',click)
    with pytest.raises(ValueError,match='not over'):
        run(ui,state)
    assert presses==[]


@pytest.mark.parametrize('field,value',[('participant_uid',56),('message','Parasite wishes to trade?'),('server','Europe')])
def test_accept_first_fresh_request_mutation_never_calls_foreground_click(monkeypatch,field,value):
    ui,state,farmer,merchant,foreground=accept_run_fixture(monkeypatch)
    reads=[0]
    def pairs(*_args):
        reads[0]+=1
        if reads[0]==2:merchant['request'][field]=value
        return deepcopy(farmer),deepcopy(merchant)
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.pair',pairs)
    click=Mock();monkeypatch.setattr(foreground,'foreground_click',click)
    with pytest.raises(ValueError,match='Expected incoming'):
        run(ui,state)
    click.assert_not_called()


@pytest.mark.parametrize('field,value',[('participant_uid',56),('message','Parasite wishes to trade?'),('server','Europe')])
def test_accept_before_press_request_mutation_never_reaches_press(monkeypatch,field,value):
    ui,state,farmer,merchant,foreground=accept_run_fixture(monkeypatch)
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.pair',lambda *_args:(deepcopy(farmer),deepcopy(merchant)))
    presses=[]
    def click(*_args,**kwargs):
        merchant['request'][field]=value
        kwargs['before_press']()
        presses.append(True)
    monkeypatch.setattr(foreground,'foreground_click',click)
    with pytest.raises(ValueError,match='Expected incoming'):
        run(ui,state)
    assert presses==[]


def request_verified(tmp_path,monkeypatch,identity,character='Spiritual',*,target_profile_id=None,server='America'):
    from conquest.merchants import delivery_probe
    path=tmp_path/'probe.json'
    monkeypatch.setattr(delivery_probe,'JOURNAL',path)
    delivery_probe.write_probe(path,{'phase':'request_verified','character':character,
        'target_profile_id':target_profile_id or character,
        'intent':{'merchant':{'character':character,'server':server,'identity':identity}}})


def accept_lease_ui(tmp_path,monkeypatch,identity):
    request_verified(tmp_path,monkeypatch,identity)
    monkeypatch.setattr('conquest.character_context.registry',lambda:None)
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    runtime=NS(observers={'Spiritual':observer},delivery_window=None,refill_window=None,
               refilling={},journal=NS(pending=lambda _character:False))
    ui=NS(coordinator=NS(purpose='delivery_accept_probe',manual_session_blocked=lambda *_args,**_kwargs:False),runtime=runtime,
          delivery_probe_thread=NS(ident=threading.get_ident(),is_alive=lambda:True),
          calibrating={'Spiritual'},calibration_cancel={'Spiritual':threading.Event()})
    monkeypatch.setattr('conquest.merchants.delivery_reservation.active',lambda *_args:None)
    return ui,observer


def test_disabled_exact_accept_probe_has_its_own_narrow_lease_permission(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    ui,observer=accept_lease_ui(tmp_path,monkeypatch,identity)
    assert lease_authorized(ui,'Spiritual')
    observer.adapter.assert_identity.assert_called_once_with()


@pytest.mark.parametrize('blocker',[
    'window','refill_window','refilling','reservation','pending','cancelled','wrong_worker','wrong_purpose','wrong_identity',
])
def test_accept_probe_lease_rejects_competing_or_changed_work(tmp_path,monkeypatch,blocker):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    ui,observer=accept_lease_ui(tmp_path,monkeypatch,identity)
    if blocker=='window':ui.runtime.delivery_window='ordinary-delivery'
    elif blocker=='refill_window':ui.runtime.refill_window='refill'
    elif blocker=='refilling':ui.runtime.refilling={'Spiritual':0}
    elif blocker=='reservation':monkeypatch.setattr('conquest.merchants.delivery_reservation.active',lambda *_args:{'phase':'reserved'})
    elif blocker=='pending':ui.runtime.journal.pending=lambda _character:True
    elif blocker=='cancelled':ui.calibration_cancel['Spiritual'].set()
    elif blocker=='wrong_worker':ui.delivery_probe_thread.ident=threading.get_ident()+1
    elif blocker=='wrong_purpose':ui.coordinator.purpose='trade'
    elif blocker=='wrong_identity':observer.adapter.identity={**identity,'pid':8}
    assert not lease_authorized(ui,'Spiritual')


@pytest.mark.parametrize('hold_phase',['manual_active','approval_pending'])
def test_accept_probe_lease_checks_uuid_scoped_manual_hold_before_disabled_probe(tmp_path,monkeypatch,hold_phase):
    from conquest.character_profiles import ProfileRegistry
    from conquest.character_context import ProfileMap,ProfileName
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    registry=ProfileRegistry(tmp_path/'profiles');profile=registry.add('Spiritual',role='Merchant')
    monkeypatch.setenv('CONQUEST_DATA_ROOT',str(registry.root))
    request_verified(tmp_path,monkeypatch,identity,target_profile_id=profile.id)
    key=ProfileName(profile.name,profile.id);seen=[]
    observers=ProfileMap();observers[key]=NS(adapter=NS(identity=identity,assert_identity=Mock()),
                                             operations=NS(target=NS(hwnd=77)))
    runtime=NS(observers=observers,delivery_window=None,refill_window=None,refilling={},
               journal=NS(pending=lambda _character:False))
    ui=NS(coordinator=NS(purpose='delivery_accept_probe',manual_session_blocked=lambda value,**_kwargs:(seen.append(value) or True)),
          runtime=runtime,delivery_probe_thread=NS(ident=threading.get_ident(),is_alive=lambda:True),
          calibrating={key},calibration_cancel={key:threading.Event()})
    assert not lease_authorized(ui,key)
    assert seen[-1].profile_id==profile.id


def bind_accept_binding(ui):
    ui.delivery_accept_binding=lambda character,expected=None:UnifiedUI.delivery_accept_binding(ui,character,expected)


def bound_show_ui(identity, *, layout=None, pane_size=(1200,800)):
    api=NS(assert_owner=Mock(),show_async=Mock(),gui=NS(GetForegroundWindow=lambda:0),backend=NS())
    host=NS(saved=NS(hwnd=77,identity=identity),api=api,mode='owned',resize=Mock(),detach=Mock())
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    selected=['overview']
    def select(value=None):
        if value is not None:selected[0]=value
        return selected[0]
    ui=NS(closed=False,app=NS(closing=False),runtime=NS(observers={'Spiritual':observer}),
          hosts={'Spiritual':host},notebook=NS(select=select),frames={'Spiritual':'spiritual-frame'},
          detail_tabs={'Spiritual':NS(select=Mock())},root=NS(update_idletasks=Mock()),
          input_bookmarks={},client_panes={'Spiritual':NS(winfo_width=lambda:pane_size[0],
              winfo_height=lambda:pane_size[1])},apply_client_compact_layout=layout)
    bind_accept_binding(ui)
    return ui,host,api,observer


def managed_registry_stub():
    return NS(resolve=lambda profile_id,**_kwargs:NS(id='Spiritual',name='Spiritual',
        server='America',local_enabled=True))


def managed_show_ui(tmp_path,monkeypatch,identity):
    from conquest.character_profiles import ProfileRegistry
    from conquest.character_context import ProfileMap,ProfileName
    registry=ProfileRegistry(tmp_path/'profiles')
    profile=registry.add('Spiritual',role='Merchant')
    monkeypatch.setenv('CONQUEST_DATA_ROOT',str(registry.root))
    request_verified(tmp_path,monkeypatch,identity,target_profile_id=profile.id)
    ui,host,api,observer=bound_show_ui(identity)
    key=ProfileName(profile.name,profile.id)
    for name,value in [('observers',observer),('hosts',host),('frames','spiritual-frame'),
                       ('detail_tabs',ui.detail_tabs['Spiritual']),('client_panes',ui.client_panes['Spiritual'])]:
        mapping=ProfileMap();mapping[key]=value
        if name=='observers':ui.runtime.observers=mapping
        else:setattr(ui,name,mapping)
    ui.notebook=NS(select=Mock(return_value='overview'))
    return registry,profile,key,ui,host,api


def test_accept_surface_embeds_only_the_exact_observer(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    api=NS(assert_owner=Mock())
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),
                operations=NS(target=NS(hwnd=77)))
    # An already-owned matching host is re-shown, not detached or replaced.
    ui=NS(runtime=NS(observers={'Spiritual':observer}),
             hosts={'Spiritual':NS(saved=NS(hwnd=77,identity=identity),api=api,mode='owned')},
             embed_merchant=Mock(),show_merchant=Mock(),
             embed_delivery_accept_merchant=Mock(),show_delivery_accept_merchant=Mock())
    bind_accept_binding(ui)
    UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    assert observer.adapter.assert_identity.call_count>=2
    assert all(call.args==(77,identity) for call in api.assert_owner.call_args_list)
    ui.embed_merchant.assert_not_called()
    ui.show_delivery_accept_merchant.assert_called_once_with('Spiritual',(77,identity,'Spiritual'))
    ui.show_merchant.assert_not_called()


def test_accept_surface_rejects_changed_saved_host_without_showing_or_reembedding(tmp_path,monkeypatch):
    saved=NS(hwnd=78,identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'})
    api=NS(assert_owner=Mock())
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),
                operations=NS(target=NS(hwnd=77)))
    ui=NS(runtime=NS(observers={'Spiritual':observer}),
          hosts={'Spiritual':NS(saved=saved,api=api,mode='owned')},
          embed_merchant=Mock(),show_merchant=Mock())
    bind_accept_binding(ui)
    with pytest.raises(ValueError,match='host changed'):
        UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    assert api.assert_owner.call_args_list==[
        ((77,identity),{}),((78,saved.identity),{})]
    ui.embed_merchant.assert_not_called()
    ui.show_merchant.assert_not_called()


def test_accept_surface_rejects_rebound_target_before_showing(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    api=NS(assert_owner=Mock(side_effect=ValueError('target process changed')))
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    ui=NS(runtime=NS(observers={'Spiritual':observer}),
          hosts={'Spiritual':NS(saved=NS(hwnd=77,identity=identity),api=api,mode='owned')},
          embed_merchant=Mock(),show_merchant=Mock())
    bind_accept_binding(ui)
    with pytest.raises(ValueError,match='target process changed'):
        UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    observer.adapter.assert_identity.assert_called_once_with()
    api.assert_owner.assert_called_once_with(77,identity)
    ui.embed_merchant.assert_not_called()
    ui.show_merchant.assert_not_called()


@pytest.mark.parametrize('changed',[('phase','accept_submitted'),('character','Dutch'),
                                    ('identity',{'pid':8,'creation_time_100ns':10})])
def test_accept_surface_requires_active_journal_binding_before_window_action(tmp_path,monkeypatch,changed):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    from conquest.merchants import delivery_probe
    state=delivery_probe.read_probe()
    field,value=changed
    if field=='identity':state['intent']['merchant']['identity']={**value,'path':'ImConquer.exe'}
    else:state[field]=value
    delivery_probe.write_probe(delivery_probe.JOURNAL,state)
    api=NS(assert_owner=Mock())
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    ui=NS(runtime=NS(observers={'Spiritual':observer}),
          hosts={'Spiritual':NS(saved=NS(hwnd=77,identity=identity),api=api,mode='owned')},
          embed_merchant=Mock(),show_merchant=Mock())
    bind_accept_binding(ui)
    with pytest.raises(ValueError):UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    observer.adapter.assert_identity.assert_not_called()
    api.assert_owner.assert_not_called()
    ui.embed_merchant.assert_not_called()
    ui.show_merchant.assert_not_called()


@pytest.mark.parametrize('phase',['embed','show'])
def test_accept_surface_rechecks_after_specialized_embed_or_show_observer_swap(tmp_path,monkeypatch,phase):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    api=NS(assert_owner=Mock())
    host=NS(saved=None if phase=='embed' else NS(hwnd=77,identity=identity),api=api,
            mode='owned',attach=Mock(),detach=Mock())
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    replacement=NS(adapter=NS(identity={'pid':8,'creation_time_100ns':10,'path':'ImConquer.exe'},
                               assert_identity=Mock()),operations=NS(target=NS(hwnd=88)))
    runtime=NS(observers={'Spiritual':observer})
    def swap(*_args):runtime.observers['Spiritual']=replacement
    ui=NS(runtime=runtime,hosts={'Spiritual':host},embed_merchant=Mock(),show_merchant=Mock(),
          embed_delivery_accept_merchant=Mock(side_effect=swap),
          show_delivery_accept_merchant=Mock(side_effect=swap))
    bind_accept_binding(ui)
    with pytest.raises(ValueError,match='exact merchant process'):
        UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    if phase=='embed':
        ui.embed_delivery_accept_merchant.assert_called_once_with('Spiritual',(77,identity,'Spiritual'))
        ui.show_delivery_accept_merchant.assert_not_called()
    else:
        ui.show_delivery_accept_merchant.assert_called_once_with('Spiritual',(77,identity,'Spiritual'))
    host.attach.assert_not_called()
    host.detach.assert_not_called()
    ui.embed_merchant.assert_not_called()
    ui.show_merchant.assert_not_called()


def test_actual_specialized_show_rejects_undersized_attached_pane_before_resize(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    monkeypatch.setattr('conquest.character_context.registry',managed_registry_stub)
    ui,host,api,_observer=bound_show_ui(identity,pane_size=(100,100))
    with pytest.raises(ValueError,match='viewport'):
        UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,'Spiritual'))
    host.resize.assert_not_called()
    host.detach.assert_not_called()
    api.show_async.assert_not_called()


def test_undersized_accept_pane_does_not_hide_sibling_host(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    monkeypatch.setattr('conquest.character_context.registry',managed_registry_stub)
    ui,host,api,_observer=bound_show_ui(identity,pane_size=(100,100))
    sibling_api=NS(assert_owner=Mock(),show_async=Mock())
    sibling=NS(saved=NS(hwnd=88,identity={'pid':8,'creation_time_100ns':10,'path':'ImConquer.exe'}),
               api=sibling_api,mode='owned',detach=Mock())
    ui.hosts['Dutch']=sibling
    with pytest.raises(ValueError,match='viewport'):
        UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,'Spiritual'))
    host.resize.assert_not_called()
    host.detach.assert_not_called()
    api.show_async.assert_not_called()
    sibling_api.show_async.assert_not_called()
    sibling.detach.assert_not_called()


def test_actual_specialized_show_rechecks_observer_after_layout_before_resize(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    monkeypatch.setattr('conquest.character_context.registry',lambda:None)
    ui,host,api,_observer=bound_show_ui(identity)
    replacement=NS(adapter=NS(identity={'pid':8,'creation_time_100ns':10,'path':'ImConquer.exe'},
                               assert_identity=Mock()),operations=NS(target=NS(hwnd=88)))
    ui.apply_client_compact_layout=lambda:ui.runtime.observers.update(Spiritual=replacement)
    with pytest.raises(ValueError,match='exact merchant process'):
        UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,'Spiritual'))
    host.resize.assert_not_called()
    host.detach.assert_not_called()
    api.show_async.assert_not_called()


@pytest.mark.parametrize('fault',['missing','disabled','renamed','different_profile','other_server'])
def test_managed_accept_profile_binding_fails_before_any_tab_or_window_mutation(tmp_path,monkeypatch,fault):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    registry,profile,key,ui,host,api=managed_show_ui(tmp_path,monkeypatch,identity)
    from conquest.merchants import delivery_probe
    state=delivery_probe.read_probe()
    if fault=='missing':state['target_profile_id']='missing-profile'
    elif fault=='disabled':registry.update(profile.id,{'local_enabled':False})
    elif fault=='renamed':
        state['character']=state['intent']['merchant']['character']='Renamed'
    elif fault=='different_profile':
        other=registry.add('Dutch',role='Merchant')
        state['target_profile_id']=other.id
    else:
        other=registry.add('Spiritual',server='Europe',role='Merchant')
        state['target_profile_id']=other.id
    delivery_probe.write_probe(delivery_probe.JOURNAL,state)
    with pytest.raises(ValueError,match='profile|request-verified'):
        UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,profile.id))
    ui.notebook.select.assert_not_called()
    ui.root.update_idletasks.assert_not_called()
    host.resize.assert_not_called()
    host.detach.assert_not_called()
    api.show_async.assert_not_called()


def test_managed_profile_name_binds_plain_journal_character_for_specialized_show(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    _registry,profile,key,ui,host,_api=managed_show_ui(tmp_path,monkeypatch,identity)
    UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,profile.id))
    assert ui.notebook.select.call_args_list[-1].args==('spiritual-frame',)
    host.resize.assert_called_once_with(1200,800)
    assert key.profile_id==profile.id


def test_managed_prepare_input_uses_profile_key_after_callback(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    _registry,profile,key,ui,host,_api=managed_show_ui(tmp_path,monkeypatch,identity)
    class ImmediateQueue:
        def put(self,entry):
            callback,done,result=entry
            callback()
            if done:done.set()
    waited=Mock()
    monkeypatch.setattr('conquest.merchants.ui.wait_for_merchant_surface',waited)
    ui.coordinator=NS(purpose='delivery_accept_probe',check=Mock())
    ui.safe_to_yield=lambda:True
    ui.ui_requests=ImmediateQueue()
    ui.grant_fence=None
    ui.prepare_delivery_accept_surface=lambda character:UnifiedUI.prepare_delivery_accept_surface(ui,character)
    ui.show_delivery_accept_merchant=lambda character,expected:UnifiedUI.show_delivery_accept_merchant(ui,character,expected)
    UnifiedUI.prepare_input(ui,'Spiritual')
    assert waited.call_args.args[0] is host
    assert key.profile_id==profile.id


def test_ui_callback_failure_preserves_callback_and_cause_details():
    try:
        try:
            raise OSError('native pane resize failed')
        except OSError as cause:
            raise RuntimeError('handoff callback failed') from cause
    except RuntimeError as error:
        detail=callback_failure(error)
    assert detail == ('Embedded client UI action failed: RuntimeError: handoff callback failed'
                      ' <- OSError: native pane resize failed')


def test_ui_callback_failure_is_single_line_and_bounded_for_adversarial_messages():
    try:
        raise RuntimeError('first\n\tsecond\x00'+'z'*1000)
    except RuntimeError as error:
        detail=callback_failure(error)
    assert '\n' not in detail and '\r' not in detail and '\x00' not in detail
    assert 'first second' in detail
    assert len(detail)<=640
