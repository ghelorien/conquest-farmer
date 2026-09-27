"""The manual-ownership reader qualifies the client's server field.

Failure modes (written before the change):
1. A Classic_B2C client is refused, so a Back2Classic farmer's trade
   request can never be read closed again and its hold never settles.
2. The snapshot labels a Back2Classic read "America", which would let
   America-only merchant and delivery paths trust it.
3. The end-of-read stability check compares the label with the raw field
   and refuses every read ("server field changed during observation").
4. An unqualified server (Classic_EU, blank) is read.
5. A server field that changes mid-read is still accepted.
"""

import struct
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import reader_1078
from conquest.merchants.reader_1078 import (
    ObservationUnavailable1078,
    TradeObservationReader1078,
)

BASE = 0x400000
ACTUAL = 0x1000
WRAPPER = 0x2000


def field(text):
    return text.ljust(64, b"\0")


def reader(monkeypatch, servers):
    """`servers` yields the raw server field for each read of it."""
    x = TradeObservationReader1078.__new__(TradeObservationReader1078)
    x.base = BASE
    x.character = "Suicide"
    x.session = NS(assert_identity=lambda: None, identity={"pid": 1})
    x.capabilities = {}
    servers = iter(servers)
    memory = {
        ACTUAL + 0xD8: struct.pack("<2I", 300, 400),
        ACTUAL + 0x94: field(b"Suicide"),
        WRAPPER + 0xA4: field(b"Suicide"),
    }

    def read(address, size):
        if address == BASE + x.server_rva:
            return next(servers)
        return memory[address]

    x._read = read
    x._u32 = lambda address: 7101 if address == ACTUAL + 0x68 else 1002
    x._u8 = lambda address: 0
    x._model = lambda index, vtable: 0
    x._actual_and_wrapper = lambda: (0, ACTUAL, WRAPPER)
    x._inventory = lambda wrapper: ([], 1300)
    x._booth = lambda actual: ([], 0, False)
    x._health = lambda actual: {"current_hp_candidate": 213}
    x._trade = lambda actual: None
    x._request = lambda actual: None
    x._assert_actor_stable = lambda *a: None
    monkeypatch.setattr(
        "conquest.merchants.manual_sessions.canonical_ownership",
        lambda result, require_closed: None,
    )
    return x


@pytest.mark.parametrize(
    "raw, label", [(b"Classic_B2C", "Back2Classic"), (b"Classic_US", "America")]
)
def test_qualified_server_reads_closed_with_its_own_label(monkeypatch, raw, label):
    x = reader(monkeypatch, [field(raw)] * 2)
    snapshot = x.read_manual_ownership()
    assert snapshot["server"] == label
    assert snapshot["canonical_manual_ownership"] is True


@pytest.mark.parametrize("raw", [b"Classic_EU", b""])
def test_unqualified_server_is_refused(monkeypatch, raw):
    x = reader(monkeypatch, [field(raw)] * 2)
    with pytest.raises(ObservationUnavailable1078, match="qualified server"):
        x.read_manual_ownership()


def test_server_field_changing_mid_read_is_refused(monkeypatch):
    x = reader(monkeypatch, [field(b"Classic_B2C"), field(b"Classic_US")])
    with pytest.raises(ObservationUnavailable1078, match="changed during"):
        x.read_manual_ownership()


def test_reader_module_keeps_its_exact_build_pin():
    assert reader_1078.CLIENT_SHA256_1078
