"""Explicit, one-time login and Market arrival; never enables merchant trading."""
import threading
import time
from pathlib import Path

from conquest.capture import CaptureUnavailable
from conquest.discord_notify import read_json,write_json
from conquest.merchants.journal import character_name
from conquest.merchants.recovery import credential_path

TERMINAL={'market','failed','cancelled'}


def save(runtime,character,phase,**fields):
    state=runtime.journal.get(character,'connect_market',{})
    state.update(phase=phase,updated_at=time.time(),**fields)
    runtime.journal.set(character,'connect_market',state)


def geometry(driver):
    """Qualify the shared pinned actor projection and current viewport twice."""
    from conquest.memory_life import CLIENT_SHA256,read_life
    from conquest.scene_input import memory_player_anchor
    from conquest.desktop_runtime import physical_coordinates
    observer=driver.observer
    if observer.adapter.expected_sha256!=CLIENT_SHA256:raise ValueError('Client build changed')
    observer.adapter.assert_identity()
    life=read_life(observer.adapter,observer.health_layout,observer.character)
    if life.dead_candidate or life.map_id not in (1002,1036):
        raise ValueError('Market travel requires a living merchant in Twin City or Market')
    anchor=memory_player_anchor(observer,life)
    gui=driver.memory.gui.viewport_size()
    with physical_coordinates():size=list(driver.target.snapshot()['client_size'])
    if (len(gui)!=2 or min(gui)<300 or min(size)<300
            or memory_player_anchor(observer,life)!=anchor or driver.memory.gui.viewport_size()!=gui):
        raise ValueError('Merchant projection or viewport changed during qualification')
    return {'client_size':size,'gui_size':gui}


def record_capability(driver,capability,evidence,*,dimensions=None):
    old=read_json(driver.qualification)
    if (old.get('client_sha256')!=driver.observer.adapter.expected_sha256
            or old.get('character')!=driver.observer.character):old={}
    old.update(client_sha256=driver.observer.adapter.expected_sha256,
               character=driver.observer.character,server='America')
    if dimensions:old.update(dimensions)
    old.setdefault('capabilities',{})[capability]=True
    if not isinstance(old.get('evidence',{}),dict):old['evidence']={'previous':old['evidence']}
    old.setdefault('evidence',{})[capability]=evidence
    write_json(driver.qualification,old)


def qualify_move(driver,travel,before,destination,check):
    """One checked movement trial; write qualification only after arrival."""
    candidate=geometry(driver)
    def verify():
        check()
        if geometry(driver)!=candidate:raise ValueError('Movement trial viewport changed')
        return candidate
    original=travel.qualify_movement
    travel.qualify_movement=verify
    try:after=travel.move(before,destination,check)
    finally:travel.qualify_movement=original
    if after['map_id']!=before['map_id'] or after['position']==before['position']:
        raise ValueError('Movement trial did not verify progress')
    record_capability(driver,'market_return',{'verified_at':time.time(),
        'identity':before['identity'],'map_id':before['map_id'],
        'before':before['position'],'after':after['position'],'source':'pinned actor projection and verified movement'},
        dimensions=candidate)
    return after


def select_client(ui,character,pid):
    if type(pid) is not int or pid<=0:raise ValueError('Select an exact open client PID')
    matches=[w for w in ui.runtime.catalog.windows() if w.identity['pid']==pid]
    if len(matches)!=1:raise ValueError('Selected client is absent or ambiguous')
    candidate=matches[0]
    observers=list(ui.runtime.observers.values())
    farmer=getattr(ui.app,'observer',None)
    if farmer:observers.append(farmer)
    if any(o.adapter.identity==candidate.identity for o in observers):
        raise ValueError('Selected client is already assigned; preserve its account')
    if character in ui.runtime.observers:raise ValueError('This merchant already has an attached client')
    return candidate


def run(ui,character,cancel,revision,selected=None):
    runtime=ui.runtime;guard=ui.coordinator
    runtime.connecting[character]=threading.get_ident()
    started=time.monotonic()
    def allowed():
        return (not cancel.is_set() and not guard.stopped and not ui.closed
                and ui.app.control.snapshot()['revision']==revision and ui.safe_to_yield())
    runtime.connect_checks[character]=allowed
    def check():
        import ctypes
        if any(ctypes.windll.user32.GetAsyncKeyState(key)&0x8000 for key in (0x7a,0x7b)):
            cancel.set()
        if not allowed():raise CaptureUnavailable('Market connection cancelled by manual control')
        guard.check()
        if time.monotonic()-started>600:raise ValueError('Market connection exceeded its bounded work time')
    try:
        check()
        if selected:
            from conquest.reconnect import login_screen
            from conquest.memory_life import read_life
            observer=runtime.observer_factory(selected,character)
            try:
                if not login_screen(selected.hwnd):read_life(observer.adapter,observer.health_layout,character)
                runtime.bind(character,observer)
            except Exception:
                observer.close();raise
            save(runtime,character,'client_found',launch_unresolved=False,
                 client_identity=selected.identity,client_hwnd=selected.hwnd)
        if character not in runtime.observers:
            try:runtime.attach(character)
            except ValueError:
                from conquest.client_wrapper import LaunchWatch
                launcher=Path(r'C:\Program Files\Classic Conquer 2.0\ImBootstrapper.exe')
                watch=LaunchWatch(runtime.catalog,[str(launcher)],cwd=launcher.parent)
                with guard.lease(character,purpose='connect_launch'):
                    check();save(runtime,character,'launching');watch.start()
                    save(runtime,character,'launching',launcher_pid=watch.process.pid,
                         launch_before=[list(key) for key in watch.before],launch_unresolved=True)
                while watch.pending:
                    check();candidate=watch.poll()
                    if candidate:
                        runtime.bind(character,runtime.observer_factory(candidate,character))
                        save(runtime,character,'client_found',launch_unresolved=False,
                             client_identity=candidate.identity,client_hwnd=candidate.hwnd)
                        break
                    time.sleep(.2)
                if character not in runtime.observers:raise ValueError('Launcher did not produce one unique client')
        driver=runtime.controllers[character].driver
        from conquest.reconnect import login_screen,submit_login
        submitted=login_screen(driver.target.hwnd)
        if submitted:
            with guard.lease(character,purpose='connect'):
                # Surface activation can deliver delayed pointer events. Wait
                # for the user's idle interval before the first login field.
                quiet=None;until=time.monotonic()+45
                while time.monotonic()<until:
                    if not allowed():raise CaptureUnavailable('Login cancelled by manual control')
                    if guard.manual_active():quiet=None
                    elif quiet is None:quiet=time.monotonic()
                    elif time.monotonic()-quiet>=.4:break
                    time.sleep(.1)
                else:raise CaptureUnavailable('Login deferred while the mouse is in use')
                check();save(runtime,character,'login_pending')
                try:submit_login(driver.target,credential_path(character),session=driver.observer.adapter)
                except Exception as error:
                    import traceback
                    frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
                            for f in traceback.extract_tb(error.__traceback__)]
                    save(runtime,character,'login_pending',login_failure={'type':type(error).__name__,'frames':frames,
                         'window':driver.target.snapshot()})
                    raise ValueError('Login submission failed; credentials were not logged') from None
            until=time.monotonic()+30
            while login_screen(driver.target.hwnd) and time.monotonic()<until:
                check();time.sleep(.2)
            if login_screen(driver.target.hwnd):raise ValueError('Login did not reach the game; no repeated submission')
        from conquest.memory_life import read_life
        until=time.monotonic()+15
        while True:
            check()
            try:life=read_life(driver.observer.adapter,driver.observer.health_layout,character);break
            except ValueError:
                if time.monotonic()>=until:raise
                time.sleep(.2)
        if submitted:
            record_capability(driver,'login',{'verified_at':time.time(),'identity':driver.observer.adapter.identity,
                'character':life.character,'map_id':life.map_id,'source':'memory-verified character after saved login'})
        save(runtime,character,'connected',identity=driver.observer.adapter.identity,map_id=life.map_id)
        from conquest.merchants.return_driver import ReturnDriver
        travel=ReturnDriver(driver,travel_only=True);stalls=0
        for _ in range(300):
            check()
            # Qualified recovery memory is permitted in both supported towns.
            before=travel.read()
            if before['map_id']==1036:
                runtime.journal.set(character,'connect_hold',True)
                save(runtime,character,'market',position=before['position'],verified_at=time.time())
                runtime.journal.event(character,'connect_market_verified',position=before['position'])
                return
            if before['map_id']!=1002:raise ValueError('Merchant is on an unsupported arrival map')
            if before.get('trade') or before.get('request'):raise ValueError('Open trade prevents Market travel')
            with guard.lease(character,purpose='connect'):
                check();before=travel.read()
                if max(abs(a-b) for a,b in zip(before['position'],(438,444)))>2:
                    save(runtime,character,'moving',position=before['position'])
                    try:travel.qualify_movement()
                    except ValueError:after=qualify_move(driver,travel,before,(438,444),check)
                    else:after=travel.move(before,(438,444),check)
                    stalls=stalls+1 if after['position']==before['position'] else 0
                    if stalls>=3:raise ValueError('Market approach stalled; no unbounded movement retry')
                else:
                    # A character already beside the Conductress still needs
                    # qualified live projection for the exact NPC/dialog input.
                    try:travel.qualify_movement()
                    except ValueError:
                        raise ValueError('Qualify a movement step before Conductress input')
                    records=travel.prepare_transfer(before,check)
                    save(runtime,character,'transfer_pending',silver_before=before['silver'],fare_pending=True)
                    travel.transfer(records,check)
                    until=time.monotonic()+10
                    while time.monotonic()<until:
                        check();after=travel.read()
                        if after['map_id']==1036:
                            if before['silver']-after['silver']!=100:
                                raise ValueError('Market fare needs reconciliation; no repeat payment')
                            save(runtime,character,'arrived',fare_pending=False)
                            break
                        time.sleep(.2)
                    else:raise ValueError('Market arrival uncertain; no repeated fare')
            time.sleep(.15)
        raise ValueError('Market route exhausted its bounded movement steps')
    except CaptureUnavailable as error:
        save(runtime,character,'cancelled',note=str(error))
    except Exception as error:
        note=str(error) if isinstance(error,(ValueError,OSError)) else 'Unexpected Market connection failure'
        save(runtime,character,'failed',note=note)
        runtime.journal.event(character,'connect_market_failed',note=note)
    finally:
        runtime.connecting.pop(character,None);runtime.connect_checks.pop(character,None)


def start(ui,character,*,client_pid=None):
    character=character_name(character);runtime=ui.runtime
    if not credential_path(character).exists():raise ValueError('Save this merchant login through the app first')
    if runtime.connecting:raise ValueError('Finish the current merchant connection first')
    if not ui.safe_to_yield() or ui.coordinator.owner or ui.coordinator.stopped:
        raise ValueError('Stop the farmer at a safe location before connecting merchants')
    if runtime.journal.pending(character):raise ValueError('Reconcile pending merchant transactions first')
    previous=runtime.journal.get(character,'connect_market',{})
    if previous.get('phase')=='transfer_pending' or previous.get('fare_pending'):
        raise ValueError('Previous Market fare needs reconciliation')
    selected=select_client(ui,character,client_pid) if client_pid is not None else None
    if previous.get('launch_unresolved') and selected is None:
        raise ValueError('Use the already-open launcher; reconcile its client before launching another')
    cancel=threading.Event();runtime.connect_cancel[character]=cancel
    runtime.connecting[character]=None
    runtime.journal.set(character,'connect_hold',True)
    save(runtime,character,'requested',note=None)
    thread=threading.Thread(target=run,args=(ui,character,cancel,ui.app.control.snapshot()['revision'],selected),
                            daemon=True,name='connect-market-'+character)
    ui.connect_threads[character]=thread;thread.start()
    return {'requested':character,'trading_enabled':False}
