import ctypes
import base64
import struct
import os

import pytest

from conquest.foreground import Input, scan_key_event, press_scan_sequence
from conquest.worker import Operations, request
from conquest.capture import CaptureUnavailable
from conquest.merchants.coordination import InputAcquisitionBusy


class Session:
    pid = 1
    identity = {"pid": 1}
    expected_sha256 = "a" * 64
    modules = [{"name": "client.exe", "base": 0x100000, "size": 0x1000}]
    hp = 51

    def assert_identity(self):
        pass

    def read(self, address, size):
        if address == 0x10000:
            return b"Parasite\0".ljust(size, b"\0")
        return struct.pack("<I", self.hp)[:size]


@pytest.mark.parametrize('code,expected', [('foreground_unavailable',CaptureUnavailable),
                                         ('input_acquisition_busy',InputAcquisitionBusy),
                                         ('bad_geometry',ValueError)])
def test_worker_preserves_recoverable_focus_errors(tmp_path,monkeypatch,code,expected):
    import io
    import json
    import urllib.error
    import urllib.request
    from types import SimpleNamespace
    info = tmp_path/'worker.json'
    info.write_text(json.dumps({'port':12345,'token':'test'}))
    def fail(*args,**kwargs):
        raise urllib.error.HTTPError('http://127.0.0.1',400,'bad request',{},
            io.BytesIO(json.dumps({'code':code,'error':'test failure'}).encode()))
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',fail)
    with pytest.raises(expected) as raised:
        request(info,'foreground-click',{})
    assert type(raised.value) is expected


@pytest.fixture
def operations():
    result = Operations.__new__(Operations)
    result.session = Session()
    result.stopping = False
    result.read_only = False
    result.target = object()
    return result


def test_sample_reads_typed_values(operations):
    operations.read_only = True
    result = operations.dispatch("sample", {"fields": [{"name": "hp", "address": "0x20000", "kind": "u32"}]})
    assert result["fields"][0]["value"] == [51]
    assert not result["qualified"]


@pytest.mark.parametrize("operation", ["foreground-click", "foreground-key", "background-key", "background-click", "foreground-drag"])
def test_read_only_worker_rejects_input_before_guard_reads(operations, operation):
    operations.read_only = True
    def fail_read(*_):
        pytest.fail("Input guard must not read memory in a read-only worker")
    operations.session.read = fail_read
    with pytest.raises(ValueError, match="input is disabled"):
        operations.dispatch(operation, {})


def test_read_block_is_available_without_input_and_preserves_bytes(operations):
    operations.read_only = True
    data = bytes(range(256))
    operations.session.read = lambda address, size: data
    result = operations.dispatch("read-block", {"address": "0x20000", "size": 256})
    assert base64.b64decode(result["data"]) == data
    assert result["size"] == 256
    assert not result["qualified"]


@pytest.mark.parametrize("address,size", [("0x20000", 0), ("0x20000", 65537),
    ("0x20000", True), ("0x20000", 2.5), ("0x20000", "4"),
    (0x20000, 4), ("0xffff", 4), ("0x7fffffffffff", 8)])
def test_invalid_read_blocks_never_reach_memory(operations, address, size):
    operations.session.read = lambda *_: pytest.fail("Invalid block reached memory")
    with pytest.raises(ValueError):
        operations.dispatch("read-block", {"address": address, "size": size})


def test_read_block_rejects_short_read(operations):
    operations.session.read = lambda *_: b'abc'
    with pytest.raises(ValueError, match="Incomplete"):
        operations.dispatch("read-block", {"address": "0x20000", "size": 4})


def test_read_block_discards_data_when_process_changes(operations):
    checks = []
    def check():
        checks.append(True)
        if len(checks) == 2:
            raise ValueError("Process restarted")
    operations.session.assert_identity = check
    with pytest.raises(ValueError, match="restarted"):
        operations.dispatch("read-block", {"address": "0x20000", "size": 4})


def scan_body():
    return {"observations": {"schema_version": 1, "source": "test observation",
        "observed_at": "2026-09-07T20:00:00+00:00", "expected_sha256": "a" * 64,
        "observations": [{"name": "hp", "kind": "u32", "value": 213}]}}


@pytest.mark.parametrize("field,value", [("max_mib", 0), ("max_mib", 4097),
    ("max_mib", True), ("max_candidates", 0), ("max_candidates", 10001),
    ("max_candidates", 1.5)])
def test_worker_scan_budget_is_bounded(operations, monkeypatch, field, value):
    monkeypatch.setattr("conquest.worker.scan", lambda *_args, **_kwargs:
                        pytest.fail("Invalid budget reached scanner"))
    with pytest.raises(ValueError):
        operations.dispatch("scan", {**scan_body(), field: value})


def test_worker_passes_requested_scan_budget(operations, monkeypatch):
    def scanner(session, observations, **options):
        assert options == {"max_seconds": 20, "max_bytes": 1024**3, "max_candidates": 10000}
        return {"qualified": False}
    monkeypatch.setattr("conquest.worker.scan", scanner)
    assert operations.dispatch("scan", {**scan_body(), "max_mib": 1024,
        "max_candidates": 10000}) == {"qualified": False}


@pytest.mark.parametrize("fields", [[], [{}] * 65, "invalid"])
def test_field_count_is_bounded(operations, fields):
    with pytest.raises(ValueError):
        operations.dispatch("sample", {"fields": fields})


def test_foreground_input_requires_character_guard(operations):
    with pytest.raises(ValueError, match="guard is required"):
        operations.dispatch("foreground-click", {"point": [1, 2]})


def test_dead_character_prevents_input(operations):
    operations.session.hp = 0
    with pytest.raises(ValueError, match="guard failed"):
        operations.dispatch("foreground-click", {"guard": {"name_address": "0x10000", "name": "Parasite",
                                                           "hp_address": "0x20000", "max_hp": 51}})


def test_unknown_operations_are_rejected(operations):
    with pytest.raises(ValueError, match="Unsupported"):
        operations.dispatch("execute", {"code": "print('not allowed')"})


def test_shutdown_is_explicit(operations):
    assert operations.dispatch("shutdown", {}) == {"stopped": True}
    assert operations.stopping


@pytest.mark.skipif(os.name != "nt", reason="Windows input structure")
def test_sendinput_structure_matches_native_abi():
    assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_physical_key_encoding_contains_the_actual_scan_code():
    down, up = scan_key_event(0x57), scan_key_event(0x57, True)
    assert down.type == up.type == 1
    assert down.data.ki.wVk == up.data.ki.wVk == 0
    assert down.data.ki.wScan == up.data.ki.wScan == 0x57
    assert down.data.ki.dwFlags == 0x8
    assert up.data.ki.dwFlags == 0xA


@pytest.mark.parametrize("code", [0, -1, 0xE057, 0x10000])
def test_bad_scan_codes_rejected(code):
    with pytest.raises(ValueError, match="scan code"):
        scan_key_event(code)


def test_modifier_chord_releases_in_reverse_order_after_failure():
    events = []
    def send(event):
        events.append((event.data.ki.wScan, event.data.ki.dwFlags))
        if len(events) == 2:
            raise OSError("Injected delivery failure")
    with pytest.raises(OSError, match="delivery failure"):
        press_scan_sequence(send, [0x1D, 0x57], sleep=lambda _: None)
    assert events == [(0x1D, 8), (0x57, 8), (0x57, 10), (0x1D, 10)]


def test_failure_releasing_key_still_attempts_modifier_cleanup():
    events = []
    def send(event):
        events.append((event.data.ki.wScan, event.data.ki.dwFlags))
        if len(events) == 3:
            raise OSError("Release failed")
    with pytest.raises(OSError, match="Release failed"):
        press_scan_sequence(send, [0x1D, 0x57], sleep=lambda _: None)
    assert events[-1] == (0x1D, 10)


def test_missing_jump_modifier_retries_without_click_and_releases_control(monkeypatch):
    from types import SimpleNamespace
    from conquest import foreground,window_host
    from conquest.capture import CaptureUnavailable
    events=[];state={'accept_control':False,'held':False}
    def send(count,pointer,size):
        event=ctypes.cast(pointer,ctypes.POINTER(Input)).contents
        flags=event.data.ki.dwFlags if event.type==1 else event.data.mi.dwFlags
        events.append((event.type,flags))
        if event.type==1:
            state['held']=state['accept_control'] and not flags&2
        return 1
    functions={
        'SetForegroundWindow':lambda *args:True,'ClientToScreen':lambda *args:True,
        'GetSystemMetrics':lambda key:{76:0,77:0,78:1920,79:1080}[key],
        'SendInput':send,'GetAsyncKeyState':lambda key:0x8000 if state['held'] and key in (0x11,0xa2) else 0}
    monkeypatch.setattr(foreground,'bind',lambda user,name,*args:functions[name])
    monkeypatch.setattr(foreground.time,'sleep',lambda _:None)
    monkeypatch.setattr(window_host,'HostApi',lambda:SimpleNamespace(thread_info=lambda hwnd:(1,
        SimpleNamespace(hwndFocus=1,hwndActive=1))))
    target=SimpleNamespace(hwnd=1,backend=SimpleNamespace(user=None,foreground=lambda:1),
        snapshot=lambda:{'foreground':1,'client_size':[1036,793],'minimized':False,'cursor':[500,400]})
    with pytest.raises(CaptureUnavailable,match='no jump click sent'):
        foreground.foreground_click(target,500,400,(1036,793),control=True,require_foreground=True)
    assert (0,2) not in events and events[-1]==(1,10) and not state['held']
    state['accept_control']=True
    foreground.foreground_click(target,500,400,(1036,793),control=True,require_foreground=True)
    assert events.count((0,2))==1 and events.count((0,4))==1
    assert events[-1]==(1,10) and not state['held']
