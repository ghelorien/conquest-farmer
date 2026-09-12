"""Experimental Ctrl state for an isolated, unowned background target.

This module sends no key/mouse messages and never activates windows. It is NOT
a production input backend. Public Win32 APIs cannot enumerate arbitrary
preexisting AttachThreadInput relationships; ownership/thread checks below do
not prove complete input-queue isolation. Use only after explicit qualification
of the target environment. The disposable helper's exit is the final watchdog
for an attachment stuck inside a native call; normal cleanup restores its full
256-byte saved table and detaches explicitly.

AttachThreadInput itself resets keyboard state: the saved table is necessarily
post-attachment, not an exact snapshot of the target's prior Win32 table. Before
any live use the caller must independently verify idle game keys/modifiers from
memory. This helper does not replace that gameplay-specific qualification.
"""
import ctypes
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def validate_spec(spec):
    if set(spec) != {'hwnd', 'identity', 'hold_seconds'}:
        raise ValueError('Unexpected background keyboard configuration')
    identity = spec['identity']
    if (type(spec['hwnd']) is not int or spec['hwnd'] <= 0 or not isinstance(identity, dict)
            or type(identity.get('pid')) is not int or identity['pid'] <= 0
            or type(identity.get('creation_time_100ns')) is not int
            or identity['creation_time_100ns'] <= 0 or not identity.get('path')):
        raise ValueError('A complete verified process identity and HWND are required')
    hold = spec['hold_seconds']
    if type(hold) not in (float, int) or not .05 <= hold <= 2:
        raise ValueError('Experimental Ctrl scope must last 0.05 to 2 seconds maximum')


def _run_scope(api, spec, stopped, emit, *, neutralize_requested=lambda: False,
               clock=time.monotonic, sleep=time.sleep):
    validate_spec(spec)
    initial = api.inspect(spec)
    helper = api.thread_id()
    if initial['thread'] == helper or initial['foreground_thread'] in (initial['thread'], helper):
        raise ValueError('Target/helper must be separate from the foreground input thread')
    attached = False
    baseline = None
    def check():
        current = api.inspect(spec)
        if current != initial:
            raise ValueError('Target identity, GUI ownership or foreground changed')
    try:
        check()
        api.attach(helper, initial['thread'], True)
        attached = True
        baseline = api.keyboard()
        if len(baseline) != 256:
            raise ValueError('Incomplete keyboard state')
        if any(baseline[key] & 0x80 for key in (0x11, 0xA2, 0xA3, 0x10, 0x12)):
            raise ValueError('Target keyboard modifiers are already held')
        check()
        held = bytearray(baseline)
        held[0x11] |= 0x80
        held[0xA2] |= 0x80
        api.set_keyboard(held)
        check()
        if not all(api.keyboard()[key] & 0x80 for key in (0x11, 0xA2)):
            raise ValueError('Helper Ctrl state was not established')
        deadline = clock() + spec['hold_seconds']
        emit({'state': 'ready', 'target_thread': initial['thread']})
        neutralized = False
        while not stopped():
            check()
            if clock() >= deadline:
                raise TimeoutError('Background Ctrl scope deadline expired')
            if neutralize_requested() and not neutralized:
                api.set_keyboard(baseline)
                check()
                state = api.keyboard()
                if any(state[key] & 0x80 for key in (0x11, 0xA2, 0xA3)):
                    raise ValueError('Helper Ctrl table did not neutralize')
                neutralized = True
                emit({'state': 'neutralized', 'target_thread': initial['thread']})
            sleep(.005)
    finally:
        if attached:
            try:
                if baseline is not None and len(baseline) == 256:
                    api.set_keyboard(baseline)
            finally:
                api.attach(helper, initial['thread'], False)
    emit({'state': 'released'})


class _NativeApi:
    def __init__(self):
        from conquest.background_keyboard_lab import WindowsApi
        from conquest.window_host import HostApi
        import win32gui
        self.keys = WindowsApi()
        self.host = HostApi()
        self.gui = win32gui
        # This helper owns no windows; this call creates only its message queue.
        win32gui.PeekMessage(0, 0, 0, 0)

    def thread_id(self):
        return self.keys.thread_id()

    def attach(self, first, second, attached):
        self.keys.attach(first, second, attached)

    def keyboard(self):
        return self.keys.keyboard()

    def set_keyboard(self, table):
        self.keys.set_keyboard(table)

    def inspect(self, spec):
        from ctypes import wintypes as w
        hwnd = spec['hwnd']
        pid = w.DWORD()
        thread = self.host.backend.window_pid(hwnd, ctypes.byref(pid))
        if not thread or pid.value != spec['identity']['pid']:
            raise ValueError('Background HWND process changed')
        if self.host.backend.identity(pid.value) != spec['identity']:
            raise ValueError('Background process identity changed')
        if (self.gui.GetWindow(hwnd, 4) or self.gui.GetParent(hwnd)
                or self.gui.GetAncestor(hwnd, 2) != hwnd or self.gui.IsIconic(hwnd)):
            raise ValueError('Background Ctrl target must be unowned, top-level and restored')
        foreground = self.gui.GetForegroundWindow()
        foreground_pid = w.DWORD()
        foreground_thread = self.host.backend.window_pid(foreground, ctypes.byref(foreground_pid))
        if not foreground_thread or foreground == hwnd or foreground_pid.value == pid.value:
            raise ValueError('Foreground must belong to an independent application')
        target_thread, info = self.host.thread_info(hwnd)
        if target_thread != thread or info.flags & 0x1e or info.hwndCapture or info.hwndMenuOwner or info.hwndMoveSize:
            raise ValueError('Target GUI thread is busy or changed')
        for value in (info.hwndActive, info.hwndFocus):
            if value and self.gui.GetAncestor(value, 2) != hwnd:
                raise ValueError('Target GUI thread shares an unexpected active/focus window')
        # Read foreground GUI state too: an attached foreign focus would fail.
        _, foreground_info = self.host.thread_info(foreground)
        if any(value and self.gui.GetAncestor(value, 2) == hwnd
               for value in (foreground_info.hwndActive, foreground_info.hwndFocus)):
            raise ValueError('Foreground input queue refers to the target')
        pointer_root = self.gui.GetAncestor(self.gui.WindowFromPoint(self.gui.GetCursorPos()), 2)
        if pointer_root == hwnd:
            raise ValueError('User pointer reached the target window')
        return {'thread': thread, 'foreground': int(foreground), 'foreground_thread': foreground_thread,
                'active': int(info.hwndActive or 0), 'focus': int(info.hwndFocus or 0)}


class BackgroundControlScope:
    """Bounded experimental context. Call check() before each external phase.

    Example only after target qualification: ``with BackgroundControlScope(
    hwnd, verified_identity) as scope: scope.check()``. This helper itself does
    not deliver any game input or establish that the game consumed Ctrl.
    Call neutralize() and wait for its acknowledgment before any external key-up
    message; verify game key state while still inside the scope before exiting.
    """
    def __init__(self, hwnd, identity, *, hold_seconds=.5):
        self.spec = {'hwnd': hwnd, 'identity': dict(identity), 'hold_seconds': hold_seconds}
        validate_spec(self.spec)
        self.process = None
        self.events = queue.Queue()
        self.timer = None
        self.ready = None
        self.receiver = None
        self.terminal = None
        self.neutralized = False

    def __enter__(self):
        try:
            executable = Path(sys.executable)
            if executable.name.casefold() == 'pythonw.exe':
                executable = executable.with_name('python.exe')
            self.process = subprocess.Popen([str(executable), '-m', 'conquest.background_keyboard', '--worker'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            def receive():
                for line in self.process.stdout:
                    try:
                        self.events.put(json.loads(line))
                    except ValueError:
                        self.events.put({'state': 'failed', 'error': 'Malformed helper response'})
            self.receiver = threading.Thread(target=receive, daemon=True)
            self.receiver.start()
            # Starts before handshake: even a hung native attachment is bounded.
            self.timer = threading.Timer(3 + self.spec['hold_seconds'], self._kill)
            self.timer.daemon = True
            self.timer.start()
            self.process.stdin.write(json.dumps(self.spec) + '\n')
            self.process.stdin.flush()
            event = self.events.get(timeout=2)
            if event.get('state') != 'ready':
                raise RuntimeError(event.get('error', 'Background helper could not start'))
            self.ready = time.monotonic()
            return self
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.terminal is not None:
            raise RuntimeError(self.terminal.get('error', 'Background Ctrl scope has ended'))
        if self.ready is None or time.monotonic() - self.ready >= self.spec['hold_seconds']:
            raise TimeoutError('Background Ctrl scope is not active')
        while not self.events.empty():
            event = self.events.get_nowait()
            if event.get('state') == 'neutralized':
                self.neutralized = True
                continue
            if event.get('state') != 'ready':
                self.terminal = event
                raise RuntimeError(event.get('error', 'Background Ctrl scope has ended'))
        if self.process.poll() is not None:
            raise RuntimeError('Background Ctrl helper exited')

    def neutralize(self):
        """Restore saved table, keep attachment, and require the helper's ACK.

        The total scope deadline is not extended. This does not post key-up;
        callers must independently deliver and verify their authorized event.
        """
        self.check()
        if self.neutralized:
            return
        self.process.stdin.write('neutralize\n')
        self.process.stdin.flush()
        remaining = self.spec['hold_seconds'] - (time.monotonic() - self.ready)
        try:
            event = self.events.get(timeout=max(.001, min(1, remaining)))
        except queue.Empty as error:
            self.close()
            raise TimeoutError('Background Ctrl neutralization was not acknowledged') from error
        if event.get('state') != 'neutralized':
            self.terminal = event
            raise RuntimeError(event.get('error', 'Background Ctrl scope ended before neutralization'))
        self.neutralized = True
        self.check()

    def _kill(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.kill()
            except OSError:
                pass

    def close(self):
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    try:
                        self.process.stdin.write('stop\n')
                        self.process.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    try:
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self._kill()
                        self.process.wait(timeout=1)
            finally:
                if self.timer:
                    self.timer.cancel()
                self.ready = None
                if self.receiver:
                    self.receiver.join(timeout=1)
                while not self.events.empty():
                    event = self.events.get_nowait()
                    if event.get('state') not in ('ready', 'neutralized'):
                        self.terminal = event
                self.process.stdin.close()
                self.process.stdout.close()
        return self.terminal

    def __exit__(self, kind, value, traceback):
        terminal = self.close()
        if kind is None and (not terminal or terminal.get('state') != 'released'):
            raise RuntimeError((terminal or {}).get('error', 'Background helper cleanup was not confirmed'))


def _worker():
    stop = threading.Event()
    neutralize = threading.Event()
    def emit(value):
        print(json.dumps(value), flush=True)
    try:
        spec = json.loads(sys.stdin.readline())
        validate_spec(spec)
        def listen():
            # EOF (parent death) also immediately releases the scope.
            for line in sys.stdin:
                if line.strip() == 'neutralize':
                    neutralize.set()
                else:
                    break
            stop.set()
        threading.Thread(target=listen, daemon=True).start()
        _run_scope(_NativeApi(), spec, stop.is_set, emit, neutralize_requested=neutralize.is_set)
    except BaseException as error:
        emit({'state': 'failed', 'error': f'{type(error).__name__}: {error}'})


if __name__ == '__main__' and '--worker' in sys.argv:
    _worker()
