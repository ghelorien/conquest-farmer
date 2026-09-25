"""Exact-build, read-only layout selection shared by memory primitives."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


CLIENT_SHA256_1074 = "c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396"
CLIENT_SHA256_1078 = "be9dd723cad8eb9068da792b5cb8ceec0d330f08aacb8c948e6f412d1520c4e0"


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
    learned_skills_offset: int
    selectable_skill_offsets: tuple[int, int]
    xp_skills_offset: int
    xp_charge_offset: int
    bow_offset: int
    skill_vtable_rva: int
    selected_skill_vtable_rva: int
    selection_renderer_pins: tuple[tuple[int, int, str], ...]
    merchant_server_rva: int
    merchant_booth_offset: int
    merchant_own_booth_offset: int
    merchant_trade_vtable_rva: int
    merchant_confirm_vtable_rva: int
    merchant_booth_vtable_rva: int
    merchant_uid_accessor_pins: tuple[tuple[int, str], ...]


_OLD_SLOTS = MappingProxyType(
    {
        "head": 0xBD8,
        "necklace": 0xBE8,
        "armor": 0xBF8,
        "bow": 0xC08,
        "arrows": 0xC18,
        "ring": 0xC28,
        "boots": 0xC48,
    }
)
_NEW_SLOTS = MappingProxyType(
    {
        "head": 0xC00,
        "necklace": 0xC10,
        "armor": 0xC20,
        "bow": 0xC30,
        "arrows": 0xC40,
        "ring": 0xC50,
        "boots": 0xC70,
    }
)
READ_LAYOUTS = {
    CLIENT_SHA256_1074: ReadBuildLayout(
        CLIENT_SHA256_1074,
        "classic-1074-player-candidate.yaml",
        "classic-1074-health-candidate.yaml",
        "classic-1074-inventory-candidate.yaml",
        0x5CF220,
        _OLD_SLOTS,
        0x6966F0,
        0x6986C0,
        0x69C730,
        0x1008,
        0x1030,
        0x1044,
        0x16,
        0x5CBA68,
        0x69A740,
        0x5D0088,
        0x1060,
        0x699564,
        0x30,
        0xC0,
        0xAE8,
        False,
        0x699370,
        (0x18, 8, 0),
        0x5CCC90,
        0x5C5E20,
        0x978,
        0x6994D8,
        0x5CCC08,
        0x5CDAF0,
        0x1968,
        (0x1980, 0x19B0),
        0x1998,
        0x3CC,
        0xC08,
        0x5CFF78,
        0x5C5A38,
        (
            (
                0x9ACC2,
                314,
                "0da14be41a59bd653ed11d0f576ebebfeb477d3a5aa9679fa5f0b30046b2ab5a",
            ),
            (
                0x1097A5,
                19,
                "213a60b72ae5bd5566a3c7af1e7106d80ce2291a7134c87584c03d5653d35075",
            ),
            (
                0x109845,
                190,
                "8db295b764220983700cdc0e9d6f69c95fef86c22a191d7e3a8800a5ecd44527",
            ),
            (
                0x10995C,
                19,
                "7e6b636f9cbe2d438fe2b3737dd4fe941c7098ba4e38ef75eba0df8bd3d96366",
            ),
        ),
        0x697860,
        0x3468,
        0x3258,
        0x5CB328,
        0x5C4F30,
        0x5C27F8,
        ((0x8DC8, "e8638d17008b486841394f10"), (0x97BC, "e86f8317008b4868394e687520")),
    ),
    CLIENT_SHA256_1078: ReadBuildLayout(
        CLIENT_SHA256_1078,
        "classic-1078-player-candidate.yaml",
        "classic-1078-health-candidate.yaml",
        "classic-1078-inventory-candidate.yaml",
        0x5EA9F8,
        _NEW_SLOTS,
        0x6B5EF0,
        0x6B8E48,
        0x6BCEF0,
        0x1030,
        0x1058,
        0x106C,
        0x16,
        0x5E7250,
        0x6BAF30,
        0x5EB8D0,
        0x1088,
        0x6B9D44,
        0x30,
        0xC0,
        0xB10,
        True,
        0x6B9B50,
        (0x18, 8, 0),
        0x5E8A60,
        0x5E12D0,
        0x988,
        0x6B9CB8,
        0x5E83F0,
        0x5E92F8,
        0x1990,
        (0x19A8, 0x19D8),
        0x19C0,
        0x3DC,
        0xC30,
        0x5EB7B8,
        0x5E0CB8,
        (
            (
                0x9BF92,
                314,
                "24d86082c85a31abb467bf6d4ea68b518bfccf7001f19205659ae842baa05886",
            ),
            (
                0x10DD75,
                19,
                "6aac4ef4f2fa3b392d27ee24dcab5ea875c590217654f37300d668aee4c152a9",
            ),
            (
                0x10DE15,
                190,
                "2ecad0ce9f726c83a31fd12ca30bad5a25e26f9ab22c9479970bdc66fe10266d",
            ),
            (
                0x10DF2C,
                19,
                "b8c9185de155072def8fdc9365403e73a2e36f7dfb16dca0940e1cffde8b84f3",
            ),
        ),
        0x6B7FC0,
        0x34B0,
        0x32A0,
        0x5E6A80,
        0x5E0148,
        0x5DD9C0,
        ((0x925B, "e8201618008b486841394f10"), (0xA1D3, "e8a80618008b4868394e687520")),
    ),
}


def read_build_layout(session):
    try:
        return READ_LAYOUTS[session.expected_sha256]
    except KeyError as error:
        raise ValueError("No qualified read layout for this client build") from error


def warehouse_read_layout(session):
    return read_build_layout(session)


def inventory_reader_layouts(session):
    """Load the exact player/inventory pair for existing reader primitives."""
    import yaml
    from conquest.addressing import PlayerLayout
    from conquest.memory_inventory import InventoryLayout

    layout = read_build_layout(session)
    root = Path(__file__).resolve().parents[2] / "profiles"
    player = PlayerLayout.model_validate(
        yaml.safe_load((root / layout.player_profile).read_text(encoding="utf-8"))
    )
    inventory = InventoryLayout.model_validate(
        yaml.safe_load((root / layout.inventory_profile).read_text(encoding="utf-8"))
    )
    return player, inventory


def actual_player_layout(session):
    """Load the exact actual-character owner, never the inventory wrapper."""
    import yaml
    from conquest.memory_health import HealthLayout

    layout = read_build_layout(session)
    root = Path(__file__).resolve().parents[2] / "profiles"
    return HealthLayout.model_validate(
        yaml.safe_load((root / layout.health_profile).read_text(encoding="utf-8"))
    ).player


def health_reader_layout(session):
    import yaml
    from conquest.memory_health import HealthLayout

    layout = read_build_layout(session)
    root = Path(__file__).resolve().parents[2] / "profiles"
    return HealthLayout.model_validate(
        yaml.safe_load((root / layout.health_profile).read_text(encoding="utf-8"))
    )


def entity_reader_layout(session):
    """Select the existing entity decoder's exact field profile."""
    import yaml
    from conquest.memory_entities import EntityLayout

    # The entity profile is keyed per build. Only 1078 has one; every other
    # build fails closed rather than inheriting another build's fields.
    layout = read_build_layout(session)
    if layout.expected_sha256 != CLIENT_SHA256_1078:
        raise ValueError("No qualified entity layout for this client build")
    root = Path(__file__).resolve().parents[2] / "profiles"
    return EntityLayout.model_validate(
        yaml.safe_load(
            (root / "classic-1078-entities-candidate.yaml").read_text(encoding="utf-8")
        )
    )
