"""Exact-build read-only layouts shared by narrowly qualified readers."""
from dataclasses import dataclass

from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.reader_1078 import CLIENT_SHA256_1078


@dataclass(frozen=True)
class WarehouseReadLayout:
    expected_sha256: str
    gui_context_rva: int
    gui_registry_rva: int
    warehouse_root_rva: int
    warehouse_deque_offset: int
    warehouse_capacity_offset: int
    warehouse_silver_offset: int
    item_vtable_rva: int
    warehouse_model_key: int
    warehouse_model_vtable_rva: int


WAREHOUSE_LAYOUTS = {
    CLIENT_SHA256: WarehouseReadLayout(CLIENT_SHA256, 0x6966F0, 0x6986C0,
        0x69C730, 0x1008, 0x1030, 0x1044, 0x5CF220, 0x16, 0x5CBA68),
    CLIENT_SHA256_1078: WarehouseReadLayout(CLIENT_SHA256_1078, 0x6B5EF0, 0x6B8E48,
        0x6BCEF0, 0x1030, 0x1058, 0x106C, 0x5EA9F8, 0x16, 0x5E7250),
}


def warehouse_read_layout(session):
    """Select only an exact, explicitly qualified read-only warehouse layout."""
    try:return WAREHOUSE_LAYOUTS[session.expected_sha256]
    except KeyError as error:raise ValueError('Warehouse reader has no qualified client layout') from error
