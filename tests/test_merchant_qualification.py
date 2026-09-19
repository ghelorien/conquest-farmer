import copy
import json
import struct
from types import SimpleNamespace
from unittest.mock import Mock
import zlib

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants.coordination import InputCoordinator
from conquest.merchants.driver import MerchantDriver,booth_dialog_ready,wait_hover_validation
from conquest.merchants.journal import Journal
from conquest.merchants.memory import GuiReader,GuiObservationChanged,HoverNotReady
from conquest.merchants.qualification import modal_controls,verify_booth_controls
from conquest.memory_life import CLIENT_SHA256


def test_price_dialog_must_finish_initial_auto_size_before_input():
    modal={'name':'Add Item to Booth','geometry':[10,20,32,32]}
    state={'windows':[modal]}
    assert not booth_dialog_ready(state)
    modal['geometry'][2:]=[264,92]
    assert booth_dialog_ready(state)
    state['windows'].append(copy.deepcopy(modal))
    assert not booth_dialog_ready(state)


@pytest.mark.parametrize('change',['price','owner','displayed_owner','selected_item','open','model'])
def test_native_price_typing_is_not_a_booth_ownership_change(change):
    from conquest.merchants.memory import assert_booth_stable
    before=bytearray(0x58);after=bytearray(before);owned=900
    offset={'price':0x54,'displayed_owner':0x4c,'selected_item':0x50,'open':12,'model':0}.get(change)
    if offset is not None:after[offset]=1
    if change=='owner':owned+=1
    session=SimpleNamespace(read_block=lambda addr,size:
        struct.pack('<I',owned) if addr==0x10000+0x3258 else bytes(after))
    if change=='price':assert_booth_stable(session,0x10000,0x20000,900,bytes(before))
    else:
        with pytest.raises(ValueError,match='ownership or selected item'):
            assert_booth_stable(session,0x10000,0x20000,900,bytes(before))


@pytest.mark.parametrize('mode',['ready','wrong','changed','stop'])
def test_hover_wait_rechecks_whole_guard_and_never_bypasses_wrong_control(mode):
    elapsed=[0.0];reads=[]
    def sleep(seconds):elapsed[0]+=seconds
    def validate():
        reads.append(elapsed[0])
        if mode=='changed' and len(reads)>1:raise ValueError('Item order changed')
        if mode!='ready' or elapsed[0]<.1:raise HoverNotReady('Wrong control')
        return 'verified'
    def check():
        if mode=='stop' and elapsed[0]>=.04:raise CaptureUnavailable('Manual takeover')
    args=dict(clock=lambda:elapsed[0],sleep=sleep)
    if mode=='ready':assert wait_hover_validation(validate,check,**args)=='verified'
    else:
        with pytest.raises(CaptureUnavailable if mode=='stop' else ValueError):
            wait_hover_validation(validate,check,**args)
    assert elapsed[0]<=.52


@pytest.mark.parametrize('mode',['settles','persistent','invalid'])
def test_gui_resampling_is_bounded_and_only_retries_renderer_races(monkeypatch,mode):
    calls=[]
    def sample(self):
        calls.append(1)
        if mode=='invalid':raise ValueError('Invalid GUI registry')
        if mode=='persistent' or len(calls)<2:raise GuiObservationChanged('GUI registry changed')
        return [{'name':'Add Item to Booth'}]
    monkeypatch.setattr(GuiReader,'_windows',sample)
    if mode=='settles':
        assert GuiReader.windows(object())==[{'name':'Add Item to Booth'}]
        assert len(calls)==2
    else:
        with pytest.raises(ValueError):GuiReader.windows(object())
        assert len(calls)==(3 if mode=='persistent' else 1)


class Memory:
    def __init__(self):
        self.data = bytearray(0x30000)
    def read_block(self,address,size):
        return bytes(self.data[address:address+size])
    def put(self,address,fmt,*values):
        struct.pack_into(fmt,self.data,address,*values)


def test_live_table_is_bound_to_window_frame_and_column_geometry():
    memory = Memory()
    context,array,window,columns = 0x11000,0x16000,0x18000,0x19000
    memory.put(0x10000,'<Q',context)
    memory.put(context+0x4338,'<IIQ',1,1,array)
    memory.put(context+0x3e38,'<I',100)
    memory.put(window+8,'<I',123)
    memory.put(array,'<I',zlib.crc32(b'##ItemTable',123))
    memory.put(array+0x18,'<Q',columns)
    memory.put(array+0x70,'<II',100,2)
    memory.put(array+0xf0,'<4f',100,50,180,210)
    memory.put(array+0x120,'<4f',100,80,180,200)
    memory.put(array+0x180,'<2Q',window,window)
    memory.put(array+0x1ac,'<f',40)
    for i in range(2):
        memory.put(columns+i*104+8,'<2f',100+i*40,140+i*40)
        memory.put(columns+i*104+52,'<f',100+i*40)
    gui = SimpleNamespace(session=memory,base=0x10000-0x6966f0)
    table = GuiReader.table(gui,{'address':window},'##ItemTable')
    assert table['row_height']==40 and table['clip']==(100,80,180,200)
    memory.put(array+0x188,'<Q',window+100)
    with pytest.raises(ValueError,match='ownership'):
        GuiReader.table(gui,{'address':window},'##ItemTable')
    memory.put(array+0x188,'<Q',window)
    memory.put(array+0x70,'<I',80)
    with pytest.raises(ValueError,match='currently rendered'):
        GuiReader.table(gui,{'address':window},'##ItemTable')


def modal_fixture(memory):
    address = 0x10000
    memory.put(address+0x18,'<4f',568,334,264,92)
    memory.put(address+0xe8,'<2f',824,400)
    memory.put(address+0x114,'<f',18)
    memory.put(address+0xf0,'<2f',576,360)
    return {'name':'Add Item to Booth','address':address,'geometry':[568.,334.,264.,92.],'scroll':[0.,0.]}


def test_hover_guard_rejects_overlapping_windows_and_other_buttons():
    memory = Memory()
    context,window = 0x11000,0x18000
    memory.put(0x10000,'<Q',context)
    memory.put(window+8,'<I',123)
    memory.put(context+0x3ec0,'<Q',window)
    memory.put(context+0x3ef0,'<I',zlib.crc32(b'Cancel',123))
    gui = SimpleNamespace(session=memory,base=0x10000-0x6966f0)
    GuiReader.assert_hovered(gui,{'address':window},'Cancel')
    with pytest.raises(ValueError,match='not over'):
        GuiReader.assert_hovered(gui,{'address':window},'OK')
    memory.put(context+0x3ec0,'<Q',window+100)
    with pytest.raises(ValueError,match='not over'):
        GuiReader.assert_hovered(gui,{'address':window},'Cancel')


def test_price_controls_use_window_local_layout_and_reject_resize():
    memory = Memory()
    modal = modal_fixture(memory)
    result = modal_controls(memory,{'windows':[modal]})
    assert result['price_field']['offset']==[72,53]
    assert result['confirm_listing']['offset']==[68,75]
    assert result['cancel_listing']['offset']==[196,75]
    memory.put(modal['address']+0x20,'<f',300)
    with pytest.raises(ValueError,match='layout differs'):
        modal_controls(memory,{'windows':[modal]})


def test_point_uses_current_gui_and_native_geometry_after_resize(tmp_path):
    profile = tmp_path/'qualification.json'
    profile.write_text(json.dumps({'client_size':[1250,1000],'gui_size':[1000,800],
        'controls':{'price_field':{'window':'Dialog','size':[200,100],'offset':[80,40]}}}))
    current = [1000,800]
    native=[1500,1200]
    driver = SimpleNamespace(qualification=profile,
        memory=SimpleNamespace(gui=SimpleNamespace(viewport_size=lambda:current)),
        target=SimpleNamespace(snapshot=lambda:{'client_size':native}))
    driver._control_layout=lambda snapshot,control,spec:MerchantDriver._control_layout(
        driver,snapshot,control,spec)
    state = {'windows':[{'name':'Dialog','geometry':[100,100,200,100],'scroll':[0,0]}]}
    assert MerchantDriver.point(driver,state,'price_field')==(270,210)
    current[:] = [1200,800]
    assert MerchantDriver.point(driver,state,'price_field')==(225,210)


def test_handoff_failure_releases_input_and_notifies_host(tmp_path):
    guard = InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    calls = []
    def prepare(character):
        calls.append(('prepare',character,guard.owner))
        raise CaptureUnavailable('Pane unavailable')
    guard.on_acquire = prepare
    guard.on_release = lambda c:calls.append(('release',c,guard.owner))
    with pytest.raises(CaptureUnavailable):
        with guard.lease('Dutch'):
            pytest.fail('Input must not run before its embedded pane is ready')
    assert guard.owner is None
    assert calls==[('prepare','Dutch','Dutch'),('release','Dutch',None)]


@pytest.mark.parametrize('typed_correctly,cancel_preserves_stock',[(True,True),(False,True),(True,False)])
def test_booth_probe_only_enters_and_cancels_before_qualification(tmp_path,monkeypatch,typed_correctly,cancel_preserves_stock):
    import conquest.merchants.qualification as module
    import conquest.warehouse_money as money
    memory = Memory()
    memory.expected_sha256 = CLIENT_SHA256
    model = 0x15000
    memory.put(model+0x50,'<I',123)
    modal = modal_fixture(memory)
    item = dict(uid=123,type_id=111805,plus=1,gem1=0,gem2=0,quantity=1,bound=False,slot=0,price=None)
    inventory = {'name':'Inventory/##ItemGrid_A800F95C','address':0x20000,'geometry':[900,300,407,175],'scroll':[0,0]}
    booth = {'name':'Booth','address':0x21000,'geometry':[20,100,620,300],'scroll':[0,0]}
    child = {'name':'Booth/##BoothChild_FFE4633E','address':0x22000,'geometry':[40,138,580,232],'scroll':[0,0]}
    state = {'identity':{'pid':100,'creation_time_100ns':1},'inventory':[item],'booth':[],
        'silver':100,'booth_open':True,'request':None,'trade':None,'windows':[modal,inventory,booth,child]}
    def table(window,label):
        inv = label=='##ItemTable'
        x,y = window['geometry'][:2]
        stride = 40 if inv else 140
        return {'columns':[{'content_x':x+i*stride,'minimum':x+i*stride,'maximum':x+(i+1)*stride}
                           for i in range(10 if inv else 4)],
                'outer':[x,y,x+400,y+160],'clip':[x,y,x+400,y+160],'row_height':40 if inv else 64}
    gui = SimpleNamespace(table=table,model=lambda *args:model,viewport_size=lambda:[1401,760])
    driver = SimpleNamespace(observer=SimpleNamespace(adapter=memory,character='Spiritual'),
        qualification=tmp_path/'qualification.json',coordinator=object(),memory=SimpleNamespace(gui=gui),
        read=lambda:copy.deepcopy(state),target=SimpleNamespace(snapshot=lambda:{'client_size':[1401,760]}))
    clicks = []
    class Probe:
        def __init__(self,*args):self.target=driver.target
        def click(self,snapshot,control,validate=None):
            validate()
            clicks.append(control)
            if control=='cancel_listing':
                state['windows'].remove(modal)
                memory.put(model+0x50,'<I',0)
                if not cancel_preserves_stock:state['inventory'].clear()
        def wait_for(self,predicate,check):
            check()
            assert predicate(state)
            return copy.deepcopy(state)
    monkeypatch.setattr(module,'MerchantDriver',Probe)
    def type_amount(*args,**kwargs):
        value = b'123,456' if typed_correctly else b'123'
        memory.data[model+0x54:model+0x60] = value+b'\0'*(12-len(value))
    monkeypatch.setattr(money,'type_amount',type_amount)
    journal = Journal(tmp_path/'journal.sqlite3')
    if typed_correctly and cancel_preserves_stock:
        recovery={'shop_setup':{'occupancy_mode':'scene_booth','booth_model':406},
                  'booth_panel':{'mode':'owned_scene_entity','draw_offset':[0,-32]}}
        driver.qualification.write_text(json.dumps({**recovery,'client_sha256':CLIENT_SHA256,
            'character':'Spiritual','server':'America','capabilities':{'inventory_panel':True,'booth_panel':True}}))
        assert verify_booth_controls(driver,journal,lambda:None)['verified']
        saved=json.loads(driver.qualification.read_text())
        assert saved['capabilities']['booth_input'] is True
        assert saved['capabilities']['inventory_panel'] is True
        assert all(saved[k]==v for k,v in recovery.items())
    else:
        with pytest.raises(ValueError):verify_booth_controls(driver,journal,lambda:None)
        assert not driver.qualification.exists()
    assert 'confirm_listing' not in clicks and 'remove_listing' not in clicks
    assert not journal.pending('Spiritual')


def test_manual_focus_change_wins_over_merchant_focus_restoration():
    from conquest.merchants.ui import UnifiedUI
    called = []
    ui = SimpleNamespace(input_bookmarks={'Dutch':{'tab':'Overview','hwnd':10,'identity':{}}},
        hosts={'Dutch':SimpleNamespace(saved=SimpleNamespace(hwnd=20),
            api=SimpleNamespace(gui=SimpleNamespace(GetForegroundWindow=lambda:99)))},
        closed=False,coordinator=SimpleNamespace(owner=None),
        app=SimpleNamespace(mouse_priority=SimpleNamespace(active=lambda:False)),
        notebook=SimpleNamespace(select=lambda *args:called.append(args)))
    UnifiedUI.restore_input(ui,'Dutch')
    assert not called


@pytest.mark.parametrize('focused', [False, True])
def test_click_requires_verified_activation_before_sending_input(tmp_path, monkeypatch, focused):
    import conquest.focus_recovery as focus
    import conquest.merchants.driver as module

    profile = tmp_path/'qualification.json'
    profile.write_text(json.dumps({'client_size':[1000,800]}))
    identity = {'pid':100,'creation_time_100ns':123}
    state = {'identity':identity,'request':None,'trade':None}
    calls = []
    driver = SimpleNamespace(
        qualification=profile,
        coordinator=SimpleNamespace(check=lambda:None),
        observer=SimpleNamespace(adapter=SimpleNamespace(identity=identity,assert_identity=lambda:None)),
        target=SimpleNamespace(hwnd=20,snapshot=lambda:{'client_size':[1000,800]}),
        point=lambda *args:(100,100),read=lambda:state)

    def activate(hwnd, expected):
        assert (hwnd, expected)==(20, identity)
        calls.append('activate')
        return focused

    def click(*args, before_press, **kwargs):
        before_press()
        calls.append('click')

    monkeypatch.setattr(focus,'activate_client',activate)
    monkeypatch.setattr(module,'foreground_click',click)
    if focused:
        MerchantDriver._click(driver,state,'accept_request')
        assert calls==['activate','click']
    else:
        with pytest.raises(CaptureUnavailable,match='foreground focus'):
            MerchantDriver._click(driver,state,'accept_request')
        assert calls==['activate']


def test_resizing_preserves_user_intent_and_geometry_evidence(tmp_path):
    from conquest.merchants.ui import UnifiedUI
    path = tmp_path/'qualification.json'
    path.write_text(json.dumps({'capabilities':{'booth_input':True,'trade':True}}))
    calls=[]
    size=[1400,900]
    ui = SimpleNamespace(
        hosts={'Dutch':SimpleNamespace(saved=True,resize=lambda *args:calls.append(('resize',args)))},
        client_panes={'Dutch':SimpleNamespace(winfo_width=lambda:size[0],winfo_height=lambda:size[1],winfo_ismapped=lambda:True)},
        render_sizes={'Dutch':(1036,793)},calibration_results={},
        pause=lambda c:calls.append(('pause',c)),
        runtime=SimpleNamespace(invalidate_refill=lambda c:calls.append(('invalidate',c)),
                                controllers={'Dutch':SimpleNamespace(driver=SimpleNamespace(qualification=path))}))
    UnifiedUI.resize_merchant(ui,'Dutch')
    assert calls==[('resize',(1400,900))]
    assert json.loads(path.read_text())['capabilities']=={'booth_input':True,'trade':True}
    assert ui.calibration_results['Dutch']['verified'] is False
    assert not ui.calibration_results['Dutch']['verified']
    calls.clear()
    UnifiedUI.resize_merchant(ui,'Dutch')
    assert calls==[('resize',(1400,900))]  # Idle polls neither pause nor focus again.


def test_unknown_calibration_error_reports_location_without_exception_payload():
    from conquest.merchants.ui import calibration_failure
    try:
        raise RuntimeError('sensitive payload must not reach diagnostics')
    except RuntimeError as error:
        result = calibration_failure(error)
    assert 'sensitive payload' not in json.dumps(result)
    assert result['diagnostic']['type']=='RuntimeError'
    assert result['diagnostic']['frames'][-1]['function'].startswith('test_unknown_calibration')


def test_display_does_not_read_memory_or_destroy_saved_calibration(tmp_path):
    from conquest.merchants.ui import UnifiedUI
    path=tmp_path/'qualification.json';path.write_text(json.dumps({'capabilities':{'booth_input':True}}))
    def qualified(capability):
        pytest.fail('Displaying the client must not synchronously validate game memory')
    ui=SimpleNamespace(hosts={'Dutch':SimpleNamespace(saved=True,resize=lambda *args:None)},
        client_panes={'Dutch':SimpleNamespace(winfo_width=lambda:1400,winfo_height=lambda:900,winfo_ismapped=lambda:True)},
        render_sizes={},calibration_results={},pause=lambda c:None,
        runtime=SimpleNamespace(invalidate_refill=lambda c:None,
                                controllers={'Dutch':SimpleNamespace(driver=SimpleNamespace(qualification=path,require_qualified=qualified))}))
    UnifiedUI.resize_merchant(ui,'Dutch')
    assert json.loads(path.read_text())['capabilities']['booth_input'] is True
    assert ui.calibration_results['Dutch']['verified'] is False


@pytest.mark.parametrize('mode',['settles','stop','cancel','continuous'])
def test_calibration_waits_for_starting_click_but_preserves_stop(mode):
    from conquest.merchants.ui import wait_for_calibration_idle
    now=[0.0];checked=[]
    guard=SimpleNamespace(stopped=False,manual_active=lambda:mode=='continuous' or now[0]<2,
                          check=lambda:checked.append(now[0]))
    def wait(seconds):
        now[0]+=seconds
        if mode=='stop':guard.stopped=True
    cancel=SimpleNamespace(is_set=lambda:mode=='cancel' and now[0]>.05,wait=wait)
    if mode=='settles':
        wait_for_calibration_idle(guard,cancel,lambda:False,clock=lambda:now[0])
        assert len(checked)==1 and 2<=checked[0]<2.2
    else:
        with pytest.raises(ValueError):
            wait_for_calibration_idle(guard,cancel,lambda:False,clock=lambda:now[0])
        assert not checked and now[0]<15.2


def test_tab_visibility_refresh_is_immediate_and_coalesced():
    from conquest.merchants.ui import UnifiedUI
    pending=[];updated=[]
    ui=SimpleNamespace(closed=False,visibility_job=None,hosts={'Spiritual':object(),'Dutch':object()},
        root=SimpleNamespace(after_idle=lambda callback:pending.append(callback) or 'idle1'),
        finish_resize=lambda c:updated.append(c),auto_show_selected=lambda:None)
    ui.refresh_visibility=lambda:UnifiedUI.refresh_visibility(ui)
    UnifiedUI.schedule_visibility(ui);UnifiedUI.schedule_visibility(ui)
    assert len(pending)==1 and not updated
    pending[0]()
    assert updated==['Spiritual','Dutch'] and ui.visibility_job is None


def test_client_tab_compacts_chrome_and_restores_it_for_detail_tabs():
    from conquest.merchants.ui import (UnifiedUI,client_tab_character,
        refresh_permission_menu,set_packed)

    class Packed:
        def __init__(self,mapped=True):self.mapped=mapped;self.calls=[]
        def winfo_manager(self):return 'pack' if self.mapped else ''
        def pack(self,**options):self.mapped=True;self.calls.append(('pack',options))
        def pack_forget(self):self.mapped=False;self.calls.append(('forget',{}))

    selected=['frame-Dutch'];detail={'Spiritual':['client-Spiritual'],'Dutch':['client-Dutch']}
    notebook=SimpleNamespace(select=lambda:selected[0])
    frames={'Spiritual':'frame-Spiritual','Dutch':'frame-Dutch'}
    tabs={name:SimpleNamespace(select=lambda n=name:detail[n][0]) for name in detail}
    clients={'Spiritual':'client-Spiritual','Dutch':'client-Dutch'}
    assert client_tab_character(notebook,frames,tabs,clients)=='Dutch'
    detail['Dutch'][0]='Inventory'
    assert client_tab_character(notebook,frames,tabs,clients) is None
    detail['Dutch'][0]='client-Dutch'

    header=Packed();dutch=(Packed(),Packed(),Packed());spiritual=(Packed(),Packed(),Packed())
    ui=SimpleNamespace(notebook=notebook,frames=frames,detail_tabs=tabs,client_tabs=clients,
        header=header,merchant_chrome={'Dutch':tuple((widget,{'fill':'x'}) for widget in dutch),
        'Spiritual':tuple((widget,{'fill':'x'}) for widget in spiritual)})
    assert UnifiedUI.apply_client_compact_layout(ui)=='Dutch'
    assert not header.mapped and not any(widget.mapped for widget in dutch)
    assert all(widget.mapped for widget in spiritual)
    detail['Dutch'][0]='Inventory'
    assert UnifiedUI.apply_client_compact_layout(ui) is None
    assert header.mapped and all(widget.mapped for widget in dutch)
    before=len(header.calls);set_packed(header,True,fill='x')
    assert len(header.calls)==before  # Repeated visibility updates do not churn geometry.

    on_change=SimpleNamespace(client_tabs=clients,apply_client_compact_layout=Mock(),schedule_visibility=Mock())
    UnifiedUI.on_tab_changed(on_change)
    on_change.apply_client_compact_layout.assert_called_once_with()
    on_change.schedule_visibility.assert_called_once_with()

    menu=Mock();refresh_permission_menu(menu,(7,8),{'enabled':False,'refill':{'enabled':True}})
    assert menu.entryconfigure.call_args_list[0].args==(7,)
    assert menu.entryconfigure.call_args_list[0].kwargs=={'label':'Enable trading & repricing'}
    assert menu.entryconfigure.call_args_list[1].args==(8,)
    assert menu.entryconfigure.call_args_list[1].kwargs=={'label':'Pause automatic refill'}


def test_resize_defers_during_delivery_without_pausing_permissions():
    from conquest.merchants.ui import UnifiedUI
    calls=[]
    ui=SimpleNamespace(hosts={'Dutch':SimpleNamespace(saved=True,resize=lambda *a:calls.append(a))},
        client_panes={'Dutch':SimpleNamespace(winfo_width=lambda:1400,winfo_height=lambda:900,winfo_ismapped=lambda:True)},
        render_sizes={'Dutch':(1036,793)},calibration_results={},coordinator=SimpleNamespace(owner='Dutch'),
        runtime=SimpleNamespace(delivery_window='reserved'))
    UnifiedUI.resize_merchant(ui,'Dutch')
    assert not calls and ui.render_sizes['Dutch']==(1036,793)
    UnifiedUI.resize_merchant(ui,'Dutch',automatic=True)
    assert calls==[(1400,900)]
