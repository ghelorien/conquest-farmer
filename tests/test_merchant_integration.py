import copy
import ctypes
import json
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
import pytest
from conquest.capture import CaptureUnavailable
from conquest.merchants.coordination import InputCoordinator,install,input_scope
from conquest.merchants.memory import deque_items,string,GuiReader
from conquest.merchants.journal import Journal


class Memory:
    def __init__(self):
        self.data = bytearray(0x20000)
    def read_block(self,address,size):
        return bytes(self.data[address:address+size])
    def put(self,address,fmt,*values):
        struct.pack_into(fmt,self.data,address,*values)


def test_deque_ring_wrap_duplicate_and_header_bounds():
    memory = Memory()
    memory.put(0x10000,'<4Q',0x11000,4,7,2)
    memory.put(0x11000,'<4Q',0x12000,0,0,0x12010)
    memory.put(0x12000,'<2Q',0x13000,0)
    memory.put(0x12010,'<2Q',0x13010,0)
    assert deque_items(memory,0x10000,40)[0]==[0x13010,0x13000]
    memory.put(0x12010,'<Q',0x13000)
    with pytest.raises(ValueError):deque_items(memory,0x10000,40)
    memory.put(0x10000,'<4Q',0x11000,3,0,2)
    with pytest.raises(ValueError):deque_items(memory,0x10000,40)


def test_short_and_heap_strings_fail_closed():
    memory = Memory()
    memory.data[0x10000:0x10008] = b'Parasite'
    memory.put(0x10010,'<QQ',8,15)
    assert string(memory,0x10000)=='Parasite'
    memory.put(0x10000,'<Q',0x11000)
    memory.data[0x11000:0x11008] = b'Parasite'
    memory.put(0x10010,'<QQ',8,32)
    assert string(memory,0x10000)=='Parasite'
    memory.put(0x10010,'<QQ',200,32)
    with pytest.raises(ValueError):string(memory,0x10000)


def test_whole_farmer_action_excludes_merchant_even_between_send_batches(tmp_path):
    guard = InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    install(guard)
    results = []
    try:
        with input_scope():
            assert guard.owner=='Farmer'
            def merchant():
                try:
                    with guard.lease('Dutch'):results.append('bad')
                except CaptureUnavailable:results.append('blocked')
            thread = threading.Thread(target=merchant);thread.start();thread.join()
            assert results==['blocked']
        with guard.lease('Dutch'):
            with input_scope():
                assert guard.owner=='Dutch'
    finally:
        install(None)


def test_cross_process_lock_and_key_up_after_global_stop(tmp_path,monkeypatch):
    import subprocess,sys
    from conquest import mouse_priority
    from conquest.foreground import scan_key_event
    guard = InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    install(guard)
    try:
        with guard.lease('Dutch'):
            code = "import msvcrt,sys; f=open(sys.argv[1],'r+b'); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)"
            result = subprocess.run([sys.executable,'-c',code,str(guard.path)],capture_output=True)
            assert result.returncode!=0
        monkeypatch.setattr(mouse_priority,'_guard',None)
        events = []
        send = mouse_priority.guarded_send(lambda n,*args:events.append(n) or n)
        guard.stop()
        down,up = scan_key_event(0x1e),scan_key_event(0x1e,True)
        with pytest.raises(CaptureUnavailable):send(1,ctypes.byref(down),ctypes.sizeof(down))
        assert send(1,ctypes.byref(up),ctypes.sizeof(up))==1 and events==[1]
    finally:
        install(None)


def test_native_tabs_bridge_authentication_handoff_and_global_stop(tmp_path,monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    from conquest.merchants.ui import UnifiedUI
    from conquest.merchants.runtime import MerchantRuntime
    from conquest.merchants.bridge import request
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(MerchantRuntime,'start',lambda self:None)
    root = tk.Tk();root.withdraw()
    control = {'enabled':False,'revision':1}
    def stop():control['enabled']=False
    app = SimpleNamespace(root=root,sidebar=ttk.Frame(root),pane=tk.Frame(root),closing=False,
        mouse_priority=SimpleNamespace(active=lambda:False),catalog=object(),thread=None,
        control=SimpleNamespace(snapshot=lambda:dict(control)),state_text=tk.StringVar(value='Off'),
        stats_text=tk.StringVar(value=''),activity_text=tk.StringVar(value='Returning from Market'),stop=stop)
    route_picker = ttk.Combobox(app.sidebar,values=['Bandit'],state='readonly')
    route_picker.pack(fill='x')
    unified = UnifiedUI(app)
    try:
        assert [unified.notebook.tab(t,'text') for t in unified.notebook.tabs()]==['Overview','Farmer','Spiritual','Dutch']
        assert app.sidebar.pack_info()['in']==unified.frames['Farmer']
        root.deiconify()
        root.attributes('-topmost',True)  # Isolate hit tests from other open apps.
        root.lift()
        for tab in ('Farmer','Overview','Farmer'):
            unified.notebook.select(unified.frames[tab])
            root.update()
            if tab=='Farmer':
                # A mapped widget can still be covered by its geometry host.
                x=route_picker.winfo_rootx()+route_picker.winfo_width()//2
                y=route_picker.winfo_rooty()+route_picker.winfo_height()//2
                assert root.winfo_containing(x,y)==route_picker
        unified.notebook.select(unified.frames['Dutch'])
        unified.detail_tabs['Dutch'].select(0)
        root.geometry('1000x760');root.update()
        pane = unified.client_panes['Dutch']
        initial_size = (pane.winfo_width(),pane.winfo_height())
        root.geometry('1400x1000');root.update()
        enlarged_size = (pane.winfo_width(),pane.winfo_height())
        assert enlarged_size[0]>initial_size[0]+350 and enlarged_size[1]>initial_size[1]+200
        unified.labels['Dutch'].set('Status\n'+('Long diagnostic line\n'*6))
        root.update()
        # Status expands to keep diagnostics visible; the game keeps usable space.
        status_label=next(child for parent in unified.frames['Dutch'].winfo_children()
            for child in parent.winfo_children() if isinstance(child,ttk.Label)
            and str(child.cget('textvariable'))==str(unified.labels['Dutch']))
        assert status_label.winfo_height()>=status_label.winfo_reqheight()
        assert pane.winfo_width()==enlarged_size[0]
        assert 400<=pane.winfo_height()<enlarged_size[1]
        unified.labels['Dutch'].set('Connecting…')
        root.update()
        assert (pane.winfo_width(),pane.winfo_height())==enlarged_size
        root.withdraw()
        assert unified.presentation.ready.wait(2)
        unified.poll()
        assert 'Returning from Market' in unified.rows['Farmer'].get()
        assert unified.header.master == root
        assert 'Net silver earned:' in unified.silver_text.get()
        assert 'Discord #shops (4h):' in unified.timer_text.get()
        assert unified.dispatch({'action':'status'})['header']['timers'] == unified.timer_text.get()
        first = request({'action':'scan','request_id':'a'})
        assert request({'action':'scan','request_id':'a'})==first
        info = json.loads(Path('.runtime/merchants/bridge.json').read_text())
        call = urllib.request.Request(f'http://127.0.0.1:{info["port"]}/merchants',data=b'{"action":"status"}')
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(call)
        assert error.value.code==403
        with pytest.raises(ValueError):request({'action':'credentials','password':'invalid'})
        control['enabled'] = True
        assert not unified.safe_to_yield()
        request({'action':'handoff-request','request_id':'safe1'})
        with pytest.raises(ValueError):
            request({'action':'handoff-grant','request_id':'safe1','safe':True,'revision':1,'expires_at':time.time()+10})
        control['enabled'] = False
        request({'action':'handoff-grant','request_id':'safe1','safe':True,'revision':1,'expires_at':time.time()+10})
        assert unified.safe_to_yield()
        control['revision'] += 1
        assert not unified.safe_to_yield()
        unified.resume('Dutch')
        unified.global_stop()
        assert not control['enabled'] and not unified.runtime.enabled('Dutch')
        assert unified.coordinator.stopped
    finally:
        unified.close();root.destroy()


def test_merchant_reads_run_concurrently_without_sharing_observers(tmp_path):
    from conquest.merchants.runtime import MerchantRuntime
    barrier = threading.Barrier(2)
    guard = InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    runtime = MerchantRuntime(object(),guard,journal=Journal(tmp_path/'journal.sqlite3'))
    def read(character):
        barrier.wait(timeout=2)
        return {'character':character,'timestamp':time.time(),'inventory':[],'booth':[],
                'capacity':40,'trade':None,'request':None,'booth_open':True}
    for character in ('Spiritual','Dutch'):
        runtime.observers[character] = SimpleNamespace(adapter=SimpleNamespace(assert_identity=lambda:None),lock=threading.RLock())
        runtime.controllers[character] = SimpleNamespace(driver=SimpleNamespace(read=lambda c=character:read(c)),reconcile=lambda s:None)
    runtime.disconnected = lambda character:False
    errors = []
    def step(character):
        try:runtime.step(character)
        except Exception as error:errors.append(error)
    threads = [threading.Thread(target=step,args=(c,)) for c in ('Spiritual','Dutch')]
    for thread in threads:thread.start()
    for thread in threads:thread.join()
    assert not errors
    assert runtime.latest['Spiritual']['character']=='Spiritual'
    assert runtime.latest['Dutch']['character']=='Dutch'


def test_pid_reuse_discards_stale_merchant_and_recovers_only_that_character(tmp_path):
    from conquest.merchants.runtime import MerchantRuntime
    journal = Journal(tmp_path/'journal.sqlite3')
    runtime = MerchantRuntime(object(),InputCoordinator(),journal=journal)
    def invalid():raise ValueError('PID reused')
    closed,recovered = [],[]
    runtime.observers['Dutch'] = SimpleNamespace(adapter=SimpleNamespace(assert_identity=invalid),close=lambda:closed.append('Dutch'))
    runtime.controllers['Dutch'] = object()
    runtime.latest['Dutch'] = {'identity':'stale'}
    def absent(character):raise ValueError('Not logged in')
    runtime.attach = absent
    runtime.recover = lambda character,crashed=False:recovered.append((character,crashed))
    runtime.step('Dutch')
    assert closed==['Dutch'] and recovered==[('Dutch',True)]
    assert 'Dutch' not in runtime.latest and 'Dutch' not in runtime.controllers
    assert journal.get('Dutch','crashed') is True
    assert journal.get('Spiritual','crashed') is None


def test_manual_character_pause_revokes_an_active_lease(tmp_path):
    enabled = [True]
    guard = InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    guard.owner_allowed = lambda character:enabled[0]
    with guard.lease('Dutch'):
        enabled[0] = False
        with pytest.raises(CaptureUnavailable,match='paused'):
            guard.check()


def test_denied_lease_never_changes_focus_and_releases_ownership(tmp_path):
    guard=InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    calls=[]
    guard.on_acquire=lambda c:calls.append(('focus',c))
    guard.on_release=lambda c:calls.append(('restore',c))
    guard.owner_allowed=lambda c:False
    with pytest.raises(CaptureUnavailable,match='paused'):
        with guard.lease('Dutch',purpose='listing'):
            pytest.fail('Denied work entered its input scope')
    assert calls==[]
    assert guard.owner is guard.thread is guard.purpose is None
    guard.owner_allowed=lambda c:True
    with guard.lease('Dutch',purpose='trade'):
        assert guard.purpose=='trade'
    assert calls==[('focus','Dutch'),('restore','Dutch')]
    assert guard.owner is guard.thread is guard.purpose is None


def test_gui_reader_accepts_live_frames_that_advance_between_rpcs():
    memory = Memory()
    context,array,window,name = 0x11000,0x15000,0x16000,0x17000
    memory.put(0x10000,'<Q',context)
    memory.put(context+0x3e58,'<IIQ',1,1,array)
    memory.put(array,'<Q',window)
    memory.put(window,'<Q',name)
    memory.data[name:name+6] = b'Booth\0'
    memory.put(window+0x18,'<4f',72,284,620,300)
    memory.data[window+0x97] = 1
    memory.put(window+0x248,'<I',105)
    original = memory.read_block
    frames = iter((100,110))
    memory.read_block = lambda address,size:struct.pack('<I',next(frames)) if address==context+0x3e38 else original(address,size)
    gui = SimpleNamespace(session=memory,base=0x10000-0x6966f0)
    result = GuiReader.windows(gui)
    assert len(result)==1 and result[0]['name']=='Booth'
    memory.put(window+0x248,'<I',90)
    frames = iter((100,110))
    assert GuiReader.windows(gui)==[]


@pytest.mark.parametrize('change',['reorder','replace','duplicate','context'])
def test_gui_registry_distinguishes_draw_order_from_membership(change):
    from conquest.merchants.memory import GuiObservationChanged
    memory=Memory();context,array=0x11000,0x15000;windows=(0x16000,0x17000)
    memory.put(0x10000,'<Q',context);memory.put(context+0x3e58,'<IIQ',2,2,array)
    memory.put(array,'<2Q',*windows)
    original=memory.read_block;reads=[0];contexts=[0]
    def read(address,size):
        if address==array:
            reads[0]+=1
            if reads[0]>1:
                return struct.pack('<2Q',*(windows[::-1] if change=='reorder' else
                    (windows[0],0x18000) if change=='replace' else
                    (windows[0],windows[0]) if change=='duplicate' else windows))
        if address==0x10000:
            contexts[0]+=1
            if change=='context' and contexts[0]>1:return struct.pack('<Q',0x12000)
        return original(address,size)
    memory.read_block=read;gui=SimpleNamespace(session=memory,base=0x10000-0x6966f0)
    if change=='reorder':assert GuiReader._windows(gui)==[]
    else:
        with pytest.raises(GuiObservationChanged):GuiReader._windows(gui)


def test_one_time_batch_never_accepts_incoming_trades(tmp_path):
    from conquest.merchants.runtime import MerchantRuntime
    journal = Journal(tmp_path/'journal.sqlite3')
    guard = InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    runtime = MerchantRuntime(object(),guard,journal=journal)
    runtime.list_once('Dutch','once:incoming')
    snapshot = {'character':'Dutch','timestamp':time.time(),'request':{'participant':'Parasite'},'trade':None}
    runtime.observers['Dutch'] = SimpleNamespace(adapter=SimpleNamespace(assert_identity=lambda:None),lock=threading.RLock())
    calls = []
    runtime.controllers['Dutch'] = SimpleNamespace(driver=SimpleNamespace(read=lambda:snapshot),
        reconcile=lambda s:None,accept_request=lambda s:calls.append('accepted'))
    runtime.disconnected = lambda character:False
    runtime.step('Dutch')
    assert runtime.manual_status('Dutch')['phase']=='needs_attention'
    assert not calls
    assert journal.get('Dutch','scan')['pending']


def test_runtime_reconciles_submitted_request_decline_after_modal_disappears(tmp_path,monkeypatch):
    from conquest.merchants.runtime import MerchantRuntime
    journal=Journal(tmp_path/'journal.sqlite3')
    runtime=MerchantRuntime(object(),InputCoordinator(lambda:True),journal=journal)
    journal.set('Dutch','enabled',True)
    journal.set('Dutch','unrelated_request_decline',{'phase':'submitted'})
    snapshot={'character':'Dutch','identity':{'pid':7},'timestamp':time.time(),
              'inventory':[],'booth':[],'silver':100,'capacity':40,
              'request':None,'trade':None,'booth_open':True}
    runtime.observers['Dutch']=SimpleNamespace(
        adapter=SimpleNamespace(assert_identity=lambda:None),lock=threading.RLock())
    controller=SimpleNamespace(driver=SimpleNamespace(read=lambda:snapshot),reconcile=lambda state:None)
    runtime.controllers['Dutch']=controller
    runtime.disconnected=lambda character:False
    calls=[]
    def reconcile_decline(actual_controller,state,*,operations_enabled):
        calls.append((actual_controller,state.get('request'),operations_enabled))
        journal.set('Dutch','unrelated_request_decline',{'phase':'verified'})
        return True
    monkeypatch.setattr('conquest.merchants.unrelated_request.decline_unrelated_request',reconcile_decline)
    runtime.step('Dutch')
    assert calls==[(controller,None,True)]
    assert journal.get('Dutch','unrelated_request_decline')['phase']=='verified'


def test_delivery_window_holds_unreserved_stock_but_allows_reserved_request(tmp_path,monkeypatch):
    from conquest.merchants.runtime import MerchantRuntime
    from conquest.merchants import delivery_reservation
    runtime=MerchantRuntime(object(),InputCoordinator(lambda:True),journal=Journal(tmp_path/'journal.sqlite3'))
    runtime.journal.set('Dutch','enabled',True)
    runtime.delivery_window='delivery-window'
    snapshot={'character':'Dutch','timestamp':time.time(),'request':{'participant':'Parasite'},'trade':None}
    runtime.observers['Dutch']=SimpleNamespace(adapter=SimpleNamespace(assert_identity=lambda:None),lock=threading.RLock())
    calls=[]
    runtime.controllers['Dutch']=SimpleNamespace(driver=SimpleNamespace(read=lambda:snapshot),
        reconcile=lambda s:calls.append('reconcile'),accept_request=lambda s:calls.append('accept'))
    runtime.disconnected=lambda character:False
    runtime.step('Dutch')
    assert calls==['reconcile']
    monkeypatch.setattr(delivery_reservation,'active',lambda *args:True)
    runtime.step('Dutch')
    assert calls==['reconcile','reconcile','accept']
