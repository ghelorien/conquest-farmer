"""Disposable, invisible dummy-window experiment. Never targets a game window.

Run through ``run_lab``: its child-process deadline bounds even a stuck Windows
input-queue attachment. No activation, cursor movement, or SendInput APIs exist
in this module. This experiment cannot qualify actual gameplay input.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time

KEYS = (0x11, 0xA2, 0x10, 0xA0, 0x12, 0xA4)


def exercise(api, target_thread, sample, check):
    """Attach a dedicated helper only to our dummy thread; always unwind."""
    attached = False
    baseline = None
    result = {}
    helper_thread = api.thread_id()
    if helper_thread == target_thread:
        raise ValueError('Dummy target and helper must have separate threads')
    result['before'] = sample('before')
    try:
        check()
        api.attach(helper_thread, target_thread, True)
        attached = True
        baseline = api.keyboard()
        result['attached'] = sample('attached')
        held = bytearray(baseline)
        held[0x11] |= 0x80
        held[0xA2] |= 0x80
        check()
        api.set_keyboard(held)
        result['held'] = sample('held')
        check()
    finally:
        if attached:
            try:
                # Only our isolated helper/dummy pair can receive this table.
                if baseline is not None:
                    api.set_keyboard(baseline)
            finally:
                api.attach(helper_thread, target_thread, False)
    result['after'] = sample('after')
    return result


class WindowsApi:
    def __init__(self):
        from ctypes import wintypes as w
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.GetCurrentThreadId.restype = w.DWORD
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
        self.user.GetWindowThreadProcessId.restype = w.DWORD
        self.user.GetKeyState.argtypes = (ctypes.c_int,)
        self.user.GetKeyState.restype = ctypes.c_short
        self.user.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.user.GetAsyncKeyState.restype = ctypes.c_short
        self.user.AttachThreadInput.argtypes = (w.DWORD, w.DWORD, w.BOOL)
        self.user.AttachThreadInput.restype = w.BOOL
        self.user.GetKeyboardState.argtypes = (ctypes.POINTER(ctypes.c_ubyte),)
        self.user.SetKeyboardState.argtypes = (ctypes.POINTER(ctypes.c_ubyte),)

    def thread_id(self):
        return self.kernel.GetCurrentThreadId()

    def attach(self, first, second, attached):
        if not self.user.AttachThreadInput(first, second, attached):
            raise ctypes.WinError(ctypes.get_last_error())

    def keyboard(self):
        state = (ctypes.c_ubyte * 256)()
        if not self.user.GetKeyboardState(state):
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(state)

    def set_keyboard(self, state):
        if len(state) != 256:
            raise ValueError('Keyboard table must contain 256 bytes')
        table = (ctypes.c_ubyte * 256).from_buffer_copy(state)
        if not self.user.SetKeyboardState(table):
            raise ctypes.WinError(ctypes.get_last_error())

    def observation(self):
        table = self.keyboard()
        return {'thread': self.thread_id(),
                'key_state': {str(key): bool(self.user.GetKeyState(key) & 0x8000) for key in KEYS},
                'keyboard_table': {str(key): bool(table[key] & 0x80) for key in KEYS},
                'async_state': {str(key): bool(self.user.GetAsyncKeyState(key) & 0x8000) for key in KEYS}}


def _child():
    import win32api
    import win32gui
    import win32con
    from ctypes import wintypes as w

    api = WindowsApi()
    stop = threading.Event()
    ready = threading.Event()
    response = threading.Event()
    finished = threading.Event()
    state = {}
    errors = []
    outputs = {}
    sample_message = win32con.WM_APP + 417
    initial_foreground = int(api.user.GetForegroundWindow() or 0)
    if not initial_foreground:
        raise RuntimeError('An independent foreground window is required')
    foreground_pid = w.DWORD()
    foreground_thread = api.user.GetWindowThreadProcessId(initial_foreground, ctypes.byref(foreground_pid))
    if foreground_pid.value == os.getpid():
        raise RuntimeError('Foreground observer must belong to another process')

    def target():
        hwnd = None
        class_name = f'ConquestKeyboardLab-{os.getpid()}'
        try:
            def callback(hwnd, message, wp, lp):
                if message == sample_message:
                    state['observation'] = api.observation()
                    response.set()
                    return 0
                return win32gui.DefWindowProc(hwnd, message, wp, lp)
            cls = win32gui.WNDCLASS()
            cls.hInstance = win32api.GetModuleHandle(None)
            cls.lpszClassName = class_name
            cls.lpfnWndProc = callback
            win32gui.RegisterClass(cls)
            # No owner/parent and no WS_VISIBLE: no implicit Tk queue linkage.
            hwnd = win32gui.CreateWindowEx(0, class_name, 'Disposable keyboard lab',
                win32con.WS_POPUP, 0, 0, 1, 1, 0, 0, cls.hInstance, None)
            state.update(hwnd=hwnd, thread=api.thread_id())
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER) or win32gui.GetParent(hwnd):
                raise RuntimeError('Dummy window unexpectedly has an owner or parent')
            ready.set()
            while not stop.is_set():
                win32gui.PumpWaitingMessages()
                stop.wait(.001)
        except BaseException as error:
            errors.append(f'target: {type(error).__name__}: {error}')
            ready.set()
        finally:
            if hwnd:
                win32gui.DestroyWindow(hwnd)
            try:
                win32gui.UnregisterClass(class_name, win32api.GetModuleHandle(None))
            except Exception:
                pass

    def check():
        if stop.is_set() or int(api.user.GetForegroundWindow() or 0) != initial_foreground:
            raise RuntimeError('Foreground changed or test stopped')
        if state['thread'] == foreground_thread or api.thread_id() == foreground_thread:
            raise RuntimeError('A foreground input queue must never be attached')

    def sample(label):
        check()
        response.clear()
        win32gui.PostMessage(state['hwnd'], sample_message, 0, 0)
        if not response.wait(1):
            raise TimeoutError(f'Dummy target did not acknowledge {label}')
        if label == 'held':
            # Give the independent observer a bounded sampling interval.
            time.sleep(.05)
        return dict(state['observation'])

    def helper():
        try:
            # Ensure this otherwise windowless thread has its own message queue.
            win32gui.PeekMessage(0, 0, 0, win32con.PM_NOREMOVE)
            outputs.update(exercise(api, state['thread'], sample, check))
        except BaseException as error:
            errors.append(f'helper: {type(error).__name__}: {error}')
        finally:
            finished.set()

    target_thread = threading.Thread(target=target, daemon=True)
    target_thread.start()
    if not ready.wait(2) or errors:
        stop.set()
        raise RuntimeError('Dummy target could not start: ' + '; '.join(errors))
    initial_async = api.observation()['async_state']
    foreground_samples = []
    observer_samples = []
    helper_thread = threading.Thread(target=helper, daemon=True)
    helper_thread.start()
    deadline = time.monotonic() + 5
    while True:
        current = int(api.user.GetForegroundWindow() or 0)
        foreground_samples.append(current)
        observer_samples.append(api.observation()['async_state'])
        if current != initial_foreground or time.monotonic() > deadline:
            stop.set()
        if finished.wait(.002):
            break
        if time.monotonic() > deadline + 1:
            raise TimeoutError('Helper watchdog expired; parent must terminate child')
    stop.set()
    helper_thread.join(1)
    target_thread.join(1)
    ctrl = str(0x11)
    controlled_keys = (str(0x11), str(0xA2))
    physical_control_unchanged = all(all(value[key] == initial_async[key]
        for key in controlled_keys) for value in observer_samples)
    return {'schema_version': 1, 'dummy_only': True, 'gameplay_qualified': False,
            'target': outputs, 'errors': errors,
            'foreground': {'hwnd': initial_foreground, 'pid': foreground_pid.value,
                'unchanged': all(value == initial_foreground for value in foreground_samples),
                'samples': len(foreground_samples)},
            'physical_modifiers_unchanged': all(value == initial_async for value in observer_samples),
            'physical_control_unchanged': physical_control_unchanged,
            'physical_observations': {'before': initial_async, 'during': observer_samples},
            'target_ctrl_down_observed': outputs.get('held', {}).get('key_state', {}).get(ctrl),
            'target_ctrl_after': outputs.get('after', {}).get('key_state', {}).get(ctrl),
            'cleanup_completed': not helper_thread.is_alive() and not target_thread.is_alive(),
            'limitation': 'No keys sent to foreground app; its interpretation of real typing was not measured.'}


def run_lab(timeout=12):
    """Run in an expendable process; timeout kills it and releases its threads."""
    if not 5 <= timeout <= 30:
        raise ValueError('Watchdog must be 5 to 30 seconds')
    completed = subprocess.run([sys.executable, '-m', 'conquest.background_keyboard_lab', '--child'],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if completed.returncode:
        raise RuntimeError(completed.stderr[-2000:])
    return json.loads(completed.stdout)


if __name__ == '__main__':
    print(json.dumps(_child() if '--child' in sys.argv else run_lab(), indent=2))
