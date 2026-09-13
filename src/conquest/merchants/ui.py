"""Unified native tabs around the unchanged farmer controls."""
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk, messagebox
import uuid
import queue
import threading
from conquest.merchants.journal import CHARACTERS, character_name
from conquest.merchants.coordination import InputCoordinator, install
from conquest.merchants.runtime import MerchantRuntime
from conquest.merchants.bridge import MerchantBridge
from conquest.merchants.background_probe import probe_busy, start_surface_restore


def calibration_failure(error):
    """Keep actionable code locations, never traceback locals or arbitrary payloads."""
    import traceback
    frames = [{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
              for f in traceback.extract_tb(error.__traceback__)]
    code = getattr(error,'winerror',None)
    detail = {'type':type(error).__name__,'frames':frames}
    if type(code) is int:
        detail['winerror'] = code
    note = str(error) if isinstance(error,(OSError,ValueError)) else (
        f'Booth verification failed: {type(error).__name__}'
        + (f' (Windows {code})' if type(code) is int else '')
        + (f' in {frames[-1]["function"]}' if frames else ''))
    return {'verified':False,'note':note,'diagnostic':detail}


def wait_for_calibration_idle(coordinator, cancel, closed, *, clock=time.monotonic):
    deadline = clock()+15
    while True:
        if cancel.is_set() or closed() or coordinator.stopped:
            raise ValueError('Booth verification stopped')
        if not coordinator.manual_active():
            coordinator.check()
            return
        if clock()>=deadline:
            raise ValueError('Mouse remained active; verify again when ready')
        cancel.wait(.1)


def wait_for_merchant_surface(host, others, check, *, clock=time.monotonic, sleep=time.sleep):
    """Wait off the Tk thread for asynchronous native show/hide acknowledgments."""
    deadline=clock()+3
    while True:
        check()
        host.api.assert_owner(host.saved.hwnd,host.saved.identity)
        gui=host.api.gui
        left,top,right,bottom=gui.GetClientRect(host.parent)
        x,y=gui.ClientToScreen(host.parent,(left,top))
        if (gui.IsWindowVisible(host.parent) and gui.IsWindowVisible(host.saved.hwnd)
                and not gui.IsIconic(host.saved.hwnd)
                and host.api.is_above(host.saved.hwnd,gui.GetAncestor(host.parent,2))
                and gui.GetWindowRect(host.saved.hwnd)==(x,y,x+right-left,y+bottom-top)
                and all(not other.api.gui.IsWindowVisible(other.saved.hwnd) for other in others if other.saved)):
            check()
            return
        if clock()>=deadline:
            raise ValueError('Waiting for the selected merchant window to become visible; no game input sent')
        sleep(.025)


class UnifiedUI:
    def __init__(self, app):
        self.app,self.root = app,app.root
        self.closed,self.grant = False,None
        self.ui_requests = queue.Queue()
        self.hosts,self.client_panes,self.detail_tabs = {},{},{}
        self.render_sizes,self.resize_jobs = {},{}
        self.visibility_job = None
        self.auto_embedding = False
        self.auto_embed_retry = {}
        self.released_clients = set()
        self.layout_status = {}
        self.last_ui_tick = time.monotonic()
        self.ui_health = {'last_gap_ms':0,'max_gap_ms':0}
        self.calibrating,self.calibration_results = set(),{}
        self.calibration_cancel = {}
        self.input_bookmarks = {}
        self.header_status = {}
        self.background_probe = {}
        self.background_cancel = threading.Event()
        self.background_surfaces = {}
        self.background_thread = None
        self.background_after = None
        self.background_after_job = None
        sidebar_host=getattr(app,'sidebar_host',app.sidebar)
        sidebar_host.pack_forget()
        self.header = ttk.Frame(self.root, padding=(12,6))
        self.header.pack(fill='x')
        self.timer_text = tk.StringVar(value='Loading shop timers…')
        self.silver_text = tk.StringVar(value='Loading verified sales…')
        self.on_sale_text = tk.StringVar(value='Loading current shop value…')
        self.header_labels = [
            ttk.Label(self.header,textvariable=self.timer_text,anchor='w'),
            ttk.Label(self.header,textvariable=self.silver_text,anchor='w',font=('Segoe UI',10,'bold')),
            ttk.Label(self.header,textvariable=self.on_sale_text,anchor='w',font=('Segoe UI',10,'bold'))]
        for label in self.header_labels:
            label.pack(fill='x')
        self.header.bind('<Configure>',lambda event:[label.configure(wraplength=max(1,event.width-24))
                                                     for label in self.header_labels])
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both',expand=True)
        self.notebook.bind('<<NotebookTabChanged>>',lambda event:self.schedule_visibility())
        self.root.bind('<Configure>',lambda event:self.schedule_visibility() if event.widget==self.root else None,add='+')
        self.frames = {name:ttk.Frame(self.notebook) for name in ('Overview','Farmer',*CHARACTERS)}
        for name,frame in self.frames.items():
            self.notebook.add(frame,text=name)
        app.content_parent = self.frames['Farmer']
        sidebar_host.pack(in_=app.content_parent,side='left',fill='both',expand=True)
        # pack(in_=...) changes geometry ownership, not the native parent.
        # These existing root children must sit above the newer notebook.
        sidebar_host.lift()
        app.pane.lift()
        self.coordinator = InputCoordinator(self.safe_to_yield,app.mouse_priority.active)
        self.runtime = MerchantRuntime(app.catalog,self.coordinator)
        self.connect_threads={}
        from conquest.merchants.presentation import MerchantPresentation
        self.presentation = MerchantPresentation(self.runtime)
        self.coordinator.owner_allowed = lambda character:character=='Farmer' or self.runtime.input_allowed(character) or (
            not getattr(self.runtime,'delivery_window',None) and not getattr(self.runtime,'refill_window',None)
            and character in self.calibrating and not self.calibration_cancel[character].is_set())
        self.coordinator.on_acquire = self.prepare_input
        self.coordinator.on_release = self.release_input
        # Get the process lock before installing any input hook or starting
        # merchant threads. A second UI cannot become a second controller.
        self.bridge = MerchantBridge(self.dispatch)
        install(self.coordinator)
        self.rows,self.labels,self.tables = {},{},{}
        self.build_overview()
        for character in CHARACTERS:
            self.build_merchant(character)
        self.root.title('Conquest — Farmer · Spiritual · Dutch')
        self.root.geometry('1080x850')
        self.root.minsize(900,700)
        from conquest.discord_notify import read_json
        saved_window = read_json('.runtime/merchants/window.json')
        import re
        geometry = saved_window.get('geometry','')
        if isinstance(geometry,str) and re.fullmatch(r'\d{3,5}x\d{3,5}[+-]\d+[+-]\d+',geometry):
            self.root.geometry(geometry)
        if saved_window.get('state')=='zoomed':
            self.root.state('zoomed')
        self.runtime.start()
        self.presentation.start()
        from conquest.discord_notify import write_json
        write_json('.runtime/merchants/app-lifecycle.json',{'state':'running','at':time.time()})
        self.root.after(500,self.poll)
        self.root.after(50,self.poll_ui_requests)

    def safe_to_yield(self):
        if self.app.closing:
            return False
        control = self.app.control.snapshot()
        if self.grant and self.grant['expires_at'] > time.time() and self.grant['revision']==control['revision']:
            return True
        if control['enabled'] or (self.app.thread and self.app.thread.is_alive()):
            return False
        from conquest.discord_notify import read_json,process_alive
        route = read_json('reports/overnight/status.json')
        # A stale heartbeat does not prove its process stopped.
        if route.get('phase') not in (None,'stopped','completed','failed') and process_alive(route.get('pid')) is not False:
            return False
        return True

    def dispatch(self, body):
        action = body.get('action')
        if action=='probe-delivery-request' and set(body)=={'action','character'}:
            from conquest.merchants.delivery_probe import start
            return start(self,body['character'])
        if action=='probe-delivery-stage' and set(body)=={'action','stage'}:
            from conquest.merchants.delivery_live import start
            return start(self,body['stage'])
        if action=='reconcile-stall-inspection' and set(body)=={'action','character'}:
            character=character_name(body['character'])
            from conquest.merchants.stall_probe import reconcile_interrupted_probe
            observer=self.runtime.observers.get(character)
            if observer is None:raise ValueError('Merchant is not attached')
            with observer.lock:
                return reconcile_interrupted_probe(self.runtime.controllers[character].driver,self.runtime.journal)
        if action=='inspect-market-stall' and set(body)=={'action','character'}:
            from conquest.merchants.connect_market import start
            return start(self,body['character'],stall_inspection=True)
        if action=='qualify-market-movement' and set(body)=={'action','character'}:
            from conquest.merchants.connect_market import start
            return start(self,body['character'],market_trial=True)
        if action=='start-readonly-diagnostics' and set(body)=={'action'}:
            import subprocess,sys
            from conquest.worker import request as worker_request
            paths=[Path('.runtime')/f'merchant-diagnostic-{c.lower()}.json' for c in CHARACTERS]
            if any(p.exists() for p in paths):
                results=[worker_request(p,'health') for p in paths]
                if not all(r.get('read_only') for r in results):
                    raise ValueError('Existing diagnostic workers are not read-only')
                return {'existing':True,'read_only':True}
            process=getattr(self,'readonly_diagnostics',None)
            if process is not None and process.poll() is None:
                return {'pid':process.pid,'starting':True,'read_only':True}
            for c in CHARACTERS:
                if c not in self.runtime.observers:raise ValueError('Both merchants must be attached')
                self.runtime.controllers[c].driver.memory.read()
            self.readonly_diagnostics=subprocess.Popen([sys.executable,'scripts/start_merchant_diagnostics.py'],
                cwd=Path.cwd(),creationflags=subprocess.CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return {'pid':self.readonly_diagnostics.pid,'read_only':True}
        if action=='peer-identity-evidence' and set(body)=={'action','character','peer'}:
            character=character_name(body['character']);peer=character_name(body['peer'])
            if character==peer:raise ValueError('Choose two different merchant clients')
            from conquest.merchants.identity_evidence import peer_evidence
            source=self.runtime.observers.get(character);other=self.runtime.observers.get(peer)
            if source is None or other is None:raise ValueError('Both merchants must be attached')
            # Fixed order avoids opposite-direction diagnostic lock inversion.
            first,second=sorted((source,other),key=lambda o:o.character)
            with first.lock,second.lock:
                before=self.runtime.controllers[peer].driver.memory.read()
                evidence=peer_evidence(source,before)
                after=self.runtime.controllers[peer].driver.memory.read()
                if any(before[k]!=after[k] for k in ('identity','character_uid','position','map_id')):
                    raise ValueError('Peer identity changed across client observations')
                return evidence
        if action=='connect-market' and set(body) in ({'action','character'},{'action','character','client_pid'}):
            from conquest.merchants.connect_market import start
            return start(self,body['character'],client_pid=body.get('client_pid'))
        if action=='delivery-window' and set(body)=={'action','request_id'}:
            key=body['request_id']
            if not isinstance(key,str) or not 1<=len(key)<=100:
                raise ValueError('Invalid delivery window ID')
            # Reserve the work mode before a grant can let a refill worker
            # start listing. The receiver's reserved trade remains permitted.
            if not self.coordinator.lock.acquire(blocking=False):
                raise ValueError('Wait for current merchant input to release')
            try:
                if self.coordinator.owner or self.coordinator.stopped:
                    raise ValueError('Merchant delivery window is unavailable')
                current=getattr(self.runtime,'delivery_window',None) or getattr(self.runtime,'refill_window',None)
                if current:
                    if current!=key or self.runtime.handoff!=key:
                        raise ValueError('Another merchant delivery window is active')
                    return {'requested':key}  # A retry cannot restart timers or revert refill to trade.
                if getattr(self,'grant',None):
                    raise ValueError('Release the current farmer grant before delivery')
                self.runtime.delivery_window=key
                self.runtime.refill_window=None
                # Publish the release key before fallible journal writes. If a
                # timer write fails, the caller can revoke this exact window;
                # no grant has yet authorized merchant input.
                self.runtime.handoff=key
                for character in CHARACTERS:
                    if self.runtime.refill_enabled(character):
                        self.runtime.refills[character].start()
            finally:self.coordinator.lock.release()
            return {'requested':key}
        if action=='delivery-refill' and set(body)=={'action','request_id'}:
            key=body['request_id']
            if (getattr(self.runtime,'delivery_window',None)!=key or self.runtime.handoff!=key
                    or not self.grant or self.grant['request_id']!=key or not self.safe_to_yield()):
                raise ValueError('Delivery refill requires the original unexpired safe window')
            from conquest.merchants import delivery_operation as operation
            receipt=operation.status(operation.Journal(operation.JOURNAL),key)
            worker=getattr(self,'delivery_workers',{}).get(key)
            if (not receipt or receipt['phase']!='verified' or worker and worker.is_alive()
                    or getattr(self,'delivery_errors',{}).get(key)):
                raise ValueError('Both delivery participants must reconcile before refill')
            held=self.runtime.journal.get(receipt['character'],'delivery_reservation',{})
            if held.get('request_id')!=key or held.get('phase')!='verified':
                raise ValueError('Merchant stock hold has not been reconciled')
            if not self.coordinator.lock.acquire(blocking=False):
                raise ValueError('Wait for merchant input to release before refill')
            try:
                self.coordinator.check()
                if self.coordinator.owner:raise ValueError('Merchant still owns delivery input')
                self.runtime.refill_window=key
                self.runtime.delivery_window=None
            finally:self.coordinator.lock.release()
            return {'refill':True,'expires_at':self.grant['expires_at']}
        if action in ('delivery-start','delivery-test','delivery-status','delivery-readiness'):
            from conquest.merchants.delivery_operation import dispatch
            return dispatch(self,body)
        if action in ('delivery-pair','delivery-reserve','delivery-ready','delivery-finish','delivery-source'):
            from conquest.merchants.delivery_bridge import dispatch
            return dispatch(self,body)
        if action=='status' and set(body)=={'action'}:
            return {'characters':self.runtime.status(),'input_owner':self.coordinator.owner,
                'handoff_requested':self.runtime.handoff,'handoff_granted':bool(self.grant and self.safe_to_yield()),
                'calibration':dict(self.calibration_results),'layout':dict(self.layout_status),
                'ui_health':{**self.ui_health,'tick_age_ms':round((time.monotonic()-self.last_ui_tick)*1000)},
                'sales_reporting':self.runtime.sales_worker.status(),
                'header':dict(self.header_status),
                'background_probe':dict(self.background_probe),
                'merchant_ui_version':29}
        if action=='background-probe' and set(body)=={'action','character','mode'}:
            from conquest.merchants.background_probe import start_probe
            character=character_name(body['character'])
            return start_probe(self,character,body['mode'])
        if action=='background-probe-stop' and set(body)=={'action'}:
            self.background_cancel.set()
            return {'stopped':True}
        if action=='background-farmer-status' and set(body)=={'action'}:
            from conquest.background_farmer_observation import snapshot
            return snapshot(self)
        if action=='background-item-status' and set(body)=={'action','character','uid'}:
            from conquest.merchants.background_item_status import item_status
            return item_status(self,character_name(body['character']),body['uid'])
        if action=='background-minimize' and set(body)=={'action'}:
            if probe_busy(self):
                raise ValueError('Wait for the active diagnostic before minimizing Conquest')
            self.ui_requests.put((self.root.iconify,None,{}))
            return {'requested':True}
        if action=='background-probe-restore' and set(body)=={'action'}:
            return start_surface_restore(self)
        if action=='market-refresh' and set(body)<= {'action','request_id','character'}:
            targets=[character_name(body['character'])] if body.get('character') else CHARACTERS
            return {c:self.runtime.market_worker.request(c,body['request_id']) for c in targets}
        if action=='resume-batch' and set(body)=={'action','character'}:
            character=character_name(body['character'])
            scan=self.runtime.journal.resume_batch(character)
            self.coordinator.resume()
            return {'resumed':character,'request_id':scan['request_id']}
        if action=='pause-merchant' and set(body)=={'action','character'}:
            character=character_name(body['character'])
            self.pause(character)
            return {'paused':character,'pending_work_preserved':True}
        if action=='merchant-enabled' and set(body)=={'action','character','enabled'} and type(body['enabled']) is bool:
            character=character_name(body['character'])
            self.runtime.enable(character,body['enabled'])
            if body['enabled']:self.coordinator.resume()
            return {'character':character,'enabled':self.runtime.enabled(character)}
        if action=='window-state' and set(body)=={'action','maximized'} and type(body['maximized']) is bool:
            state = 'zoomed' if body['maximized'] else 'normal'
            self.ui_requests.put((lambda:self.root.state(state),None,{}))
            return {'requested':state}
        if action=='return-route-status' and set(body)=={'action','character'}:
            character=character_name(body['character'])
            observer=self.runtime.observers.get(character)
            if observer is None:
                raise ValueError('Merchant is not attached')
            from conquest.merchants.stalls import scene_flags
            with observer.lock:
                snapshot=self.runtime.controllers[character].driver.memory.read(recovery=True)
                flags=scene_flags(observer) if snapshot['map_id']==1036 else []
            return {'character':character,'map_id':snapshot['map_id'],'position':snapshot['position'],
                    'flags':flags,'return_state':self.runtime.journal.get(character,'shop_return')}
        if action=='reload-app' and set(body)=={'action'}:
            if (probe_busy(self)
                    or not self.safe_to_yield() or self.coordinator.owner or self.calibrating or self.runtime.refilling
                    or any(self.runtime.enabled(c) or self.runtime.journal.pending(c) for c in CHARACTERS)):
                raise ValueError('Pause merchants and finish active work before reloading')
            self.ui_requests.put((self.app.restart,None,{}))
            return {'requested':True}
        if action=='refill-enabled' and set(body)=={'action','character','enabled'} and type(body['enabled']) is bool:
            character=character_name(body['character'])
            if body['enabled']:self.resume_refill(character)
            else:self.runtime.set_refill_enabled(character,False)
            return {'character':character,'enabled':self.runtime.refill_enabled(character)}
        if action=='attach-farmer-client' and set(body)=={'action','client_pid','client_started'}:
            from conquest.merchants.farmer_connection import attach
            queued_at=time.monotonic()
            done,result=threading.Event(),{}
            self.ui_requests.put((lambda:result.update(attach(self,body['client_pid'],body['client_started'],
                                                             queued_at=queued_at)),done,result))
            if not done.wait(3):
                result['expired']=True
                raise ValueError('Farmer attachment pending; inspect current state before retrying')
            if result.get('error'):raise ValueError(result['error'])
            return result
        if action=='embed-client' and set(body)=={'action','character'}:
            character = character_name(body['character'])
            queued_at = time.monotonic()
            self.ui_requests.put((lambda:self.embed_client(character,queued_at=queued_at),None,{}))
            return {'requested':character,'listing_submitted':False}
        if action=='verify-booth' and set(body)=={'action','character'}:
            character = character_name(body['character'])
            self.ui_requests.put((lambda:self.start_qualification(character),None,{}))
            return {'requested':character,'listing_submitted':False}
        if action=='scan' and set(body)<= {'action','request_id','character'}:
            targets = [character_name(body['character'])] if body.get('character') else CHARACTERS
            return {c:self.runtime.journal.request_scan(c,body['request_id']) for c in targets}
        if action=='list-once' and set(body)<= {'action','request_id','character'}:
            targets = [character_name(body['character'])] if body.get('character') else CHARACTERS
            return {c:self.runtime.list_once(c,body['request_id']) for c in targets}
        if action=='receipts' and set(body)<= {'action','after'}:
            after = body.get('after',0)
            if type(after) is not int or after<0:
                raise ValueError('Invalid receipt cursor')
            return {'events':self.runtime.journal.events(after)}
        if action=='refill-check' and set(body)=={'action','request_id'}:
            if not isinstance(body['request_id'],str) or not 1<=len(body['request_id'])<=100:
                raise ValueError('Invalid request ID')
            for character in CHARACTERS:
                if self.runtime.refill_enabled(character):
                    self.runtime.refills[character].start()
            self.runtime.handoff=body['request_id']
            return {'requested':body['request_id']}
        if action=='handoff-request' and set(body)=={'action','request_id'}:
            if not isinstance(body['request_id'],str) or not 1 <= len(body['request_id']) <=100:
                raise ValueError('Invalid request ID')
            self.runtime.handoff = body['request_id']
            return {'requested':body['request_id'],'ready':self.safe_to_yield()}
        if action=='handoff-grant' and set(body)=={'action','request_id','revision','expires_at','safe'}:
            if (body['request_id'] != self.runtime.handoff or body['safe'] is not True
                    or body['revision'] != self.app.control.snapshot()['revision']
                    or type(body['expires_at']) not in (int,float)
                    or not 0 < body['expires_at']-time.time() <= 15):
                raise ValueError('Farmer must explicitly grant a current safe handoff (maximum 15 seconds)')
            self.grant = dict(body)
            self.runtime.work_deadline = body['expires_at']
            return {'granted':True}
        if action=='handoff-release' and set(body)=={'action','request_id'}:
            if body['request_id'] != self.runtime.handoff:
                raise ValueError('Handoff request mismatch')
            self.grant = None
            # Revocation prevents the next input, but release events still run.
            if self.coordinator.owner:
                return {'released':False,'waiting_for_input_release':True}
            self.runtime.handoff = None
            self.runtime.delivery_window = None
            self.runtime.refill_window = None
            self.runtime.finish_handoff()
            return {'released':True}
        raise ValueError('Unsupported merchant command or arguments')

    def build_overview(self):
        frame = self.frames['Overview']
        ttk.Label(frame,text='Conquest',font=('Segoe UI',22,'bold')).pack(anchor='w',padx=20,pady=15)
        ttk.Label(frame,text='Manage Spiritual and Dutch shops · America').pack(anchor='w',padx=20)
        for name in ('Farmer',*CHARACTERS):
            box = ttk.LabelFrame(frame,text=name,padding=15);box.pack(fill='x',padx=20,pady=10)
            text = tk.StringVar(value='Connecting…')
            self.rows[name] = text
            ttk.Label(box,textvariable=text,wraplength=900).pack(anchor='w')
        row = ttk.Frame(frame);row.pack(fill='x',padx=20,pady=10)
        ttk.Button(row,text='Update both shops now',command=self.list_once).pack(side='left')
        ttk.Button(row,text='How shop controls work',command=self.shop_help).pack(side='left',padx=8)
        ttk.Button(row,text='Configure Discord #shops',command=self.configure_shops).pack(side='left',padx=8)
        ttk.Button(row,text='Stop all (including farmer)',command=self.global_stop).pack(side='right')
        self.input_note = tk.StringVar()
        ttk.Label(frame,textvariable=self.input_note,wraplength=900).pack(anchor='w',padx=20,pady=10)

    def configure_shops(self):
        from tkinter import simpledialog,messagebox
        from conquest.merchants.sales_report import save_webhook
        value = simpledialog.askstring('Discord #shops','Paste the webhook URL created in #shops. It is encrypted locally.',
                                       show='*',parent=self.root)
        if value:
            try:
                save_webhook(value)
                messagebox.showinfo('Discord #shops','Saved locally. Scheduled sales reports will use this webhook.',parent=self.root)
            except ValueError:
                messagebox.showerror('Discord #shops','Enter a valid Discord channel webhook URL.',parent=self.root)

    def build_merchant(self, character):
        frame = self.frames[character]
        text = tk.StringVar(value='Connecting…');self.labels[character] = text
        status = ttk.Frame(frame,height=120)
        status.pack(fill='x',padx=12,pady=(8,4));status.pack_propagate(False)
        status_label=ttk.Label(status,textvariable=text,wraplength=950,anchor='nw',justify='left')
        status_label.pack(fill='x')
        status.bind('<Configure>',lambda event:status_label.configure(wraplength=max(200,event.width)))
        controls = ttk.Frame(frame);controls.pack(fill='x',padx=12)
        self.batch_buttons=getattr(self,'batch_buttons',{})
        self.manage_buttons=getattr(self,'manage_buttons',{})
        self.refill_buttons=getattr(self,'refill_buttons',{})
        for index,(label,callback,buttons) in enumerate((
            ('Update shop now',lambda:self.list_once(character),self.batch_buttons),
            ('Enable auto-manage',lambda:self.toggle_manage(character),self.manage_buttons),
            ('Pause auto-refill',lambda:self.toggle_refill(character),self.refill_buttons))):
            button=ttk.Button(controls,text=label,command=callback)
            button.grid(row=0,column=index,sticky='ew',padx=(0,6),pady=2)
            buttons[character]=button
            controls.columnconfigure(index,weight=1)
        more=ttk.Menubutton(controls,text='More / help')
        menu=tk.Menu(more,tearoff=False);more.configure(menu=menu)
        menu.add_command(label='How shop controls work',command=self.shop_help)
        menu.add_command(label='Download prices only (no shop changes)',command=lambda:self.scan(character))
        menu.add_separator()
        for label,callback in (
            ('Show game in this tab',lambda:self.embed_client(character)),
            ('Check shop controls',lambda:self.start_qualification(character)),
            ('Open game in separate window',lambda:self.release_merchant(character)),
            ('Retry reconnect',lambda:self.runtime.recoveries[character].retry()),
            ('Set up automatic login',lambda:self.credentials(character))):
            menu.add_command(label=label,command=callback)
        more.grid(row=0,column=3,padx=(0,6),pady=2)
        ttk.Button(controls,text='Stop all (including farmer)',command=self.global_stop).grid(row=0,column=4,pady=2)
        ttk.Label(frame,text='Update shop now: download prices, list inventory and update shop prices once.',
                  wraplength=950).pack(anchor='w',padx=12,pady=(3,0))
        tabs = ttk.Notebook(frame);tabs.pack(fill='both',expand=True,padx=12,pady=12)
        self.detail_tabs[character] = tabs
        tabs.bind('<<NotebookTabChanged>>',lambda event:self.schedule_visibility())
        client = ttk.Frame(tabs)
        tabs.add(client,text='Client')
        pane = ttk.Frame(client,width=1,height=1)
        # Expand with the viewport; a fixed requested size clipped in-game windows.
        pane.pack(fill='both',expand=True)
        pane.pack_propagate(False)
        self.client_panes[character] = pane
        pane.bind('<Configure>',lambda event,c=character:self.schedule_resize(c))
        pane.bind('<Map>',lambda event:self.schedule_visibility())
        pane.bind('<Unmap>',lambda event:self.schedule_visibility())
        tables = {}
        for label,columns in (
            ('Inventory',('UID','Item','Plus','Sockets','Quantity')),
            ('Booth',('UID','Item','Plus','Sockets','Price')),
            ('Waiting items',('UID','Item','Current','Target','Reason')),
            ('Comparisons',('UID','Item','Current','Target','Reason')),
            ('Trades',('Time','Event','Details')),
            ('Price history',('Time','Event','Details')),
            ('Sales',('Time','Event','Details')),
            ('Logs',('Time','Event','Details'))):
            holder = ttk.Frame(tabs);tabs.add(holder,text=label)
            if label=='Waiting items':
                ttk.Label(holder,text='Items left unchanged until a safe price, shop space or safe input is available. '
                          'The reason for each item is below.',wraplength=950).pack(anchor='w',padx=8,pady=8)
            tree = ttk.Treeview(holder,columns=columns,show='headings')
            scroll = ttk.Scrollbar(holder,orient='vertical',command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right',fill='y');tree.pack(fill='both',expand=True)
            for col in columns:
                tree.heading(col,text=col);tree.column(col,width=130 if col!='Details' and col!='Reason' else 440)
            tables[label] = tree
        self.tables[character] = tables

    def shop_help(self):
        messagebox.showinfo('Shop controls',
            'Update shop now\nDownloads current prices, lists eligible inventory and updates existing shop prices. '
            'Runs once, then stops. Auto-refill keeps its own setting.\n\n'
            'Download prices only (under More / help)\nUpdates saved market data without changing your shop. '
            'Use Update shop now when you want those prices applied.\n\n'
            'Auto-manage\nEnables incoming trades, new-stock listing, scheduled price updates and reconnect recovery. '
            'Pausing it also pauses an active shop update.\n\n'
            'Auto-refill\nEvery fifteen minutes, checks for empty shop slots and fills them from inventory using saved prices, '
            'highest value first. Works independently of auto-manage. It does not reprice existing listings.\n\n'
            'Waiting items\nItems that need a safe price, free shop space or safe input. '
            'Each item has a reason in the Waiting items tab. Unknown prices are never guessed.\n\n'
            'Stop all (including farmer)\nStops farming, merchant actions and auto-refill.',parent=self.root)

    def toggle_manage(self, character):
        if self.runtime.enabled(character):self.pause(character)
        else:self.resume(character)

    def toggle_refill(self, character):
        if self.runtime.refill_enabled(character):self.runtime.set_refill_enabled(character,False)
        else:self.resume_refill(character)

    def pause(self, character):
        connecting=self.runtime.connect_cancel.get(character)
        if connecting:connecting.set()
        if self.background_probe.get('character')==character:
            self.background_cancel.set()
        cancel = self.calibration_cancel.get(character)
        if cancel:
            cancel.set()
        self.runtime.enable(character,False)

    def schedule_resize(self, character):
        old = self.resize_jobs.pop(character,None)
        if old:
            self.root.after_cancel(old)
        self.resize_jobs[character] = self.root.after(150,lambda:self.finish_resize(character))

    def schedule_visibility(self):
        if not self.closed and self.visibility_job is None:
            self.visibility_job = self.root.after_idle(self.refresh_visibility)

    def refresh_visibility(self):
        self.visibility_job = None
        if not self.closed:
            for character in tuple(self.hosts):
                self.finish_resize(character)
            self.auto_show_selected()

    def auto_show_selected(self):
        if self.closed or probe_busy(self) or self.auto_embedding or self.coordinator.owner or self.calibrating:
            return
        selected=self.notebook.select()
        character=next((c for c in CHARACTERS if selected==str(self.frames[c])),None)
        if (not character or character in self.released_clients or not self.client_panes[character].winfo_ismapped()
                or time.monotonic()<self.auto_embed_retry.get(character,0)):
            return
        host=self.hosts.get(character)
        if host and host.saved:return
        # Attachment is independent of booth/trade read availability. A foreign
        # shop panel must not make the actual game window inaccessible.
        if character not in self.runtime.observers:return
        self.auto_embedding=True
        try:
            self.embed_client(character,automatic=True)
            self.auto_embed_retry[character]=time.monotonic()+2
        finally:
            self.auto_embedding=False

    def finish_resize(self, character):
        self.resize_jobs.pop(character,None)
        if self.closed or probe_busy(self):
            return
        try:
            self.resize_merchant(character)
            host=self.hosts.get(character)
            if host and host.saved:
                self.layout_status.setdefault(character,{}).update(
                    native_visible=bool(host.api.gui.IsWindowVisible(host.saved.hwnd)),
                    selected=bool(self.client_panes[character].winfo_ismapped()))
        except (OSError,ValueError):
            self.pause(character)
            self.calibration_results[character] = {'verified':False,'note':'Client layout unavailable; re-embed before continuing'}

    def resize_merchant(self, character, *, automatic=False):
        if probe_busy(self):
            return
        host = self.hosts.get(character)
        if not host or not host.saved:
            return
        pane = self.client_panes[character]
        size = (pane.winfo_width(),pane.winfo_height())
        previous = self.render_sizes.get(character)
        if pane.winfo_ismapped() and min(size)>1 and previous!=size:
            # Stop before changing the surface that any in-flight input targets.
            if not automatic:
                self.runtime.invalidate_refill(character)
                self.pause(character)
            self.render_sizes[character] = size
            # Display never waits on process memory/calibration or rewrites its
            # evidence. Drivers still verify native + GUI dimensions before input.
            self.calibration_results[character] = {'verified':False,
                'note':'Client visible; automation checks control dimensions before input'}
        host.resize(*size)

    def embed_client(self, character, *, queued_at=None, automatic=False):
        if probe_busy(self):
            if not automatic:
                self.defer_background_action(lambda:self.embed_client(character))
            return
        self.released_clients.discard(character)
        started = time.monotonic()
        try:
            self.embed_merchant(character,automatic=automatic)
            self.layout_status[character] = {'embed_ms':round((time.monotonic()-started)*1000),
                'queue_ms':round((started-queued_at)*1000) if queued_at is not None else 0}
        except (OSError,ValueError) as error:
            self.calibration_results[character] = {'verified':False,'note':str(error)}

    def embed_merchant(self, character, *, automatic=False):
        if probe_busy(self):
            raise ValueError('Background diagnostic owns the client; wait for restoration')
        if (not self.safe_to_yield() or self.runtime.enabled(character) and not automatic
                or self.calibrating and character not in self.calibrating):
            raise ValueError('Pause the merchant and wait for a safe farmer handoff before embedding')
        observer = self.runtime.observers.get(character)
        if not observer:
            raise ValueError('Waiting for a verified merchant process')
        from conquest.window_host import EmbeddedWindow
        host = self.hosts.setdefault(character,EmbeddedWindow(mode='owned'))
        if host.saved and host.saved.identity!=observer.adapter.identity:
            host.detach()
        self.notebook.select(self.frames[character])
        self.detail_tabs[character].select(0)
        self.root.update_idletasks()
        pane = self.client_panes[character]
        if not host.saved:
            host.attach(observer.operations.target.hwnd,observer.adapter.identity,pane.winfo_id(),
                        pane.winfo_width(),pane.winfo_height())
        self.resize_merchant(character,automatic=automatic)
        return observer

    def show_merchant(self, character):
        if probe_busy(self):
            raise ValueError('Background diagnostic owns the client; wait for restoration')
        if self.closed or self.app.closing:
            raise ValueError('App is closing')
        if not self.safe_to_yield():
            raise ValueError('Farmer has not granted a safe handoff')
        host = self.hosts.get(character)
        observer = self.runtime.observers.get(character)
        if not host or not host.saved or not observer or host.saved.identity!=observer.adapter.identity:
            self.embed_merchant(character,automatic=True)
            host = self.hosts[character]
        foreground = host.api.gui.GetForegroundWindow()
        bookmark = {'tab':self.notebook.select(),'hwnd':foreground,'identity':None}
        if foreground:
            try:
                import ctypes
                from ctypes import wintypes
                pid = wintypes.DWORD()
                host.api.backend.window_pid(foreground,ctypes.byref(pid))
                bookmark['identity'] = host.api.backend.identity(pid.value)
            except (OSError,ValueError):
                pass
        self.input_bookmarks[character] = bookmark
        self.notebook.select(self.frames[character])
        self.detail_tabs[character].select(0)
        self.root.update_idletasks()
        # Switching Tk tabs does not synchronously hide the owned top-level
        # game. Explicitly hide siblings before showing the next input owner.
        for other,other_host in self.hosts.items():
            if other!=character and other_host.saved:
                other_host.api.assert_owner(other_host.saved.hwnd,other_host.saved.identity)
                other_host.api.show_async(other_host.saved.hwnd,0)
        self.resize_merchant(character,automatic=True)

    def prepare_input(self, character):
        if character=='Farmer' or self.coordinator.purpose=='connect_launch':
            return
        if self.coordinator.purpose=='connect':
            observer=self.runtime.observers.get(character)
            from conquest.reconnect import login_screen
            if observer and login_screen(observer.operations.target.hwnd):
                # Keep the native login shell until authentication completes;
                # its activation handler must not compete with an owned host.
                from conquest.focus_recovery import activate_client
                if not activate_client(observer.operations.target.hwnd,observer.adapter.identity):
                    raise ValueError('Activate the selected login client before continuing')
                return
        done,result = threading.Event(),{}
        self.ui_requests.put((lambda:self.show_merchant(character),done,result))
        if not done.wait(5):
            # The UI queue checks this before running a timed-out request.
            result['expired'] = True
            raise ValueError('Embedded merchant pane did not become available')
        if result.get('error'):
            raise ValueError(result['error'])
        host=self.hosts[character]
        others=[h for c,h in self.hosts.items() if c!=character]
        wait_for_merchant_surface(host,others,self.coordinator.check)

    def release_input(self, character):
        if character!='Farmer':
            self.ui_requests.put((lambda:self.restore_input(character),None,{}))

    def restore_input(self, character):
        bookmark = self.input_bookmarks.pop(character,None)
        host = self.hosts.get(character)
        if (not bookmark or not host or not host.saved or self.closed
                or self.coordinator.owner or self.app.mouse_priority.active()
                or host.api.gui.GetForegroundWindow()!=host.saved.hwnd
                or self.notebook.select()!=str(self.frames[character])):
            return
        if bookmark['tab'] in self.notebook.tabs():
            self.notebook.select(bookmark['tab'])
            self.root.update_idletasks()
            self.resize_merchant(character)
        if bookmark['identity'] and host.api.owns_window(bookmark['hwnd'],bookmark['identity']):
            # Restore only the still-existing previous window. A user focus
            # change above wins; idle polling never activates a merchant.
            try:
                host.api.gui.SetForegroundWindow(bookmark['hwnd'])
            except Exception:
                pass

    def poll_ui_requests(self):
        if self.closed:
            return
        now = time.monotonic()
        gap = round((now-self.last_ui_tick)*1000)
        self.ui_health = {'last_gap_ms':gap,'max_gap_ms':max(gap,self.ui_health['max_gap_ms'])}
        self.last_ui_tick = now
        while True:
            try:
                callback,done,result = self.ui_requests.get_nowait()
            except queue.Empty:
                break
            try:
                if not result.get('expired'):
                    callback()
            except Exception as error:
                result['error'] = str(error) if isinstance(error,(ValueError,OSError)) else 'Embedded client UI action failed'
            finally:
                if done:
                    done.set()
        self.root.after(50,self.poll_ui_requests)

    def focus_clicked_merchant(self):
        if self.closed or self.coordinator.owner:
            return False
        for character,host in self.hosts.items():
            if not host.saved or host.mode!='owned':
                continue
            started = time.monotonic()
            if host.api.activate_owned_click(host.saved,host.parent):
                self.layout_status.setdefault(character,{})['click_focus_ms'] = round((time.monotonic()-started)*1000)
                return True
        return False

    def start_qualification(self, character):
        if self.calibrating:
            self.calibration_results[character] = {'verified':False,'note':'Another booth verification is active'}
            return
        try:
            observer = self.embed_merchant(character)
            self.calibrating.add(character)
            cancel = self.calibration_cancel[character] = threading.Event()
            self.calibration_results[character] = {'verified':False,'note':'Waiting for mouse idle, then verifying booth controls'}
            self.coordinator.resume()
            def work():
                try:
                    from conquest.merchants.qualification import verify_booth_controls
                    from conquest.desktop_runtime import physical_coordinates
                    def check():
                        if cancel.is_set() or self.closed:
                            raise ValueError('Booth verification stopped')
                        self.coordinator.check()
                    wait_for_calibration_idle(self.coordinator,cancel,lambda:self.closed)
                    with self.coordinator.lease(character),observer.lock,physical_coordinates():
                        if self.runtime.journal.pending(character):
                            raise ValueError('Reconcile pending transactions before panel verification')
                        from conquest.merchants.inventory_panel import verify_inventory_panel
                        driver=self.runtime.controllers[character].driver
                        if any(w['name']=='Add Item to Booth' for w in driver.read()['windows']):
                            # Resume a canceled/unsubmitted qualification dialog
                            # without toggling Inventory beneath that modal.
                            driver.require_qualified('inventory_panel')
                        else:verify_inventory_panel(driver,check)
                        result = verify_booth_controls(driver,self.runtime.journal,check)
                    self.calibration_results[character] = result
                except Exception as error:
                    failure = calibration_failure(error)
                    self.calibration_results[character] = failure
                    if not failure['note'].startswith(('Mouse remained active','Automation stopped or manual input active','Booth verification stopped')):
                        self.runtime.journal.set(character,'attention',{'kind':'calibration','note':failure['note']})
                    self.runtime.journal.event(character,'booth_calibration_failed',**failure)
                finally:
                    self.calibrating.discard(character)
            threading.Thread(target=work,daemon=True,name=f'qualify-{character}').start()
        except (ValueError,OSError) as error:
            self.calibration_results[character] = {'verified':False,'note':str(error)}

    def release_merchant(self, character):
        if probe_busy(self):
            self.defer_background_action(lambda:self.release_merchant(character))
            return
        self.pause(character)
        if self.coordinator.owner==character or character in self.calibrating:
            self.calibration_results[character] = {'verified':False,'note':'Waiting for input release; press Release client again'}
            return
        host = self.hosts.get(character)
        self.released_clients.add(character)
        if host:
            host.detach()

    def resume(self, character):
        self.coordinator.resume()
        self.runtime.enable(character,True)
        self.runtime.journal.set(character,'new_stock',True)

    def resume_refill(self, character):
        self.coordinator.resume()
        self.runtime.set_refill_enabled(character,True)

    def scan(self, character=None):
        self.dispatch({'action':'market-refresh','request_id':f'manual:{uuid.uuid4().hex}',**({'character':character} if character else {})})

    def list_once(self, character=None):
        try:
            for c in ([character] if character else CHARACTERS):
                existing=self.runtime.journal.get(c,'scan',{})
                if existing.get('pending'):
                    if not self.runtime.enabled(c):self.dispatch({'action':'resume-batch','character':c})
                else:
                    self.dispatch({'action':'list-once','request_id':f'once:{uuid.uuid4().hex}','character':c})
        except (ValueError,OSError) as error:
            messagebox.showerror('One-time listing',str(error),parent=self.root)

    def global_stop(self):
        self.grant = None
        for cancel in self.calibration_cancel.values():
            cancel.set()
        self.runtime.global_stop()
        self.app.stop()

    def credentials(self, character):
        from conquest.merchants.recovery import save_credentials
        dialog = tk.Toplevel(self.root);dialog.title(f'{character} — local encrypted login')
        dialog.transient(self.root)
        ttk.Label(dialog,text='Stored with Windows encryption for this character only.').pack(padx=15,pady=10)
        username,password = tk.StringVar(),tk.StringVar()
        for label,value in (('Username',username),('Password',password)):
            ttk.Label(dialog,text=label).pack(anchor='w',padx=15)
            ttk.Entry(dialog,textvariable=value,show='*' if label=='Password' else '').pack(fill='x',padx=15,pady=5)
        def save():
            try:
                save_credentials(character,username.get(),password.get())
            except (ValueError,OSError):
                messagebox.showerror('Login','Could not save credentials. Enter both fields and check local file access.',parent=dialog)
                return
            username.set('');password.set('');dialog.destroy()
        ttk.Button(dialog,text='Save locally',command=save).pack(padx=15,pady=10)

    @staticmethod
    def fill(tree, rows):
        normalized = [tuple(str(v) for v in row) for row in rows]
        if getattr(tree,'_merchant_rows',None) == normalized:
            return
        tree._merchant_rows = normalized
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert('','end',values=row)

    def update_header(self, statuses):
        from conquest.merchants.dashboard import header_text
        now = time.time()
        data=self.presentation.latest
        self.header_status = header_text(data['sales'],data['reporting'],statuses,now=now)
        self.timer_text.set(self.header_status['timers'])
        self.silver_text.set(self.header_status['silver'])
        self.on_sale_text.set(self.header_status['on_sale'])

    def poll(self):
        if self.app.closing:
            self.close();return
        try:
            self.auto_show_selected()
            data=self.presentation.latest
            if data is None:return
            statuses = data['characters']
            self.update_header(statuses)
            control = self.app.control.snapshot()
            self.rows['Farmer'].set(f'{"Enabled" if control["enabled"] else "Stopped"} · {self.app.state_text.get()} · {self.app.stats_text.get()}')
            self.input_note.set(f'Input owner: {self.coordinator.owner or "none"}. '
                + ('Farmer handoff available.' if self.safe_to_yield() else 'Waiting for the farmer to stop or explicitly grant a safe handoff.'))
            events = data['events']
            for character,state in statuses.items():
                snapshot = state['snapshot'] or {}
                from conquest.merchants.dashboard import merchant_text
                note=merchant_text(state,now=time.time(),waiting_items=data['tables'][character]['deferred'],
                                   global_stopped=self.coordinator.stopped)
                if character in self.background_surfaces and self.background_probe.get('state')=='restoration_required':
                    note += ('\nClient restoration needs attention: '
                             + str(self.background_probe.get('restoration_error') or self.background_probe.get('error') or 'native window restore failed')
                             + '. Embed client retries restoration.')
                active_batch=state['scan'].get('pending')
                self.batch_buttons[character].configure(text='Updating shop…' if active_batch and state['enabled'] else
                    'Resume shop update' if active_batch else 'Update shop now',
                    state='disabled' if active_batch and state['enabled'] else 'normal')
                self.manage_buttons[character].configure(text=(
                    'Pause shop update' if active_batch and state['scan'].get('one_time') else 'Pause auto-manage')
                    if state['enabled'] else 'Enable auto-manage',
                    state='disabled' if active_batch and not state['enabled'] else 'normal')
                self.refill_buttons[character].configure(text='Pause auto-refill' if state['refill']['enabled'] else 'Enable auto-refill')
                host = self.hosts.get(character)
                if host and host.saved:
                    try:
                        self.resize_merchant(character)
                    except (ValueError,OSError):
                        self.pause(character)
                        note += '\nEmbedded client changed; reconnect and verify again'
                self.rows[character].set(note);self.labels[character].set(note)
                if time.monotonic()-data['collected_at']>5:
                    self.rows[character].set(note+'\nStatus refresh delayed; client display remains available')
                    self.labels[character].set(self.rows[character].get())
                if self.notebook.select()!=str(self.frames[character]):continue
                tables = self.tables[character]
                selected_detail=self.detail_tabs[character].tab(self.detail_tabs[character].select(),'text')
                for label,key in (('Inventory','inventory'),('Booth','booth')):
                    if selected_detail!=label:continue
                    self.fill(tables[label],[(i['uid'],i['name'],i['plus'],f'{i["gem1"]}/{i["gem2"]}',i['price'] if key=='booth' else i['quantity']) for i in snapshot.get(key,[])])
                for label,key in (('Comparisons','comparisons'),('Waiting items','deferred')):
                    if selected_detail!=label:continue
                    self.fill(tables[label],[(p['uid'],p['name'],p.get('old_price'),p.get('price'),p['reason']) for p in data['tables'][character][key]])
                own = [e for e in events if e['character']==character]
                for label,kind in (('Trades','delivery_verified'),('Price history','listing_verified'),('Sales','sale_verified'),('Logs',None)):
                    if selected_detail!=label:continue
                    self.fill(tables[label],[(time.strftime('%H:%M:%S',time.localtime(e['timestamp'])),e['event'],e['payload']) for e in own
                        if kind is None or e['event']==kind or label=='Sales' and e['event']=='sales_history_reconciled'])
        finally:
            if not self.closed:
                self.root.after(1000,self.poll)

    def defer_background_action(self, callback):
        """Cancel diagnostics, restore their HWND, then honor explicit UI intent."""
        self.background_cancel.set()
        self.background_after = callback
        if self.background_after_job is None:
            self.background_after_job = self.root.after(100,self.finish_background_action)

    def finish_background_action(self):
        self.background_after_job = None
        if self.closed:
            return
        if self.background_probe.get('state') in ('running','restoring'):
            self.background_after_job = self.root.after(100,self.finish_background_action)
            return
        callback,self.background_after = self.background_after,None
        if callback:
            if self.background_surfaces:
                start_surface_restore(self,on_complete=callback)
            else:
                callback()

    def close(self, *, reason='closed'):
        if probe_busy(self):
            self.defer_background_action(self.app.restart if reason=='restarting' else self.app.close)
            self.app.state_text.set('Waiting for background client restoration; Conquest remains open')
            return False
        if not self.closed:
            self.closed = True
            self.background_cancel.set()
            from conquest.discord_notify import write_json
            write_json('.runtime/merchants/app-lifecycle.json',{'state':reason,'at':time.time()})
            write_json('.runtime/merchants/window.json',{'geometry':self.root.geometry(),'state':self.root.state()})
            if self.visibility_job is not None:
                self.root.after_cancel(self.visibility_job)
                self.visibility_job = None
            for job in self.resize_jobs.values():
                self.root.after_cancel(job)
            self.resize_jobs.clear()
            self.grant = None
            for cancel in self.calibration_cancel.values():
                cancel.set()
            self.presentation.close();self.runtime.close();self.bridge.close();install(None)
            for host in self.hosts.values():
                host.detach()
        return True
