"""Bounded localhost diagnostics owned by the embedded client's lifetime."""
import hmac
import math
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
import traceback


class EmbeddedBridge:
    # Reuse the existing worker protocol without exposing foreground input,
    # process launching, arbitrary execution, or memory writes.
    allowed = {'reconnect-retry','health','sample','sample-npcs','town','read-block','inspect-object','background-click','controls','reload-app','revive-click','native-window-mode','foreground-click','foreground-key','foreground-drag','route-jump'}

    def __init__(self, operations, lock, info_path, snapshot, lifetime=43200,*,control_update=None,on_reload=None,on_native_window=None,on_route_jump=None,on_sample_npcs=None,on_town=None,read_only=False):
        self.operations,self.lock = operations,lock
        self.read_only=bool(read_only)
        if self.read_only:
            self.allowed={'health','sample','read-block','inspect-object'}
        from conquest.character_context import current
        self.character_context=current()
        self.info_path = Path(info_path)
        self.snapshot = snapshot
        self.control_update,self.on_reload = control_update,on_reload
        self.on_native_window,self.native_probe_mode=on_native_window,False
        self.on_route_jump=on_route_jump
        self.on_sample_npcs=on_sample_npcs
        self.on_town=on_town
        self.on_reconnect=None
        self.stop = threading.Event()
        self.deadline = float('inf') if lifetime is None else time.monotonic()+lifetime
        self.token = secrets.token_hex(32)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self,*args): pass

            def do_POST(self):
                self.close_connection = True
                try:
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0<size<=65536:
                        raise ValueError('Invalid request size')
                    payload = self.rfile.read(size)
                    if not hmac.compare_digest(self.headers.get('X-Conquest-Token',''),bridge.token):
                        self.send_error(403)
                        return
                    body = json.loads(payload)
                    if not isinstance(body,dict):
                        raise ValueError('Expected an object')
                    if 'profile_id' in body:
                        if bridge.character_context is None or body.pop('profile_id')!=bridge.character_context.profile.id:
                            raise ValueError('This bridge belongs to a different character profile')
                    operation = self.path.strip('/')
                    if operation not in bridge.allowed:
                        raise ValueError('Operation is not available in the embedded bridge')
                    if bridge.stop.is_set() or time.monotonic()>=bridge.deadline:
                        raise ValueError('Embedded connection has stopped')
                    with bridge.lock:
                        if bridge.stop.is_set():
                            raise ValueError('Client is being released')
                        if operation in ('foreground-click','foreground-key','foreground-drag') and not bridge.native_probe_mode:
                            raise ValueError('Operation is not available outside a native-window diagnostic')
                        if operation in ('background-click','revive-click','foreground-click','foreground-key','foreground-drag','route-jump'):
                            expiry = body.get('expires_at')
                            if type(expiry) not in (int,float) or not math.isfinite(expiry) or not 0<expiry-time.time()<=5:
                                raise ValueError('A background diagnostic must expire within five seconds')
                            if bridge.snapshot()['control']['enabled']:
                                raise ValueError('Stop farming before an input diagnostic')
                            if operation in ('foreground-click','foreground-key','foreground-drag'):
                                window=bridge.operations.target.snapshot()
                                if window['root_hwnd']!=window['hwnd']:
                                    raise ValueError('Wait for the native client window before probing')
                        from conquest.mouse_priority import active, MESSAGE
                        if operation=='town' and body.get('action') not in ('supplies','shop','gear','vendor-status','ground-items','service-locate','service-dialog','warehouse-items','warehouse-reconcile-scroll') and active():
                            from conquest.town_trade import TownObservationUnavailable
                            raise TownObservationUnavailable(MESSAGE)
                        if operation=='town':
                            if bridge.on_town is None:
                                raise ValueError('Town actions are unavailable')
                            if body.get('action') not in ('supplies','shop','gear','vendor-status','ground-items','service-locate','service-dialog','warehouse-items','warehouse-reconcile-scroll'):
                                expiry = body.get('expires_at')
                                if type(expiry) not in (int,float) or not math.isfinite(expiry) or not 0<expiry-time.time()<=5:
                                    raise ValueError('Town input must expire within five seconds')
                                if bridge.snapshot()['control']['enabled']:
                                    raise ValueError('Stop farming before town input')
                            read_only=body.get('action') in ('supplies','shop','gear','vendor-status','ground-items','service-locate','service-dialog','warehouse-items','warehouse-reconcile-scroll')
                            town=bridge.on_town
                            previous=getattr(town,'check_input',None)
                            revision=bridge.snapshot()['control'].get('revision') if not read_only else None
                            def check_town_input():
                                from conquest.capture import CaptureUnavailable
                                current=bridge.snapshot()['control']
                                if (bridge.stop.is_set() or time.time()>=body['expires_at'] or type(revision) is not int
                                        or current.get('revision')!=revision or current['enabled'] or current.get('paused')
                                        or active()):
                                    raise CaptureUnavailable('Town input permission changed; reobserve before continuing')
                                if previous:previous()
                            attach_guard=not read_only and hasattr(town,'__dict__')
                            try:
                                if attach_guard:town.check_input=check_town_input
                                result = town({k:v for k,v in body.items() if k!='expires_at'})
                            finally:
                                if attach_guard:town.check_input=previous
                        elif operation=='sample-npcs':
                            if body or bridge.on_sample_npcs is None:
                                raise ValueError('Vendor reader is unavailable or has unsupported arguments')
                            result = bridge.on_sample_npcs()
                        elif operation=='route-jump':
                            if bridge.on_route_jump is None:
                                raise ValueError('Route jumping is unavailable')
                            result=bridge.on_route_jump(body)
                        elif operation=='controls':
                            if bridge.control_update is None:
                                raise ValueError('Control updates are unavailable')
                            result = bridge.control_update(body)
                        elif operation=='native-window-mode':
                            if set(body)!={'detached'} or type(body['detached']) is not bool or bridge.on_native_window is None:
                                raise ValueError('Native window mode is unavailable or invalid')
                            if body['detached'] and bridge.snapshot()['control']['enabled']:
                                raise ValueError('Stop farming before detaching the client')
                            bridge.on_native_window(body['detached'])
                            result={'window_change_queued':True}
                        elif operation=='reconnect-retry':
                            if body or bridge.on_reconnect is None:
                                raise ValueError('Reconnect retry is unavailable or has unsupported arguments')
                            bridge.on_reconnect()
                            result={'reconnect_queued':True}
                        elif operation=='reload-app':
                            if body or bridge.on_reload is None:
                                raise ValueError('App reload is unavailable or has unsupported arguments')
                            # The app callback queues a protected safe-spot handoff.
                            # Keep farming intent until that coordinator owns input.
                            bridge.on_reload()
                            result = {'reload_queued':True}
                        elif operation=='health':
                            result = bridge.health()
                        else:
                            result = bridge.operations.dispatch(operation,body)
                    status = 200
                except (ValueError,OSError,KeyError,TypeError) as error:
                    result,status = {'error':str(error)},400
                    from conquest.capture import CaptureUnavailable
                    if self.path.strip('/')=='route-jump' and isinstance(error,CaptureUnavailable):
                        result['code']='foreground_unavailable'
                    from conquest.merchants.coordination import InputAcquisitionBusy
                    if isinstance(error,InputAcquisitionBusy):
                        result['code']='input_acquisition_busy'
                    if getattr(error,'code',None) == 'town_observation_unavailable':
                        result['code'] = error.code
                except Exception as error:
                    # A bridge-side crash can occur after an input callback has
                    # started.  Do not expose exception text (which can carry
                    # request data), and never classify this as pre-input.
                    frames = traceback.extract_tb(error.__traceback__)[-6:]
                    result,status = {
                        'error':'Unexpected embedded bridge error; input outcome is uncertain',
                        'code':'input_outcome_uncertain',
                        'type':type(error).__name__,
                        'frames':[{'file':Path(frame.filename).name,
                                   'function':frame.name,'line':frame.lineno}
                                  for frame in frames],
                    },400
                encoded = json.dumps(result).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        if self.info_path.exists():
            raise ValueError('An embedded connection file already exists; check its owner first')
        self.server = HTTPServer(('127.0.0.1',0),Handler)
        self.server.timeout = .2
        self.thread = threading.Thread(target=self.run,name='embedded-diagnostics',daemon=True)
        try:
            self.info_path.parent.mkdir(parents=True,exist_ok=True)
            self.info_path.write_text(json.dumps({'port':self.server.server_port,'token':self.token,
                'target_pid':operations.session.pid,'expires_at':None if lifetime is None else time.time()+lifetime}),encoding='utf-8')
            self.thread.start()
        except Exception:
            self.server.server_close()
            raise

    def health(self):
        """The same fresh, locked observation for localhost and in-process callers."""
        with self.lock:
            if self.stop.is_set() or time.monotonic()>=self.deadline:
                raise ValueError('Embedded connection has stopped')
            result = self.operations.dispatch('health',{})
            if self.character_context:
                result['profile_id'] = self.character_context.profile.id
            result['embedded_controls'] = self.snapshot()
            from conquest.mouse_priority import active,mark_observation_gap
            mouse_busy = active()
            cursor_gap = result.get('window', {}).get('cursor_available') is False
            if cursor_gap:mark_observation_gap()
            result['embedded_controls']['manual_mouse'] = mouse_busy or cursor_gap
            from conquest.merchants.coordination import manual_session_blocked
            result['embedded_controls']['manual_input_fence'] = manual_session_blocked('Farmer')
            result['window_mode'] = getattr(self,'window_mode','unknown')
            return result

    def sync_window_mode(self,host):
        # Owned embedding is still a native top-level client. Update only after
        # the UI applied the change, not from the requested detached boolean.
        with self.lock:
            self.native_probe_mode=not host.saved or host.mode=='owned'
            self.window_mode=host.mode if host.saved else 'detached'

    def run(self):
        try:
            while not self.stop.is_set() and time.monotonic()<self.deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()
            self.info_path.unlink(missing_ok=True)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('Waiting for an embedded diagnostic to finish')
