"""Restore hidden merchant hosts without stealing the farmer's input."""
from conquest.merchants.journal import CHARACTERS


def _farmer_health_cache(ui):
    """Fetch farmer health off the Tk poll thread, sharing a short-lived read."""
    import os
    import threading
    import time
    from pathlib import Path

    from conquest.character_context import state_path
    from conquest.worker import request

    lock=getattr(ui,'_readonly_farmer_health_lock',None)
    if lock is None:
        lock=threading.Lock()
        ui._readonly_farmer_health_lock=lock
        ui._readonly_farmer_health_cache={'pending':False,'requested_at':0.0,'health':None}
    cache=ui._readonly_farmer_health_cache
    now=time.monotonic()
    with lock:
        if not cache['pending'] and now-cache['requested_at']>=0.5:
            cache['pending']=True
            cache['requested_at']=now
            info=Path(state_path('.runtime')) / f'embedded-worker-{os.getpid()}.json'

            def read_health():
                try:
                    health=request(info,'health')
                except Exception:
                    health=None
                with lock:
                    cache['health']=health
                    cache['pending']=False

            threading.Thread(target=read_health,name='readonly-farmer-health',daemon=True).start()
        return cache['health']


def _readonly_market_hosting_safe(ui):
    """Require a fresh, stopped farmer baseline before hosting 1078 HWNDs."""
    import time

    from conquest.character_context import state_path
    from conquest.discord_notify import process_alive, read_json
    from conquest.merchants.background_probe import probe_busy

    app, runtime, coordinator = ui.app, ui.runtime, ui.coordinator
    control = app.control.snapshot()
    fence = getattr(ui, 'grant_fence', None)
    if (ui.closed or app.closing or app.thread and app.thread.is_alive()
            or control.get('enabled') is not False or control.get('paused')
            or not ui.safe_to_yield() or app.mouse_priority.active()
            or coordinator.owner or coordinator.manual_active()
            or runtime.manual_handoff_status() is not None
            or ui.grant is not None or fence and (fence.active is not None
                or fence.actions or fence.workers)
            or any(coordinator.manual_session_blocked(c) for c in ('Farmer', *CHARACTERS))
            or probe_busy(ui)
            or runtime.connecting or runtime.refilling
            or getattr(runtime, 'delivery_window', None)
            or getattr(runtime, 'refill_window', None)
            or ui.calibrating):
        return False

    farmer = getattr(app, 'observer', None)
    if farmer is None:
        return False
    try:
        farmer.adapter.assert_identity()
        route = read_json(state_path('reports/overnight/status.json'))
        route_pid = route.get('pid')
        if (route.get('phase') not in ('stopped', 'completed', 'failed')
                or type(route_pid) is not int or process_alive(route_pid) is not False):
            return False
        health = _farmer_health_cache(ui)
        if health is None:
            return False
        controls = health.get('embedded_controls') or {}
        life = controls.get('life') or {}
        if (health.get('profile_id') != runtime.manual_target('Farmer')
                or health.get('target') != farmer.adapter.identity
                or life.get('map_id') != 1036
                or life.get('dead_candidate') is not False
                or type(life.get('current_hp')) is not int or life['current_hp'] <= 0
                or controls.get('control', {}).get('enabled') is not False
                or controls.get('manual_mouse')
                or controls.get('manual_input_fence')
                or controls.get('external_execution') is not False
                or type(controls.get('observed_at')) not in (int, float)
                or not 0 <= time.time() - controls['observed_at'] <= 1):
            return False

        # Include farmer-side holds and unresolved durable merchant receipts.
        from conquest.merchants.booth_probe_1078 import _farmer_journals_clear
        _farmer_journals_clear(runtime)
        with runtime.journal.db() as db:
            pending = db.execute(
                "SELECT 1 FROM transactions WHERE phase NOT IN "
                "('verified','aborted','operator_overridden') LIMIT 1").fetchone()
        if pending:
            return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


def restore(ui, *, host_factory=None):
    if ui.closed or ui.coordinator.owner or ui.calibrating: return
    if not ui.coordinator.lock.acquire(blocking=False): return
    try:
        if ui.coordinator.owner: return
        if host_factory is None:
            from conquest.window_host import EmbeddedWindow
            host_factory=lambda:EmbeddedWindow(mode='owned')
        for character in CHARACTERS:
            if character in ui.released_clients: continue
            pane=ui.client_panes[character]
            host=ui.hosts.get(character)
            if host and host.saved: continue
            observer=ui.runtime.observers.get(character)
            if observer is None: continue
            if getattr(observer,'merchant_observation_only',False):
                # Read-only hosting is a no-activation geometry operation. It
                # must not select a tab or create an input-capable surface.
                if not _readonly_market_hosting_safe(ui): continue
                try:
                    observer.adapter.assert_identity()
                    if host is None:
                        host=host_factory()
                        ui.hosts[character]=host
                    if host.mode!='owned' or host.api.gui.IsIconic(observer.hwnd):
                        continue
                    if host.saved:
                        continue
                    size=(pane.winfo_width(),pane.winfo_height())
                    from conquest.client_attachment import require_viewport
                    require_viewport(*size)
                    host.attach(observer.hwnd,observer.adapter.identity,pane.winfo_id(),*size)
                    observer.adapter.assert_identity()
                    ui.coordinator.surface_blocks[character]=True
                    ui.layout_status[character]={'attached':True,'native_visible':False,
                        'selected':False,'auto_read_only_host':True}
                    status=getattr(ui.runtime,'attachments',{}).get(character)
                    if status:
                        status.attached=True
                        status.enter('memory')
                        status.observation_ready=True
                        ui.runtime.journal.set(character,'attachment',status.snapshot())
                except (ValueError,OSError):
                    continue
                continue
            # Showing/selecting a tab remains the existing, guarded UI path.
            if pane.winfo_ismapped(): continue
            try:
                observer.adapter.assert_identity()
                host=host or host_factory()
                ui.hosts[character]=host
                host.attach(observer.operations.target.hwnd,observer.adapter.identity,pane.winfo_id(),
                    max(1,pane.winfo_width()),max(1,pane.winfo_height()))
                ui.layout_status[character]={'attached':True,'native_visible':False,'selected':False}
                ui.calibration_results[character]={'verified':False,
                    'note':'Client embedded; input geometry is checked when its tab opens'}
            except (ValueError,OSError) as error:
                ui.calibration_results[character]={'verified':False,'note':str(error)}
    finally:
        ui.coordinator.lock.release()
