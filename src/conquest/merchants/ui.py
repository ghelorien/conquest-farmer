"""Unified native tabs around the unchanged farmer controls."""
from conquest.character_context import state_path, ProfileMap, is_farmer_owner
import json
import sqlite3
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


def callback_failure(error):
    """Return a bounded, useful UI-callback failure without a traceback dump."""
    def message(value,limit=180):
        # Callback errors reach a durable probe journal.  Keep control
        # characters/newlines from corrupting that record or its UI rendering.
        text=''.join(char if char.isprintable() else ' ' for char in str(value))
        text=' '.join(text.split())
        return text if len(text)<=limit else text[:limit-1]+'…'
    chain=[];seen=set();current=error
    while current is not None and id(current) not in seen and len(chain)<3:
        seen.add(id(current))
        detail=message(current)
        chain.append(type(current).__name__+(f': {detail}' if detail else ''))
        current=current.__cause__ or current.__context__
    return ('Embedded client UI action failed: '+' <- '.join(chain))[:640]


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


def client_tab_character(notebook, frames, detail_tabs, client_tabs):
    """Return the merchant whose native Client pane currently owns the view."""
    selected=notebook.select()
    for character in CHARACTERS:
        if (selected==str(frames[character]) and
                str(detail_tabs[character].select())==str(client_tabs[character])):
            return character
    return None


def set_packed(widget, visible, **options):
    """Show or hide packed chrome without disturbing unrelated geometry."""
    if visible:
        if not widget.winfo_manager():widget.pack(**options)
    elif widget.winfo_manager():
        widget.pack_forget()


def refresh_permission_menu(menu, entries, state):
    """Refresh the saved permission entries without relying on menu offsets."""
    manage,refill=entries
    menu.entryconfigure(manage,label=('Pause' if state['enabled'] else 'Enable')+' trading & repricing')
    menu.entryconfigure(refill,label=('Pause' if state['refill']['enabled'] else 'Enable')+' automatic refill')


class UnifiedUI:
    def __init__(self, app):
        self.app,self.root = app,app.root
        app.stale_handoff_dispatch=self.dispatch
        host=getattr(app,'host',None)
        if host is not None and getattr(host,'mode',None)=='owned':
            # Preserve the saved standalone preference, but an owned game in
            # this shared UI must remain within its Farmer pane.
            host.api.constrain_owned_to_parent=True
        self.closed,self.grant = False,None
        from conquest.merchants.grant_fence import GrantFence
        self.grant_fence=GrantFence()
        self.input_revision_marker=(app.control.snapshot()['revision'],bool(app.mouse_priority.active()))
        self.ui_requests = queue.Queue()
        self.hosts,self.client_panes,self.client_tabs,self.detail_tabs = {},{},{},{}
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
        self.merchant_chrome = {}
        self.permission_menu_entries = {}
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
        self.notebook.bind('<<NotebookTabChanged>>',lambda event:self.on_tab_changed())
        self.root.bind('<Configure>',lambda event:self.schedule_visibility() if event.widget==self.root else None,add='+')
        self.frames=ProfileMap()
        for name in ('Overview','Farmer',*CHARACTERS):
            frame=ttk.Frame(self.notebook);self.frames[name]=frame
            self.notebook.add(frame,text=str(name))
        app.content_parent = self.frames['Farmer']
        sidebar_host.pack(in_=app.content_parent,side='left',fill='both',expand=True)
        # pack(in_=...) changes geometry ownership, not the native parent.
        # These existing root children must sit above the newer notebook.
        sidebar_host.lift()
        app.pane.lift()
        self.coordinator = InputCoordinator(self.safe_to_yield,app.mouse_priority.active)
        self.runtime = MerchantRuntime(app.catalog,self.coordinator)
        self.runtime.configure_manual_farmer(lambda:getattr(app,'observer',None),app.control.snapshot)
        from conquest.merchants.delivery_status import enrich
        self.runtime.status_projection=lambda states:enrich(self,states)
        self.connect_threads={}
        from conquest.merchants.presentation import MerchantPresentation
        self.presentation = MerchantPresentation(self.runtime)
        def owner_allowed(character):
            if is_farmer_owner(character):
                return True
            # A disabled merchant may perform only the single receipt-bound
            # delivery accept probe.  It must not fall through the historical
            # broad calibration exception when a delivery/refill fence exists.
            if self.coordinator.purpose=='delivery_accept_probe':
                from conquest.merchants.delivery_accept_probe import lease_authorized
                return lease_authorized(self,character)
            if self.coordinator.purpose=='delivery_probe_abort':
                from conquest.merchants.delivery_abort_probe import lease_authorized
                return lease_authorized(self,character)
            if self.coordinator.purpose=='booth_probe_1078_no_submit':
                return self.coordinator.booth_probe_authorized(character)
            return self.runtime.input_allowed(character) or (
                not getattr(self.runtime,'delivery_window',None) and not getattr(self.runtime,'refill_window',None)
                and character in self.calibrating and not self.calibration_cancel[character].is_set())
        self.coordinator.owner_allowed = owner_allowed
        self.coordinator.on_acquire = self.prepare_input
        self.coordinator.on_release = self.release_input
        self.coordinator.fence=self.grant_fence
        # Get the process lock before installing any input hook or starting
        # merchant threads. A second UI cannot become a second controller.
        self.bridge = MerchantBridge(self.dispatch)
        install(self.coordinator)
        self.rows,self.labels,self.tables = ProfileMap(),{},{}
        self.manual_displayed,self.manual_texts,self.manual_buttons = ProfileMap(),ProfileMap(),ProfileMap()
        self.build_overview()
        for character in CHARACTERS:
            self.build_merchant(character)
        self.root.title('Conquest')
        from conquest.portable_ui import install as install_profiles
        install_profiles(self)
        self.root.geometry('1080x850')
        self.root.minsize(640,480)
        from conquest.discord_notify import read_json
        saved_window = read_json(state_path('.runtime/merchants/window.json'))
        import re
        geometry = saved_window.get('geometry','')
        if isinstance(geometry,str) and re.fullmatch(r'\d{3,5}x\d{3,5}[+-]\d+[+-]\d+',geometry):
            from conquest.client_attachment import fit_geometry,available_work_areas
            parts=re.fullmatch(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)',geometry)
            try:
                w,h,x,y=fit_geometry(*map(int,parts.groups()),available_work_areas())
                self.root.geometry(f'{w}x{h}{x:+d}{y:+d}')
            except (OSError,ValueError):pass
        if saved_window.get('state')=='zoomed':
            self.root.state('zoomed')
        self.runtime.start()
        self.presentation.start()
        from conquest.discord_notify import write_json
        write_json(state_path('.runtime/merchants/app-lifecycle.json'),{'state':'running','at':time.time()})
        self.root.after(500,self.poll)
        self.root.after(50,self.poll_ui_requests)

    def safe_to_yield(self):
        if self.app.closing:
            return False
        control = self.app.control.snapshot()
        if self.grant:
            fence=getattr(self,'grant_fence',None)
            if fence and (fence.active is None or fence.active.request_id!=self.grant['request_id']):
                return False
            if (self.grant['expires_at'] > time.time() and self.grant['revision']==control['revision']
                    and not control['enabled'] and not control.get('paused')):
                return True
            fence=getattr(self,'grant_fence',None)
            if fence and self.grant['request_id'] in fence.requests:
                fence.revoke(self.grant['request_id'])
            return False
        if control['enabled'] or (self.app.thread and self.app.thread.is_alive()):
            return False
        from conquest.discord_notify import read_json,process_alive
        route = read_json(state_path('reports/overnight/status.json'))
        # A stale heartbeat does not prove its process stopped.
        if route.get('phase') not in (None,'stopped','completed','failed') and process_alive(route.get('pid')) is not False:
            return False
        return True

    def dispatch(self, body):
        from conquest.portable_ui import normalize_command
        from conquest.character_context import profile_status
        body=normalize_command(body)
        if body=={'action':'profiles'}:return {'profiles':profile_status()}
        action = body.get('action')
        if action in ('merchant-booth-probe-1078', 'merchant-booth-probe-status-1078'):
            from conquest.merchants.booth_probe_1078 import dispatch
            return dispatch(self, body)
        if action=='merchant-booth-confirm-diagnostic-1078':
            if set(body)!={'action','character'}:
                raise ValueError('1078 booth confirm diagnostic requires exactly one merchant')
            from conquest.merchants.booth_confirm_diagnostic_1078 import collect
            return collect(self.runtime,body['character'])
        if action in ('merchant-observe-1078','merchant-listing-preflight-1078',
                      'merchant-booth-target-preflight-1078'):
            if set(body)!={'action','character'}:
                raise ValueError('Read-only merchant observation requires exactly one character')
            from conquest.merchants.observe_1078 import observe
            return observe(self.runtime,body['character'],
                           listing_preflight=action=='merchant-listing-preflight-1078',
                           booth_target_preflight=action=='merchant-booth-target-preflight-1078')
        if action=='merchant-restoration-preview-1078':
            if set(body)!={'action','character'}:
                raise ValueError('Read-only restoration preview requires exactly one merchant')
            from conquest.merchants.restoration_preview_1078 import preview
            return preview(self.runtime,body['character'])
        if action=='merchant-refill-preview-1078':
            if set(body)!={'action','character'}:
                raise ValueError('Read-only refill preview requires exactly one merchant')
            from conquest.merchants.refill_preview_1078 import preview
            return preview(self.runtime,body['character'])
        if action=='merchant-refill-cursor-reconcile-1078':
            if set(body)!={'action','character'}:
                raise ValueError('1078 refill cursor reconciliation requires exactly one merchant')
            from conquest.merchants.refill_cursor_reconcile_1078 import reconcile
            return reconcile(self.runtime,body['character'])
        if action=='merchant-market-arrival-1078':
            if set(body)!={'action','character'}:
                raise ValueError('Market arrival settlement requires exactly one merchant')
            from conquest.merchants.recovery_safety import settle_market_arrival_1078
            return settle_market_arrival_1078(self.runtime,body['character'])
        if action in ('delivery-stale-pre-admission-preview','delivery-stale-pre-admission-clear'):
            from conquest.merchants.pre_admission_clear import dispatch
            return dispatch(self,body)
        if action in ('farmer-loop-acceptance', 'farmer-loop-acceptance-status','farmer-loop-acceptance-abort',
                      'farmer-loop-acceptance-override-preview','farmer-loop-acceptance-override'):
            from conquest.merchant_loop_acceptance import configure
            return configure(self, body)
        if action=='notification-workers-restart' and set(body)=={'action','worker'}:
            from conquest.notification_workers import restart
            return restart(body['worker'])
        if action=='delivery-target' and set(body)=={'action','character'}:
            from conquest.merchants.farmer_trade import delivery_target_status
            return delivery_target_status(self,character_name(body['character']))
        if action=='trade-qualification-prep-target' and set(body)=={'action','character'}:
            from conquest.merchants.trade_qualification_prep import target_projection
            return target_projection(self,character_name(body['character']))
        if action in ('recovery-status','recovery-recheck','recovery-override'):
            allowed={'action','character'} if action=='recovery-status' else {'action','character','incident_id'}
            if action=='recovery-override':
                allowed |= {'operator_confirmed','confirmation_reference','incident_digest'}
                if 'operator' in body:allowed.add('operator')
            if set(body)!=allowed:raise ValueError('Unsupported recovery command arguments')
            character=character_name(body['character'])
            incidents=self._merchant_recovery_incidents(character)
            incident=next((row for row in incidents if row['id']==body.get('incident_id')),None) if action!='recovery-status' else None
            if action=='recovery-status':return {'character':character,'incidents':incidents}
            if incident is None:raise ValueError('Unknown recovery incident')
            if action=='recovery-recheck':
                fresh=self._merchant_fresh_recheck(character,incident)
                return {'incident':fresh,'incident_digest':fresh.get('digest'),'rechecked':True}
            if body.get('operator_confirmed') is not True or body.get('confirmation_reference')!=body.get('incident_digest'):
                raise ValueError('Exact incident digest confirmation is required')
            if body.get('incident_digest')!=incident.get('digest'):
                raise ValueError('Incident evidence changed; recheck before overriding')
            return self._apply_merchant_override(character,incident,body['incident_digest'],
                                                 operator=body.get('operator'))
        if action=='farmer-view-height' and set(body)=={'action','scale'}:
            scale = float(body['scale'])
            if not 1 <= scale <= 1.15: raise ValueError('Height scale must be between 1 and 1.15')
            if self.app.control.snapshot()['enabled']: raise ValueError('Stop farming before resizing')
            from conquest.discord_notify import write_json
            write_json(state_path('.runtime/farmer-view.json'), {'height_scale':scale})
            from conquest.farmer_view import apply
            self.ui_requests.put((lambda:apply(self.app),None,{}))
            return {'height_scale':scale,'queued':True}
        if action=='disconnect-merchant' and set(body)=={'action','character'}:
            from conquest.merchants.disconnect import disconnect
            return disconnect(self.runtime,body['character'])
        if action=='start-account-diagnostic' and set(body)=={'action','character'}:
            import subprocess,sys
            character=character_name(body['character'])
            path=Path(state_path(f'.runtime/account-diagnostic-{character.lower()}.json'))
            if path.exists():
                from conquest.worker import request as worker_request
                result=worker_request(path,'health')
                if not result.get('read_only'):raise ValueError('Diagnostic worker is not read-only')
                return {'existing':True,'read_only':True}
            process=subprocess.Popen([sys.executable,'-m','conquest.merchants.diagnostic_worker',character],
                cwd=Path.cwd(),creationflags=subprocess.CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return {'starting':True,'read_only':True,'pid':process.pid}
        if action=='test-merchant-recovery' and set(body)=={'action','character'}:
            from conquest.merchants.recovery_trial import start
            return start(self,body['character'])
        if action=='cancel-empty-delivery' and set(body)=={'action','character'}:
            from conquest.merchants.empty_delivery_cancel import start
            return start(self,body['character'])
        if action=='probe-delivery-request' and set(body)=={'action','character','uids'}:
            from conquest.merchants.delivery_probe import start
            return start(self,body['character'],uids=body['uids'])
        if isinstance(action,str) and action.startswith('probe-delivery-abort-'):
            from conquest.merchants.delivery_abort_probe import dispatch
            return dispatch(self,body)
        if action=='probe-delivery-recheck' and set(body)=={'action'}:
            from conquest.merchants.delivery_probe import recheck
            return recheck(self)
        if action=='probe-delivery-promote':
            if set(body)!={'action'}:raise ValueError('Unsupported delivery promotion arguments')
            from conquest.merchants.delivery_promotion import promote_current
            return promote_current(self)
        if action=='probe-delivery-reconciliation-diagnostic':
            if set(body)!={'action'}:raise ValueError('Unsupported reconciliation diagnostic arguments')
            from conquest.merchants.delivery_probe import read_probe
            from conquest.merchants.delivery_bridge import pair
            from conquest.merchants.manual_runtime import probe_attempt_projection
            with self.coordinator.lock:
                last=probe_attempt_projection(getattr(self.runtime,'last_probe_reconciliation',None))
                state=read_probe(read_only=True)
                if not state:raise ValueError('No supervised delivery probe exists')
                character=character_name(state['character'])
                farmer,merchant=pair(self,character)
                return {**self.runtime.inspect_probe_reconciliation(character,farmer,merchant),'last_attempt':last}
        if action=='probe-delivery-reconcile-request':
            if set(body)!={'action'}:raise ValueError('Unsupported request reconciliation arguments')
            from conquest.merchants.delivery_request_reconciliation import reconcile_request
            return reconcile_request(self)
        if action=='probe-delivery-override':
            allowed={'action','operator_confirmed','confirmation_reference','incident_digest'}
            if 'operator' in body:allowed.add('operator')
            if set(body)!=allowed:raise ValueError('Unsupported trade-probe override arguments')
            from conquest.merchants.delivery_probe import operator_override
            return operator_override(self,operator_confirmed=body['operator_confirmed'],
                confirmation_reference=body['confirmation_reference'],
                incident_digest=body['incident_digest'],operator=body.get('operator'))
        if action=='probe-delivery-stage' and set(body)=={'action','stage'}:
            from conquest.merchants.delivery_live import start
            return start(self,body['stage'])
        if action=='prepare-trade-qualification' and set(body)=={'action','character','selected_uid'}:
            from conquest.merchants.trade_qualification_prep import start
            return start(self,body['character'],selected_uid=body['selected_uid'])
        if action=='trade-qualification-prep-status' and set(body)=={'action'}:
            from conquest.merchants.trade_qualification_prep import status
            return status(self)
        if action=='trade-qualification-prep-recheck' and set(body)=={'action'}:
            from conquest.merchants.trade_qualification_prep import recheck
            return recheck(self)
        if action=='trade-qualification-prep-override':
            allowed={'action','operator_confirmed','confirmation_reference','incident_digest'}
            if 'operator' in body:allowed.add('operator')
            if set(body)!=allowed:raise ValueError('Unsupported trade-prep override arguments')
            from conquest.merchants.trade_qualification_prep import operator_override
            return operator_override(self,operator_confirmed=body['operator_confirmed'],
                confirmation_reference=body['confirmation_reference'],
                incident_digest=body['incident_digest'],operator=body.get('operator'))
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
            paths=[Path(state_path(f'.runtime/merchant-diagnostic-{c.lower()}.json')) for c in CHARACTERS]
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
            from conquest.application_layout import RuntimeLayout
            layout=RuntimeLayout.resolve();repo=layout.root
            self.readonly_diagnostics=subprocess.Popen(
                [str(layout.python()),str(layout.script('start_merchant_diagnostics.py'))],
                cwd=repo,env=layout.environment(),creationflags=subprocess.CREATE_NO_WINDOW,
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
                from conquest.merchant_loop_acceptance import refill_source
                for character in CHARACTERS:
                    if self.runtime.refill_enabled(character) and (
                            self.runtime.refills[character].due()
                            or self.runtime.journal.get(character,'new_stock',False)):
                        self.runtime.refills[character].start(visit_id=self.grant.get('visit_id'),
                            town_visit_id=self.grant.get('town_visit_id'),operation_id=key,
                            source_delivery_operation_id=refill_source(key,character))
            finally:self.coordinator.lock.release()
            return {'refill':True,'expires_at':self.grant['expires_at']}
        if action=='delivery-service-retry':
            from conquest.merchants.service_retry import dispatch
            return dispatch(self,body)
        if action in ('delivery-start','delivery-test','delivery-status','delivery-readiness','delivery-reconcile','delivery-cleanup','delivery-recheck','delivery-override'):
            from conquest.merchants.delivery_operation import dispatch
            return dispatch(self,body)
        if action in ('delivery-pair','delivery-reserve','delivery-ready','delivery-finish','delivery-source','delivery-disposition'):
            from conquest.merchants.delivery_bridge import dispatch
            return dispatch(self,body)
        if action=='focus-farmer' and set(body)=={'action'}:
            queued_at=time.monotonic()
            done,result=threading.Event(),{}
            def focus_farmer():
                from conquest.merchants.farmer_surface import focus_idle_farmer
                result.update(focus_idle_farmer(self,queued_at=queued_at))
            self.ui_requests.put((focus_farmer,done,result))
            if not done.wait(3):
                result['expired']=True
                raise ValueError('Farmer focus request expired')
            if result.get('error'):raise ValueError(result['error'])
            return result
        if action=='manual-status' and set(body) in ({'action'},{'action','character'}):
            character=body.get('character')
            if character is not None:character='Farmer' if character=='Farmer' else character_name(character)
            return {'sessions':self.runtime.manual_status(character),
                    'farmer':self.runtime.manual_farmer_status(),
                    'handoff':self.runtime.manual_handoff_status()}
        if action=='manual-handoff-status' and set(body)=={'action'}:
            return {'handoff':self.runtime.manual_handoff_status()}
        if action=='manual-handoff-start' and set(body) in ({'action'},{'action','operator'}):
            return self.runtime.start_manual_handoff(operator=body.get('operator','bridge operator'))
        if action=='manual-handoff-end' and set(body) in ({'action','session_id'},{'action','session_id','operator'}):
            return self.runtime.end_manual_handoff(body['session_id'],operator=body.get('operator','bridge operator'))
        if action in ('manual-approve','manual-reject'):
            allowed={'action','binding'} | ({'operator'} if 'operator' in body else set())
            if set(body)!=allowed:raise ValueError('Unsupported manual decision arguments')
            binding=body.get('binding')
            if not isinstance(binding,dict):raise ValueError('Exact displayed approval binding is required')
            operation=self.runtime.approve_manual if action=='manual-approve' else self.runtime.reject_manual
            return operation(binding,operator=body.get('operator','local UI'))
        if action=='manual-override':
            allowed={'action','session_id','confirmation_reference','operator','reason'}
            if set(body)!=allowed:raise ValueError('Unsupported manual override arguments')
            return self.runtime.override_manual(body['session_id'],
                confirmation_reference=body['confirmation_reference'],operator=body['operator'],reason=body['reason'])
        if action=='status' and set(body)=={'action'}:
            return {'characters':self.runtime.status(),'input_owner':self.coordinator.owner,
                'handoff_requested':self.runtime.handoff,'handoff_granted':bool(self.grant and self.safe_to_yield()),
                'calibration':dict(self.calibration_results),'layout':dict(self.layout_status),
                'ui_health':{**self.ui_health,'tick_age_ms':round((time.monotonic()-self.last_ui_tick)*1000)},
                'sales_reporting':self.runtime.sales_worker.status(),
                'manual_sessions':self.runtime.manual_status(),
                'manual_handoff':self.runtime.manual_handoff_status(),
                'manual_farmer':self.runtime.manual_farmer_status(),
                'header':dict(self.header_status),
                'background_probe':dict(self.background_probe),
                'merchant_ui_version':30}
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
            callback=lambda:self.embed_client(character,queued_at=queued_at)
            fence=getattr(self,'grant_fence',None)
            if fence:callback=fence.guard_callback(fence.capture(),callback)
            self.ui_requests.put((callback,None,{}))
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
        if action=='handoff-grant' and set(body) in ({'action','request_id','revision','expires_at','safe'},
                {'action','request_id','revision','expires_at','safe','scope','visit_id'}):
            market=body.get('scope')=='market_visit'
            control=self.app.control.snapshot()
            if 'scope' in body and not market:raise ValueError('Unknown handoff scope')
            if (body['request_id'] != self.runtime.handoff or body['safe'] is not True
                    or body['revision'] != control['revision'] or control['enabled'] or control.get('paused')
                    or type(body['expires_at']) not in (int,float)
                    or not 0 < body['expires_at']-time.time() <= (60 if market else 15)):
                raise ValueError('Farmer must explicitly grant a current bounded safe handoff')
            visit=None
            if market:
                from conquest.merchants.service_visit import validate_grant
                visit=validate_grant(self,body)
            fence=getattr(self,'grant_fence',None)
            if fence:
                from conquest.merchants.service_visit import farmer_id
                fence.activate(body['request_id'],body['revision'],body['expires_at'],
                    scope='market_visit' if market else 'hunting',farmer_profile_id=farmer_id())
            self.grant = dict(body)
            if visit:
                self.grant['town_visit_id']=visit.get('town_visit_id')
                self.grant['farmer_profile_id']=visit['farmer_profile_id']
                for refill in getattr(self.runtime,'refills',{}).values():
                    if refill.state().get('pending'):
                        refill.start(visit_id=visit['visit_id'],town_visit_id=visit.get('town_visit_id'),
                                     operation_id=body['request_id'])
            self.runtime.work_deadline = body['expires_at']
            return {'granted':True}
        if action=='handoff-release' and set(body)=={'action','request_id'}:
            fence=getattr(self,'grant_fence',None)
            if fence and body['request_id'] in fence.requests:
                released=fence.revoke(body['request_id'])
                if body['request_id']!=self.runtime.handoff:return released
                self.grant=None
                if not released['released']:return released
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
        ttk.Label(frame,text='Characters and coordinated input on this PC').pack(anchor='w',padx=20)
        for name in ('Farmer',*CHARACTERS):
            box = ttk.LabelFrame(frame,text=name,padding=15);box.pack(fill='x',padx=20,pady=10)
            text = tk.StringVar(value='Connecting…')
            self.rows[name] = text
            ttk.Label(box,textvariable=text,wraplength=900).pack(anchor='w')
            manual=tk.StringVar(value='Checking manual visitor status…')
            self.manual_texts[name]=manual
            ttk.Separator(box).pack(fill='x',pady=8)
            ttk.Label(box,textvariable=manual,wraplength=900,justify='left').pack(anchor='w',fill='x')
            actions=ttk.Frame(box);actions.pack(fill='x',pady=(6,0))
            approve=ttk.Button(actions,text='Approve exact visitor',state='disabled',
                command=lambda target=name:self.approve_manual_displayed(target))
            reject=ttk.Button(actions,text='Reject request',state='disabled',
                command=lambda target=name:self.reject_manual_displayed(target))
            override=ttk.Button(actions,text='Resolve attention hold…',state='disabled',
                command=lambda target=name:self.override_manual_displayed(target))
            approve.pack(side='left');reject.pack(side='left',padx=6);override.pack(side='left')
            self.manual_buttons[name]={'approve':approve,'reject':reject,'override':override}
        row = ttk.Frame(frame);row.pack(fill='x',padx=20,pady=10)
        ttk.Button(row,text='Update all shops now',command=self.list_once).pack(side='left')
        ttk.Button(row,text='How shop controls work',command=self.shop_help).pack(side='left',padx=8)
        ttk.Button(row,text='Configure Discord #shops',command=self.configure_shops).pack(side='left',padx=8)
        ttk.Button(row,text='Stop all (including farmer)',command=self.global_stop).pack(side='right')
        handoff_row=ttk.LabelFrame(frame,text='Operator manual handoff',padding=8)
        handoff_row.pack(fill='x',padx=20,pady=(0,8))
        self.manual_handoff_text=tk.StringVar(value='No global handoff active.')
        ttk.Label(handoff_row,textvariable=self.manual_handoff_text,wraplength=860,justify='left').pack(anchor='w')
        self.manual_handoff_button=ttk.Button(handoff_row,text='Start manual handoff',command=self.toggle_manual_handoff)
        self.manual_handoff_button.pack(anchor='w',pady=(5,0))
        self.discord_note=tk.StringVar(value='Checking Discord notification services...')
        ttk.Label(frame,textvariable=self.discord_note,wraplength=900).pack(anchor='w',padx=20,pady=6)
        self.input_note = tk.StringVar()
        ttk.Label(frame,textvariable=self.input_note,wraplength=900).pack(anchor='w',padx=20,pady=10)

    def refresh_manual_operator(self, statuses, control, *, now=None):
        """Refresh views atomically; button callbacks retain only this displayed JSON."""
        from conquest.merchants.manual_operator import displayed,status_text,action_state
        now=time.time() if now is None else now
        farmer=self.runtime.manual_farmer_status()
        for target in ('Farmer',*CHARACTERS):
            row=farmer.get('session') if target=='Farmer' else self.runtime.manual_status(target)
            shown=displayed(row)
            self.manual_displayed[target]=shown
            intent=(control if target=='Farmer' else {
                'enabled':bool(statuses.get(target,{}).get('enabled')),
                'refill_enabled':bool(statuses.get(target,{}).get('refill',{}).get('enabled'))})
            self.manual_texts[target].set(status_text(target,shown,farmer_status=farmer,
                                                       intent=intent,now=now))
            state=action_state(shown,now=now)
            for action,button in self.manual_buttons[target].items():
                button.configure(state='normal' if state[action] else 'disabled')
        handoff=self.runtime.manual_handoff_status()
        if hasattr(self,'manual_handoff_text'):
            if handoff:
                phase=handoff['phase'].replace('_',' ')
                count=len(handoff.get('participants',[]))
                note=handoff.get('reason') or ('Waiting for stable closed windows.' if phase in ('preparing','ending') else 'User controls all game actions; automation remains fenced.')
                self.manual_handoff_text.set(f'{phase.title()} · {count} participant(s) · {note}')
                self.manual_handoff_button.configure(text='End manual handoff')
            else:
                if getattr(getattr(self.runtime,'manual_1078_registry',None),'read_only_build',False):
                    self.manual_handoff_text.set('1078 manual observation ready; farming and merchant automation are unavailable. Start, then wait for Ready.')
                else:
                    self.manual_handoff_text.set('No global handoff active. Start fences attached Farmer/merchant automation before you act.')
                self.manual_handoff_button.configure(text='Start manual handoff')

    def toggle_manual_handoff(self):
        handoff=self.runtime.manual_handoff_status()
        try:
            if handoff:
                self.runtime.end_manual_handoff(handoff['id'],operator='local UI')
            else:
                self.runtime.start_manual_handoff(operator='local UI')
        except (ValueError,OSError) as error:
            messagebox.showerror('Manual handoff',str(error),parent=self.root)

    def _displayed_manual(self, target, *, binding=False):
        from conquest.merchants.manual_operator import displayed
        row=displayed(self.manual_displayed.get(target))
        if row is None:raise ValueError('The displayed manual session is no longer available')
        if binding and not isinstance(row.get('approval_binding'),dict):
            raise ValueError('The displayed session has no exact approval binding')
        return row

    def approve_manual_displayed(self, target):
        from conquest.merchants.manual_operator import exact_binding_text,visitor_text,decision_seconds
        try:row=self._displayed_manual(target,binding=True)
        except ValueError as error:
            messagebox.showerror('Manual visitor',str(error),parent=self.root);return None
        seconds=decision_seconds(row)
        prompt=(f"Target: {target}\nVisitor: {visitor_text(row.get('visitor'))}\n"
                f"Decision time remaining: {seconds if seconds is not None else '?'} second(s)\n\n"
                "Approve this exact request? Approval only saves permission and activates the memory-observed "
                "manual interval. It sends no gameplay input and does not enable farming, trading or refill.\n\n"
                "Exact approval binding:\n"+exact_binding_text(row))
        if not messagebox.askyesno('Approve exact manual visitor',prompt,parent=self.root):return None
        try:return self.runtime.approve_manual(row['approval_binding'],operator='local UI')
        except (ValueError,OSError) as error:
            messagebox.showerror('Manual visitor',str(error),parent=self.root);return None

    def reject_manual_displayed(self, target):
        from conquest.merchants.manual_operator import exact_binding_text,visitor_text
        try:row=self._displayed_manual(target,binding=True)
        except ValueError as error:
            messagebox.showerror('Manual visitor',str(error),parent=self.root);return None
        prompt=(f"Target: {target}\nVisitor: {visitor_text(row.get('visitor'))}\n\n"
                "Reject this exact request? This persists decline intent only; any native decline remains at the "
                "independently qualified input boundary.\n\nExact approval binding:\n"+exact_binding_text(row))
        if not messagebox.askyesno('Reject exact manual request',prompt,parent=self.root):return None
        try:return self.runtime.reject_manual(row['approval_binding'],operator='local UI')
        except (ValueError,OSError) as error:
            messagebox.showerror('Manual visitor',str(error),parent=self.root);return None

    def override_manual_displayed(self, target):
        from tkinter import simpledialog
        try:row=self._displayed_manual(target)
        except ValueError as error:
            messagebox.showerror('Manual visitor',str(error),parent=self.root);return None
        if row.get('phase')!='needs_attention' and not row.get('rebaseline'):
            messagebox.showerror('Manual visitor','Only a displayed attention/rebaseline hold can be overridden.',parent=self.root);return None
        reason=simpledialog.askstring('Manual visitor hold','Reason for the explicit disposition/retry:',parent=self.root)
        if not reason:return None
        reference=simpledialog.askstring('Manual visitor hold',
            'Enter a durable confirmation reference for this exact displayed session:\n'+str(row.get('id')),parent=self.root)
        if not reference:return None
        if not messagebox.askyesno('Confirm manual visitor disposition',
                f"Target: {target}\nExact session: {row.get('id')}\nReason: {reason}\n\n"
                "This does not prove a transfer or enable automation. Fresh stable memory is still required.",parent=self.root):
            return None
        try:return self.runtime.override_manual(row['id'],confirmation_reference=reference,
                                                operator='local UI',reason=reason)
        except (ValueError,OSError) as error:
            messagebox.showerror('Manual visitor',str(error),parent=self.root);return None

    def configure_shops(self):
        from tkinter import simpledialog,messagebox
        from conquest.merchants.sales_report import save_webhook
        value = simpledialog.askstring('Discord #shops','Paste the webhook URL created in #shops. It is encrypted locally.',
                                       show='*',parent=self.root)
        if value:
            try:
                save_webhook(value)
                from conquest.merchants.alerts import ensure_monitor
                ensure_monitor()
                messagebox.showinfo('Discord #shops','Saved locally. Scheduled sales reports will use this webhook.',parent=self.root)
            except ValueError:
                messagebox.showerror('Discord #shops','Enter a valid Discord channel webhook URL.',parent=self.root)

    def build_merchant(self, character):
        frame = self.frames[character]
        text = tk.StringVar(value='Connecting…');self.labels[character] = text
        status = ttk.Frame(frame)
        status.pack(fill='x',padx=12,pady=(8,4))
        status_label=ttk.Label(status,textvariable=text,wraplength=950,anchor='nw',justify='left')
        status_label.pack(fill='x')
        status.bind('<Configure>',lambda event:status_label.configure(wraplength=max(200,event.width)))
        controls = ttk.Frame(frame);controls.pack(fill='x',padx=12)
        self.batch_buttons=getattr(self,'batch_buttons',{})
        self.merchant_buttons=getattr(self,'merchant_buttons',{})
        primary=ttk.Button(controls,text='Pause merchant',command=lambda:self.toggle_merchant(character))
        primary.grid(row=0,column=0,sticky='ew',padx=(0,6),pady=2)
        self.merchant_buttons[character]=primary
        update=ttk.Button(controls,text='Update shop now',command=lambda:self.list_once(character))
        update.grid(row=0,column=1,sticky='ew',padx=(0,6),pady=2)
        self.batch_buttons[character]=update
        ttk.Button(controls,text='Show manual view',
                   command=lambda c=character:self.request_manual_handoff_surface(c)).grid(row=0,column=2,sticky='ew',padx=(0,6),pady=2)
        controls.columnconfigure(0,weight=1)
        controls.columnconfigure(1,weight=1)
        controls.columnconfigure(2,weight=1)
        more=ttk.Menubutton(controls,text='Settings & details')
        menu=tk.Menu(more,tearoff=False);more.configure(menu=menu)
        self.permission_menus=getattr(self,'permission_menus',{})
        self.permission_menus[character]=menu
        menu.add_command(label='How shop controls work',command=self.shop_help)
        menu.add_command(label='Full status details',command=lambda:self.show_merchant_details(character))
        menu.add_command(label='Recovery status',command=lambda:self.show_recovery_status(character))
        menu.add_command(label='Recheck recovery',command=lambda:self.recheck_merchant_recovery(character))
        menu.add_command(label='Override recovery & resume',command=lambda:self.override_merchant_recovery(character))
        menu.add_separator()
        menu.add_command(label='Toggle trading & repricing permission',command=lambda:self.toggle_manage(character))
        manage_entry=menu.index('end')
        menu.add_command(label='Toggle automatic refill permission',command=lambda:self.toggle_refill(character))
        refill_entry=menu.index('end')
        self.permission_menu_entries[character]=(manage_entry,refill_entry)
        menu.add_separator()
        from conquest.portable_ui import copy_diagnostics
        menu.add_command(label='Copy attachment diagnostics',command=lambda:copy_diagnostics(self,character))
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
        recovery = ttk.Frame(frame)
        recovery.pack(fill='x',padx=12,pady=(3,0))
        self.recovery_texts = getattr(self,'recovery_texts',{})
        recovery_text = tk.StringVar(value='Checking for unresolved recovery holds…')
        self.recovery_texts[character] = recovery_text
        ttk.Label(recovery,textvariable=recovery_text,width=55).pack(side='left',fill='x',expand=True)
        ttk.Button(recovery,text='Recheck',command=lambda c=character:self.recheck_merchant_recovery(c)).pack(side='left',padx=(6,0))
        ttk.Button(recovery,text='Override & resume',command=lambda c=character:self.override_merchant_recovery(c)).pack(side='left',padx=(6,0))
        help_text=ttk.Label(frame,text='Pause merchant stops both activities without closing the game. Settings keeps separate permissions.',
                  wraplength=950)
        help_text.pack(anchor='w',padx=12,pady=(3,0))
        tabs = ttk.Notebook(frame);tabs.pack(fill='both',expand=True,padx=12,pady=12)
        self.detail_tabs[character] = tabs
        tabs.bind('<<NotebookTabChanged>>',lambda event:self.on_tab_changed())
        client = ttk.Frame(tabs)
        tabs.add(client,text='Client')
        self.client_tabs[character] = client
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
        self.merchant_chrome[character] = (
            (status,{'fill':'x','padx':12,'pady':(8,4),'before':controls}),
            (recovery,{'fill':'x','padx':12,'pady':(3,0),'before':tabs}),
            (help_text,{'anchor':'w','padx':12,'pady':(3,0),'before':tabs}))

    # ------------------------------------------------------------------
    # Merchant recovery holds
    # ------------------------------------------------------------------
    @staticmethod
    def _recovery_digest(value):
        from conquest.recovery_override import evidence_digest
        return evidence_digest(value)

    @staticmethod
    def _recovery_items_text(items):
        if not items:return 'Items: none recorded'
        rendered=[]
        for item in items[:20]:
            if isinstance(item,dict):
                rendered.append('UID '+str(item.get('uid','?'))+
                    (' · +'+str(item.get('plus')) if item.get('plus') is not None else '')+
                    (' · '+str(item.get('type_id')) if item.get('type_id') is not None else ''))
            else:rendered.append(str(item))
        if len(items)>20:rendered.append(f'… and {len(items)-20} more')
        return 'Items: '+', '.join(rendered)

    def _merchant_recovery_incidents(self, character):
        """Read unresolved holds for one merchant without mutating journals."""
        incidents=[]
        # Normal merchant listing/delivery journals are distinct from the
        # farmer-delivery source journal.  Keep them separate: sending a
        # listing ID to delivery_operation would look in the wrong database.
        try:
            journal=self.runtime.journal
            for row in journal.pending(character):
                if row.get('kind') not in ('listing','delivery'):continue
                try:before=json.loads(row.get('before_json') or '{}')
                except (TypeError,ValueError):before={}
                incidents.append({'id':f"{row['kind']}:{row['id']}",
                    'kind':row['kind'],'request_id':row['id'],'phase':row.get('phase'),
                    'state':row,'items':before.get('items') or ([before.get('item')] if before.get('item') else []),
                    'digest':journal.original_evidence_digest(row['id']),
                    'note':'Listing confirmation needs reconciliation' if row['kind']=='listing'
                          else 'Merchant transaction needs reconciliation'})
        except (OSError,ValueError,KeyError,AttributeError,sqlite3.Error):
            pass
        try:
            from conquest.merchants.journal import Journal
            from conquest.merchants import delivery_operation
            delivery_journal=Journal(delivery_operation.JOURNAL) if Path(delivery_operation.JOURNAL).exists() else None
            for row in (delivery_journal.pending(character) if delivery_journal else []):
                if row.get('kind')!='farmer_delivery':continue
                request_id=row.get('id')
                try:receipt=delivery_operation.status(delivery_journal,request_id) or {}
                except (OSError,ValueError,KeyError):receipt={}
                if not receipt:continue
                digest=delivery_journal.original_evidence_digest(request_id)
                incidents.append({'id':f'farmer-delivery:{request_id}','kind':'farmer-delivery',
                    'request_id':request_id,'phase':receipt.get('phase'),
                    'state':receipt,'items':receipt.get('items') or [],'digest':digest,
                    'note':receipt.get('reason') or 'Merchant delivery needs reconciliation'})
        except (OSError,ValueError,KeyError,sqlite3.Error):
            # The status panel will continue to show the runtime error.  Do
            # not fabricate a clear state when the durable journal is unreadable.
            pass

        state=self.runtime.journal.get(character,'shop_return') or {}
        if state.get('phase') not in (None,'complete','operator_overridden'):
            incidents.append({'id':f'shop-return:{character}','kind':'shop-return',
                'phase':state.get('phase'),'state':state,
                'items':state.get('wanted') or state.get('before',{}).get('inventory',[]),
                'digest':self._recovery_digest(state),
                'note':state.get('note') or state.get('reason') or 'Shop return needs attention'})

        for key,kind,phases,note in (
                ('connect_market','connect-market',None,
                 'Market connection needs reconciliation'),
                ('stall_probe','stall-probe',('submitted','observed'),
                 'Stall inspection needs reconciliation')):
            state=self.runtime.journal.get(character,key) or {}
            if kind=='connect-market':
                from conquest.merchants.connect_market import TERMINAL
                if not state or state.get('phase')=='operator_overridden':continue
                if state.get('phase') in TERMINAL and not (state.get('fare_pending') or state.get('launch_unresolved')):continue
            elif state.get('phase') not in phases:continue
            incidents.append({'id':f'{kind}:{character}','kind':kind,'phase':state.get('phase'),
                'state':state,'items':state.get('inventory_before') or [],
                'digest':self._recovery_digest(state),'note':state.get('note') or note})

        safety=self.runtime.journal.get(character,'recovery_safety') or {}
        market_safety=self.runtime.journal.get(character,'market_safety') or {}
        attention=self.runtime.journal.get(character,'attention') or {}
        if (market_safety and market_safety.get('phase')!='operator_overridden') or (
                attention.get('kind')=='market_safety' and not market_safety):
            state=market_safety or attention
            incidents.append({'id':f'market-safety:{character}','kind':'market-safety',
                'phase':state.get('phase') or 'paused','state':state,'items':[],
                'digest':self._recovery_digest(state),
                'note':attention.get('note') or 'Market safety hold requires review'})
        elif safety.get('active') or (self.runtime.journal.get(character,'connect_hold',False)
                                      and safety.get('phase') not in ('operator_overridden',None)):
            state={'safety':safety,'attention':attention,
                   'connect_hold':self.runtime.journal.get(character,'connect_hold',False)}
            incidents.append({'id':f'recovery-safety:{character}','kind':'recovery-safety',
                'phase':safety.get('phase') or 'connect_hold','state':state,'items':[],
                'digest':self._recovery_digest(safety),
                'note':attention.get('note') or 'Protective recovery stop requires review'})
        elif attention:
            # Attention is a single selected incident.  It is never cleared
            # together with a delivery or shop-return hold.
            incidents.append({'id':f'attention:{character}','kind':'attention',
                'phase':attention.get('kind','needs_attention'),'state':attention,'items':[],
                'digest':self._recovery_digest(attention),
                'note':attention.get('note') or 'Merchant needs attention'})
        return incidents

    def _choose_merchant_recovery(self, character):
        incidents=self._merchant_recovery_incidents(character)
        if not incidents:return None
        if len(incidents)==1:return incidents[0]
        dialog=tk.Toplevel(self.root);dialog.title('Choose recovery incident');dialog.transient(self.root)
        ttk.Label(dialog,text='Select exactly one incident to inspect or override:',wraplength=600).pack(padx=12,pady=(12,6))
        choices=tk.Listbox(dialog,width=100,height=min(10,len(incidents)),exportselection=False)
        choices.pack(padx=12,fill='both',expand=True)
        for incident in incidents:
            choices.insert('end',f"{incident['id']} · {incident['phase']} · {incident['note']}")
        selected=[]
        def choose():
            index=choices.curselection()
            if index:selected.append(incidents[index[0]]);dialog.destroy()
        buttons=ttk.Frame(dialog);buttons.pack(fill='x',padx=12,pady=10)
        ttk.Button(buttons,text='Select',command=choose).pack(side='right')
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right',padx=(0,6))
        dialog.protocol('WM_DELETE_WINDOW',dialog.destroy)
        dialog.grab_set();self.root.wait_window(dialog)
        return selected[0] if selected else None

    def _merchant_fresh_recheck(self, character, incident):
        if incident['kind']=='farmer-delivery':
            incident=dict(incident)
            try:
                result=self.dispatch({'action':'delivery-recheck','request_id':incident['request_id']})
            except (OSError,ValueError,KeyError,TypeError) as error:
                incident['rechecked']={'recheck_unavailable':type(error).__name__,
                                       'reason':'Fresh participant memory unavailable; resume requires a fresh replan'}
                return incident
            incident['digest']=result.get('incident_digest') or incident.get('digest')
            incident['rechecked']=result.get('rechecked')
            incident['state']=result.get('receipt') or incident['state']
            return incident
        if incident['kind'] in ('listing','delivery'):
            if self.coordinator.owner:
                return dict(incident,rechecked={'recheck_unavailable':'Merchant input active'})
            snapshot={'recheck_unavailable':'merchant memory unavailable'}
            controller=self.runtime.controllers.get(character)
            observer=self.runtime.observers.get(character)
            if controller is not None and observer is not None:
                try:
                    with observer.lock:
                        snapshot=controller.driver.read()
                        # Normal reconciliation is allowed to settle an incident
                        # proved by fresh ownership memory before an override.
                        controller.reconcile(snapshot)
                except (OSError,ValueError,KeyError,TypeError):pass
            incident=dict(incident);incident['rechecked']={'snapshot':snapshot}
            return incident
        if incident['kind']=='connect-market':
            from conquest.merchants.connect_market import recheck
            incident=dict(incident)
            try:snapshot=recheck(self.runtime,character)
            except (OSError,ValueError,KeyError,TypeError):snapshot={'recheck_unavailable':'Merchant memory unavailable'}
            incident['rechecked']={'snapshot':snapshot}
            return incident
        if incident['kind']=='stall-probe':
            snapshot={'recheck_unavailable':'merchant memory unavailable'}
            controller=self.runtime.controllers.get(character)
            if controller is not None:
                try:snapshot=controller.driver.memory.read(recovery=True)
                except (OSError,ValueError,KeyError,TypeError):pass
            incident=dict(incident);incident['rechecked']={'snapshot':snapshot}
            return incident
        if incident['kind']=='shop-return':
            snapshot={'recheck_unavailable':'merchant memory unavailable'}
            observer=self.runtime.observers.get(character)
            if observer is not None:
                try:
                    snapshot=self.runtime.controllers[character].driver.memory.read(recovery=True)
                except (OSError,ValueError,KeyError,TypeError):pass
            incident=dict(incident);incident['rechecked']={'snapshot':snapshot}
            return incident
        # Safety/attention incidents are historical journal holds.  Rechecking
        # them still takes a fresh read when a merchant is attached; it does
        # not clear the protective stop.
        snapshot={'recheck_unavailable':'merchant memory unavailable'}
        observer=self.runtime.observers.get(character)
        if observer is not None:
            try:snapshot=self.runtime.controllers[character].driver.memory.read(recovery=True)
            except (OSError,ValueError,KeyError,TypeError):pass
        return dict(incident,rechecked={'snapshot':snapshot,'state':incident.get('state',{})})

    def _show_merchant_preview(self, incident, *, action):
        digest=incident.get('digest') or '(unavailable)'
        text=(f"Incident: {incident['id']}\nPhase: {incident.get('phase')}\n"
              f"{incident.get('note','')}\n{self._recovery_items_text(incident.get('items',[]))}\n"
              "")
        if action=='recheck':
            messagebox.showinfo('Recovery recheck',text+'\n\nNo game input was sent.',parent=self.root)
            return True
        if len(digest)!=64:return False
        return messagebox.askyesno('Confirm recovery override',
            text+'\n\nConfirm this exact incident to override this one hold.\n'
            'The old result will remain unverified. Continue from current stock?',
            parent=self.root)

    def recheck_merchant_recovery(self, character):
        incident=self._choose_merchant_recovery(character)
        if not incident:
            textvar=self.recovery_texts.get(character)
            if textvar is not None:textvar.set('No unresolved recovery holds')
            return None
        try:incident=self._merchant_fresh_recheck(character,incident)
        except (OSError,ValueError,KeyError,TypeError) as error:
            messagebox.showerror('Recovery recheck',str(error),parent=self.root);return None
        self._show_merchant_preview(incident,action='recheck')
        return incident

    def _apply_merchant_override(self, character, incident, digest, *, operator=None):
        if not self.coordinator.lock.acquire(blocking=False):
            raise ValueError('Merchant input is active; wait before overriding')
        try:
            if self.coordinator.owner:
                raise ValueError('Merchant input is active; wait before overriding')
            options={'operator_confirmed':True,'confirmation_reference':digest,
                     'incident_digest':digest,'operator':operator}
            kind=incident['kind']
            if kind=='farmer-delivery':
                return self.dispatch({'action':'delivery-override','request_id':incident['request_id'],**options})
            fresh=self._merchant_fresh_recheck(character,incident).get('rechecked',{})
            if kind in ('listing','delivery'):
                key=incident['request_id']
                with self.runtime.journal.db() as db:
                    row=db.execute('SELECT phase FROM transactions WHERE id=? AND character=?',(key,character)).fetchone()
                if row and row['phase'] in ('verified','aborted'):
                    return {'reconciled':True,'request_id':key}
                result=self.runtime.journal.operator_override(key,fresh_evidence=fresh,**options)
                self.runtime.journal.set(character,'new_stock',True)
                return result
            if kind=='shop-return':
                return self.runtime.returns[character].operator_override(fresh.get('snapshot',{}),**options)
            if kind=='recovery-safety':
                from conquest.merchants.recovery_safety import operator_override
                return operator_override(self.runtime,character,fresh_evidence=fresh,**options)
            if kind=='market-safety':
                from conquest.merchants.market_guard import operator_override
                return operator_override(self.runtime,character,fresh_evidence=fresh,**options)
            if kind=='connect-market':
                from conquest.merchants.connect_market import operator_override
                return operator_override(self.runtime,character,**options)
            if kind=='stall-probe':
                from conquest.merchants.stall_probe import operator_override
                controller=self.runtime.controllers.get(character)
                return operator_override(getattr(controller,'driver',None),self.runtime.journal,
                    dict(fresh.get('snapshot',{}),character=character),character=character,**options)
            if kind=='attention':
                journal=self.runtime.journal
                with journal.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row=db.execute("SELECT value FROM state WHERE character=? AND name='attention'",(character,)).fetchone()
                    original=json.loads(row[0]) if row else {}
                    if self._recovery_digest(original)!=digest:
                        raise ValueError('Incident evidence changed; recheck before overriding')
                    audit={'outcome':'operator_overridden','original_state':original,
                           'confirmation_reference':digest,'fresh_evidence':fresh}
                    db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                        (character,'attention_operator_overridden',json.dumps(audit),time.time()))
                    db.execute("UPDATE state SET value='null' WHERE character=? AND name='attention'",(character,))
                return audit
            raise ValueError('Unknown recovery incident')
        finally:self.coordinator.lock.release()

    def override_merchant_recovery(self, character):
        incident=self._choose_merchant_recovery(character)
        if not incident:return None
        # Preserve independently selected trading/refill permissions.
        permissions=(self.runtime.enabled(character),self.runtime.refill_enabled(character))
        if not self._show_merchant_preview(incident,action='override'):return None
        try:result=self._apply_merchant_override(character,incident,incident['digest'])
        except (OSError,ValueError,KeyError,TypeError) as error:
            messagebox.showerror('Recovery override',str(error),parent=self.root);return None
        self.recovery_texts[character].set('Hold cleared; queued for a fresh stock check')
        self.runtime.journal.set(character,'new_stock',True)
        # Resume only permissions already enabled. User-selected pauses and
        # Global Stop remain intact; the native scheduler owns focus and input.
        if not self.coordinator.stopped and permissions[0]:
            self.runtime.enable(character,True)
        return result

    def shop_help(self):
        messagebox.showinfo('Shop controls',
            'Pause / Resume merchant\nPauses both trading and refill without closing the game. Resume restores your previous permissions. Change individual permissions under Settings & details.\n\n'
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

    def show_recovery_status(self, character):
        text=self.recovery_texts.get(character)
        messagebox.showinfo(character+' recovery',text.get() if text else 'Recovery status is unavailable.',parent=self.root)

    def toggle_merchant(self, character):
        from conquest.merchants.simple_controls import toggle
        toggle(self,character)

    def show_merchant_details(self, character):
        from conquest.merchants.dashboard import merchant_text
        data=self.presentation.latest
        if not data:return
        messagebox.showinfo(character+' status',merchant_text(data['characters'][character],now=time.time(),
            waiting_items=data['tables'][character]['deferred'],global_stopped=self.coordinator.stopped),parent=self.root)

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

    def on_tab_changed(self):
        # Apply the compact geometry synchronously: an embed can validate its
        # pane before Tk gets a later idle visibility refresh.
        if len(self.client_tabs)==len(CHARACTERS):self.apply_client_compact_layout()
        self.schedule_visibility()

    def apply_client_compact_layout(self):
        """Give the selected native Client pane the vertical space it needs.

        The Client tab retains its pause/stop/settings controls and all detail
        tabs.  Status and recovery text remain reachable from Settings &
        details, and reappear immediately outside Client.
        """
        # Tab events may be delivered while the notebook is still being built.
        if len(self.client_tabs)!=len(CHARACTERS):return None
        character=client_tab_character(self.notebook,self.frames,self.detail_tabs,self.client_tabs)
        set_packed(self.header,character is None,fill='x',before=self.notebook)
        for name,widgets in self.merchant_chrome.items():
            for widget,options in widgets:
                set_packed(widget,name!=character,**options)
        return character

    def schedule_visibility(self):
        if not self.closed and self.visibility_job is None:
            self.visibility_job = self.root.after_idle(self.refresh_visibility)

    def refresh_visibility(self):
        self.visibility_job = None
        if not self.closed:
            layout=getattr(self,'apply_client_compact_layout',None)
            if layout:layout()
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
        if self.runtime.manual_handoff_status() is not None:
            try:self.show_manual_handoff_surface(character,selected=True)
            except (OSError,ValueError):
                self.calibration_results[character]={'verified':False,
                    'note':'Manual view unavailable; keep this Client tab selected and do not use automation controls'}
            return
        if host and host.saved:return
        # Attachment is independent of booth/trade read availability. A foreign
        # shop panel must not make the actual game window inaccessible.
        observer=self.runtime.observers.get(character)
        if observer is None or getattr(observer,'merchant_observation_only',False):return
        self.auto_embedding=True
        try:
            self.embed_client(character,automatic=True)
            self.auto_embed_retry[character]=time.monotonic()+2
        finally:
            self.auto_embedding=False

    def show_manual_handoff_surface(self, character, *, selected=False):
        """User-requested hosted view during a global manual handoff.

        This is deliberately limited to verified window ownership, notebook
        selection and asynchronous show/hide/resize. It neither acquires an
        input lease nor focuses/clicks/types into Conquer, changes a saved
        control, reconciles a trade, or attaches a replacement process.
        """
        handoff=self.runtime.manual_handoff_status()
        if handoff is None:raise ValueError('Start Manual handoff before using manual game view')
        if handoff.get('phase') not in ('ready','ending'):
            raise ValueError('Wait for Manual handoff Ready before showing a game view')
        if self.closed or self.app.closing or probe_busy(self):raise ValueError('Manual game view is unavailable now')
        observer=self.runtime.observers.get(character)
        if observer is None:raise ValueError('Selected merchant has no verified attached process')
        observer.adapter.assert_identity()
        if not selected:
            self.notebook.select(self.frames[character]);self.detail_tabs[character].select(0)
            self.root.update_idletasks()
        pane=self.client_panes[character]
        if not pane.winfo_ismapped() or min(pane.winfo_width(),pane.winfo_height())<=1:
            raise ValueError('Select this merchant Client tab to show its manual view')
        from conquest.window_host import EmbeddedWindow
        host=self.hosts.setdefault(character,EmbeddedWindow(mode='owned'))
        if host.saved and host.saved.identity!=observer.adapter.identity:
            raise ValueError('Merchant host identity changed; manual view is withheld')
        hwnd=observer.hwnd if getattr(observer,'merchant_observation_only',False) else observer.operations.target.hwnd
        if not host.saved:
            if host.api.gui.IsIconic(hwnd):
                # EmbeddedWindow.attach restores an iconic top-level window.
                # Manual view is intentionally no-activate, so leave that
                # explicit user action outside this path.
                raise ValueError('Merchant is minimized; restore it yourself before manual view')
            # This is an explicit user surface operation on the already
            # verified attached HWND, never discovery or input preparation.
            host.attach(hwnd,observer.adapter.identity,pane.winfo_id(),
                        pane.winfo_width(),pane.winfo_height())
        for other,other_host in self.hosts.items():
            if other!=character and other_host.saved:
                other_host.api.assert_owner(other_host.saved.hwnd,other_host.saved.identity)
                other_host.api.show_async(other_host.saved.hwnd,0)
        host.resize(pane.winfo_width(),pane.winfo_height())
        self.layout_status.setdefault(character,{}).update(manual_handoff_view=True,
            native_visible=True,selected=True)

    def request_manual_handoff_surface(self, character):
        try:self.show_manual_handoff_surface(character)
        except (OSError,ValueError) as error:
            messagebox.showinfo('Manual handoff view',str(error),parent=self.root)

    def finish_resize(self, character):
        self.resize_jobs.pop(character,None)
        if self.closed or probe_busy(self):
            return
        if (getattr(self.runtime.observers.get(character),'merchant_observation_only',False)
                and self.runtime.manual_handoff_status() is None):
            # Safe automatic 1078 hosting may follow the pane's geometry, but
            # it never activates the client or changes the selected notebook tab.
            status=self.layout_status.get(character,{})
            host=self.hosts.get(character)
            observer=self.runtime.observers.get(character)
            pane=self.client_panes[character]
            if (status.get('auto_read_only_host') and host and host.saved
                    and observer and host.saved.identity==observer.adapter.identity
                    and pane.winfo_ismapped()):
                try:
                    observer.adapter.assert_identity()
                    size=(pane.winfo_width(),pane.winfo_height())
                    if min(size)>1:
                        host.resize(*size)
                        status.update(native_visible=bool(host.api.gui.IsWindowVisible(host.saved.hwnd)),
                                      selected=True)
                except (OSError,ValueError):
                    self.calibration_results[character]={'verified':False,
                        'note':'Read-only client layout is unavailable; input remains fenced'}
            return
        # A delayed Tk layout event is not allowed to alter merchant permission
        # while a delivery or another native input handoff owns the surface.
        # Explicit input preparation still validates geometry with automatic=True;
        # this merely defers the stale resize callback until that handoff ends.
        if (getattr(getattr(self,'coordinator',None),'owner',None)
                or getattr(self.runtime,'delivery_window',None)):
            return
        try:
            host=self.hosts.get(character)
            if host and host.saved and not host.is_alive():
                # A lost process is recovery work, not a user Pause command.
                # Forget only the verified stale HWND; never touch its replacement.
                host.detach()
                self.render_sizes.pop(character,None)
                self.layout_status[character]={'native_visible':False,'selected':False}
                self.calibration_results[character]={'verified':False,'note':'Client closed; waiting for reconnect'}
                return
            self.resize_merchant(character)
            host=self.hosts.get(character)
            if host and host.saved:
                self.layout_status.setdefault(character,{}).update(
                    native_visible=bool(host.api.gui.IsWindowVisible(host.saved.hwnd)),
                    selected=bool(self.client_panes[character].winfo_ismapped()))
        except (OSError,ValueError):
            if (self.runtime.manual_handoff_status() is not None
                    or getattr(self.runtime.observers.get(character),'merchant_observation_only',False)):
                self.calibration_results[character] = {'verified':False,
                    'note':'Read-only client layout is unavailable; input remains fenced and saved permissions are unchanged'}
                return
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
        from conquest.character_context import registry
        if registry() and pane.winfo_ismapped() and min(size)>1:
            from conquest.client_attachment import require_viewport, ViewportTooSmall
            manual_blocks=getattr(self,'_manual_viewport_blocks',{})
            try:
                require_viewport(*size)
                if character in manual_blocks:
                    self.coordinator.surface_blocks[character]=manual_blocks.pop(character)
            except ViewportTooSmall:
                if self.runtime.manual_handoff_status() is not None:
                    if character not in manual_blocks:
                        manual_blocks[character]=bool(self.coordinator.surface_blocks.get(character))
                        self._manual_viewport_blocks=manual_blocks
                    self.coordinator.surface_blocks[character]=True
                    self.calibration_results[character]={'verified':False,
                        'note':'Manual handoff view is too small; saved permissions are unchanged'}
                    host.api.assert_owner(host.saved.hwnd,host.saved.identity)
                    host.api.show_async(host.saved.hwnd,0)
                    return
                self.coordinator.surface_blocks[character]=True
                self.runtime.invalidate_refill(character)
                host.detach();self.released_clients.add(character)
                self.calibration_results[character]={'verified':False,'note':'Full viewport does not fit. Use More / help → Open game in separate window.'}
                raise
        previous = self.render_sizes.get(character)
        if pane.winfo_ismapped() and min(size)>1 and previous!=size:
            # A layout event is not a user Pause. Defer it while input or a
            # reserved delivery owns the surface; explicit input preparation
            # may resize before the driver validates its current geometry.
            if not automatic and (getattr(getattr(self,'coordinator',None),'owner',None)
                    or getattr(self.runtime,'delivery_window',None)):
                return
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

            status=getattr(self.runtime,'attachments',{}).get(character)
            if status:
                status.fail(error)
                self.runtime.journal.set(character,'attachment',status.snapshot())

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
        layout=getattr(self,'apply_client_compact_layout',None)
        if layout:layout()
        self.root.update_idletasks()
        pane = self.client_panes[character]
        from conquest.character_context import registry
        status=getattr(self.runtime,'attachments',{}).get(character)
        if status:status.enter('attachment',pane_size=[pane.winfo_width(),pane.winfo_height()])
        if registry():
            from conquest.client_attachment import require_viewport
            require_viewport(pane.winfo_width(),pane.winfo_height())
        if not host.saved:
            hwnd=observer.hwnd if getattr(observer,'merchant_observation_only',False) else observer.operations.target.hwnd
            host.attach(hwnd,observer.adapter.identity,pane.winfo_id(),
                        pane.winfo_width(),pane.winfo_height())
        self.resize_merchant(character,automatic=automatic)
        read_only=getattr(observer,'merchant_observation_only',False)
        self.coordinator.surface_blocks[character]=read_only
        if status:
            status.attached=True;status.enter('memory' if read_only else 'behavior')
            if read_only:status.observation_ready=True
            self.runtime.journal.set(character,'attachment',status.snapshot())
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
        layout=getattr(self,'apply_client_compact_layout',None)
        if layout:layout()
        self.root.update_idletasks()
        # Switching Tk tabs does not synchronously hide the owned top-level
        # game. Explicitly hide siblings before showing the next input owner.
        for other,other_host in self.hosts.items():
            if other!=character and other_host.saved:
                other_host.api.assert_owner(other_host.saved.hwnd,other_host.saved.identity)
                other_host.api.show_async(other_host.saved.hwnd,0)
        self.resize_merchant(character,automatic=True)

    def prepare_input(self, character):
        if is_farmer_owner(character) or self.coordinator.purpose=='connect_launch':
            return
        if self.coordinator.purpose=='connect':
            observer=self.runtime.observers.get(character)
            from conquest.reconnect import login_screen
            if observer and login_screen(observer.operations.target.hwnd):
                # Keep the native login shell until authentication completes;
                # its activation handler must not compete with an owned host.
                from conquest.focus_recovery import activate_client
                if not activate_client(observer.operations.target.hwnd,observer.adapter.identity):
                    # The standalone login has no wrapper owner yet. Activate
                    # our already-owned safe farmer via the verified Conquest
                    # caption, then request the login window from that input queue.
                    farmer=getattr(self.app,'observer',None)
                    self.coordinator.check()
                    if farmer and self.safe_to_yield():
                        from conquest.window_host import HostApi
                        HostApi().activate_owned_caption(farmer.operations.target.hwnd,farmer.adapter.identity)
                    if not activate_client(observer.operations.target.hwnd,observer.adapter.identity):
                        raise ValueError('Activate the selected login client before continuing')
                return
        done,result = threading.Event(),{}
        # A staged acceptance has no safe fallback to the previously selected
        # pane.  Select the named observer's owned host on the Tk thread,
        # creating it only when none is attached, then wait for its native
        # surface below.  This does not inspect a screen or send game input.
        if self.coordinator.purpose=='delivery_probe_abort':
            capability=self.coordinator._probe_abort_capability
            def callback():
                from conquest.merchants.delivery_abort_surface import prepare
                result['profile']=prepare(self,character,capability)
        elif self.coordinator.purpose=='delivery_accept_probe':
            def callback():
                result['profile']=self.prepare_delivery_accept_surface(character)
        else:
            callback=lambda:self.show_merchant(character)
        fence=getattr(self,'grant_fence',None)
        if fence:callback=fence.guard_callback(fence.capture(),callback)
        self.ui_requests.put((callback,done,result))
        if not done.wait(5):
            # The UI queue checks this before running a timed-out request.
            result['expired'] = True
            raise ValueError('Embedded merchant pane did not become available')
        if result.get('error'):
            raise ValueError(result['error'])
        profile=result.get('profile',character)
        host=self.hosts[profile]
        others=[other for _key,other in self.hosts.items() if other is not host]
        wait_for_merchant_surface(host,others,self.coordinator.check)

    def prepare_delivery_accept_surface(self, character):
        """Show the exact observer's owned host for one acceptance probe."""
        observer,target,host,identity,profile,profile_id=self.delivery_accept_binding(character)
        expected=(target.hwnd,identity,profile_id)
        if not host or not host.saved:
            self.embed_delivery_accept_merchant(character,expected)
            # The attach path may not authorize presentation after a journal,
            # observer, or HWND replacement.
            observer,target,host,identity,profile,profile_id=self.delivery_accept_binding(character,expected)
        self.show_delivery_accept_merchant(character,expected)
        # Do not return control to the worker after a same-named replacement.
        return self.delivery_accept_binding(character,expected)[4]

    def delivery_accept_binding(self, character, expected=None):
        """Read-only journal/observer/HWND binding for a staged accept."""
        # Read the durable receipt on the UI thread before touching an owned
        # window.  A stale/partial/replaced receipt cannot authorize focus,
        # embedding, or any eventual click.
        from conquest.merchants.delivery_probe import read_probe
        state=read_probe()
        if (not isinstance(state,dict) or state.get('phase')!='request_verified'
                or state.get('character')!=character):
            raise ValueError('Trade acceptance probe is no longer request-verified; no client action sent')
        intent=state.get('intent')
        merchant=intent.get('merchant') if isinstance(intent,dict) else None
        if (not isinstance(merchant,dict) or merchant.get('character')!=character
                or merchant.get('server')!='America' or not isinstance(merchant.get('identity'),dict)):
            raise ValueError('Trade acceptance merchant binding is unreadable; no client action sent')
        from conquest.character_context import registry,ProfileName
        profiles=registry();profile_id=state.get('target_profile_id')
        if not isinstance(profile_id,str) or not profile_id:
            raise ValueError('Trade acceptance merchant profile binding is unreadable; no client action sent')
        if profiles:
            try:
                resolved=profiles.resolve(profile_id,role='Merchant',server=merchant['server'])
            except ValueError as error:
                raise ValueError('Trade acceptance merchant profile is unavailable; no client action sent') from error
            # The serialized ID is the authority.  ProfileRegistry.resolve also
            # accepts names for normal UI entry, so reject that fallback here.
            if (resolved.id!=profile_id or not resolved.local_enabled
                    or resolved.name!=character or resolved.name!=merchant['character']
                    or resolved.server!=merchant['server']):
                raise ValueError('Trade acceptance merchant profile changed; no client action sent')
            profile=ProfileName(resolved.name,resolved.id)
        else:
            if profile_id!=character:
                raise ValueError('Trade acceptance merchant profile changed; no client action sent')
            profile=character
        receipt_identity=merchant['identity']
        if (type(receipt_identity.get('pid')) is not int or receipt_identity['pid']<=0
                or type(receipt_identity.get('creation_time_100ns')) is not int
                or receipt_identity['creation_time_100ns']<=0
                or not isinstance(receipt_identity.get('path'),str) or not receipt_identity['path']):
            raise ValueError('Trade acceptance merchant identity is incomplete; no client action sent')
        observer=self.runtime.observers.get(profile)
        if (not observer or observer.adapter.identity!=receipt_identity):
            raise ValueError('Waiting for the exact merchant process before trade acceptance')
        observer.adapter.assert_identity()
        target=observer.operations.target
        if type(getattr(target,'hwnd',None)) is not int or target.hwnd<=0:
            raise ValueError('Trade acceptance merchant window is unavailable; no client action sent')
        if expected is not None and (target.hwnd,receipt_identity,profile_id)!=expected:
            raise ValueError('Trade acceptance merchant mapping changed; no client action sent')
        host=self.hosts.get(profile)
        # HostApi.assert_owner proves that the current observer's target HWND
        # still belongs to the full journaled process identity.  Use the
        # existing host API when possible; it avoids creating any window or
        # changing presentation state during this read-only binding check.
        if host:
            host.api.assert_owner(target.hwnd,receipt_identity)
        else:
            from conquest.window_host import HostApi
            HostApi().assert_owner(target.hwnd,receipt_identity)
        if host and host.saved:
            # Never detach, reconnect, or guess at a replacement during a
            # delivery.  A saved host must still be the observer's exact HWND
            # and complete process identity before it may be shown.
            host.api.assert_owner(host.saved.hwnd,host.saved.identity)
            if (host.saved.hwnd!=target.hwnd or host.saved.identity!=receipt_identity):
                raise ValueError('Selected merchant host changed; re-embed was not attempted')
            if host.mode!='owned':
                raise ValueError('Selected merchant is not an owned host; no client action sent')
        return observer,target,host,receipt_identity,profile,profile_id

    def embed_delivery_accept_merchant(self, character, expected):
        """Attach only ``expected``; generic merchant fallback is forbidden."""
        if probe_busy(self):
            raise ValueError('Background diagnostic owns the client; no client action sent')
        observer,target,host,identity,profile,profile_id=self.delivery_accept_binding(character,expected)
        if host and host.saved:
            return
        if not self.safe_to_yield():
            raise ValueError('Farmer has not granted a safe handoff')
        from conquest.window_host import EmbeddedWindow
        host=host or EmbeddedWindow(mode='owned')
        if not host.mode=='owned':
            raise ValueError('Selected merchant is not an owned host; no client action sent')
        self.notebook.select(self.frames[profile])
        self.detail_tabs[profile].select(0)
        layout=getattr(self,'apply_client_compact_layout',None)
        if layout:layout()
        self.root.update_idletasks()
        pane=self.client_panes[profile]
        from conquest.character_context import registry
        if registry():
            from conquest.client_attachment import require_viewport
            require_viewport(pane.winfo_width(),pane.winfo_height())
        # Re-read just before the native operation; never replace a stale host
        # with a newly observed same-named process.
        observer,target,current,identity,profile,profile_id=self.delivery_accept_binding(character,expected)
        if current is not None and current is not host:
            raise ValueError('Selected merchant host changed; no client action sent')
        self.hosts[profile]=host
        host.attach(expected[0],expected[1],pane.winfo_id(),pane.winfo_width(),pane.winfo_height())
        self.delivery_accept_binding(character,expected)

    def show_delivery_accept_merchant(self, character, expected):
        """Present only a receipt-bound owned host, without generic lookup."""
        if self.closed or self.app.closing:
            raise ValueError('App is closing')
        observer,target,host,identity,profile,profile_id=self.delivery_accept_binding(character,expected)
        if not host or not host.saved:
            raise ValueError('Selected merchant host is unavailable; no client action sent')
        foreground=host.api.gui.GetForegroundWindow()
        bookmark={'tab':self.notebook.select(),'hwnd':foreground,'identity':None}
        if foreground:
            try:
                import ctypes
                from ctypes import wintypes
                pid=wintypes.DWORD()
                host.api.backend.window_pid(foreground,ctypes.byref(pid))
                bookmark['identity']=host.api.backend.identity(pid.value)
            except (OSError,ValueError):
                pass
        self.input_bookmarks[profile]=bookmark
        self.notebook.select(self.frames[profile])
        self.detail_tabs[profile].select(0)
        layout=getattr(self,'apply_client_compact_layout',None)
        if layout:layout()
        self.root.update_idletasks()
        # The layout callback can reconnect a merchant.  Rebind before hiding
        # siblings or making the receipt-bound host visible.  Viewport
        # rejection is also pre-presentation: it must not hide another client.
        observer,target,host,identity,profile,profile_id=self.delivery_accept_binding(character,expected)
        pane=self.client_panes[profile]
        from conquest.character_context import registry
        if registry():
            from conquest.client_attachment import require_viewport
            require_viewport(pane.winfo_width(),pane.winfo_height())
        self.delivery_accept_binding(character,expected)
        for _key,other_host in self.hosts.items():
            if other_host is not host and other_host.saved:
                other_host.api.assert_owner(other_host.saved.hwnd,other_host.saved.identity)
                other_host.api.show_async(other_host.saved.hwnd,0)
        self.delivery_accept_binding(character,expected)
        host.resize(pane.winfo_width(),pane.winfo_height())
        self.delivery_accept_binding(character,expected)

    def release_input(self, character):
        if not is_farmer_owner(character):
            callback=lambda:self.restore_input(character)
            fence=getattr(self,'grant_fence',None)
            if fence:callback=fence.guard_callback(fence.capture(),callback)
            self.ui_requests.put((callback,None,{}))

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
        fence=getattr(self,'grant_fence',None)
        if fence:
            control=self.app.control.snapshot()
            marker=(control['revision'],bool(self.app.mouse_priority.active()))
            if marker!=getattr(self,'input_revision_marker',marker):
                fence.invalidate()
            self.input_revision_marker=marker
        while True:
            try:
                callback,done,result = self.ui_requests.get_nowait()
            except queue.Empty:
                break
            try:
                if not result.get('expired'):
                    callback()
            except Exception as error:
                # The worker journal needs the actual callback failure to
                # distinguish an unavailable layout from a failed UI action.
                # Keep it concise and never expose traceback locals.
                result['error'] = callback_failure(error)
            finally:
                if done:
                    done.set()
        self.root.after(50,self.poll_ui_requests)

    def focus_clicked_merchant(self):
        # A pointer click must not make a hosted merchant the app's input
        # target while the farmer still owns an active route or combat input.
        if self.closed or self.coordinator.owner or not self.safe_to_yield():
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
        observer=self.runtime.observers.get(character)
        try:read_only=(getattr(observer,'merchant_observation_only',False)
                       or self.runtime.read_only_1078(character,force=True))
        except (ValueError,OSError):
            self.calibration_results[character] = {'verified':False,
                'note':'Merchant build could not be verified; booth input is unavailable'}
            return
        if read_only:
            self.calibration_results[character] = {'verified':False,
                'note':'1078 merchant input is unavailable; read-only observation does not qualify booth controls'}
            return
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
        if hasattr(self,'discord_note'):
            self.discord_note.set(data.get('notification_health','Checking Discord notification services...'))

    def poll(self):
        if self.app.closing:
            self.close();return
        try:
            if not probe_busy(self):
                from conquest.merchants.restore_hosts import restore
                restore(self)
            self.auto_show_selected()
            data=self.presentation.latest
            if data is None:return
            statuses = data['characters']
            self.update_header(statuses)
            control = self.app.control.snapshot()
            if hasattr(self,'manual_texts'):
                self.refresh_manual_operator(statuses,control)
            self.rows['Farmer'].set(f'{self.app.state_text.get()} · {self.app.activity_text.get()}\n{self.app.stats_text.get()}')
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
                self.merchant_buttons[character].configure(text='Pause merchant' if
                    state['enabled'] or state['refill']['enabled'] else 'Resume merchant')
                entries=getattr(self,'permission_menu_entries',{}).get(character)
                if entries and character in getattr(self,'permission_menus',{}):
                    refresh_permission_menu(self.permission_menus[character],entries,state)
                from conquest.merchants.simple_controls import summary
                note=summary(state,now=time.time(),global_stopped=self.coordinator.stopped)
                recovery_reader=getattr(self,'_merchant_recovery_incidents',None)
                holds=recovery_reader(character) if callable(recovery_reader) and getattr(self,'runtime',None) is not None else []
                if character in getattr(self,'recovery_texts',{}):
                    self.recovery_texts[character].set(
                        f'{len(holds)} recovery hold(s) need attention · select one with Recheck'
                        if holds else 'No unresolved recovery holds')
                host = self.hosts.get(character)
                if host and host.saved:
                    # Share the verified dead-client handling with resize events.
                    # A disconnected HWND must not overwrite trading/recovery intent.
                    self.finish_resize(character)
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
            write_json(state_path('.runtime/merchants/app-lifecycle.json'),{'state':reason,'at':time.time()})
            write_json(state_path('.runtime/merchants/window.json'),{'geometry':self.root.geometry(),'state':self.root.state()})
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
