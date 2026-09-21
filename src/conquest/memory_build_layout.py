"""Exact-build, read-only layout selection shared by memory primitives."""
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


CLIENT_SHA256_1074='c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396'
CLIENT_SHA256_1078='be9dd723cad8eb9068da792b5cb8ceec0d330f08aacb8c948e6f412d1520c4e0'


@dataclass(frozen=True)
class ReadBuildLayout:
    expected_sha256: str
    player_profile: str
    health_profile: str
    inventory_profile: str
    item_vtable_rva: int
    equipment_slots: Mapping[str, int]
    gui_context_rva: int
    gui_registry_rva: int
    warehouse_root_rva: int
    warehouse_deque_offset: int
    warehouse_capacity_offset: int
    warehouse_silver_offset: int
    warehouse_model_key: int
    warehouse_model_vtable_rva: int
    shop_root_rva: int
    shop_vtable_rva: int
    dialog_records_offset: int
    map_rva: int
    life_status_offset: int
    life_appearance_offset: int
    life_revive_gate_offset: int
    conservative_life_block: bool
    entity_root_rva: int
    entity_pointer_offsets: tuple[int, ...]
    entity_collection_vtable_rva: int
    entity_actor_vtable_rva: int
    entity_attribute_pointer_offset: int
    ground_registry_rva: int
    ground_holder_vtable_rva: int
    ground_actor_vtable_rva: int


_OLD_SLOTS=MappingProxyType({'head':0xbd8,'necklace':0xbe8,'armor':0xbf8,'bow':0xc08,
                              'arrows':0xc18,'ring':0xc28,'boots':0xc48})
_NEW_SLOTS=MappingProxyType({'head':0xc00,'necklace':0xc10,'armor':0xc20,'bow':0xc30,
                              'arrows':0xc40,'ring':0xc50,'boots':0xc70})
READ_LAYOUTS = {
    CLIENT_SHA256_1074: ReadBuildLayout(CLIENT_SHA256_1074,'classic-1074-player-candidate.yaml','classic-1074-health-candidate.yaml',
        'classic-1074-inventory-candidate.yaml',0x5CF220,_OLD_SLOTS,0x6966F0,0x6986C0,
        0x69C730,0x1008,0x1030,0x1044,0x16,0x5CBA68,0x69A740,0x5D0088,0x1060,
        0x699564,0x30,0xc0,0xae8,False,0x699370,(0x18,8,0),0x5ccc90,0x5c5e20,0x978,
        0x6994d8,0x5ccc08,0x5cdaf0),
    CLIENT_SHA256_1078: ReadBuildLayout(CLIENT_SHA256_1078,'classic-1078-player-candidate.yaml','classic-1078-health-candidate.yaml',
        'classic-1078-inventory-candidate.yaml',0x5EA9F8,_NEW_SLOTS,0x6B5EF0,0x6B8E48,
        0x6BCEF0,0x1030,0x1058,0x106C,0x16,0x5E7250,0x6BAF30,0x5EB8D0,0x1088,
        0x6B9D44,0x30,0xc0,0xb10,True,0x6B9B50,(0x18,8,0),0x5e8a60,0x5e12d0,0x988,
        0x6B9CB8,0x5e83f0,0x5e92f8),
}


def read_build_layout(session):
    try:return READ_LAYOUTS[session.expected_sha256]
    except KeyError as error:raise ValueError('No qualified read layout for this client build') from error


def warehouse_read_layout(session):
    return read_build_layout(session)


def inventory_reader_layouts(session):
    """Load the exact player/inventory pair for existing reader primitives."""
    import yaml
    from conquest.addressing import PlayerLayout
    from conquest.memory_inventory import InventoryLayout
    layout=read_build_layout(session);root=Path(__file__).resolve().parents[2]/'profiles'
    player=PlayerLayout.model_validate(yaml.safe_load((root/layout.player_profile).read_text(encoding='utf-8')))
    inventory=InventoryLayout.model_validate(yaml.safe_load((root/layout.inventory_profile).read_text(encoding='utf-8')))
    return player,inventory


def actual_player_layout(session):
    """Load the exact actual-character owner, never the inventory wrapper."""
    import yaml
    from conquest.memory_health import HealthLayout
    layout=read_build_layout(session);root=Path(__file__).resolve().parents[2]/'profiles'
    return HealthLayout.model_validate(yaml.safe_load(
        (root/layout.health_profile).read_text(encoding='utf-8'))).player


def health_reader_layout(session):
    import yaml
    from conquest.memory_health import HealthLayout
    layout=read_build_layout(session);root=Path(__file__).resolve().parents[2]/'profiles'
    return HealthLayout.model_validate(yaml.safe_load(
        (root/layout.health_profile).read_text(encoding='utf-8')))


def entity_reader_layout(session):
    """Select the existing entity decoder's exact field profile."""
    import yaml
    from conquest.memory_entities import EntityLayout
    layout=read_build_layout(session);root=Path(__file__).resolve().parents[2]/'profiles'
    old=EntityLayout.model_validate(yaml.safe_load(
        (root/'classic-1074-entities-candidate.yaml').read_text(encoding='utf-8')))
    return old.model_copy(update={
        'expected_sha256':layout.expected_sha256,'root_rva':layout.entity_root_rva,
        'pointer_offsets':layout.entity_pointer_offsets,
        'collection_vtable_rva':layout.entity_collection_vtable_rva,
        'monster_vtable_rva':layout.entity_actor_vtable_rva,
        'max_hp_offset':0x3f0 if layout.expected_sha256==CLIENT_SHA256_1078 else old.max_hp_offset,
        'level_offset':0x708 if layout.expected_sha256==CLIENT_SHA256_1078 else old.level_offset,
        'attribute_pointer_offset':layout.entity_attribute_pointer_offset,
    })
