"""Opt-in, bounded background diagnostics. No production input routing."""
from conquest.character_context import state_path
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
import struct
from pathlib import Path
import threading
import time
import uuid

from conquest.win32 import bind


MODES = ('observe', 'post-hover', 'send-hover', 'cancel-hover',
         'park-observe', 'park-post-hover', 'park-send-hover', 'park-cancel-hover',
         'barrier-hover', 'barrier-burst-hover', 'park-barrier-hover', 'park-barrier-burst-hover',
         'park-barrier-click', 'park-ctrl', 'park-drag-cancel', 'park-price-cancel')

# Qualification stopped after an interrupted drag left its source item missing.
# Keep diagnostic implementations reviewable, but expose no live input bypass.
READ_ONLY_MODES = ('observe', 'park-observe')


def validate_live_mode(mode):
    if mode not in MODES:
        raise ValueError('Unknown background diagnostic mode')
    if mode not in READ_ONLY_MODES:
        raise ValueError('Background input qualification failed interrupted-drag reconciliation; live input experiments are disabled')


def code_evidence(adapter, base):
    """Read-only code/window-handler provenance; no text buffers or secrets."""
    data = Path(state_path('reports/merchant-image.bin')).read_bytes()
    ranges = ((0x398e0,0x710),(0xc0220,0x380),(0xf160,0x144),
              (0x1eeb0,0x4e0),(0x4c675,0x1c6),(0xd36a0,0x140),(0x1bf2b0,0x400))
    matches = {hex(start):adapter.read_block(base+start,size)==data[start:start+size]
               for start,size in ranges}
    head,count = struct.unpack('<QQ',adapter.read_block(base+0x6986c0,16))
    if not 0 < count <= 128:
        raise ValueError('Invalid window-handler registry')
    root = struct.unpack('<Q',adapter.read_block(head+8,8))[0]
    stack,seen,handlers = [root],set(),[]
    while stack:
        ptr = stack.pop()
        if ptr == head:continue
        if ptr in seen or len(seen)>=count:raise ValueError('Unstable window-handler registry')
        seen.add(ptr)
        row = adapter.read_block(ptr,56)
        left,right = struct.unpack_from('<Q',row)[0],struct.unpack_from('<Q',row,16)[0]
        key,model = struct.unpack_from('<I',row,32)[0],struct.unpack_from('<Q',row,40)[0]
        vtable = struct.unpack('<Q',adapter.read_block(model,8))[0]
        handler = struct.unpack('<Q',adapter.read_block(vtable+0x28,8))[0]
        handlers.append({'key':key,'vtable_rva':hex(vtable-base),'handler_rva':hex(handler-base)})
        rva = handler-base
        if not 0 <= rva < len(data)-0x800:
            raise ValueError('Window-handler code lies outside inspected module')
        matches[hex(rva)] = adapter.read_block(handler,0x800)==data[rva:rva+0x800]
        stack.extend((left,right))
    if len(seen)!=count or adapter.read_block(base+0x6986c0,16)!=struct.pack('<QQ',head,count):
        raise ValueError('Window-handler registry changed during observation')
    return {'code_matches':matches,'game_state':struct.unpack('<I',adapter.read_block(base+0x663b4c,4))[0],
            'window_handlers':handlers}


def probe_busy(ui):
    return (getattr(ui, 'background_probe', {}).get('state') in ('running', 'restoring')
            or bool(getattr(ui, 'background_surfaces', {})))


def isolated_ctrl_probe(target, adapter, identity, reader, check, sample, samples, report,
                        *, scope_factory=None, clock=time.monotonic, sleep=time.sleep):
    """Release attempted Ctrl even if neutralization fails before normal key-up."""
    from copy import deepcopy
    from conquest.background_keyboard import BackgroundControlScope
    identity = deepcopy(identity)
    scope_factory = scope_factory or BackgroundControlScope
    attempted = False
    scope = None

    def key_up():
        # Cleanup intentionally bypasses cancellation/focus guards, but never
        # sends a release into a replaced process or reused HWND.
        adapter.assert_identity()
        if adapter.identity != identity:
            raise ValueError('Ctrl cleanup target process identity changed')
        target.post(0x101, 0x11, 0xc01d0001)

    def verify_release():
        deadline = clock()+.45
        while clock()<deadline:
            state = reader.snapshot()
            if not state['modifiers']['ctrl'] and not state['queue']['size']:
                report['ctrl_release_verified'] = True
                return
            sleep(.005)
        raise ValueError('Ctrl release was not verified')

    try:
        scope = scope_factory(target.hwnd, identity, hold_seconds=1.5)
        with scope:
            try:
                check(); scope.check()
                attempted = True
                report['key_messages_sent'] = True
                target.post(0x100, 0x11, 0x001d0001)
                deadline = clock()+.45
                while clock()<deadline:
                    scope.check(); sample('ctrl-down')
                    if samples[-1]['gui']['modifiers']['ctrl']:
                        report['ctrl_verified'] = True
                        break
                    sleep(.005)
                if not report.get('ctrl_verified'):
                    raise ValueError('Target did not acknowledge isolated Ctrl state')
            finally:
                scope.neutralize()
                if attempted:
                    key_up()
                    verify_release()
        report['helper_detached'] = True
    finally:
        if attempted and not report.get('ctrl_release_verified'):
            # The with block has now exited. Explicitly finish cleanup once
            # more before releasing ImGui's queued Ctrl state after an error.
            # A failed neutralize ACK must not skip this key-up path.
            try:
                scope.close()
                process = getattr(scope, 'process', None)
                if process is not None and process.poll() is None:
                    raise ValueError('Ctrl helper still running; release table state is unknown')
                report['helper_exited_before_fallback_release'] = True
                key_up()
                report['ctrl_fallback_keyup_sent'] = True
                verify_release()
            except Exception as cleanup_error:
                # Preserve the original failure. The outer completion barrier
                # will stop automation if any key/input remains uncertain.
                report['ctrl_fallback_cleanup'] = {'verified': False,
                    'error_type': type(cleanup_error).__name__}


class BackgroundCleanupRequired(ValueError):
    def __init__(self, message, report):
        super().__init__(message)
        self.probe_report = report


def settle_input(reader, *, include_keys=False, timeout=2,
                 clock=time.monotonic, sleep=time.sleep):
    """Read-only completion barrier; neutral old state is not an acknowledgement."""
    deadline, frames, previous, initial = clock()+timeout, [], None, None
    while clock() < deadline:
        state = reader.snapshot()
        frame = state['frame']
        if initial is None:
            initial = frame
        if previous is not None and frame < previous:
            raise ValueError('GUI frame counter reset during input cleanup')
        previous = frame
        neutral = (not state['queue']['size'] and not state['backend']['buttons_down']
                   and not any(state['mouse_down']) and not state['active']['id']
                   and not any(state['modifiers'].values()) and not state.get('key_mods',0))
        if include_keys:
            keys = reader.session.read_block(state['context']+0xe04,0x285*16)
            if len(keys) != 0x285*16:
                raise ValueError('Incomplete keyboard cleanup observation')
            neutral = neutral and not any(keys[::16])
        if neutral:
            if frame > initial and (not frames or frames[-1] != frame):
                frames.append(frame)
            if len(frames) >= 2:
                return {'settled':True,'frames':frames[-2:],'queue_drained':True}
        else:
            frames.clear()
        sleep(.01)
    raise ValueError('Pending background input did not settle across two fresh frames')


def reconciliation_read(driver, *, timeout=.5, clock=time.monotonic, sleep=time.sleep):
    """Retry only render races; never repeat input or hide identity/read failures."""
    from conquest.merchants.memory import GuiObservationChanged
    deadline, attempts = clock()+timeout, 0
    while True:
        attempts += 1
        try:
            return driver.read(), attempts
        except ValueError as error:
            transient = isinstance(error, GuiObservationChanged) or str(error)=='Invalid GUI geometry'
            remaining = deadline-clock()
            if not transient or remaining <= 0:
                raise
            sleep(min(.025,remaining))


def cleanup_probe(ui, character, report, reader, driver, before):
    """Runs even after cancellation; never sends input or restores focus."""
    from conquest.merchants.qualification import stock
    unsettled = None
    sent = any(report.get(k) for k in ('input_messages_sent','button_messages_sent','key_messages_sent'))
    if sent:
        try:
            report['input_cleanup'] = settle_input(reader,include_keys=bool(report.get('key_messages_sent')))
            report['queue_drained'] = True
            if report.get('button_messages_sent'):
                report['release_verified'] = True
        except Exception as error:
            unsettled = str(error) if isinstance(error,(ValueError,OSError)) else type(error).__name__
            report['input_cleanup'] = {'settled':False,'error':unsettled}
            report['release_verified'] = False
            ui.coordinator.stop()  # An uncertain queued input must not be reused by production.
    try:
        after, attempts = reconciliation_read(driver)
        report['stock_unchanged'] = stock(before) == stock(after)
        report['position_unchanged'] = before['position'] == after['position']
        report['final_reconciliation'] = {'available':True,'read_attempts':attempts,
            'stock_unchanged':report['stock_unchanged'],'position_unchanged':report['position_unchanged']}
    except Exception as error:
        report['stock_unchanged'] = report['position_unchanged'] = None
        report['final_reconciliation'] = {'available':False,
            'error':str(error) if isinstance(error,(ValueError,OSError)) else type(error).__name__}
    changed = report['stock_unchanged'] is not True or report['position_unchanged'] is not True
    if unsettled or changed:
        note = unsettled or 'Final background diagnostic stock/position verification requires attention'
        report['outcome'] = 'stopped'
        report['cleanup_error'] = note
        try:
            ui.runtime.journal.set(character,'attention',{'kind':'background_input_cleanup','note':note})
        except Exception:
            report['attention_persistence_error'] = True
    elif sent:
        try:
            if (ui.runtime.journal.get(character,'attention') or {}).get('kind')=='background_input_cleanup':
                ui.runtime.journal.set(character,'attention',None)
        except Exception:
            report['attention_persistence_error'] = True
    if unsettled:
        raise BackgroundCleanupRequired(unsettled,report)


def restore_surface(surface):
    callback = getattr(surface,'before_restore',None)
    if callback:
        callback()
    surface.restore()


@contextmanager
def restoration_lease(ui):
    """Window recovery honors external input owners, including while stopped."""
    coordinator = ui.coordinator
    if not coordinator.lock.acquire(blocking=False):
        raise ValueError('Another input action is running; restoration deferred')
    file, owned = None, False
    try:
        if coordinator.owner:
            raise ValueError('Another input action owns the client; restoration deferred')
        import msvcrt
        coordinator.path.parent.mkdir(parents=True,exist_ok=True)
        file = coordinator.path.open('a+b')
        file.seek(0,2)
        if file.tell()==0:
            file.write(b'0');file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
        coordinator.owner,coordinator.thread = 'Background restoration',threading.get_ident()
        owned = True
        yield
    finally:
        if owned:
            coordinator.owner = coordinator.thread = None
        if file:
            file.close()
        coordinator.lock.release()


@contextmanager
def reserved_surface(target, identity, mode, *, ui=None, character=None):
    surface = None
    try:
        if mode.startswith('park-'):
            from conquest.background_surface import BackgroundSurface
            surface = BackgroundSurface()
            if ui is not None:
                ui.background_surfaces[character] = surface
            surface.park(target.hwnd, identity, expected_size=target.snapshot()['client_size'])
        yield surface
    finally:
        if surface is not None:
            try:
                restore_surface(surface)
            finally:
                # A failed restoration must retain its exact original state.
                # Do not allow ordinary embedding to replace the saved owner.
                if ui is not None and surface.saved is None:
                    ui.background_surfaces.pop(character, None)


def start_surface_restore(ui, *, on_complete=None):
    """Retry retained window recovery off Tk, with no gameplay input."""
    if getattr(ui, 'background_probe', {}).get('state') in ('running', 'restoring'):
        ui.background_cancel.set()
        raise ValueError('Background diagnostic is stopping; wait for client restoration')
    if not getattr(ui, 'background_surfaces', {}):
        if on_complete:
            ui.ui_requests.put((on_complete, None, {}))
        return {'restored': True}
    ui.background_probe = {**ui.background_probe, 'state': 'restoring'}
    def work():
        error = None
        try:
            with restoration_lease(ui):
                for character, surface in list(ui.background_surfaces.items()):
                    restore_surface(surface)
                    ui.background_surfaces.pop(character, None)
        except Exception as failure:
            error = str(failure) if isinstance(failure, (ValueError, OSError)) else type(failure).__name__
        ui.background_probe = {**ui.background_probe,
            'state': 'restoration_required' if ui.background_surfaces else 'complete',
            'restoration_error': error}
        if not ui.background_surfaces and on_complete:
            ui.ui_requests.put((on_complete, None, {}))
    thread = threading.Thread(target=work, name='background-surface-restore', daemon=True)
    ui.background_thread = thread
    thread.start()
    return {'restoration_requested': True}


@contextmanager
def diagnostic_lease(ui, character):
    """Exclude existing input without invoking foreground handoff callbacks."""
    coordinator = ui.coordinator
    if not coordinator.lock.acquire(blocking=False):
        raise ValueError('Another input action is running')
    file = None
    owned = False
    try:
        if (coordinator.owner or coordinator.stopped or not ui.safe_to_yield()
                or ui.calibrating or ui.runtime.refilling
                or any(ui.runtime.enabled(c) or ui.runtime.journal.pending(c)
                       for c in ui.runtime.controllers)):
            raise ValueError('Background diagnostics require idle, paused operations and reconciled stock')
        import msvcrt
        coordinator.path.parent.mkdir(parents=True, exist_ok=True)
        file = coordinator.path.open('a+b')
        if file.tell() == 0:
            file.write(b'0'); file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        coordinator.owner, coordinator.thread = character, threading.get_ident()
        owned = True
        yield
    finally:
        if owned:
            coordinator.owner = coordinator.thread = None
        if file:
            file.close()
        coordinator.lock.release()


def run_probe(ui, character, mode, cancel):
    validate_live_mode(mode)
    import win32gui as gui
    import win32process
    import os
    from conquest.window_host import HostApi
    native_host = HostApi()
    app_windows = []
    def collect_app_window(hwnd, _):
        if (win32process.GetWindowThreadProcessId(hwnd)[1]==os.getpid()
                and gui.GetClassName(hwnd)=='TkTopLevel'
                and gui.GetWindowText(hwnd).startswith('Conquest')):
            app_windows.append(hwnd)
    gui.EnumWindows(collect_app_window,None)
    if len(app_windows)!=1:
        raise ValueError('Conquest native window identity is ambiguous')
    conquest_hwnd = app_windows[0]
    from conquest.merchants.background_observation import BackgroundObservationReader
    from conquest.merchants.qualification import stock
    from conquest.merchants.memory import GuiReader
    observer = ui.runtime.observers[character]
    driver = ui.runtime.controllers[character].driver
    target = driver.target
    with diagnostic_lease(ui, character), observer.lock, reserved_surface(target,observer.adapter.identity,mode,ui=ui,character=character) as surface:
        before = driver.read()
        if before.get('request') or before.get('trade'):
            raise ValueError('Trade must finish before background diagnostics')
        original = target.snapshot()
        identity = observer.adapter.identity
        owner = gui.GetWindow(target.hwnd, 4)
        reader = BackgroundObservationReader(observer.adapter)
        viewport = GuiReader(observer.adapter).viewport_size()
        windows = [w for w in before['windows'] if w['name'] == 'Inventory/##ItemGrid_A800F95C']
        if len(windows) != 1:
            raise ValueError('Expected one memory-identified inventory grid')
        window = windows[0]
        table = driver.memory.gui.table(window, '##ItemTable')
        left, top, right, bottom = table['clip']
        logical = (table['columns'][0]['content_x']+20, table['outer'][1]+20)
        if not left+2 < logical[0] < right-2 or not top+2 < logical[1] < bottom-2:
            raise ValueError('Inventory hover target is clipped')
        point = tuple(round(v*n/g) for v,n,g in zip(logical, original['client_size'], viewport))
        if not all(0 <= p < min(n, 32768) for p,n in zip(point, original['client_size'])):
            raise ValueError('Invalid background target coordinates')
        packed = point[0] | point[1] << 16
        samples = []
        started = time.monotonic()
        def check():
            if cancel.is_set() or ui.closed or ui.coordinator.stopped:
                raise ValueError('Background diagnostic stopped')
            observer.adapter.assert_identity()
            if surface is not None:
                surface.check()
            current = target.snapshot()
            if (current['client_size'] != original['client_size'] or current['minimized']
                    or observer.adapter.identity != identity or gui.GetWindow(target.hwnd,4) != owner):
                raise ValueError('Diagnostic target changed')
            if current['foreground'] in (target.hwnd, owner, conquest_hwnd):
                raise ValueError('User activated the game or Conquest; background diagnostic stopped')
            if gui.GetAncestor(gui.WindowFromPoint(tuple(current['cursor'])),2) == target.hwnd:
                raise ValueError('Physical pointer reached the target game; diagnostic stopped')
            current['input_threads'] = {}
            if win32process.GetWindowThreadProcessId(conquest_hwnd)[1]!=os.getpid():
                raise ValueError('Conquest native window identity changed')
            current['conquest_minimized'] = bool(gui.IsIconic(conquest_hwnd))
            for name,handle in (('game',target.hwnd),('foreground',current['foreground'])):
                thread,info = native_host.thread_info(handle)
                current['input_threads'][name] = {'thread':thread,'focus':int(info.hwndFocus or 0),
                    'active':int(info.hwndActive or 0),'capture':int(info.hwndCapture or 0)}
            return current
        def sample(stage):
            desktop = check()
            observed = reader.snapshot()
            samples.append({'elapsed':round(time.monotonic()-started,4), 'stage':stage,
                            'desktop':desktop, 'gui':observed})
            ui.background_probe.update({'stage':stage,'frame':observed['frame'],
                'button_down':any(observed['mouse_down']),'active_control':observed['active']['id']})
        report = {'character':character, 'mode':mode, 'identity':identity,
                  'initial_stock':stock(before), 'initial_position':before['position'],
                  'viewport':viewport, 'point':point, 'target_window':window['address'],
                  'samples':samples, 'qualified':False, 'button_messages_sent':False}
        def cleanup():
            with observer.lock:
                cleanup_probe(ui,character,report,reader,driver,before)
        if surface is not None:
            surface.before_restore = cleanup
        report['code_evidence'] = code_evidence(observer.adapter,driver.memory.gui.base)
        report['click_code_audit'] = {hex(rva):observer.adapter.read_block(driver.memory.gui.base+rva,size).hex()
            for rva,size in ((0xd4830,0x150),(0xf89eb9,0x800),(0xf68754,0x800))}
        if not all(report['code_evidence']['code_matches'].values()):
            raise ValueError('Captured input code no longer matches the running client')
        input_mode = mode.removeprefix('park-')
        try:
            until = time.monotonic()+1
            while time.monotonic() < until:
                sample('baseline'); time.sleep(.02)
            baseline = {s['gui']['frame'] for s in samples}
            if len(baseline) < 2:
                raise ValueError('GUI frames did not advance during baseline')
            check()
            if input_mode == 'ctrl':
                from conquest.background_keyboard import BackgroundControlScope
                state = reader.snapshot()
                keys = observer.adapter.read_block(state['context']+0xe04,0x285*16)
                if (any(keys[::16]) or any(state['modifiers'].values()) or state['active']['id']
                        or state['backend']['buttons_down'] or state['queue']['size']):
                    raise ValueError('Ctrl probe requires idle target keyboard, buttons and controls')
                if code_evidence(observer.adapter,driver.memory.gui.base)!=report['code_evidence']:
                    raise ValueError('Input handlers changed before Ctrl test')
                isolated_ctrl_probe(target, observer.adapter, identity, reader, check, sample,
                                    samples, report, scope_factory=BackgroundControlScope)
            elif input_mode in ('barrier-hover','barrier-burst-hover','barrier-click','drag-cancel','price-cancel'):
                if code_evidence(observer.adapter,driver.memory.gui.base)!=report['code_evidence']:
                    raise ValueError('Input handlers or game state changed during baseline')
                allowed = {0x62d00,0xfa2e0,0x107c30,0x7e870,0xb7150,0x1175b0,0x112230,
                           0x7f650,0xa8c50,0xed2a0,0x1103f0,0x10e6a0,0xdc240,0x10bcf0,
                           0xb87c0,0x98000,0x10daa0}
                if (report['code_evidence']['game_state']!=1010
                        or any(int(row['handler_rva'],16) not in allowed
                               for row in report['code_evidence']['window_handlers'])):
                    raise ValueError('Character barrier is not qualified for these message handlers')
                state = reader.snapshot()
                ctx = state['context']
                if observer.adapter.read_block(ctx+0x72,1)!=b'\x01':
                    raise ValueError('Input event trickling is disabled')
                keys = observer.adapter.read_block(ctx+0xe04,0x285*16)
                if (any(keys[::16]) or any(state['modifiers'].values()) or state['active']['id']
                        or state['backend']['buttons_down'] or state['queue']['size']):
                    raise ValueError('Character barrier requires idle game keys, buttons, controls and queue')
                if any(w['name']=='Add Item to Booth' for w in before['windows']):
                    raise ValueError('Finish the existing price dialog before a character barrier test')
                pairs = 12 if input_mode in ('barrier-burst-hover','barrier-click') else 1
                if input_mode in ('drag-cancel','price-cancel'):pairs=0
                report['barrier_pairs'] = pairs
                # U+0001 is rejected before callbacks by the inspected text
                # filter; it only supplies the proven event-trickling boundary.
                if pairs > 1 and not logical[0]+2 < right-2:
                    raise ValueError('Adjacent barrier target is clipped')
                if pairs:report['input_messages_sent'] = True
                for index in range(pairs):
                    check()
                    adjacent = round((logical[0]+(index%2))*original['client_size'][0]/viewport[0])
                    target.post(0x200,0,adjacent | point[1] << 16)
                    target.post(0x102,1,1)
                if input_mode=='drag-cancel':
                    from conquest.merchants.background_drag_cancel import run_drag_cancel
                    run_drag_cancel(driver,before,reader,report,sample,check)
                if input_mode=='price-cancel':
                    from conquest.merchants.background_price_probe import run_price_probe
                    run_price_probe(driver,before,reader,report,sample,check)
                if input_mode == 'barrier-click':
                    # One reversible inventory selection. Never double-click,
                    # right-click, drag, trade, or confirm a listing here.
                    deadline = time.monotonic()+1
                    expected_id = None
                    while time.monotonic()<deadline:
                        sample('priming')
                        state = samples[-1]['gui']
                        if (state['hover']['window']==window['address'] and state['hover']['id']
                                and state['want_capture_mouse'] and not state['active']['id']):
                            expected_id = state['hover']['id']
                            break
                        time.sleep(.005)
                    if expected_id is None:
                        raise ValueError('Inventory control did not acknowledge hover before click')
                    fresh = driver.read()
                    if stock(fresh)!=stock(before) or fresh.get('trade') or fresh.get('request'):
                        raise ValueError('Stock or trade changed before click')
                    check()
                    state = reader.snapshot()
                    if (state['hover']!={'window':window['address'],'id':expected_id}
                            or not state['want_capture_mouse'] or state['active']['id']):
                        raise ValueError('Inventory hover expired before click')
                    attempted = False
                    def button_phase(message, flags):
                        target.post(0x200,flags,packed)
                        target.post(0x102,1,1)
                        target.post(message,flags,packed)
                        for index in range(12):
                            adjacent = point[0]+index%2
                            target.post(0x200,flags,adjacent | point[1] << 16)
                            target.post(0x102,1,1)
                    try:
                        attempted = True
                        report['button_messages_sent'] = True
                        button_phase(0x201,1)
                        deadline = time.monotonic()+1
                        while time.monotonic()<deadline:
                            sample('button-down')
                            state = samples[-1]['gui']
                            if state['active']['id']==expected_id and state['mouse_down'][0]:
                                report['press_verified'] = True
                                break
                            time.sleep(.005)
                        if not report.get('press_verified'):
                            raise ValueError('Background inventory press was not acknowledged')
                        check()
                        button_phase(0x202,0)
                        attempted = False
                    finally:
                        if attempted:
                            observer.adapter.assert_identity()
                            if observer.adapter.identity==identity:
                                target.post(0x202,0,packed)
                        # Cleanup observes release even after cancellation. It
                        # sends no further input and never switches focus.
                        deadline = time.monotonic()+1.5
                        while time.monotonic()<deadline:
                            state = reader.snapshot()
                            if not state['backend']['buttons_down'] and not any(state['mouse_down']):
                                report['release_verified'] = True
                                break
                            time.sleep(.005)
                        if not report.get('release_verified'):
                            raise ValueError('Background button release requires attention')
            elif input_mode == 'post-hover':
                report['input_messages_sent'] = True
                target.post(0x200, 0, packed)
            elif input_mode in ('send-hover', 'cancel-hover'):
                send = bind(target.backend.user, 'SendMessageTimeoutW',
                    [wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM,
                     wintypes.UINT,wintypes.UINT,ctypes.POINTER(ctypes.c_size_t)], wintypes.LPARAM)
                reply = ctypes.c_size_t()
                report['input_messages_sent'] = True
                if not send(target.hwnd, 0x200, 0, packed, 0x2|0x20, 300, ctypes.byref(reply)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if input_mode == 'cancel-hover':
                    class Tracking(ctypes.Structure):
                        _fields_ = [('size',wintypes.DWORD),('flags',wintypes.DWORD),
                                    ('hwnd',wintypes.HWND),('hover_time',wintypes.DWORD)]
                    track = bind(target.backend.user,'TrackMouseEvent',[ctypes.POINTER(Tracking)],wintypes.BOOL)
                    operation = Tracking(ctypes.sizeof(Tracking),0x80000002,target.hwnd,0)
                    report['tracking_cancel_accepted'] = bool(track(ctypes.byref(operation)))
            until = time.monotonic()+1.5
            while time.monotonic() < until:
                sample('after'); time.sleep(.005)
            after = driver.read()
            report['stock_unchanged'] = stock(before) == stock(after)
            report['position_unchanged'] = before['position'] == after['position']
            report['hover_frames'] = sorted({s['gui']['frame'] for s in samples
                if s['stage']=='after' and s['gui']['hover']['window']==window['address']
                and s['gui']['hover']['id']})
            report['queue_drained'] = samples[-1]['gui']['queue']['size']==0
            report['outcome'] = 'observed'
        except Exception as error:
            report['outcome'] = 'stopped'
            report['error'] = str(error) if isinstance(error,(ValueError,OSError)) else type(error).__name__
            from conquest.merchants.ui import calibration_failure
            report['diagnostic'] = calibration_failure(error)['diagnostic']
        if surface is None:
            cleanup()
        return report


def start_probe(ui, character, mode):
    validate_live_mode(mode)
    if probe_busy(ui):
        raise ValueError('A background diagnostic is already running')
    if character not in ui.runtime.observers:
        raise ValueError('Merchant is not connected')
    cancel = threading.Event()
    if not hasattr(ui, 'background_surfaces'):
        ui.background_surfaces = {}
    ui.background_cancel = cancel
    probe_id = uuid.uuid4().hex
    ui.background_probe = {'id':probe_id,'state':'running','character':character,'mode':mode}
    def work():
        try:
            report = run_probe(ui,character,mode,cancel)
        except Exception as error:
            report = {**getattr(error,'probe_report',{}),'character':character,'mode':mode,'outcome':'stopped',
                      'error':str(error) if isinstance(error,(ValueError,OSError)) else type(error).__name__}
            from conquest.merchants.ui import calibration_failure
            report['diagnostic'] = calibration_failure(error)['diagnostic']
        path = Path(state_path('reports/merchants/background'))/(probe_id+'.json')
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        except OSError:
            report['report_error'] = 'Could not save diagnostic report'
        finally:
            ui.background_probe = {'id':probe_id,
                'state':'restoration_required' if ui.background_surfaces else 'complete','report':str(path),
                **{k:v for k,v in report.items() if k not in ('samples', 'state')}}
    ui.background_thread = threading.Thread(target=work,name='background-input-probe',daemon=True)
    ui.background_thread.start()
    return {'requested':probe_id}
