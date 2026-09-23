"""Native launcher and supervised foreground farmer."""
from conquest.character_context import farmer_name
from conquest.character_context import installation_path, state_path
import argparse
import ctypes
import json
import logging
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

import yaml

from conquest.addressing import PlayerLayout
from conquest.desktop_runtime import LocalSession, NormalizedFrames, WindowGeometry, physical_coordinates
from conquest.trial import TrialConfig, run_trial
from conquest.win32 import WindowsBackend
from conquest.window_host import EmbeddedWindow, use_unaware_dpi
from conquest.control import FarmingControl
from conquest.control_runtime import ControlRuntime
from conquest.embedded_observer import EmbeddedObserver
from conquest.memory_health import HealthLayout
from conquest.memory_entities import EntityLayout
from conquest.desktop_launch import elevated_start
from conquest.client_wrapper import ClientCatalog, LaunchWatch, pinned_client
from conquest.nearby_monsters import NearbyMonsters
from conquest.farm_telemetry import PickupHistory,pickup_values,activity_text,automation_status,item_label,pause_message,farm_stats
from conquest.routes import RouteLibrary
from conquest.route_choices import saved_route_choices, route_label
from conquest.reconnect import Reconnector,login_screen,submit_login
from conquest.focus_recovery import AutoRefocuser,activate_client,activate_focused_client


class EventQueue(logging.Handler):
    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def emit(self, record):
        self.messages.put((record.getMessage(), getattr(record, 'fields', {})))


class DesktopApp:
    def __init__(self, root, profile, *, catalog=None, launch_watch=None, observer_factory=None, output=None, requires_elevation=True):
        self.root, self.profile = root, Path(profile)
        self.backend, self.host = WindowsBackend(), EmbeddedWindow(mode='owned')
        from conquest.discord_notify import read_json
        self.host.api.height_scale = float(read_json(state_path('.runtime/farmer-view.json')).get('height_scale',1.0))
        self.messages, self.thread = queue.Queue(), None
        self.requires_elevation = requires_elevation
        from conquest.character_context import current
        from conquest.client_attachment import AttachmentStatus
        self.character_context=current()
        self.attachment=AttachmentStatus()
        self.output = Path(output or state_path('reports/desktop-farming'))
        self.output.mkdir(parents=True, exist_ok=True)
        self.status_path = self.output / 'app-state.json'
        self.client = None
        self.control = FarmingControl(self.output/'controls.json' if output else state_path('.runtime/native-controls.json'))
        self.catalog = catalog or ClientCatalog(self.backend)
        launcher = Path(installation_path(r'C:\Program Files\Classic Conquer 2.0\ImBootstrapper.exe'))
        self.launch_watch = launch_watch or LaunchWatch(self.catalog,[str(launcher)],cwd=launcher.parent)
        self.observer_factory = observer_factory or self.make_observer
        self.clients = []
        self.observer = self.runtime = None
        self.reconnector=Reconnector(self.reconnect_client,
            lambda fields:self.messages.put(('reconnect_state',fields)))
        self.reconnect_pending=False
        self.refocuser=AutoRefocuser()
        self.closing = False
        self.pointer_down = False
        self.farming_on_key_down=False
        from conquest.mouse_priority import install
        self.mouse_priority=install()
        self.last = {'state':'Off', 'kills':0, 'attempts':0, 'app_started_at':time.time()}
        from conquest.session_kills import SessionKills
        self.kill_session=SessionKills(self.output)
        self.last.update(self.kill_session.snapshot())
        root.title('Conquest Farmer')
        root.geometry('540x850')
        root.minsize(500, 700)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TButton', padding=5)
        style.configure('Treeview', rowheight=24)
        from conquest.sidebar import ScrollableSidebar
        self.sidebar_host=ScrollableSidebar(root)
        self.sidebar=self.sidebar_host.content
        self.sidebar_host.pack(side='left', fill='both', expand=True)
        heading=(self.character_context.profile.label or self.character_context.profile.name) if self.character_context else 'Conquest Farmer'
        ttk.Label(self.sidebar, text=heading, font=('Segoe UI',20,'bold')).pack(anchor='w')
        self.client_text = tk.StringVar(value='Finding Conquer…')
        self.client_footer = ttk.Frame(self.sidebar)
        self.route_details_frame = ttk.Frame(self.client_footer, padding=(0,6))
        self.setup_frame = ttk.Frame(self.client_footer)
        self.client_frame = ttk.Frame(self.sidebar)
        client_row = ttk.Frame(self.client_frame)
        client_row.pack(fill='x',pady=(4,6))
        self.client_picker = ttk.Combobox(client_row,textvariable=self.client_text,state='readonly')
        self.client_picker.pack(side='left',fill='x',expand=True)
        self.client_picker.bind('<<ComboboxSelected>>',self.select_client)
        ttk.Button(client_row,text='Refresh',command=self.refresh_client).pack(side='left',padx=(6,0))
        self.state_text = tk.StringVar(value='Off')
        ttk.Label(self.sidebar, textvariable=self.state_text, font=('Segoe UI',15,'bold'), wraplength=460).pack(anchor='w')
        self.activity_text = tk.StringVar(value='Farming is off')
        activity_frame=ttk.Frame(self.sidebar,height=40)
        activity_frame.pack(fill='x',pady=(3,0))
        activity_frame.pack_propagate(False)
        ttk.Label(activity_frame,textvariable=self.activity_text,wraplength=455).pack(anchor='w')
        self.route_status={}
        self.telemetry_poll_at=0
        self.pickup_history=PickupHistory(self.output/'pickups.jsonl')
        self.stats_text = tk.StringVar(value='Kills 0\nLevel —  ·  Next level Estimating…')
        ttk.Label(self.sidebar, textvariable=self.stats_text,justify='left').pack(anchor='w', pady=4)
        row = ttk.Frame(self.sidebar)
        self.foreground_row = row
        row.pack(fill='x', pady=6)
        self.start_button = ttk.Button(row, text='Start farming → Conquer', command=self.start)
        self.start_button.pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Stop', command=self.stop).pack(side='left', padx=(8,0))
        self.foreground_note = ttk.Label(self.sidebar, text='Temporary foreground mode · Pheasants\nF11 pauses / resumes · F12 stops\nReturns to farming when Conquer regains focus.', wraplength=450)
        self.foreground_note.pack(anchor='w',pady=8)
        row = ttk.Frame(self.sidebar)
        self.tools_row = row
        row.pack(fill='x', pady=5)
        ttk.Button(row, text='Show / focus Conquer', command=self.show_game).pack(side='left', padx=8)
        ttk.Button(row, text='Reload app', command=self.restart).pack(side='left')
        ttk.Button(row, text='Retry reconnect', command=self.retry_reconnect).pack(side='left')
        self.client_frame.pack(fill='x',pady=(0,4))
        self.attachment_text=tk.StringVar(value='Client not attached')
        ttk.Label(self.client_frame,textvariable=self.attachment_text,wraplength=440).pack(fill='x')
        ttk.Button(self.client_frame,text='Copy attachment diagnostics',command=self.copy_attachment_diagnostics).pack(anchor='w')
        ttk.Button(self.client_frame,text='Use separate game window',command=self.show_game).pack(anchor='w')
        ttk.Button(self.client_frame,text='Retry automation setup',command=self.retry_behavior_setup).pack(anchor='w')
        row = ttk.Frame(self.client_frame)
        row.pack(fill='x', pady=4)
        self.launch_button = ttk.Button(row,text='Launch client',command=self.launch)
        self.launch_button.pack(side='left')
        ttk.Button(row, text='Embed client', command=self.embed).pack(side='left', padx=7)
        ttk.Button(row, text='Release client', command=self.release).pack(side='left')
        ttk.Button(self.setup_frame, text='Check calibration', command=lambda:self.start(True)).pack(anchor='w')
        route_frame = ttk.LabelFrame(self.sidebar,text='Saved routes',padding=5)
        route_frame.pack(fill='x',pady=(6,0))
        self.route_library = RouteLibrary()
        self.saved_routes = saved_route_choices(self.route_library.all())
        self.route_selection_path = self.output/'selected-route.json'
        self.selected_route = None
        self.route_text = tk.StringVar(value='Choose a saved route')
        route_row = ttk.Frame(route_frame)
        route_row.pack(fill='x')
        self.route_picker = ttk.Combobox(route_row,textvariable=self.route_text,state='readonly',
            values=[route_label(r) for r in self.saved_routes])
        self.route_picker.pack(side='left',fill='x',expand=True)
        self.route_picker.bind('<<ComboboxSelected>>',self.select_route)
        ttk.Button(route_row,text='Save copy',command=self.save_route_copy,padding=3).pack(side='left',padx=(5,0))
        self.route_note = tk.StringVar(value='Save once, reuse on future runs.\nChoose a route to load its monster group.\nFull travel / hunt / restock cycle is being validated.')
        ttk.Label(self.route_details_frame,textvariable=self.route_note,wraplength=450).pack(anchor='w')
        from conquest.session_plan import plan_note
        self.session_note=tk.StringVar(value=plan_note())
        session_row=ttk.Frame(self.route_details_frame);session_row.pack(fill='x',pady=(4,0))
        ttk.Label(session_row,textvariable=self.session_note).pack(side='left')
        ttk.Button(session_row,text='Resume leveling',command=self.resume_leveling,padding=2).pack(side='right')
        self.restore_route()
        self.level_presets=json.loads(Path('profiles/leveling-presets.json').read_text(encoding='utf-8'))
        self.level_preset_text=tk.StringVar(value='Browse routes by level')
        self.level_preset_picker=ttk.Combobox(self.route_details_frame,textvariable=self.level_preset_text,state='readonly',
            values=[p['name']+(' · needs survey' if p['status']=='needs_survey' else '') for p in self.level_presets])
        self.level_preset_picker.pack(fill='x',pady=(4,0))
        self.level_preset_picker.bind('<<ComboboxSelected>>',self.select_level_preset)
        farm_row = ttk.Frame(self.sidebar)
        farm_row.pack(fill='x',pady=(8,4))
        ttk.Button(farm_row, text='Farming On · F10', command=lambda:self.update_ids(True)).pack(side='left',fill='x',expand=True)
        ttk.Button(farm_row, text='Off', command=lambda:self.update_ids(False)).pack(side='left',padx=6)
        ttk.Label(farm_row,text='F11 pause · F12 stop').pack(side='right')
        from conquest.merchants.farmer_preferences import rollout_enabled as transfers_enabled
        try:
            self.transfer_character=TrialConfig.model_validate(yaml.safe_load(self.profile.read_text())).character
        except (OSError,ValueError,yaml.YAMLError):
            self.transfer_character=None
        self.merchant_transfers=tk.BooleanVar(value=bool(self.transfer_character and transfers_enabled(self.transfer_character)))
        ttk.Checkbutton(self.sidebar,text='Automatic merchant delivery',variable=self.merchant_transfers,
                        state='normal' if self.transfer_character else 'disabled',
                        command=self.save_merchant_transfers).pack(anchor='w',pady=(0,4))

        # Recovery controls deliberately live beside the farmer controls as
        # well as in the merchant tabs.  They only close one selected durable
        # hold; they never turn off the manual-stop or fresh-observation
        # guards used by update_ids/start_embedded_farm.
        self.recovery_frame = ttk.LabelFrame(self.sidebar, text='Manual handoff & recovery', padding=6)
        self.recovery_frame.pack(fill='x', pady=(0,4))
        self.recovery_text = tk.StringVar(value='Checking for unresolved farmer holds…')
        ttk.Label(self.recovery_frame, textvariable=self.recovery_text,
                  wraplength=420).pack(anchor='w')
        recovery_row = ttk.Frame(self.recovery_frame)
        recovery_row.pack(fill='x', pady=(4,0))
        ttk.Button(recovery_row, text='Recheck', command=self.recheck_farmer_recovery).pack(side='left')
        ttk.Button(recovery_row, text='Override & resume',
                   command=self.override_farmer_recovery).pack(side='left', padx=(6,0))
        ttk.Button(self.recovery_frame,text='Clear stale handoff…',
                   command=self.clear_stale_handoff).pack(anchor='w',pady=(4,0))
        self.manual_handoff_note=tk.StringVar(value='Manual handoff: Start, then wait for Ready before touching any client.')
        ttk.Label(self.recovery_frame,textvariable=self.manual_handoff_note,wraplength=420).pack(anchor='w',pady=(5,0))
        ttk.Button(self.recovery_frame,text='Manual handoff…',
                   command=self.manual_handoff).pack(anchor='w',pady=(4,0))

        self.mouse_note=tk.StringVar(value='Mouse control: automatic · move mouse to take over')
        ttk.Label(self.sidebar,textvariable=self.mouse_note).pack(anchor='w',pady=(0,4))

        # Reserve the footer first; both live lists share the remaining space.
        footer = self.client_footer
        footer.pack(side='bottom',fill='x')
        self.route_details_button=ttk.Button(footer,text='Show route details',command=self.toggle_route_details)
        self.route_details_button.pack(anchor='w',pady=(4,0))
        self.details_button=ttk.Button(footer,text='Show client tools & details',command=self.toggle_details)
        self.details_button.pack(anchor='w',pady=(4,0))
        self.setup_frame.pack(in_=footer,fill='x')
        self.setup_frame.pack_forget()
        self.ids = tk.StringVar(value='')
        self.ids_label = ttk.Label(self.setup_frame, text='Matched nearby IDs (automatic)')
        self.ids_label.pack(anchor='w',pady=(6,3))
        self.ids_entry = ttk.Entry(self.setup_frame, textvariable=self.ids,state='readonly')
        self.ids_entry.pack(fill='x')
        self.memory_text = tk.StringVar(value='Embed a client to see nearby monster IDs')
        ttk.Label(self.setup_frame, textvariable=self.memory_text, wraplength=450).pack(anchor='w')
        self.detail_text = tk.StringVar(value='')
        ttk.Label(self.setup_frame, textvariable=self.detail_text, wraplength=450).pack(anchor='w', pady=4)

        self.activity_panel=ttk.Frame(self.sidebar)
        self.activity_panel.pack(fill='both',expand=True,pady=(4,0))
        self.nearby = NearbyMonsters(self.activity_panel,self.toggle_nearby)
        self.nearby.pack(side='bottom',fill='x',pady=(6,0))
        pickup_frame=ttk.LabelFrame(self.activity_panel,text='Pickup history · newest first',padding=6)
        pickup_frame.pack(fill='both',expand=True)
        pickup_body=ttk.Frame(pickup_frame)
        pickup_body.pack(fill='both',expand=True)
        self.pickup_tree=ttk.Treeview(pickup_body,columns=('time','item','amount'),show='headings',height=9)
        for column,title,width in [('time','Local time',125),('item','Picked up',205),('amount','Amount',65)]:
            self.pickup_tree.heading(column,text=title)
            self.pickup_tree.column(column,width=width,minwidth=width,stretch=column=='item')
        pickup_scroll=ttk.Scrollbar(pickup_body,orient='vertical',command=self.pickup_tree.yview)
        self.pickup_tree.configure(yscrollcommand=pickup_scroll.set)
        self.pickup_tree.pack(side='left',fill='both',expand=True)
        pickup_scroll.pack(side='right',fill='y')
        for pickup in self.pickup_history.rows:
            self.pickup_tree.insert('',0,values=pickup_values(pickup))
        self.pane = tk.Frame(root, background='#101820')
        self.pane.bind('<Configure>', self.resize)
        self.sidebar_host.bind_children()
        self.refresh_client()
        self.record(state='Off')
        self.refresh_farmer_recovery()
        self.detail_text.set('Embedding checks actual memory access. Administrator access is only relevant if Windows denies that read.')
        root.after(200, self.poll)
        root.after(25, self.poll_pointer_focus)
        try:
            from conquest.discord_notify import ensure_monitor
            ensure_monitor()
        except OSError:
            self.detail_text.set('Discord notifier could not start; farming is unaffected')

    def save_merchant_transfers(self):
        from conquest.merchants.farmer_preferences import set_delivery_enabled,rollout_enabled
        try:
            set_delivery_enabled(self.transfer_character,bool(self.merchant_transfers.get()))
            self.detail_text.set('Automatic merchant delivery '+('On' if self.merchant_transfers.get() else 'Off')+
                ' for '+self.transfer_character)
        except (OSError,ValueError) as error:
            self.merchant_transfers.set(rollout_enabled(self.transfer_character))
            self.detail_text.set('Could not save merchant transfer setting: '+str(error))

    def clear_stale_handoff(self):
        if getattr(self,'_stale_handoff_checking',False):return
        dispatch=getattr(self,'stale_handoff_dispatch',None)
        if dispatch is None:
            messagebox.showerror('Clear stale handoff','Merchant controls are not ready.',parent=self.root)
            return
        self._stale_handoff_checking=True
        def fail(error):
            self._stale_handoff_checking=False
            messagebox.showerror('Cannot clear handoff',str(error),parent=self.root)
        def after_pointer_idle(callback):
            # Clicking our own confirmation is physical mouse activity too.
            # Wait without input/locks; never suppress the mouse guard.
            deadline=time.monotonic()+10
            self.detail_text.set('Checking handoff; leave the mouse still briefly.')
            def check():
                if self.closing:
                    self._stale_handoff_checking=False
                    return
                try:
                    if self.mouse_priority.active():
                        if time.monotonic()>=deadline:
                            raise ValueError('Mouse is still active. Try again when ready.')
                        self.root.after(100,check)
                        return
                    callback()
                except (OSError,ValueError) as error:fail(error)
            self.root.after(100,check)
        def preview_and_confirm():
            preview=dispatch({'action':'delivery-stale-pre-admission-preview'})
            item=preview.get('item') or {}
            if not messagebox.askyesno('Clear stale handoff',
                    'Clear this expired, unsubmitted delivery reservation?\n\n'
                    +str(preview['request_id'])+'\nItem UID: '+str(item.get('uid','unknown'))+'\n\n'
                    'Trade history is kept. Farming stays Off.',parent=self.root):
                self._stale_handoff_checking=False
                return
            digest=preview['preview_digest']
            def clear():
                dispatch({'action':'delivery-stale-pre-admission-clear',
                    'preview_digest':digest,'confirmation_reference':digest,'operator':'desktop'})
                self._stale_handoff_checking=False
                self.detail_text.set('Stale handoff cleared. Farming remains Off.')
                messagebox.showinfo('Clear stale handoff','Reservation cleared. Farming remains Off.',parent=self.root)
            after_pointer_idle(clear)
        after_pointer_idle(preview_and_confirm)

    def manual_handoff(self):
        """Small farmer-pane entry point; merchant bridge remains authenticated."""
        dispatch=getattr(self,'stale_handoff_dispatch',None)
        if dispatch is None:
            messagebox.showerror('Manual handoff','Merchant controls are not ready.',parent=self.root);return
        try:
            current=dispatch({'action':'manual-status'}).get('handoff')
            if current:
                if messagebox.askyesno('End manual handoff',
                        'Keep the mouse idle after ending. Automation stays fenced until closed windows and stable ownership have been observed for five seconds.',parent=self.root):
                    result=dispatch({'action':'manual-handoff-end','session_id':current['id'],'operator':'farmer pane'})
                    self.manual_handoff_note.set('Manual handoff: settling closed windows; keep the mouse idle.')
            else:
                result=dispatch({'action':'manual-handoff-start','operator':'farmer pane'})
                self.manual_handoff_note.set('Manual handoff: Preparing — wait for Ready before touching any client.')
        except (OSError,ValueError) as error:
            messagebox.showerror('Manual handoff',str(error),parent=self.root)

    # ------------------------------------------------------------------
    # Durable recovery holds
    # ------------------------------------------------------------------
    @staticmethod
    def _recovery_digest(value):
        from conquest.recovery_override import evidence_digest
        return evidence_digest(value)

    def _farmer_recovery_incidents(self):
        """Return one row per unresolved farmer hold, without repairing it."""
        from conquest.discord_notify import read_json
        from conquest.recovery_override import read_recovered
        incidents = []

        # Protected withdrawals use SQLite and must be read without creating a
        # journal.  ``pending`` returns a durable unreadable marker when a
        # state file cannot be read, which is useful evidence for the dialog.
        try:
            from conquest.protected_withdrawal import pending
            for state in pending():
                operation = state.get('operation_id') or 'unreadable'
                digest = self._recovery_digest(state)
                incidents.append({'id':str(operation), 'kind':'protected-withdrawal',
                    'phase':state.get('phase','blocked'), 'state':state,
                    'items':(state.get('items') or ([state.get('uid')] if state.get('uid') else [])),
                    'digest':digest, 'note':state.get('error','Protected warehouse withdrawal needs reconciliation'),
                    'operation_id':operation})
        except (OSError,ValueError):
            # The next status refresh can retry.  Never infer that no hold
            # exists from a failed read.
            incidents.append({'id':'protected-withdrawal:unreadable',
                'kind':'protected-withdrawal', 'phase':'blocked', 'state':{},
                'items':[], 'digest':None,
                'note':'Protected withdrawal journal could not be read; no automatic input is allowed'})

        # These journals belong to the farmer profile and are deliberately
        # inspected independently so a user can select exactly one incident.
        json_journals = (
            ('merchant-journey', ('prepared','outbound_pending','market','return_pending')),
            ('meteor-consolidation', ('withdrawing','travelling','exchange_ready','exchange_pending',
                                      'storing_scroll','stored_in_market','returning','carried_in_market')),
            ('overflow', ('departing','market','returning')),
            ('market-route-departure', ('prepared','submitted')),
        )
        for name, phases in json_journals:
            path = Path(state_path(('reports/banking/' if name!='market-route-departure' else '.runtime/')+
                                   name+'.json'))
            state = read_recovered(path)
            if not isinstance(state,dict) or state.get('phase') not in phases:
                continue
            ident = state.get('operation_id') or state.get('visit_id') or name
            items = state.get('items') or state.get('deposit_pending') or []
            if isinstance(items,dict): items=[items]
            incidents.append({'id':f'{name}:{ident}', 'kind':name,
                'phase':state.get('phase'), 'state':state, 'items':items,
                'digest':self._recovery_digest(state),
                'note':state.get('reason') or f'{name} is awaiting recovery reconciliation',
                'path':str(path), 'pending_phases':phases})

        # Overnight is a route controller hold rather than a transaction.  It
        # is shown here only when the controller explicitly entered attention.
        route_path = Path(state_path('reports/overnight/status.json'))
        route = read_json(route_path)
        if isinstance(route,dict) and route.get('phase')=='needs_attention':
            incidents.append({'id':'overnight:needs_attention','kind':'overnight',
                'phase':'needs_attention','state':route,'items':[],
                'digest':self._recovery_digest(route),
                'note':route.get('detail') or route.get('error') or 'Route controller needs attention',
                'path':str(route_path), 'pending_phases':('needs_attention',)})
        return incidents

    @staticmethod
    def _recovery_items_text(items):
        if not items:return 'Items: none recorded'
        rendered=[]
        for item in items[:20]:
            if isinstance(item,dict):
                rendered.append('UID '+str(item.get('uid','?'))+
                    (' · +'+str(item.get('plus')) if item.get('plus') is not None else '')+
                    (' · '+str(item.get('type_id')) if item.get('type_id') is not None else ''))
            else: rendered.append(str(item))
        if len(items)>20:rendered.append(f'… and {len(items)-20} more')
        return 'Items: '+', '.join(rendered)

    def _choose_farmer_recovery(self):
        incidents=self._farmer_recovery_incidents()
        if not incidents:return None
        if len(incidents)==1:return incidents[0]
        # A list selection is intentional: a multi-hold override must never
        # clear every incident at once.
        dialog=tk.Toplevel(self.root);dialog.title('Choose recovery incident');dialog.transient(self.root)
        ttk.Label(dialog,text='Select exactly one incident to inspect or override:',wraplength=500).pack(padx=12,pady=(12,6))
        choices=tk.Listbox(dialog,width=84,height=min(10,len(incidents)),exportselection=False)
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

    def _show_recovery_preview(self, incident, *, action):
        digest=incident.get('digest') or '(unavailable — journal read failed)'
        text=(f"Incident: {incident['id']}\nPhase: {incident.get('phase')}\n"
              f"{incident.get('note','')}\n{self._recovery_items_text(incident.get('items',[]))}\n"
              "")
        if action=='recheck':
            messagebox.showinfo('Recovery recheck',text+'\n\nNo game input was sent.',parent=self.root)
            return True
        if not digest or len(digest)!=64:return False
        return messagebox.askyesno('Confirm recovery override',
            text+'\n\nConfirm this exact incident to override this one hold.\n'
            'The old result will remain unverified. Continue from the current game state?',
            parent=self.root)

    def _farmer_fresh_recheck(self, incident):
        from conquest.worker import request
        fresh={'recheck_unavailable':'Farmer client is not attached'}
        info=self.last.get('worker_info_path')
        if incident.get('kind')=='meteor-consolidation':
            # A Meteor override may only be planned from a matching live bag
            # and warehouse read, never the historical transfer journal.
            from conquest import meteor_banking
            try:
                fresh=meteor_banking.recheck_worker(info) if info else fresh
            except (OSError,ValueError,KeyError,TypeError) as error:
                fresh={'recheck_unavailable':type(error).__name__,
                       'reason':'Fresh Meteor bag/warehouse memory unavailable'}
            return dict(incident,rechecked=fresh)
        if info:
            try:
                health=request(info,'health')
                fresh=dict(health.get('embedded_controls') or {})
                fresh['target']=health.get('target')
            except (OSError,ValueError,KeyError,TypeError) as error:
                fresh={'recheck_unavailable':str(error)}
        return dict(incident,rechecked=fresh)

    def _farmer_resume_if_fresh(self, revision, fresh=None, *, deadline=None):
        # A click on Override & resume is explicit intent, but a later Stop wins.
        expected_revision,epoch=revision if isinstance(revision,tuple) else (revision,getattr(self,'_recovery_epoch',0))
        if self.control.snapshot()['revision']!=expected_revision or getattr(self,'_recovery_epoch',0)!=epoch:return False
        unified=getattr(self,'unified',None)
        if unified and unified.coordinator.stopped:
            self.recovery_text.set('Hold cleared; Global Stop remains active')
            return False
        deadline=deadline or time.monotonic()+10
        current=self._farmer_fresh_recheck({}).get('rechecked',{})
        life=current.get('life') or {}
        from conquest.character_context import farmer_name
        usable=(current.get('observations_available') and life.get('character')==farmer_name()
                and 0<=time.time()-current.get('observed_at',0)<=2
                and not current.get('external_execution'))
        if self.mouse_priority.active() or not usable:
            self.recovery_text.set('Hold cleared; waiting for fresh memory and mouse release')
            if time.monotonic()<deadline:
                self.root.after(250,lambda:self._farmer_resume_if_fresh(revision,deadline=deadline))
            return False
        if self.control.snapshot()['revision']!=expected_revision or getattr(self,'_recovery_epoch',0)!=epoch:return False
        if unified and unified.coordinator.stopped:return False
        try:self.update_control({'enabled':True})
        except (OSError,ValueError) as error:
            self.recovery_text.set('Hold cleared; '+str(error));return False
        self.recovery_text.set('Hold cleared; farming requested')
        return True

    def _apply_farmer_override(self, incident, digest):
        from conquest.route_controller import controller_guard
        with controller_guard() as acquired:
            if not acquired:raise ValueError('Route is still active; wait for it to stop before overriding')
            fresh=self._farmer_fresh_recheck(incident).get('rechecked',{})
            if fresh.get('external_execution'):
                raise ValueError('Farmer input is still active; wait before overriding')
            if incident['kind']=='protected-withdrawal':
                from conquest.protected_withdrawal import operator_override
                return operator_override(incident['operation_id'],operator_confirmed=True,
                    confirmation_reference=digest,incident_digest=digest,fresh_evidence=fresh)
            if incident['kind']=='meteor-consolidation':
                # Meteor recovery is intentionally not the generic JSON hold:
                # its next action needs a fresh memory read of *both* bag and
                # warehouse, and its terminal journal must enter the dedicated
                # Meteor archive before a new banking operation can begin.
                from conquest import meteor_banking
                return meteor_banking.operator_override(operator_confirmed=True,
                    confirmation_reference=digest,incident_digest=digest,
                    fresh_evidence=fresh)
            from conquest.recovery_override import operator_override
            return operator_override(Path(incident['path']),pending_phases=incident['pending_phases'],
                operator_confirmed=True,confirmation_reference=digest,incident_digest=digest,
                fresh_evidence=fresh,incident=incident['kind'])

    def recheck_farmer_recovery(self):
        incident=self._choose_farmer_recovery()
        if not incident:
            self.recovery_text.set('No unresolved farmer recovery holds')
            return None
        incident=self._farmer_fresh_recheck(incident)
        self._show_recovery_preview(incident,action='recheck')
        self.refresh_farmer_recovery()
        return incident

    def refresh_farmer_recovery(self):
        incidents=self._farmer_recovery_incidents()
        if not incidents:self.recovery_text.set('No unresolved farmer recovery holds');return incidents
        self.recovery_text.set(f"{len(incidents)} recovery hold(s) need attention · select one with Recheck")
        return incidents

    def override_farmer_recovery(self):
        incident=self._choose_farmer_recovery()
        if not incident:return None
        revision=(self.control.snapshot()['revision'],getattr(self,'_recovery_epoch',0))
        incident=self._farmer_fresh_recheck(incident)
        if not self._show_recovery_preview(incident,action='override'):return None
        try:result=self._apply_farmer_override(incident,incident['digest'])
        except (OSError,ValueError) as error:
            messagebox.showerror('Recovery override',str(error),parent=self.root);return None
        self.record(recovery_override={'incident':incident['id'],'digest':incident['digest'],'at':time.time()})
        self.refresh_farmer_recovery()
        self._farmer_resume_if_fresh(revision)
        return result

    def dispatch_recovery(self, body):
        """Small local bridge used by profile-aware clients and focused tests."""
        action=body.get('action') if isinstance(body,dict) else None
        if action not in ('recovery-status','recovery-recheck','recovery-override'):
            raise ValueError('Unsupported recovery command')
        incidents=self._farmer_recovery_incidents()
        if action=='recovery-status':return {'incidents':incidents}
        incident_id=body.get('incident_id')
        incident=next((row for row in incidents if row['id']==incident_id),None)
        if incident is None:raise ValueError('Unknown recovery incident')
        if action=='recovery-recheck':
            fresh=self._farmer_fresh_recheck(incident)
            return {'incident':fresh,'incident_digest':fresh.get('digest'),'rechecked':fresh.get('rechecked')}
        if body.get('operator_confirmed') is not True:
            raise ValueError('Operator confirmation is required for this incident')
        digest=body.get('incident_digest')
        if digest!=incident.get('digest'):raise ValueError('Incident evidence changed; recheck before overriding')
        # UI confirmation is the final boundary; the command remains useful
        # to local tests without opening dialogs.
        if body.get('confirmation_reference')!=digest:
            raise ValueError('Exact incident digest confirmation is required')
        revision=(self.control.snapshot()['revision'],getattr(self,'_recovery_epoch',0))
        result=self._apply_farmer_override(incident,digest)
        self.record(recovery_override={'incident':incident['id'],'digest':digest,'at':time.time()})
        self.refresh_farmer_recovery()
        self._farmer_resume_if_fresh(revision)
        return result

    def dispatch(self, body):
        """Handle the small profile-local recovery surface."""
        return self.dispatch_recovery(body)

    def toggle_route_details(self):
        if self.route_details_frame.winfo_manager():
            self.route_details_frame.pack_forget()
            self.route_details_button.configure(text='Show route details')
        else:
            self.route_details_frame.pack(fill='x',after=self.route_details_button)
            self.route_details_button.configure(text='Hide route details')

    def toggle_details(self):
        if self.setup_frame.winfo_manager():
            self.setup_frame.pack_forget()
            self.details_button.configure(text='Show client tools & details')
        else:
            self.setup_frame.pack(fill='x',in_=self.details_button.master)
            self.details_button.configure(text='Hide client tools & details')

    def update_kill_metrics(self, action='refresh'):
        metrics=getattr(self,'kill_session',None)
        if metrics is None:return
        try:
            getattr(metrics,action)()
        except (OSError,ValueError,sqlite3.Error):
            metrics.error='Kill metrics unavailable; farming controls remain active'
        self.last.update(metrics.snapshot())

    def record(self, **fields):
        self.last.update(fields)
        if getattr(self,'kill_session',None):self.last.update(self.kill_session.snapshot())
        self.last.update(pid=os.getpid(), updated_at=time.time(), ui_revision=5,
            character=getattr(self,'transfer_character',None),
            selected_route=self.selected_route.id if self.selected_route else None,
            client_tools_revision=2,runback_monitor_revision=1,meteor_loop_revision=1,return_path_revision=2,
            scatter_projection_revision=3,level_eta_revision=1,empty_restock_revision=1,supply_refill_revision=1,scatter_ammo_minimum_revision=1,market_warehouse_click_revision=1)
        temporary = self.status_path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(self.last, indent=2))
            temporary.replace(self.status_path)
        except PermissionError:
            # Windows readers may briefly deny replacement. The next status
            # update retries; telemetry must not interrupt the input executor.
            pass

    def refresh_client(self):
        if self.host.saved:
            return
        self.clients = self.catalog.windows()
        self.client_picker.configure(values=[c.label for c in self.clients],state='readonly')
        selected = next((c for c in self.clients if self.client and
                        (c.identity,c.hwnd)==(self.client[2],self.client[1])),None)
        if selected is None and len(self.clients)==1:
            selected = self.clients[0]
        self.client = None
        if selected:
            self.choose_client(selected)
        else:
            self.client_text.set('Select a client' if self.clients else 'Launch Conquer to begin')

    def choose_client(self, candidate):
        self.client = (candidate.identity['pid'],candidate.hwnd,candidate.identity)
        self.client_text.set(candidate.label)

    def select_client(self, _event=None):
        index = self.client_picker.current()
        if not self.host.saved and 0<=index<len(self.clients):
            self.choose_client(self.clients[index])

    def retry_reconnect(self):
        from conquest.storage_halt import active
        if active():return
        if self.observer is not None and getattr(self.observer,'read_only_build',False):
            self.state_text.set('Reconnect is disabled for this observation-only client version')
            return
        if self.observer is None or not login_screen(self.observer.operations.target.hwnd):
            return
        self.reconnector.retry()
        self.reconnect_pending=True
        self.record(state='Reconnecting',reconnection={'state':'retry_requested'})

    def reconnect_client(self):
        from conquest.storage_halt import active
        if active():raise ValueError('Storage full: automatic reconnect is disabled')
        if self.observer is None:
            raise ValueError('Hosted client is unavailable for reconnect')
        if getattr(self.observer,'read_only_build',False):
            raise ValueError('Reconnect is disabled for this observation-only client version')
        with self.observer.lock:
            return submit_login(self.observer.operations.target, session=self.observer.adapter)

    def refocus_if_farming(self):
        if self.observer is not None and getattr(self.observer,'read_only_build',False):
            return
        if hasattr(self,'attachment') and not self.attachment.ready:
            return
        if getattr(self,'unified',None) and (self.unified.coordinator.owner or
                (self.unified.grant and self.unified.safe_to_yield())):
            return
        import win32gui
        state=self.control.snapshot()
        if self.closing or not self.client or not self.host.saved:
            self.refocuser.step(False,False,lambda:False)
            return
        if self.mouse_priority.active():
            return
        pid,hwnd,identity=self.client
        root=win32gui.GetAncestor(hwnd,2)
        focused=(win32gui.GetForegroundWindow()==root and not win32gui.IsIconic(root))
        def town_route_active():
            from conquest.discord_notify import read_json
            route=read_json(state_path('reports/overnight/status.json'))
            return (route.get('phase') in ('starting','restocking')
                    and 0<=time.time()-route.get('updated_at',0)<12
                    and not Path(state_path('.runtime/overnight.stop')).exists()
                    and not ctypes.windll.user32.GetAsyncKeyState(0x7b)&0x8000)
        def activate():
            with self.control.lock:
                if not self.control.snapshot()['enabled'] and not town_route_active():
                    return False
                restored=activate_client(hwnd,identity,api=self.host.api)
                if restored:
                    self.focus_game()
                return restored
        result=self.refocuser.step(state['enabled'] or town_route_active(),focused,activate)
        if result is not None:
            self.last.update(auto_refocus={'restored':result,'attempted_at':time.time()})

    def show_game(self):
        try:
            if not self.host.saved:
                self.refresh_client()
            if self.client is None:
                raise ValueError('No unique matching character client is open')
            _,hwnd,identity = self.client
            if self.host.saved:
                if self.host.saved.hwnd!=hwnd or self.host.saved.identity!=identity:
                    raise ValueError('Hosted client identity changed before focus')
            from conquest.merchants.coordination import input_scope
            with input_scope():
                if self.host.saved:
                    if getattr(self,'unified',None):
                        self.unified.notebook.select(self.unified.frames['Farmer'])
                    self.root.deiconify()
                    self.root.update_idletasks()
                    width,height=self.pane.winfo_width(),self.pane.winfo_height()
                    if not self.pane.winfo_ismapped() or min(width,height)<1:
                        raise ValueError('Farmer pane is not visible for focus')
                    # Tab changes can move an owned top-level client. Reapply the
                    # current pane geometry before native activation, without using
                    # any remembered desktop coordinate.
                    self.resize_host(width,height)
                    if not self.host.saved:
                        raise ValueError('Farmer pane could not retain the hosted client')
                    self.root.update_idletasks()
                    self.root.lift()
                focused=activate_focused_client(hwnd,identity,api=self.host.api,
                                                focus=self.focus_game if self.host.saved else None)
                if not focused:
                    raise ValueError('Conquer did not receive foreground focus')
            return True
        except Exception as error:
            self.state_text.set(str(error))
            return False

    def focus_game(self):
        focused = self.host.focus()
        self.record(keyboard_focus_hwnd=focused)
        return focused

    def poll_pointer_focus(self):
        """Hand off an actual click in the client, without stealing sidebar focus."""
        try:
            on_key=bool(self.host.api.key_state(0x79)&0x8000)
            if on_key and not self.farming_on_key_down:
                self.update_ids(True)
            self.farming_on_key_down=on_key
            manual=self.mouse_priority.active()
            self.mouse_note.set('Mouse control: yours · resumes after 2s idle' if manual else 'Mouse control: automatic · move mouse to take over')
            if self.host.saved and self.host.mode=='owned':
                self.resize_host(self.pane.winfo_width(),self.pane.winfo_height())
            down = bool(self.host.api.key_state(1) & 0x8000)
            pressed = down and not self.pointer_down
            self.pointer_down = down
            unified = getattr(self,'unified',None)
            if pressed and unified:
                unified.focus_clicked_merchant()
            if pressed and self.host.saved and self.host.api.pointer_in_client(self.host.saved.hwnd):
                self.focus_game()
        except Exception as error:
            self.detail_text.set(f'Client keyboard focus: {error}')
        self.root.after(25,self.poll_pointer_focus)

    def start(self, calibration=False):
        # Memory-only farming requires the explicit pinned embedding flow.
        # Reject before discovery can choose a foreground client or elevate.
        try:
            config = TrialConfig.model_validate(yaml.safe_load(self.profile.read_text()))
            if config.observation_mode != 'legacy_visual':
                raise ValueError('Memory-only farming requires an exactly selected embedded client. '
                    'Use --embed-client --client-pid PID --client-started CREATION_TIME '
                    '--client-hwnd HWND, then enable Farming On in the app.')
        except Exception as error:
            self.state_text.set(str(error))
            self.record(state='Stopped', result={'detail':str(error)})
            return
        if self.thread and self.thread.is_alive():
            return
        if self.host.saved:
            self.state_text.set('Release the embedded client before foreground farming')
            return
        try:
            self.refresh_client()
            if self.client is None:
                raise ValueError('No unique matching character client is open')
            if not ctypes.windll.shell32.IsUserAnAdmin():
                self.state_text.set('Waiting for Windows approval…')
                self.record(state='Waiting for Windows approval')
                self.root.update_idletasks()
                from conquest.application_layout import application_root
                elevated_start(self.root.winfo_id(),application_root(),self.profile,calibration)
                # No run or embedded client exists here. Do not write a stop
                # request that would cancel the elevated app's incoming Start.
                self.root.destroy()
                return
            (self.output / 'stop.request').unlink(missing_ok=True)
            self.record(state='Checking calibration' if calibration else 'Starting', kills=0, attempts=0)
            self.start_button.state(['disabled'])
            self.root.iconify()
            if not self.show_game():
                raise ValueError('Windows could not bring Conquer forward; farming remains stopped')
            self.record(foreground_after_start=self.backend.foreground())
            import win32gui
            if tuple(win32gui.GetClientRect(self.client[1])[2:]) != config.client_size:
                # The foreground profile was inspected at the maximized size.
                # SW_RESTORE alone reverts Conquer to its small default client.
                win32gui.ShowWindow(self.client[1], 3)
                if tuple(win32gui.GetClientRect(self.client[1])[2:]) != config.client_size:
                    raise ValueError('Maximized client does not match this screen profile')
            if not calibration:self.update_kill_metrics('begin')
            self.thread = threading.Thread(target=self.run, args=(config,calibration), daemon=False)
            self.thread.start()
        except Exception as error:
            self.root.deiconify()
            self.record(state='Stopped', result={'detail':str(error)})
            self.state_text.set(str(error))
            self.start_button.state(['!disabled'])

    def run(self, config, calibration):
        session = None
        try:
            import win32gui
            import cv2
            from conquest.vision import health_ratio
            from conquest.memory_health import HealthLayout, MemoryHealthReader
            with physical_coordinates():
                pid, hwnd, identity = self.client
                self.host.api.assert_owner(hwnd, identity)
                physical_size = tuple(win32gui.GetClientRect(hwnd)[2:])
                self.messages.put(('window_geometry', {'physical_size':physical_size,
                    'logical_size':config.client_size}))
                sx, sy = (actual/logical for actual, logical in zip(physical_size, config.client_size))
                if abs(sx-sy) > .005 or not .75 <= sx <= 2:
                    raise ValueError('Client aspect or scale differs from calibration')
                layout = PlayerLayout.model_validate(yaml.safe_load(Path(config.player_profile).read_text()))
                session = LocalSession(pid, hwnd, layout.expected_sha256, config.client_size, physical_size)
                factory = lambda h,s,o,p: NormalizedFrames(h,s,o,p,physical_size=physical_size)
                camera = factory(hwnd, config.client_size, config.capture_output, config.capture_origin)
                try:
                    frame = camera.read()
                    cv2.imwrite(str(self.output/'calibration.png'), frame.image)
                    visual_hp = health_ratio(frame.image, config.client_size)
                    health_layout = HealthLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-health-candidate.yaml').read_text()))
                    reading = MemoryHealthReader(session, health_layout, config.character).read()
                    if abs(visual_hp - reading.current_hp/reading.max_hp) > .03:
                        raise ValueError('Visual HP and the memory candidate disagree')
                    self.messages.put(('calibration_verified', {'health_ratio':visual_hp,
                        'hp_candidate':reading.current_hp, 'max_hp_candidate':reading.max_hp,
                        'physical_size':physical_size, 'logical_size':config.client_size}))
                finally:
                    camera.close()
                if not calibration:
                    logger = logging.getLogger('desktop-farmer')
                    logger.handlers = [EventQueue(self.messages)]
                    logger.setLevel(logging.INFO)
                    result = run_trial(self.profile, None, self.output, 1800, logger,
                        session_override=session, camera_factory=factory)
                    self.messages.put(('finished', result))
                else:
                    self.messages.put(('finished', {'reason':'calibration_complete'}))
        except Exception as error:
            self.messages.put(('failed', {'detail':str(error)}))
        finally:
            if session:
                session.close()

    def stop(self):
        self._recovery_epoch=getattr(self,'_recovery_epoch',0)+1
        self.update_kill_metrics('stop')
        if getattr(self,'unified',None):
            self.unified.grant = None
            self.unified.runtime.global_stop()
        if getattr(self,'reload_cancel',None):self.reload_cancel.set()
        from conquest.safe_reload import RESUME
        RESUME.unlink(missing_ok=True)
        stopped=self.control.update({'enabled':False})
        self.record(manual_stop_revision=stopped['revision'])
        (self.output/'stop.request').write_text('Stop requested from desktop app')
        self.state_text.set('Stopping…' if self.thread and self.thread.is_alive() else 'Off')

    def restart(self):
        if getattr(self,'reload_preparing',False):return False
        if getattr(getattr(getattr(self,'unified',None),'runtime',None),'connecting',None):
            self.state_text.set('Wait for the merchant connection to finish before reloading')
            return False
        # A diagnostic detach retains the observer and exact client identity.
        # Restore hosting before the usual safe-spot/pinned-client handoff.
        if self.observer and not self.host.saved:
            if not self.change_native_window(False,resume=False):return False
        if not self.host.saved:return self._restart_now()
        info=self.last.get('worker_info_path')
        if not info or not self.selected_route:
            self.state_text.set('Reload deferred: waiting for memory connection and route')
            return False
        unified=getattr(self,'unified',None)
        if unified:
            if unified.coordinator.owner or unified.calibrating:
                self.state_text.set('Wait for merchant input before preparing the farmer for reload')
                return False
            # Revival and safety input require the farmer's actual visible
            # surface, even when reload was requested from a merchant tab.
            unified.notebook.select(unified.frames['Farmer'])
            self.root.update_idletasks()
        from conquest.discord_notify import read_json,process_alive
        status=read_json(state_path('reports/overnight/status.json'))
        self.reload_resume=bool(self.control.snapshot()['enabled'] or
            (status.get('phase') in ('hunting','restocking','starting') and process_alive(status.get('pid'))))
        self.reload_cancel=threading.Event();self.reload_preparing=True
        self.record(reload_preparing=True,current_activity='Moving to a safe spot for app reload')
        self.state_text.set('Preparing a safe nearby reload')
        route_id=self.selected_route.id
        def prepare():
            try:
                from conquest.application_layout import RuntimeLayout
                layout=RuntimeLayout.resolve();repo=layout.root
                # Import checks run while the current farmer still protects the player.
                checked=subprocess.run([str(layout.python(windowed=True)),
                    str(layout.script('start_desktop_app.py')),'--check-imports'],
                    cwd=repo,env=layout.environment(),capture_output=True,timeout=20)
                if checked.returncode:raise ValueError('Startup check failed; keeping current app')
                from conquest.safe_reload import prepare as prepare_reload
                proof=prepare_reload(info,route_id,self.reload_cancel,
                    lambda note:self.messages.put(('reload_activity',{'activity':note})))
                self.messages.put(('reload_ready',proof))
            except Exception as error:
                self.messages.put(('reload_failed',{'detail':str(error)}))
        threading.Thread(target=prepare,daemon=True,name='safe-reload').start()
        return False

    def _restart_now(self):
        if self.thread and self.thread.is_alive():
            self.state_text.set('Stop foreground farming before reloading')
            return False
        unified = getattr(self,'unified',None)
        if unified and (unified.coordinator.owner or unified.calibrating):
            self.state_text.set('Wait for merchant input to finish before reloading')
            return False
        from conquest.application_layout import RuntimeLayout
        layout=RuntimeLayout.resolve();repo=layout.root
        # Validate new source while the approved parent and client are intact.
        try:
            checked = subprocess.run(
                [str(layout.python(windowed=True)),
                 str(layout.script('start_desktop_app.py')),'--check-imports'],
                cwd=repo, env=layout.environment(), capture_output=True, timeout=20)
            if checked.returncode:
                self.state_text.set('Reload canceled: startup check failed. See reports/desktop-startup-error.txt')
                return False
        except (OSError, subprocess.TimeoutExpired):
            self.state_text.set('Reload canceled: startup check could not finish')
            return False
        if self.host.saved:
            try:
                from conquest.safe_reload import validate_handoff,save_resume
                validate_handoff(self.last['worker_info_path'],self.reload_proof)
                save_resume(self.reload_proof,self.reload_resume)
            except (ValueError,KeyError,AttributeError,OSError) as error:
                self.state_text.set(f'Reload deferred: {error}')
                return False
        selected = self.host.saved
        if selected and not self.release():
            from conquest.safe_reload import RESUME
            RESUME.unlink(missing_ok=True)
            return False
        # A child of the approved app inherits its existing administrator token.
        # This button does not invoke runas or display another consent request.
        args = [str(layout.python(windowed=True)),str(layout.script('start_desktop_app.py')),
                '--profile',str(self.profile.resolve())]
        from conquest.character_context import context_arguments
        args += context_arguments()
        if selected:
            args += ['--embed-client','--client-pid',str(selected.identity['pid']),
                     '--client-started',str(selected.identity['creation_time_100ns']),
                     '--client-hwnd',str(selected.hwnd)]
        try:
            if unified:
                # Release owned game windows and the bridge before the new app starts.
                if unified.close(reason='restarting') is False:
                    return False
            subprocess.Popen(args,cwd=repo,env=layout.environment())
            self.root.destroy()
            return True
        except OSError as error:
            from conquest.safe_reload import RESUME
            RESUME.unlink(missing_ok=True)
            self.state_text.set(f'Could not reload the app: {error}')
            return False

    def launch(self):
        try:
            if self.launch_watch.pending:
                self.launch_watch.cancel()
                self.launch_button.configure(text='Launch client')
                self.state_text.set(self.launch_watch.note)
                self.record(state='Off')
                return
            if self.host.saved or (self.thread and self.thread.is_alive()):
                raise ValueError('Stop farming and release the current client before launching another')
            if self.character_context:
                from conquest.character_context import current
                context=current()
                if not context.installation:raise ValueError('Configure this character’s game installation on this PC first')
                launcher=context.installation/'ImBootstrapper.exe'
                self.launch_watch=LaunchWatch(self.catalog,[str(launcher)],cwd=launcher.parent)
            self.launch_watch.start()
            self.launch_button.configure(text='Cancel launch')
            self.state_text.set(self.launch_watch.note)
            self.record(state='Launching client')
        except Exception as error:
            self.launch_button.configure(text='Launch client')
            self.state_text.set(str(error))

    def make_observer(self,pid,hwnd):
        from conquest.identity import fingerprint
        from conquest.memory_build_layout import READ_LAYOUTS, CLIENT_SHA256_1078
        # The authenticated attach path probes an exact PID before selecting it
        # in the picker. Multiple open clients leave self.client unset here.
        identity=self.backend.identity(pid)
        if self.client is not None and self.client[0]!=pid:
            raise ValueError('Observer PID differs from the selected client')
        digest=fingerprint(Path(identity['path']))['sha256']
        layout=READ_LAYOUTS.get(digest)
        if layout is None:
            from conquest.memory import UnsupportedClientBuildError
            raise UnsupportedClientBuildError('Unsupported game version; its memory layout has not been qualified')
        health = HealthLayout.model_validate(yaml.safe_load(Path('profiles').joinpath(layout.health_profile).read_text()))
        entities = EntityLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-entities-candidate.yaml').read_text()))
        if digest==CLIENT_SHA256_1078:
            # Build the entity profile through the selected layout after the
            # observer opens its exact-SHA read-only session.
            entities=entities.model_copy(update={'expected_sha256':digest,
                'root_rva':layout.entity_root_rva,'pointer_offsets':layout.entity_pointer_offsets,
                'collection_vtable_rva':layout.entity_collection_vtable_rva,
                'monster_vtable_rva':layout.entity_actor_vtable_rva,
                'max_hp_offset':0x3f0,'level_offset':0x708,
                'attribute_pointer_offset':layout.entity_attribute_pointer_offset})
        return EmbeddedObserver(pid,hwnd,health,entities,farmer_name(),context=self.character_context)

    def embed(self,candidate=None):
        if getattr(self,'embed_layout_pending',False):
            return
        if self.observer:
            if candidate and candidate!=self.client:
                self.state_text.set('Release the current client before selecting another')
                return
            return self.change_native_window(False)
        if self.thread and self.thread.is_alive():
            self.state_text.set('Stop farming before embedding the client')
            return
        try:
            if self.host.saved:
                raise ValueError('Release the current embedded client first')
            if candidate:
                self.choose_client(candidate)
            else:
                self.refresh_client()
            if self.client is None:
                raise ValueError('Select a Conquer client to embed')
            self.attachment.enter('access')
            # Creating the real observer checks an actual process-memory read.
            # Never request elevation based on the Embed button alone.
            self.observer = self.observer_factory(self.client[0],self.client[1])
            self.attachment.enter('identity',pid=self.client[0],hwnd=self.client[1],
                                  process_created=self.client[2].get('creation_time_100ns'))
            if self.character_context:
                from conquest.client_attachment import verify_observer
                evidence=verify_observer(self.character_context,self.observer)
                self.attachment.evidence.update(evidence)
            self.attachment.enter('attachment')
            self.embedded_layout()
            self.root.update_idletasks()
            if self.character_context:
                # Windows applies maximization asynchronously. Idle geometry
                # tasks alone do not deliver its native Configure events.
                self.embed_layout_pending=True
                self.attachment_text.set('Preparing full game viewport…')
                observer=self.observer
                self.root.after(100,lambda:self.wait_for_embed_layout(observer,time.monotonic()+2))
                return
            self.finish_embed()
        except Exception as error:
            self.embed_failed(error)

    def wait_for_embed_layout(self,observer,deadline,previous=None):
        if self.observer is not observer or getattr(self,'closing',False):
            self.embed_layout_pending=False
            return
        try:
            from conquest.client_attachment import require_viewport, ViewportTooSmall
            size=(self.pane.winfo_width(),self.pane.winfo_height())
            self.attachment.evidence['pane_size']=list(size)
            self.attachment.evidence['required_pane_size']=[1036,793]
            fits=size[0]>=1036 and size[1]>=793 and self.pane.winfo_ismapped()
            if time.monotonic()<deadline and (not fits or size!=previous):
                self.root.after(100,lambda:self.wait_for_embed_layout(observer,deadline,size))
                return
            require_viewport(*size)
            if not self.pane.winfo_ismapped():
                raise ViewportTooSmall('Select the farmer tab and retry Embed; the game pane is not visible.')
            self.embed_layout_pending=False
            self.finish_embed()
        except Exception as error:
            self.embed_failed(error)

    def finish_embed(self):
        try:
            self.host.attach(self.client[1], self.client[2], self.pane.winfo_id(),
                             self.pane.winfo_width(), self.pane.winfo_height())
            self.attachment.attached=True
            automation_build=bool(getattr(self.observer,'automation_ready_build',False))
            if getattr(self,'unified',None):self.unified.coordinator.surface_blocks['Farmer']=bool(getattr(self.observer,'read_only_build',False) and not automation_build)
            self.attachment.enter('memory')
            if getattr(self.observer,'read_only_build',False) and not automation_build:
                self.initialize_read_only_attachment()
            else:
                self.observer.focus_client=self.host.focus
                self.initialize_attached_behavior()
            self.client_picker.configure(state='disabled')
            if not getattr(self.observer,'read_only_build',False) or automation_build:
                self.attachment.ready=True
                self.attachment.observation_ready=True
                self.attachment_text.set('Client attached · automation ready · farming Off')
                self.state_text.set('Client embedded · farming Off')
            self.record(state='Embedded', hwnd=self.client[1],attachment=self.attachment.snapshot())
        except Exception as error:
            self.embed_failed(error)

    def embed_failed(self,error):
        self.embed_layout_pending=False
        note=self.attachment.fail(error)
        self.state_text.set(note)
        self.attachment_text.set(f'{self.attachment.stage}: {note}')
        if self.attachment.stage not in ('memory','behavior') or not self.host.saved:
            try:
                self.host.detach()
                self.stop_observer()
                self.attachment.attached=False
                self.compact()
            except Exception as cleanup_error:
                self.attachment.evidence['restoration_error']=type(cleanup_error).__name__
        # Hosting succeeded: retain the window and memory session. A failed
        # terrain/recovery initializer must not silently eject the client.
        self.record(state='Client attached; automation blocked' if self.attachment.attached else 'Embed failed',
                    attachment=self.attachment.snapshot())

    def initialize_attached_behavior(self):
        self.runtime = ControlRuntime(self.control,None,None,None,farmer_name(),observer=self.observer)
        self.runtime.start()
        self.attachment.enter('behavior')
        if self.character_context:
            from conquest.client_attachment import remember_installation
            remember_installation(self.character_context,self.client[2]['path'])
        if hasattr(self.observer,'start_bridge'):
            worker_info = Path(state_path('.runtime'))/f'embedded-worker-{os.getpid()}.json'
            self.observer.start_bridge(worker_info,self.runtime.snapshot,control_update=self.update_control,
                on_reload=lambda:self.messages.put(('reload_requested',{})),
                on_native_window=lambda detached:self.messages.put(('native_window_requested',{'detached':detached})))
            self.observer.bridge.on_reconnect=lambda:self.messages.put(('reconnect_requested',{}))
            self.observer.bridge.sync_window_mode(self.host)
            if hasattr(self.observer,'adapter'):
                from conquest.navigation import read_terrain
                from conquest.route_recovery import RouteRecovery,EmbeddedRecoveryInput
                from conquest.route_input import RouteJumpInput
                current=self.observer()
                terrain=read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),current.get('life',{}).get('map_id',1002))
                self.observer.bridge.on_route_jump=RouteJumpInput(self.observer,terrain)
                self.runtime.recovery=RouteRecovery(self.control,self.observer.session.identity,terrain,
                    EmbeddedRecoveryInput(self.observer,self.control),state_path('.runtime/death-return.json'))
                if self.selected_route:
                    self.runtime.recovery.enabled=(self.character_context.settings.get('recover_after_death',self.selected_route.recover_after_death) if self.character_context else self.selected_route.recover_after_death)
            self.record(worker_info_path=str(worker_info.resolve()))
        self.attachment.ready=True
        self.attachment.observation_ready=True
        self.attachment.attached=bool(self.host.saved)
        if getattr(self,'unified',None):self.unified.coordinator.surface_blocks['Farmer']=False
        self.attachment_text.set('Client attached · automation ready · farming Off')

    def initialize_read_only_attachment(self):
        """Expose pinned memory observations without initializing any automation."""
        self.attachment.enter('behavior',mode='exact_build_read_only')
        # Prove an initial exact-build sample before exposing the checkpoint.
        # ControlRuntime has no dispatcher/recovery here, so it only publishes
        # the observer's read-only snapshots and cannot issue game input.
        initial=self.observer()
        if initial.get('observations_available') is not True:
            raise ValueError(initial.get('observation_note','Initial memory observation is unavailable'))
        self.runtime=ControlRuntime(self.control,None,None,None,farmer_name(),observer=self.observer,
            disable_on_close=False)
        self.runtime.start()
        worker_info=Path(state_path('.runtime'))/f'embedded-readonly-{os.getpid()}.json'
        self.observer.start_read_only_bridge(worker_info,self.runtime.snapshot)
        self.record(worker_info_path=str(worker_info.resolve()))
        self.attachment.attached=bool(self.host.saved)
        self.attachment.observation_ready=True
        self.attachment.ready=False
        self.attachment_text.set('Client attached · observation ready · input and farming disabled for this client version')
        self.state_text.set('Client embedded · observation only')

    def retry_behavior_setup(self):
        if not self.observer or not self.host.saved:
            return self.embed()
        if self.control.snapshot()['enabled'] or (self.thread and self.thread.is_alive()):
            self.attachment_text.set('Stop farming before retrying setup')
            return
        if (getattr(self.observer,'read_only_build',False)
                and not getattr(self.observer,'automation_ready_build',False)):
            self.attachment_text.set('Observation-only client is already attached; farming remains disabled')
            return
        try:
            if self.runtime:self.runtime.close()
            if getattr(self.observer,'bridge',None):
                self.observer.bridge.close();self.observer.bridge=None
            self.initialize_attached_behavior()
        except Exception as error:
            self.attachment_text.set(self.attachment.fail(error))
            self.record(attachment=self.attachment.snapshot())

    def copy_attachment_diagnostics(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.attachment.copy_text())

    def change_native_window(self,detached,*,resume=True):
        """Apply queued hosting changes without rewriting the user's intent."""
        try:
            if not self.observer or not self.client:
                raise ValueError('Select and embed a client first')
            with self.observer.lock:
                if detached:
                    if (self.control.snapshot()['enabled'] or
                            (self.thread and self.thread.is_alive())):
                        raise ValueError('Stop farming before detaching the client')
                    self.host.detach()
                elif not self.host.saved:
                    if getattr(self,'character_context',None):
                        from conquest.client_attachment import require_viewport
                        require_viewport(self.pane.winfo_width(),self.pane.winfo_height())
                    # The same lock serializes bridge input and window changes.
                    # On may already be queued; it must not veto reattachment.
                    self.host.attach(self.client[1],self.client[2],self.pane.winfo_id(),
                                     self.pane.winfo_width(),self.pane.winfo_height())
                self.observer.bridge.sync_window_mode(self.host)
            self.record(state='Native client input check' if detached else 'Embedded',
                        hwnd=self.client[1],window_mode='detached' if detached else self.host.mode,
                        embed_error=None)
            self.state_text.set('Client detached' if detached else 'Client embedded')
            if not detached and resume and self.control.snapshot()['enabled']:
                self.messages.put(('farm_requested',{}))
            return True
        except Exception as error:
            # Retain the existing memory connection and HWND for a retry.
            self.state_text.set(str(error))
            self.record(embed_error=str(error))
            return False

    def embedded_layout(self):
        if getattr(self,'unified',None):
            self.unified.notebook.select(self.unified.frames['Farmer'])
        self.sidebar_host.embedded()
        # Dynamic status/count text must not resize the game's rendering
        # surface. Only resizing the top-level window changes the pane.
        self.foreground_row.pack_forget()
        self.foreground_note.pack_forget()
        self.setup_frame.pack_forget()
        self.details_button.configure(text='Show client tools & details')
        self.root.geometry('1500x800')
        if self.root.state() != 'withdrawn':
            self.root.state('zoomed')
        self.pane.pack(in_=getattr(self,'content_parent',self.root),side='right', fill='both', expand=True)

    def compact(self):
        self.pane.pack_forget()
        if self.root.state() != 'withdrawn':
            self.root.state('normal')
        self.sidebar_host.compact()
        self.root.geometry('540x850')
        self.client_picker.configure(state='readonly')
        self.foreground_row.pack(before=self.tools_row,fill='x',pady=6)
        self.foreground_note.pack(before=self.tools_row,anchor='w',pady=8)
        self.nearby.refresh([],self.control.snapshot()['target_type_ids'],available=False)
        self.setup_frame.pack(fill='x',in_=self.details_button.master)
        self.details_button.configure(text='Hide client tools & details')

    def toggle_nearby(self,type_id):
        try:
            types = set(self.control.snapshot()['target_type_ids'])
            types.symmetric_difference_update({type_id})
            current = self.control.update({'target_type_ids':sorted(types),'target_ids':[]})
            if self.selected_route and sorted(types)!=sorted(self.selected_route.monster_type_ids):
                self.selected_route = None
                self.route_selection_path.unlink(missing_ok=True)
                self.route_text.set('Custom monster selection')
                self.route_note.set('Saved routes are unchanged.\nChoose a route again to restore its monster group.\nFull travel / hunt / restock cycle is being validated.')
            if self.runtime:
                data = self.runtime.snapshot()
                self.nearby.refresh(data['monsters'],current['target_type_ids'],
                    available=data.get('observations_available',False))
        except ValueError as error:
            self.memory_text.set(str(error))

    def resume_leveling(self):
        from conquest.session_plan import resume_leveling
        resume_leveling()
        self.session_note.set('Automatic leveling')
        self.record(route_hold=None)

    def restore_route(self):
        if not self.route_selection_path.exists():
            return
        try:
            route=self.route_library.load(json.loads(self.route_selection_path.read_text())['route_id'])
            control=self.control.snapshot()
            if not control['target_ids'] and (sorted(control['target_type_ids'])==sorted(route.monster_type_ids)
                    or control['target_type_ids']==[route.monster_type_ids[0]]):
                self.control.update({'target_type_ids':list(route.monster_type_ids)})
                self.display_route(route)
        except (ValueError,KeyError,OSError):
            self.route_note.set('Saved selection unavailable; choose a route again.\nRoute definitions remain in profiles/routes.\nFarming is Off after an app restart.')

    def display_route(self,route):
        self.selected_route=route
        self.route_text.set(route_label(route))
        if getattr(self,'runtime',None) and self.runtime.recovery:
            self.runtime.recovery.enabled=route.recover_after_death
        supplies=route.supplies
        self.route_note.set(f'Levels {route.recommended_levels[0]}–{route.recommended_levels[1]} · Normal monster family selected\n'
            'Restock below 3 arrows, when potions are empty, or inventory is full')

    def select_level_preset(self,_event=None):
        index=self.level_preset_picker.current()
        if index<0:return
        preset=self.level_presets[index]
        if not preset['saved_route']:
            self.route_note.set('Named route plan saved; travel and hunting area still need surveying.\nYour current route remains selected.')
            return
        route=self.route_library.load(preset['saved_route'])
        self.route_picker.current(next(i for i,r in enumerate(self.saved_routes) if r.id==route.id))
        self.select_route()

    def select_route(self,_event=None):
        try:
            index=self.route_picker.current()
            if index<0:
                return
            route=self.saved_routes[index]
            if self.thread and self.thread.is_alive():
                raise ValueError('Stop the foreground run before changing routes')
            self.control.update({'enabled':False,'target_type_ids':list(route.monster_type_ids),'target_ids':[]})
            from conquest.session_plan import follow_manual_route,plan_note
            follow_manual_route(route)
            if hasattr(self,'session_note'):self.session_note.set(plan_note())
            temporary=self.route_selection_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'route_id':route.id}),encoding='utf-8')
            temporary.replace(self.route_selection_path)
            self.display_route(route)
            self.record()
        except (ValueError,OSError) as error:
            self.route_text.set(route_label(self.selected_route) if self.selected_route else 'Choose a saved route')
            self.route_note.set(str(error))

    def save_route_copy(self):
        if not self.selected_route:
            self.route_note.set('Choose a route before saving a copy.')
            return
        name=simpledialog.askstring('Save route copy','Name for the reusable route:',parent=self.root)
        if name is None:
            return
        try:
            name=name.strip()
            route_id=re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')[:48]
            data=self.selected_route.model_dump()
            data.update(id=route_id,name=name)
            self.route_library.save(data)
            self.saved_routes=saved_route_choices(self.route_library.all())
            self.route_picker.configure(values=[route_label(r) for r in self.saved_routes])
            self.route_picker.current(next(i for i,r in enumerate(self.saved_routes) if r.id==route_id))
            self.select_route()
        except (ValueError,OSError) as error:
            self.route_note.set(str(error))

    def update_ids(self, enabled):
        if not enabled:
            self._recovery_epoch=getattr(self,'_recovery_epoch',0)+1
        if enabled:
            from conquest.merchants.delivery_operation import guard_protected_assets
            try:guard_protected_assets()
            except ValueError as error:
                self.memory_text.set(str(error))
                return
        if enabled and getattr(self,'character_context',None):
            if self.character_context.profile.role!='Farmer' or not self.attachment.ready:
                self.memory_text.set('Selected farmer is not automation-ready; see attachment diagnostics')
                return
        if enabled and getattr(self,'unified',None):
            self.unified.grant = None
            self.unified.coordinator.resume()
        if enabled:
            from conquest.storage_halt import clear_by_user
            clear_by_user()
            self.record(storage_halt=None)
        self.record(control_intent={'enabled':enabled,'source':'UI button or F10','time':time.time()})
        if not enabled:
            self.record(kills_per_hour=0)
        try:
            if getattr(self,'reload_preparing',False):
                if enabled:raise ValueError('Reload preparation owns input; Stop cancels it')
                self.reload_cancel.set()
            with self.control.lock:
                fresh_start=bool(enabled and not self.control.snapshot()['enabled'])
                current = self.control.update({'enabled':enabled})
                if fresh_start:
                    Path(state_path('.runtime/overnight.stop')).unlink(missing_ok=True)
                    self._fresh_controller_start_revision=current['revision']
                elif not enabled:
                    self._fresh_controller_start_revision=None
                    path=Path(state_path('.runtime/overnight.stop'));path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_text('Stopped by user: Farming Off',encoding='utf-8')
            self.record(manual_stop_revision=None if enabled else current['revision'])
            self.update_kill_metrics('begin' if enabled else 'stop')
            if enabled and self.host.saved:
                self.start_embedded_farm()
            elif not enabled and self.thread:
                (self.output/'stop.request').write_text('Farming Off')
            if enabled and self.runtime is None:
                self.control.publish(current['revision'],'waiting_for_observation','Embed a client to observe selected monster IDs')
            self.memory_text.set(self.control.snapshot()['note'])
        except ValueError as error:
            self.memory_text.set(str(error))

    def update_control(self, body):
        body=dict(body)
        explicit_start=body.pop('explicit_restart',False)
        explicit_stop=body.pop('explicit_stop',False)
        if (type(explicit_start) is not bool or type(explicit_stop) is not bool
                or explicit_start and explicit_stop
                or explicit_start and body.get('enabled') is not True
                or explicit_stop and body.get('enabled') is not False):
            raise ValueError('Explicit route control marker does not match enabled')
        if body.get('enabled') is False:
            self._recovery_epoch=getattr(self,'_recovery_epoch',0)+1
        if body.get('enabled'):
            from conquest.merchants.delivery_operation import guard_protected_assets
            guard_protected_assets()
        if body.get('enabled') and getattr(self,'character_context',None):
            if self.character_context.profile.role!='Farmer' or not self.attachment.ready:
                raise ValueError('Selected farmer is not automation-ready; see attachment diagnostics')
        from conquest.storage_halt import active
        if body.get('enabled') and active():raise ValueError('Storage full: press Farming On manually after clearing storage')
        if body.get('enabled') and getattr(self,'reload_preparing',False):
            raise ValueError('Reload preparation owns input; Stop cancels it')
        if 'route_id' in body:
            if set(body)!={'route_id'} or not isinstance(body['route_id'],str):
                raise ValueError('Route selection requires only a saved route ID')
            if self.control.snapshot()['enabled'] or (self.thread and self.thread.is_alive()):
                raise ValueError('Stop farming before selecting another route')
            route=self.route_library.load(body['route_id'])
            current=self.control.update({'enabled':False,'target_type_ids':list(route.monster_type_ids),'target_ids':[]})
            self.messages.put(('route_selected',{'route_id':route.id}))
            return {'route_queued':route.id}
        with self.control.lock:
            stop=Path(state_path('.runtime/overnight.stop'))
            if body.get('enabled') is True and stop.exists() and not explicit_start:
                raise ValueError('Farming restart requires explicit_restart')
            fresh_start=bool(explicit_start and not self.control.snapshot()['enabled'])
            current = self.control.update(body)
            if fresh_start:
                stop.unlink(missing_ok=True)
                self._fresh_controller_start_revision=current['revision']
            elif explicit_stop:
                self._fresh_controller_start_revision=None
                stop.parent.mkdir(parents=True,exist_ok=True)
                stop.write_text('Stopped by user: Farming Off',encoding='utf-8')
        if 'enabled' in body:
            self.messages.put(('control_intent',{'enabled':body['enabled'],'source':'authenticated bridge','time':time.time()}))
        if body.get('enabled') is True:
            if getattr(self,'unified',None):
                self.unified.grant = None
                self.unified.coordinator.resume()
            self.messages.put(('farm_requested',{}))
        elif body.get('enabled') is False and self.thread:
            (self.output/'stop.request').write_text('Farming Off')
        return current

    def start_embedded_farm(self):
        try:
            return self._start_embedded_farm()
        except Exception as error:
            # Preserve the actual startup error. Otherwise the unused generic
            # encounter engine replaces it with unrelated capability blockers,
            # while the town controller keeps reporting that it is hunting.
            intent=self.control.snapshot()
            if intent['enabled']:
                reason='Unable to start farming: '+str(error)
                if self.runtime is not None:
                    self.runtime.external_failure=(intent['revision'],reason)
                self.control.publish(intent['revision'],'runner_stopped','Farm runner stopped: '+reason)
                self.record(state='Farming could not start',current_activity=reason,result={'detail':str(error)})
            return False

    def _start_embedded_farm(self):
        fresh_start=(getattr(self,'_fresh_controller_start_revision',None)
                     ==self.control.snapshot()['revision'])
        from conquest.merchants.delivery_operation import guard_protected_assets
        guard_protected_assets()
        if hasattr(self,'attachment') and not self.attachment.ready:
            raise ValueError('Automation is blocked; see attachment diagnostics')
        from conquest.storage_halt import active
        if active():return
        from conquest.storage_overflow import pending
        from conquest import meteor_banking
        from conquest.merchants.delivery_journey import pending as merchant_journey_pending
        if (pending() or meteor_banking.pending() or merchant_journey_pending()) and self.selected_route:
            from conquest.route_controller import ensure_running
            if ensure_running(self.selected_route.id,fresh_start=fresh_start) and fresh_start:
                self._fresh_controller_start_revision=None
            return
        if self.thread and self.thread.is_alive():
            self.show_game()
            return
        if not self.host.saved and self.observer:
            if not self.change_native_window(False,resume=False):
                raise ValueError('Client reattachment failed; see embedding status')
        if not self.host.saved or self.host.mode!='owned':
            raise ValueError('Open the hosted native client before starting farming')
        from conquest.navigation import straight_waypoints,path_boundary
        from conquest.routes import MONSTER_NAMES,route_monster_name,route_monster_names
        types=self.control.snapshot()['target_type_ids']
        base_types=[kind for kind in types if kind in MONSTER_NAMES]
        if len(base_types)!=1:
            raise ValueError('Choose one supported leveling monster family')
        route=self.selected_route or self.route_library.load(MONSTER_NAMES[base_types[0]].lower())
        if sorted(route.monster_type_ids)!=types:
            raise ValueError('Selected route and monster group differ')
        monster_name=route_monster_name(route)
        monster_variants=route_monster_names(route)[1:]
        if self.selected_route is None:self.display_route(route)
        self.show_game()  # The runner waits for focus while preserving Farming On.
        config=TrialConfig.model_validate(yaml.safe_load(self.profile.read_text()))
        if getattr(self.observer,'automation_ready_build',False):
            config=config.model_copy(update={
                'player_profile':'profiles/classic-1078-player-candidate.yaml',
                'inventory_profile':'profiles/classic-1078-inventory-candidate.yaml'})
        observation=self.observer()
        if observation.get('connection_state')=='login':
            self.reconnect_pending=True
            self.record(state='Reconnecting')
            return
        life=observation['life']
        if life['map_id']!=route.map_id:
            raise ValueError('Travel to the selected route map before starting combat')
        from conquest.combat_ranges import read_combat_ranges,route_combat_settings
        ranges=read_combat_ranges(self.observer,require_scatter=False)
        from conquest.equipment import read_equipment
        from conquest.arrow_upgrades import current_arrow,NORMAL_ARROWS
        inventory=self.observer.town_trade.inventory.read()
        reserves=[i.type_id for i in inventory.items if i.amount>=3]
        ammo_type=current_arrow(read_equipment(self.observer),route.supplies.arrow_type,reserves,
                                equipped_ammo=inventory.equipped_ammo)
        self.record(ammunition={'type_id':ammo_type,'name':NORMAL_ARROWS[ammo_type]})
        self.record(combat_ranges=ranges)
        from conquest.navigation import read_terrain
        terrain=self.runtime.recovery.terrain
        if terrain.map_id!=route.map_id:
            terrain=read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),route.map_id)
            self.runtime.recovery.terrain=terrain
        if terrain.source_sha256!=route.terrain_sha256:
            raise ValueError('Selected route terrain differs from the installed map')
        # Recovery returns to this location before the travel loop resumes.
        episode=self.runtime.recovery.episode
        departure=(tuple(episode['death_position']) if episode and episode['phase'] not in ('completed','cancelled')
                   else tuple(life['position']))
        path=terrain.path(departure,route.hunting_anchor)
        approach=tuple(straight_waypoints(path,12)[1:])
        from conquest.viewport import size_for
        viewport=size_for(self.observer)
        config=config.model_copy(update={'observation_mode':'memory_only','client_size':viewport,'player_anchor':(viewport[0]//2,viewport[1]//2),
            'monster':monster_name,'monster_variants':monster_variants,'expected_map':route.map_id,
            **route_combat_settings(route,ranges),
            'kite_when_surrounded':route.kite_when_surrounded,
            'hunting_anchor':route.hunting_anchor,'boundary':route.hunting_boundary,'patrol_search':route.patrol_search,'route':route.patrol,'approach_route':approach,
            'approach_boundary':path_boundary(path,(terrain.width,terrain.height)),'loot_allowlist':(),
            'maximum_actions':1000})
        from conquest.farmer_profile import load_combat_speed
        speed=load_combat_speed(config.character)
        config=config.model_copy(update={'ammo_type':ammo_type,'target_threshold':.84,'interval':speed.action_interval,
                                         'combat_speed':speed,'attack_progress_timeout':1.2})
        # Town Off can arrive while path planning runs. Commit the startup
        # under the same lock as bridge commands, without switching Off back On.
        from conquest.character_context import apply_overrides
        config=apply_overrides(config)
        if ranges['scatter'] is None:
            # Preferences cannot enable a skill absent from this character.
            config=config.model_copy(update={'attack_button':'left','adaptive_scatter':False,'jump_scatter':False})
        with self.observer.lock:
            if not self.control.snapshot()['enabled']:
                return
            self.native_recovery=self.runtime.recovery
            self.runtime.recovery=None
            self.runtime.external_failure=None
            self.runtime.external_execution=True
            with self.control.lock:
                previous=self.control.snapshot()
                if not previous['enabled']:
                    return
                current=self.control.update({'input_mode':'foreground'})
                if getattr(self,'_fresh_controller_start_revision',None)==previous['revision']:
                    self._fresh_controller_start_revision=current['revision']
            (self.output/'stop.request').unlink(missing_ok=True)
            self.update_kill_metrics('begin')
            self.record(state='Starting farm',attempts=0)
            self.thread=threading.Thread(target=self.run_embedded_farm,args=(config,),daemon=False)
            self.thread.start()

    def run_embedded_farm(self,config):
        from conquest.native_farm import NativeFarmSupervisor
        session=None
        result={'reason':'startup_error'}
        try:
            import win32gui
            with physical_coordinates():
                pid,hwnd,identity=self.client
                physical_size=tuple(win32gui.GetClientRect(hwnd)[2:])
                layout=PlayerLayout.model_validate(yaml.safe_load(Path(config.player_profile).read_text()))
                session=LocalSession(pid,hwnd,layout.expected_sha256,config.client_size,physical_size)
                factory=lambda h,s,o,p:WindowGeometry(h,physical_size)
                supervisor=NativeFarmSupervisor(self.observer,self.control,self.native_recovery,
                    lambda event,fields:self.messages.put((event,fields)))
                logger=logging.getLogger('desktop-farmer')
                logger.handlers=[EventQueue(self.messages)]
                logger.setLevel(logging.INFO)
                result={'reason':'requested_stop'}
                while self.control.snapshot()['enabled']:
                    result=run_trial(self.profile,None,self.output,1800,logger,session_override=session,
                        camera_factory=factory,config_override=config,supervisor=supervisor)
                    if result['reason'] not in ('duration_limit','action_limit'):
                        break
                self.messages.put(('finished',result))
        except Exception as error:
            self.messages.put(('failed',{'detail':str(error)}))
        finally:
            if session:
                session.close()
            restart_for_new_intent=False
            if result.get('reason') in ('requested_stop','emergency_stop','control_changed'):
                restart_for_new_intent=self.control.finish_session(
                    getattr(locals().get('supervisor'),'revision',self.control.snapshot()['revision']),result['reason'])
            else:
                intent=self.control.snapshot()
                reason=result.get('reason','Run interrupted')
                self.runtime.external_failure=(intent['revision'],reason)
                self.control.publish(intent['revision'],'runner_stopped','Farm runner stopped: '+reason)
            self.runtime.recovery=self.native_recovery
            self.runtime.external_execution=False
            if restart_for_new_intent:
                self.messages.put(('farm_requested',{}))

    def render_matched_ids(self, data):
        available = data.get('observations_available', False)
        self.ids_label.configure(text='Matched nearby IDs (automatic)' if available
                                 else 'Last observed IDs (refresh pending)')
        if available:
            text = ', '.join(map(str, data['control']['resolved_target_ids']))
            if text != self.ids.get():
                left = self.ids_entry.index('@0')
                self.ids.set(text)
                self.ids_entry.xview(left)

    def stop_observer(self):
        if self.runtime:
            self.runtime.close()
            if self.runtime.thread and self.runtime.thread.is_alive():
                raise RuntimeError('Waiting for the last memory observation to stop; retry shortly')
            self.runtime = None
        if self.observer:
            self.observer.close()
            self.observer = None

    def release(self):
        try:
            self.launch_watch.cancel()
            self.launch_button.configure(text='Launch client')
            self.stop_observer()
            self.host.detach()
            self.compact()
            self.record(state='Off')
            self.state_text.set('Client released · Off')
            return True
        except Exception as error:
            self.state_text.set(f'Could not restore client: {error}')
            return False

    def resize_host(self,width,height):
        if getattr(self,'character_context',None) and self.host.saved and self.pane.winfo_ismapped():
            from conquest.client_attachment import require_viewport, ViewportTooSmall
            try:
                require_viewport(width,height)
                if getattr(self,'_farmer_viewport_blocked',False):
                    if getattr(self,'unified',None):
                        self.unified.coordinator.surface_blocks['Farmer']=self._farmer_surface_block_before_viewport
                    marker=(self.attachment.stage,repr(self.attachment.error),len(self.attachment.history))
                    if marker==self._farmer_viewport_attachment_marker:
                        self.attachment.ready=self._farmer_ready_before_viewport
                    self._farmer_viewport_blocked=False
            except ViewportTooSmall as error:
                if not getattr(self,'_farmer_viewport_blocked',False):
                    self._farmer_viewport_blocked=True
                    self._farmer_ready_before_viewport=self.attachment.ready
                    self._farmer_viewport_attachment_marker=(self.attachment.stage,repr(self.attachment.error),len(self.attachment.history))
                    self._farmer_surface_block_before_viewport=(bool(self.unified.coordinator.surface_blocks.get('Farmer'))
                                                               if getattr(self,'unified',None) else False)
                if getattr(self,'unified',None):self.unified.coordinator.surface_blocks['Farmer']=True
                self.attachment.ready=False
                self.attachment_text.set(str(error))
                # A tab/layout transition can briefly map a zero/small pane.
                # Keep its verified owned host intact: detaching restores a
                # floating top-level client over the wrapper's tabs. Input is
                # blocked until a later full viewport check succeeds.
                self.host.api.assert_owner(self.host.saved.hwnd,self.host.saved.identity)
                self.host.api.show_async(self.host.saved.hwnd,0)
                self.record(attachment=self.attachment.snapshot())
                return
        self.host.resize(width,height)

    def resize(self, event):
        try:
            self.resize_host(event.width, event.height)
        except Exception as error:
            self.state_text.set(str(error))

    def poll(self):
        try:
            from conquest.session_plan import plan_note
            try:self.session_note.set(plan_note())
            except Exception:
                # A display/schema change must not starve control, healing or reload events.
                self.session_note.set('Session settings updating')
            keep_open = self._poll()
        except Exception as error:
            intent=self.control.snapshot()
            self.control.publish(intent['revision'],'waiting_for_recovery',str(error))
            self.record(state='Waiting for recovery',result={'detail':str(error)})
            self.state_text.set(f'Client check failed: {error}')
            keep_open = True
        if keep_open:
            self.root.after(200,self.poll)

    def _poll(self):
        if time.monotonic()>=getattr(self,'_manual_handoff_status_at',0):
            self._manual_handoff_status_at=time.monotonic()+1
            dispatch=getattr(self,'stale_handoff_dispatch',None)
            if dispatch:
                try:
                    handoff=dispatch({'action':'manual-handoff-status'}).get('handoff')
                    if handoff:
                        phase=handoff.get('phase')
                        note={'preparing':'Preparing — wait for Ready before touching any client.',
                              'ready':'Ready — trade manually; automation remains paused.',
                              'ending':'Settling — keep the mouse idle.',
                              'needs_attention':'Needs attention — do not trade.'}.get(phase,'Manual handoff active.')
                        self.manual_handoff_note.set('Manual handoff: '+note)
                    else:self.manual_handoff_note.set('Manual handoff: Start, then wait for Ready before touching any client.')
                except (ValueError,OSError):pass
        from conquest.storage_halt import enforce
        storage_halted=enforce(self)
        from conquest.safe_reload import resume_after_embed
        if not self.closing and not storage_halted:resume_after_embed(self)
        if not self.closing and not storage_halted:
            self.refocus_if_farming()
        if (not self.closing and not storage_halted and self.observer is not None
                and not getattr(self.observer,'read_only_build',False)
                and Path(state_path('.runtime/account.dpapi')).exists()):
            self.reconnector.step(login_screen(self.observer.operations.target.hwnd))
        if self.host.saved and not self.host.is_alive():
            self.stop_observer()
            self.host.detach()
            self.client = None
            self.compact()
            self.refresh_client()
            self.record(state='Client closed')
            self.state_text.set('Client closed · farming Off')
            self.memory_text.set('Launch or select a client to reconnect')
        if self.runtime:
            data = self.runtime.snapshot()
            if (self.attachment.ready and self.reconnect_pending and not login_screen(self.observer.operations.target.hwnd)
                    and data.get('observations_available') and data.get('life')
                    and time.time()-data.get('observed_at',0)<1
                    and not data['life'].get('dead_candidate')):
                self.reconnect_pending=False
                if self.control.snapshot()['enabled'] and not (self.thread and self.thread.is_alive()):
                    self.start_embedded_farm()
            self.nearby.refresh(data['monsters'],data['control']['target_type_ids'],
                available=data.get('observations_available',False))
            self.render_matched_ids(data)
            if getattr(self.observer,'read_only_build',False) and not self.attachment.ready:
                self.state_text.set('Client embedded · observation only')
            else:
                self.state_text.set('Farming On · '+data['control']['execution_state'].replace('_',' ')
                    if data['control']['enabled'] else 'Client embedded · farming Off')
            self.memory_text.set(data['control']['note'] if data.get('observations_available') else
                data.get('observation_note','Waiting for the client'))
        while not self.messages.empty():
            event, fields = self.messages.get_nowait()
            from conquest.loot_audit import append as append_loot_audit
            try:
                if append_loot_audit(self.output,event,fields):
                    self.last['loot_audit_note']=None
            except (OSError,ValueError,TypeError) as error:
                # Diagnostics must never interrupt healing, input release or
                # processing the verified pickup itself.
                self.last['loot_audit_note']='Pickup audit unavailable: '+str(error)
            activity = {'attack_attempt':('Casting Scatter at nearby monsters' if fields.get('button')=='right'
                                         else 'Using single attacks at nearby monsters'),
                        'discarding_loot':'Dropping unwanted +0 loot',
                        'loot_discarded':'Unwanted loot dropped and ignored',
                        'loot_discard_unverified':'Uncertain discard skipped; continuing farming',
                        'loot_discard_deferred':'Optional inventory cleanup deferred; continuing farming',
                        'healing_attempt':'Using a healing potion','reload_attempt':'Reloading arrows',
                        'death_detected':'Dead — preparing to revive',
                        'revival_verified':'Revived — returning to the hunting area',
                        'memory_pickup_owned':"Skipping another player's loot",
                        'memory_pickup_attempt':('Picking up '+item_label(fields)) if event=='memory_pickup_attempt' else ''}
            if event in activity:
                self.last.update(activity=activity[event],activity_at=time.time())
            if event=='shop_panel_closed':
                self.last.update(activity=fields['activity'],activity_at=time.time())
            if event=='automation_work':
                self.record(automation_work=fields)
            elif event=='control_intent':
                self.record(control_intent=fields)
                if not fields['enabled']:self.record(kills_per_hour=0)
            elif event=='route_selected':
                route=self.route_library.load(fields['route_id'])
                self.saved_routes=saved_route_choices(self.route_library.all())
                self.route_picker.configure(values=[route_label(r) for r in self.saved_routes])
                self.route_selection_path.write_text(json.dumps({'route_id':route.id}),encoding='utf-8')
                self.display_route(route)
                if self.runtime.recovery:self.runtime.recovery.cancel()
                self.record()
            elif event=='reload_requested':
                if self.restart():
                    return False
            elif event=='reload_activity':
                self.state_text.set(fields['activity'])
                self.record(activity=fields['activity'])
            elif event=='reload_ready':
                self.reload_proof=fields
                if not self.reload_cancel.is_set() and self._restart_now():return False
                self.messages.put(('reload_failed',{'detail':'Safe handoff deferred; current app retained'}))
            elif event=='reload_failed':
                self.reload_preparing=False
                self.record(reload_preparing=False,activity='Reload deferred',reload_detail=fields['detail'])
                if self.reload_resume and not self.reload_cancel.is_set():
                    self.update_control({'enabled':True})
            elif event=='reconnect_requested':
                self.retry_reconnect()
            elif event=='reconnect_state':
                self.reconnect_pending=True
                self.record(reconnection=fields,state=('Reconnect needs attention' if fields['state']=='reconnect_exhausted' else
                    'Reconnecting' if fields['state']!='connection_restored' else 'Checking reconnected character'))
            elif event=='farm_requested':
                if self.control.snapshot()['enabled']:
                    self.start_embedded_farm()
            elif event=='farm_state':
                self.record(state=fields['note'])
            elif event=='focus_requested':
                if self.control.snapshot()['enabled'] and not self.mouse_priority.active():
                    self.show_game()
            elif event=='native_window_requested':
                self.change_native_window(fields['detached'])
            elif event == 'memory_loot_retry':
                self.record(loot_reader_note=fields['detail'])
            elif event in ('loot_discarded','loot_discard_deferred','loot_discard_unverified'):
                self.record(inventory_cleanup={'event':event,**fields,'timestamp':time.time()})
            elif event == 'memory_loot_ready':
                self.record(loot_reader_note=None)
            elif event == 'memory_loot_observed':
                self.record(loot_observation=fields)
            elif event == 'memory_pickup_verified':
                try:
                    pickup=self.pickup_history.add(fields)
                    if pickup is None:continue
                    self.pickup_tree.insert('',0,values=pickup_values(pickup))
                    for row in self.pickup_tree.get_children()[200:]:
                        self.pickup_tree.delete(row)
                except OSError as error:
                    self.detail_text.set(f'Could not save pickup history: {error}')
                self.record(pickups=fields['total'],last_pickup=fields,loot_reader_note=None)
            elif event in ('memory_pickup_attempt','memory_pickup_unverified','memory_pickup_owned','memory_pickup_approach'):
                self.record(pickup_status=event,pickup_detail=fields)
            elif event == 'patrol_expanded':
                self.record(patrol_search=fields)
            elif event == 'region_rotated':
                self.record(region_rotation=fields,activity=fields['activity'],activity_at=time.time())
            elif event == 'kill_verified':
                # The durable log is authoritative; a runner's total resets
                # during ordinary banking, recovery and action-limit rollover.
                pass
            elif event == 'experience_sample':
                self.record(experience=fields,experience_observed_at=time.time())
            elif event in ('runback_progress','runback_finished'):
                self.record(runback=fields)
            elif event in ('attack_strategy_changed','attack_strategy_recheck'):
                self.record(attack_strategy=fields,activity=fields['activity'],activity_at=time.time())
            elif event=='xp_skill_state':
                self.record(xp_skill=fields)
            elif event in ('xp_fly_attempt','xp_fly_verified'):
                self.record(xp_fly={'event':event,**fields},activity=fields['activity'],activity_at=time.time())
            elif event == 'attack_attempt':
                self.record(attempts=fields['number'], state='Farming')
            elif event == 'health_observation':
                self.record(health_ratio=fields['health_ratio'], ammo=fields['ammo'], position=fields['position'])
            elif event == 'boundary_return_started':
                self.record(navigation_blocked=False,state='Returning to hunting spot',boundary_return=fields)
            elif event == 'boundary_return_completed':
                self.record(state='Patrolling',boundary_return=None)
            elif event == 'movement_attempt':
                self.record(navigation_blocked=False)
                self.record(state='Travelling to hunting area' if fields.get('approaching') else 'Patrolling')
            elif event == 'navigation_wait':
                self.record(navigation_blocked=True,state=pause_message(fields.get('reason')))
            elif event == 'movement_recovery':
                self.record(navigation_blocked=True,state='Taking another path',
                            activity=fields.get('activity'),activity_at=time.time())
            elif event == 'navigation_resumed':
                self.record(navigation_blocked=False,state='Patrolling')
            elif event in ('paused','resumed'):
                self.record(state=pause_message(fields.get('reason')) if event == 'paused' else 'Running')
            elif event == 'calibration_verified':
                self.record(calibration=fields)
            elif event == 'window_geometry':
                self.record(window_geometry=fields)
            elif event in ('failed','finished'):
                self.record(state='Stopped', result=fields)
                self.detail_text.set(fields.get('detail') or fields.get('reason', 'Stopped'))
                if event == 'failed':
                    self.root.deiconify()
            self.state_text.set(self.last['state'])
            self.stats_text.set(farm_stats(self.last,self.control.snapshot()['enabled']))
        if not self.closing and time.monotonic()-self.telemetry_poll_at>=.5:
            self.telemetry_poll_at=time.monotonic()
            self.update_kill_metrics()
            try:
                self.route_status=json.loads(Path(state_path('reports/overnight/status.json')).read_text(encoding='utf-8'))
            except (OSError,ValueError):
                pass
            control=self.control.snapshot()
            self.stats_text.set(farm_stats(self.last,control['enabled']))
            if (self.attachment.ready and control['enabled'] and self.runtime and self.host.saved and self.selected_route
                    and time.monotonic()>=getattr(self,'controller_check_at',0)):
                self.controller_check_at=time.monotonic()+2
                from conquest.route_controller import ensure_running
                try:
                    fresh_start=(getattr(self,'_fresh_controller_start_revision',None)
                                 ==control['revision'])
                    if ensure_running(self.selected_route.id,fresh_start=fresh_start):
                        if fresh_start:self._fresh_controller_start_revision=None
                        self.record(route_controller='Restarting automatic route management')
                except (OSError,ValueError) as error:
                    self.record(route_controller_error=str(error))
            if (self.observer is not None and getattr(self.observer,'read_only_build',False)
                    and not self.attachment.ready):
                self.state_text.set('Client embedded · observation only')
                self.activity_text.set('Input, route and recovery automation disabled for this client version')
            else:
                life=self.runtime.snapshot().get('life') if self.runtime else None
                execution,activity=automation_status(self.route_status,self.last,control,life)
                self.state_text.set(execution)
                self.activity_text.set(activity)
                if self.last.get('automation_status')!=execution:
                    self.record(automation_status=execution)
                if self.last.get('current_activity') != self.activity_text.get():
                    self.record(current_activity=self.activity_text.get())
        if self.thread and not self.thread.is_alive():
            self.thread = None
            self.start_button.state(['!disabled'])
        if self.launch_watch.pending:
            candidate = self.launch_watch.poll()
            if not self.launch_watch.pending:
                self.launch_button.configure(text='Launch client')
            if candidate:
                self.embed(candidate)
            elif not self.launch_watch.pending:
                self.refresh_client()
                self.state_text.set(self.launch_watch.note)
                self.record(state=self.launch_watch.note)
        if self.closing and self.thread is None:
            try:
                self.stop_observer()
                self.host.detach()
                self.record(state='Closed')
                self.root.destroy()
                return False
            except Exception as error:
                self.closing = False
                self.state_text.set(f'Keep this app open: client restoration failed: {error}')
        if storage_halted:enforce(self)
        return True

    def close(self):
        if getattr(self,'unified',None):
            if self.unified.close() is False:
                return False
        self.launch_watch.cancel()
        self.stop()
        self.closing = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='profiles/desktop-foreground.local.yaml')
    parser.add_argument('--profile-id')
    parser.add_argument('--data-root')
    parser.add_argument('--manage-profiles',action='store_true')
    parser.add_argument('--migrate-from')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--start',action='store_true')
    action.add_argument('--calibrate',action='store_true')
    action.add_argument('--launch-client',action='store_true')
    action.add_argument('--embed-client',action='store_true')
    parser.add_argument('--client-pid',type=int)
    parser.add_argument('--client-started',type=int)
    parser.add_argument('--client-hwnd',type=int)
    args = parser.parse_args()
    selected = (args.client_pid,args.client_started,args.client_hwnd)
    if args.embed_client and any(value is None or value<=0 for value in selected):
        parser.error('--embed-client needs the selected process ID, creation time and HWND')
    if not args.embed_client and any(value is not None for value in selected):
        parser.error('Client identity arguments require --embed-client')
    use_unaware_dpi()
    root = tk.Tk()
    app = DesktopApp(root, args.profile)
    from conquest.merchants.ui import UnifiedUI
    try:
        app.unified = UnifiedUI(app)
        from conquest.merchants.alerts import ensure_monitor as ensure_shop_monitor
        ensure_shop_monitor()
    except (ValueError,OSError) as error:
        app.state_text.set(f'Merchant UI unavailable: {error}')
    if args.start or args.calibrate or args.launch_client or args.embed_client:
        def continue_action():
            if args.embed_client:
                try:app.embed(pinned_client(app.catalog,selected))
                except ValueError as error:app.state_text.set(str(error))
            elif args.launch_client:app.launch()
            else:app.start(args.calibrate)
        root.after(800,continue_action)
    root.mainloop()
    if getattr(app,'profile_editor_requested',None):
        import sys,subprocess
        from conquest.character_context import context_arguments
        args=context_arguments()
        if '--profile-id' in args:args[args.index('--profile-id')+1]=app.profile_editor_requested
        from conquest.application_layout import RuntimeLayout
        layout=RuntimeLayout.resolve()
        subprocess.Popen([str(layout.python(windowed=True)),str(layout.script('start_desktop_app.py')),
                          *args,'--manage-profiles'],cwd=layout.root,env=layout.environment(),
                         creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == '__main__':
    main()
