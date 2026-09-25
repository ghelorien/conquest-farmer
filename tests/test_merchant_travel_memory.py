"""Travel must not depend on booth IDs or produce a delivery snapshot."""

from types import SimpleNamespace as NS
import struct
import pytest
from conquest.merchants import memory


def test_merchant_memory_fails_closed_outside_the_1078_session_reader(monkeypatch):
    from conquest import memory_build_layout
    from conquest.memory_life import CLIENT_SHA256

    def untouched(*args):
        pytest.fail("An unqualified merchant reader must not read client memory")

    adapter = NS(
        expected_sha256=CLIENT_SHA256,
        modules=[{"name": "imconquer.exe", "base": 0x140000000, "size": 1}],
        identity={"pid": 7},
        read=untouched,
        read_block=untouched,
        assert_identity=lambda: None,
    )
    observer = NS(adapter=adapter, character="Dutch", health_layout=object())
    with pytest.raises(ValueError, match="Unqualified merchant client"):
        memory.MerchantMemory(observer)
    # Even if a retired layout is still registered, only 1078 is accepted.
    monkeypatch.setattr(
        memory_build_layout,
        "read_build_layout",
        lambda session: NS(expected_sha256=session.expected_sha256),
    )
    with pytest.raises(ValueError, match="only for 1078"):
        memory.MerchantMemory.for_observer(observer)
    reader = memory.MerchantMemory.__new__(memory.MerchantMemory)
    with pytest.raises(ValueError, match="not qualified"):
        reader.read_travel()
    with pytest.raises(ValueError, match="not qualified"):
        reader.booth_pointer(0, 1)
    for flags in ({"recovery": True}, {"farmer_preflight": True}):
        with pytest.raises(ValueError, match="not qualified"):
            reader.read(**flags)


def test_transit_waypoint_uses_camera_anchor_and_current_viewport(monkeypatch):
    from conquest.merchants import return_driver as route

    monkeypatch.setattr(route, "clear_segment", lambda *args: True)
    path = [(100, 100 + i) for i in range(20)]
    assert route.transit_waypoint(None, path, (700, 300), (1400, 900)) == (100, 112)
    # Near the left camera edge, the same twelve-tile click is off screen.
    assert route.transit_waypoint(None, path, (220, 300), (1400, 900)) == (100, 104)
    with pytest.raises(ValueError, match="No visible"):
        route.transit_waypoint(None, path, (90, 300), (1400, 900))


def test_stall_approach_does_not_target_the_assumed_booth_standing_tile():
    from conquest.merchants.return_driver import stall_approach
    from conquest.navigation import line_tiles

    terrain = NS(travel_path=lambda start, end: line_tiles(start, end))
    distance, target = stall_approach(terrain, (223, 185), {"position": [230, 185]})
    assert target == (228, 185) and distance == 6


@pytest.mark.parametrize("variant", ["valid", "missing_uid", "changed_accessor"])
def test_character_identity_uses_pinned_self_field_not_booth_field(variant):
    base = 0x1000000
    actor = 0x2000000
    values = {
        base + 0x8DC8: bytes.fromhex("e8638d17008b486841394f10"),
        base + 0x97BC: bytes.fromhex("e86f8317008b4868394e687520"),
        actor + 0x68: struct.pack("<I", 123456),
        actor + 0x3258: bytes(4),
    }
    if variant == "missing_uid":
        values[actor + 0x68] = bytes(4)
    if variant == "changed_accessor":
        values[base + 0x8DC8] = bytes(12)
    session = NS(read_block=lambda address, size: values[address])
    if variant == "valid":
        assert memory.character_uid(session, base, actor) == 123456
    else:
        with pytest.raises(ValueError):
            memory.character_uid(session, base, actor)


@pytest.mark.parametrize("transient", [False, True])
def test_travel_retries_only_torn_observations_without_sending_input(
    monkeypatch, transient
):
    from conquest.merchants import return_driver as route

    calls = []

    def read():
        calls.append("read")
        if len(calls) == 1:
            if transient:
                raise memory.TransitObservationChanged("position changed")
            raise ValueError("wrong character")
        return {"position": [223, 185]}

    monkeypatch.setattr(route.time, "sleep", lambda _: None)
    driver = route.ReturnDriver(
        NS(observer=object(), memory=NS(read_travel=read)), travel_only=True
    )
    if transient:
        assert driver.read() == {"position": [223, 185]} and calls == ["read", "read"]
    else:
        with pytest.raises(ValueError, match="wrong character"):
            driver.read()
        assert calls == ["read"]
