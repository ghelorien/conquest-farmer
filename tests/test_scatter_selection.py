import struct
from types import SimpleNamespace as NS
import pytest
from conquest import scatter_selection as s


def reader_fixture(monkeypatch):
    monkeypatch.setattr(
        s, "MemoryGui", lambda session, layout=None: NS(base=0, read=lambda name: None)
    )
    blocks = {
        0x6986C0: struct.pack("<QQ", 0x10000, 1),
        0x10008: struct.pack("<Q", 0x20000),
    }
    node = bytearray(0x38)
    struct.pack_into("<3Q", node, 0, 0x10000, 0x10000, 0x10000)
    struct.pack_into("<I", node, 0x20, 1)
    struct.pack_into("<Q", node, 0x28, 0x30000)
    control = bytearray(0xFC)
    struct.pack_into("<Q", control, 0, 0x5C5A38)
    blocks[0x20000] = node
    blocks[0x30000] = control

    def read_block(address, size):
        if address in blocks:
            return bytes(blocks[address][:size])
        # Field rereads (e.g. selected ID at +0xF8) address inside a block.
        for base, data in blocks.items():
            if base < address and address + size <= base + len(data):
                return bytes(data[address - base : address - base + size])
        raise KeyError(address)

    reader = s.SelectionReader(NS(read_block=read_block, assert_identity=lambda: None))
    reader.qualified = True
    return reader, blocks, control


def test_selected_id_is_read_from_qualified_control(monkeypatch):
    reader, blocks, control = reader_fixture(monkeypatch)
    assert reader.selected() == 0
    struct.pack_into("<I", control, 0xF8, 8001)
    assert reader.selected() == 8001
    struct.pack_into("<Q", control, 0, 0x123456)
    with pytest.raises(ValueError, match="identity"):
        reader.selected()


def test_registry_cycle_is_rejected(monkeypatch):
    reader, blocks, _ = reader_fixture(monkeypatch)
    struct.pack_into("<I", blocks[0x20000], 0x20, 2)
    struct.pack_into("<Q", blocks[0x20000], 0, 0x20000)
    with pytest.raises(ValueError, match="unavailable"):
        reader.selected()


def test_selection_waits_for_memory_confirmation(monkeypatch):
    selected = [0]
    opened = [False]
    now = [0.0]
    events = []
    clicks = []

    def window(name):
        if not opened[0]:
            raise ValueError("Not open")
        return NS()

    reader = NS(
        selected=lambda: selected[0],
        gui=NS(read=window),
        menu_point=lambda: (1148, 979),
        scatter_point=lambda actor: (1296, 857),
    )
    monkeypatch.setattr(s, "SelectionReader", NS(for_session=lambda adapter: reader))
    monkeypatch.setattr(s.time, "monotonic", lambda: now[0])
    state = s.ScatterSelection(
        NS(
            adapter=None,
            health_layout=None,
            character="Parasite",
            # A 1078 observer supplies its own exact-build life read.
            read_life=lambda: NS(dead_candidate=False, object_address=7),
        ),
        lambda e, p: events.append(e),
    )
    assert state.step(clicks.append) and clicks == [(1148, 979)]
    opened[0] = True
    assert state.step(clicks.append) and len(clicks) == 1
    now[0] = 0.3
    assert state.step(clicks.append) and clicks[-1] == (1296, 857)
    assert "scatter_selection_verified" not in events
    selected[0] = 8001
    assert not state.step(clicks.append)
    assert events[-1] == "scatter_selection_verified"
    assert not state.step(clicks.append) and len(clicks) == 2


@pytest.mark.parametrize("level", [0, 4, 5, 9])
def test_selectable_scatter_does_not_depend_on_skill_level(monkeypatch, level):
    reader, blocks, _ = reader_fixture(monkeypatch)
    actor = 0x40000
    blocks[actor + 0x1980] = struct.pack("<3Q", 0, 0, 0)
    blocks[actor + 0x19B0] = struct.pack("<3Q", 0x50000, 0x50010, 0x50010)
    blocks[0x50000] = struct.pack("<2Q", 0x60000, 0)
    skill = bytearray(0x38)
    struct.pack_into("<Q", skill, 0, 0x5CFF78)
    struct.pack_into("<I", skill, 8, 1)
    struct.pack_into("<II", skill, 0x10, 8001, level)
    skill[0x18:0x20] = b"Scatter\0"
    blocks[0x60000] = skill
    assert reader.entries(actor) == [8001]
    struct.pack_into("<I", skill, 8, 0)
    with pytest.raises(ValueError, match="unavailable"):
        reader.entries(actor)


def test_icon_uses_current_popup_and_column_geometry(monkeypatch):
    reader, _, _ = reader_fixture(monkeypatch)
    window = NS(
        name="Skills", position=(1268.0, 829.0), size=(152.0, 60.0), scroll=(0.0, 0.0)
    )
    reader.gui.read = lambda name: window
    reader.entries = lambda actor: [8001]
    columns = bytearray(7 * 0x68)
    struct.pack_into("<f", columns, 0x10, 40)
    struct.pack_into("<f", columns, 0x34, 1276)
    reader.table = lambda w: (7, (1276.0, 837.0, 1412.0, 881.0), columns)
    assert reader.scatter_point(1) == (1296, 857)
    window.scroll = (0.0, 10.0)
    with pytest.raises(ValueError, match="scrolled"):
        reader.scatter_point(1)
