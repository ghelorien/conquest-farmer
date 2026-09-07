import ctypes
import struct
import os

import pytest

from conquest.foreground import Input, scan_key_event, press_scan_sequence
from conquest.worker import Operations, request


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


@pytest.fixture
def operations():
    result = Operations.__new__(Operations)
    result.session = Session()
    result.stopping = False
    result.target = object()
    return result


def test_sample_reads_typed_values(operations):
    result = operations.dispatch("sample", {"fields": [{"name": "hp", "address": "0x20000", "kind": "u32"}]})
    assert result["fields"][0]["value"] == [51]
    assert not result["qualified"]


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
