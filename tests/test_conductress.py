import struct
from types import SimpleNamespace
import pytest
from conquest import conductress as c


@pytest.fixture
def dialog(monkeypatch):
    records = [
        {
            "kind": 0,
            "option": 0,
            "text": "Where are you heading? I can teleport you for a price of 100 silver.",
        }
    ]
    records += [
        {"kind": 1, "option": i, "text": name}
        for i, name in enumerate(
            [
                "Phoenix Castle",
                "Desert City",
                "Ape Mountain",
                "Bird Island.",
                "Mine Cave",
                "Market",
                "Just passing by.",
            ]
        )
    ]
    data = {
        "records": records,
        "window": SimpleNamespace(
            position=(378.0, 20.0), size=(280.0, 198.0), scroll=(0.0, 0.0)
        ),
        "table": (398.0, 130.0, 618.0, 88.0),
    }
    monkeypatch.setattr(c, "read_dialog", lambda observer: data)
    return data


def test_choices_use_live_dialog_table_position(dialog):
    assert c.destination_point(None, "Phoenix Castle") == (453, 141)
    assert c.destination_point(None, "Bird Island.") == (563, 163)
    dialog["window"].position = (478.0, 120.0)
    dialog["table"] = (498.0, 230.0, 718.0, 88.0)
    assert c.destination_point(None, "Phoenix Castle") == (553, 241)


@pytest.mark.parametrize("change", ["price", "id", "size", "input", "scroll"])
def test_changed_dialog_never_selects_destination(dialog, change):
    if change == "price":
        dialog["records"][0]["text"] = "Price is 1000 silver"
    if change == "id":
        dialog["records"][1]["option"] = 5
    if change == "size":
        dialog["window"].size = (280.0, 100.0)
    if change == "input":
        dialog["records"].append({"kind": 2, "text": "", "option": 0})
    if change == "scroll":
        dialog["window"].scroll = (1.0, 22.0)
    with pytest.raises(ValueError):
        c.destination_point(None, "Phoenix Castle")


def test_resized_twin_dialog_keeps_exact_destination_checks(dialog):
    dialog["window"].size = (356.0, 234.0)
    dialog["table"] = (398.0, 130.0, 714.0, 88.0)
    assert c.destination_point(None, "Phoenix Castle") == (477, 141)
    assert c.destination_point(None, "Bird Island.") == (635, 163)


def test_dialog_reader_copies_and_rechecks_deque_and_strings(monkeypatch):
    actor, table, shared, record, window = (
        0x100000,
        0x200000,
        0x300000,
        0x400000,
        0x500000,
    )
    from conquest.memory_build_layout import CLIENT_SHA256_1078, READ_LAYOUTS

    dialog_offset = READ_LAYOUTS[CLIENT_SHA256_1078].dialog_records_offset
    memory = {
        actor + dialog_offset: struct.pack("<4Q", table, 1, 0, 1),
        table: struct.pack("<Q", shared),
        shared: struct.pack("<2Q", record, 0),
    }
    raw = bytearray(48)
    struct.pack_into("<3I", raw, 0, 1, 32, 0)
    raw[16:22] = b"Market"
    struct.pack_into("<2Q", raw, 32, 6, 15)
    memory[record] = bytes(raw)
    dc = bytearray(56)
    struct.pack_into("<2f", dc, 8, 618, 130)
    struct.pack_into("<f", dc, 16, 398)
    struct.pack_into("<f", dc, 52, 88)
    memory[window + 0xE0] = bytes(dc)
    # The observer wrapper selects its read layout from the 1078 client build.
    adapter = SimpleNamespace(
        read_block=lambda a, n: memory[a][:n],
        expected_sha256=CLIENT_SHA256_1078,
        assert_identity=lambda: None,
    )
    observer = SimpleNamespace(
        adapter=adapter,
        character="Parasite",
        read_life=lambda: SimpleNamespace(object_address=actor, dead_candidate=False),
    )
    monkeypatch.setattr(
        c,
        "MemoryGui",
        lambda s, layout=None: SimpleNamespace(
            read=lambda name: SimpleNamespace(address=window)
        ),
    )
    result = c.read_dialog(observer)
    assert result["records"] == [{"kind": 1, "option": 0, "text": "Market"}]
    assert result["table"] == (398, 130, 618, 88)
    reads = 0

    def changed(a, n):
        nonlocal reads
        if a == record:
            reads += 1
            if reads == 2:
                return bytes(n)
        return memory[a][:n]

    adapter.read_block = changed
    with pytest.raises(ValueError, match="changed"):
        c.read_dialog(observer)


def test_uncertain_teleport_is_not_paid_again(monkeypatch, tmp_path, dialog):
    import json

    path = tmp_path / "trips.json"
    path.write_text(
        json.dumps(
            {
                "trips": [
                    dict(
                        destination_map=1011,
                        source_map=1002,
                        verified=True,
                        option="Phoenix Castle",
                        arrival_map=1002,
                        arrival_position=[958, 555],
                        price=100,
                    )
                ]
            }
        )
    )
    monkeypatch.setattr(c, "TRIPS", path)
    monkeypatch.setattr(c.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(c.time, "time", lambda: 10)
    calls = []

    def town(action, **fields):
        calls.append(action)
        if action == "service-dialog":
            return dialog
        return {"silver": 1000}

    health = {
        "embedded_controls": {
            "observed_at": 10,
            "life": dict(
                object_address=123,
                map_id=1002,
                dead_candidate=False,
                position=[958, 555],
            ),
        }
    }
    loop = SimpleNamespace(
        living=lambda: health,
        health=lambda: health,
        town=town,
        travel=lambda p: None,
        record=lambda *a, **kw: None,
        check_stop=lambda: None,
    )
    with pytest.raises(ValueError, match="no repeat payment"):
        c.take_saved_trip(loop, 1011)
    assert calls.count("conductress-travel") == 1
    health["embedded_controls"]["life"]["map_id"] = 1011
    calls.clear()
    with pytest.raises(ValueError, match="source map"):
        c.take_saved_trip(loop, 1011)
    assert calls == []


def test_a_conductress_outside_twin_city_rides_the_saved_service(monkeypatch, tmp_path):
    # A verified trip from another city goes through the live-verified
    # saved-service path of the Phoenix Market trips.
    import json
    from pathlib import Path
    from conquest import banking, meteor_banking

    market = json.loads(Path("profiles/meteor-banking.json").read_text(encoding="utf-8"))
    outbound = market["origins"]["1011"]["outbound"]
    trip = {
        "source_map": 1011,
        "npc_name": "Conductress",
        "option": "Market",
        "destination_map": 1036,
        "arrival_map": 1036,
        "price": 100,
        "verified": True,
        "service": {
            "approach": outbound["approach"],
            "identity": outbound["identity"],
            "records": outbound["dialogs"][0]["records"],
        },
    }
    path = tmp_path / "trips.json"
    path.write_text(json.dumps({"trips": [trip]}), encoding="utf-8")
    monkeypatch.setattr(c, "TRIPS", path)
    plans, events = [], []
    monkeypatch.setattr(meteor_banking, "trip", lambda loop, plan: plans.append(plan))
    monkeypatch.setattr(banking, "ensure_transport", lambda loop, minimum: None)
    life = {"map_id": 1011, "object_address": 1, "position": [228, 250]}
    loop = SimpleNamespace(
        living=lambda: {"embedded_controls": {"life": life}},
        record=lambda event, **fields: events.append(event),
    )
    assert c.take_saved_trip(loop, 1036) is True
    [plan] = plans
    assert (plan["source_map"], plan["destination_map"], plan["fare"]) == (1011, 1036, 100)
    assert plan["identity"] == outbound["identity"]
    assert plan["approach"] == outbound["approach"]
    assert plan["dialogs"] == outbound["dialogs"]
    assert events == ["conductress_departing", "conductress_arrived"]


def test_phoenix_twin_city_ride_is_never_paid_for():
    # Live 2026-09-29 (Toxic): Phoenix's "Twin City" option charged 100 and
    # dropped the farmer at Phoenix's own west gate, twice. It stays saved
    # as evidence, unverified, so travel never buys it.
    import json
    from pathlib import Path

    trips = json.loads(Path("profiles/conductress-routes.json").read_text(encoding="utf-8"))
    [ride] = [t for t in trips["trips"] if t["source_map"] == 1011]
    assert ride["option"] == "Twin City" and ride["verified"] is False
    assert (ride["arrival_map"], ride["arrival_position"]) == (1011, [11, 376])
    life = {"map_id": 1011, "object_address": 1, "position": [228, 250]}
    no_calls = SimpleNamespace(
        living=lambda: {"embedded_controls": {"life": life}},
        record=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no ride")),
    )
    assert c.take_saved_trip(no_calls, 1002) is False


def test_the_ape_city_trip_rides_to_ape_mountain_then_portal_9():
    import json
    from pathlib import Path
    from conquest.world_travel import connection_path

    trips = json.loads(Path("profiles/conductress-routes.json").read_text(encoding="utf-8"))
    [ape] = [t for t in trips["trips"] if t["destination_map"] == 1020]
    assert (ape["source_map"], ape["option"], ape["arrival_map"]) == (1002, "Ape Mountain", 1002)
    assert ape["arrival_position"] == [555, 957] and ape["price"] == 100
    # Phoenix -> Twin City (the Conductress above) -> Ape City by portal 9.
    path = connection_path(1011, 1020)
    assert [(e["source_map"], e["portal_id"], e["destination_map"]) for e in path] == [
        (1011, 0, 1002),
        (1002, 9, 1020),
    ]
