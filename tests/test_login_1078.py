from contextlib import nullcontext
from types import SimpleNamespace as NS
import hashlib
import struct
import zlib

import pytest

from conquest import login_1078 as native
from conquest.memory_shop import GuiWindow


def fixture(monkeypatch):
    from conquest import memory_shop
    from conquest.merchants import memory

    base = 0x140000000
    window = GuiWindow(0x20000, "Login", (840.0, 486.0), (208.0, 186.0), (0.0, 0.0))
    blocks = {
        base + rva: bytes([n + 1]) * size
        for n, (rva, size, _) in enumerate(native.CODE_BLOCKS)
    }
    monkeypatch.setattr(
        native,
        "CODE_BLOCKS",
        tuple(
            (rva, size, hashlib.sha256(blocks[base + rva]).hexdigest())
            for rva, size, _ in native.CODE_BLOCKS
        ),
    )
    blocks.update({base + rva: value for rva, value in native.LITERALS})
    blocks[base + native.ROOT_RVA + 0x78] = struct.pack(
        "<Q", base + native.LOGIN_VTABLE_RVA
    )
    blocks[base + native.LOGIN_VTABLE_RVA + 0x10] = struct.pack("<Q", base + 0xE7BD0)
    blocks[base + native.ROOT_RVA + 0x708] = b"\0"
    blocks[base + 0x6B5EF0] = struct.pack("<Q", 0x40000)
    blocks[window.address + 8] = struct.pack("<I", 123)
    blocks[0x40000 + 0x3F04] = struct.pack("<I", zlib.crc32(b"##username", 123))
    blocks[0x40000 + 0x3F20] = struct.pack("<Q", window.address)
    blocks[window.address + 0xE0] = struct.pack(
        "<14f", 848, 668, 968.5, 652, 848, 494, 1040, 664, 848, 494, 0, 0, 0, 12
    )
    reads = []

    def read(address, size):
        reads.append((address, size))
        return blocks[address][:size]

    session = NS(
        expected_sha256=native.CLIENT_SHA256_1078,
        modules=[{"name": "ImConquer.exe", "base": base}],
        assert_identity=lambda: None,
        read_block=read,
    )
    gui = NS(base=base, read=lambda name: window)
    monkeypatch.setattr(memory_shop.MemoryGui, "for_session", lambda session: gui)
    hover = []
    monkeypatch.setattr(
        memory.GuiReader,
        "for_session",
        lambda session: NS(
            viewport_size=lambda: [1888, 1080],
            assert_hovered=lambda w, label: hover.append((w, label)),
        ),
    )
    return session, window, blocks, base, reads, hover


def test_exact_layout_does_not_read_login_field_contents(monkeypatch):
    s, w, blocks, base, reads, hover = fixture(monkeypatch)
    assert native.form_points(s, w) == ((944, 519), (944, 557), (944, 639))
    model = base + native.ROOT_RVA + 0x78
    assert not any(model + 0x48 <= address < model + 0xC8 for address, _ in reads)
    native.assert_hovered(s, w, "##password")
    assert hover == [({"address": w.address}, "##password")]


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("build", "exact client"),
        ("code", "renderer"),
        ("label", "label"),
        ("model", "model"),
        ("error", "resolved"),
        ("geometry", "layout"),
        ("nan", "layout"),
    ],
)
def test_native_login_fails_closed_on_changed_proof(monkeypatch, mutation, match):
    s, w, b, base, _, _ = fixture(monkeypatch)
    if mutation == "build":
        s.expected_sha256 = "other"
    if mutation == "code":
        b[base + native.CODE_BLOCKS[0][0]] = b"changed"
    if mutation == "label":
        b[base + 0x5E4360] = b"Other\0"
    if mutation == "model":
        b[base + native.ROOT_RVA + 0x78] = bytes(8)
    if mutation == "error":
        b[base + native.ROOT_RVA + 0x708] = b"\1"
    if mutation in ("geometry", "nan"):
        raw = bytearray(b[w.address + 0xE0])
        struct.pack_into("<f", raw, 4, float("nan") if mutation == "nan" else 670)
        b[w.address + 0xE0] = bytes(raw)
    with pytest.raises(ValueError, match=match):
        native.form_points(s, w)


def test_error_reader_dispatches_native_root_and_rejects_unknown_text(monkeypatch):
    from conquest.reconnect import LoginErrorReader

    s, w, b, base, _, _ = fixture(monkeypatch)
    reader = LoginErrorReader(s)
    assert reader.root == base + native.ROOT_RVA
    raw = struct.pack("<QQQQBB", 0x30000, 0, 25, 31, 1, 0)
    b[reader.root + 0x6E8] = raw
    b[0x30000] = b"Unknown connection error."
    with pytest.raises(ValueError, match="Unrecognized"):
        reader.read()


@pytest.mark.parametrize(
    "change", [None, "wrong_field", "wrong_window", "changed_during_read"]
)
def test_keyboard_focus_is_exact_and_stable(monkeypatch, change):
    s, w, b, base, reads, _ = fixture(monkeypatch)
    if change == "wrong_field":
        b[0x43F04] = struct.pack("<I", zlib.crc32(b"##password", 123))
    if change == "wrong_window":
        b[0x43F20] = struct.pack("<Q", w.address + 8)
    if change == "changed_during_read":
        original = s.read_block
        count = [0]

        def read(address, size):
            if address == 0x43F04:
                count[0] += 1
                if count[0] > 1:
                    return bytes(4)
            return original(address, size)

        s.read_block = read
    if change:
        with pytest.raises(ValueError, match="keyboard focus"):
            native.assert_active_field(s, w, "##username")
    else:
        native.assert_active_field(s, w, "##username")
    assert not any(
        base + native.ROOT_RVA + 0xC0 <= address < base + native.ROOT_RVA + 0x140
        for address, _ in reads
    )


def test_submit_native_preserves_guards_and_keeps_credentials_out_of_result(
    monkeypatch,
):
    from conquest import reconnect, focus_recovery, desktop_runtime, foreground

    s, w, b, base, _, hover = fixture(monkeypatch)
    s.identity = {"pid": 7}
    s.viewport_size = lambda: (1888, 1080)
    target = NS(hwnd=7, snapshot=lambda: {"client_size": [1888, 1080]})
    calls = []
    monkeypatch.setattr(reconnect, "login_screen", lambda hwnd: True)
    monkeypatch.setattr(focus_recovery, "activate_client", lambda *a: True)
    monkeypatch.setattr(reconnect, "dismiss_login_error", lambda *a: False)
    monkeypatch.setattr(
        reconnect,
        "load_credentials",
        lambda path: {"username": "private-user", "password": "private-pass"},
    )
    monkeypatch.setattr(desktop_runtime, "physical_coordinates", nullcontext)

    def click(*args, **kwargs):
        kwargs["before_press"]()
        calls.append("click")
        b[0x43F04] = struct.pack("<I", zlib.crc32(hover[-1][1].encode(), 123))

    def field(target, value, *, field_guard):
        field_guard()
        calls.append("field")

    monkeypatch.setattr(foreground, "foreground_click", click)
    monkeypatch.setattr(reconnect, "type_login_field", field)
    assert reconnect.submit_login.__wrapped__(target, session=s) == {"submitted": True}
    assert calls == ["click", "field", "click", "field", "click"]
    assert [label for _, label in hover] == [
        "##username",
        "##username",
        "##password",
        "##password",
        "Login",
    ]


def test_invalid_native_form_never_loads_credentials_or_clicks(monkeypatch):
    from conquest import reconnect, focus_recovery, foreground

    s, w, b, base, _, _ = fixture(monkeypatch)
    s.identity = {"pid": 7}
    monkeypatch.setattr(reconnect, "login_screen", lambda hwnd: True)
    monkeypatch.setattr(focus_recovery, "activate_client", lambda *a: True)
    monkeypatch.setattr(reconnect, "dismiss_login_error", lambda *a: False)
    monkeypatch.setattr(
        reconnect,
        "load_credentials",
        lambda *a: pytest.fail("No credential decryption"),
    )
    monkeypatch.setattr(
        foreground, "foreground_click", lambda *a, **k: pytest.fail("No input")
    )
    b[base + native.ROOT_RVA + 0x708] = b"\1"
    with pytest.raises(ValueError, match="resolved"):
        reconnect.submit_login.__wrapped__(NS(hwnd=7), session=s)
