from types import SimpleNamespace
from pathlib import Path

import yaml

from conquest.memory_build_layout import (
    CLIENT_SHA256_1078,
    READ_LAYOUTS,
    entity_reader_layout,
)
from conquest.memory_entities import EntityLayout

PROFILE = (
    Path(__file__).resolve().parents[1]
    / "profiles/classic-1078-entities-candidate.yaml"
)


def test_1078_entities_profile_agrees_with_read_layout():
    entities = EntityLayout.model_validate(yaml.safe_load(PROFILE.read_text()))
    layout = READ_LAYOUTS[CLIENT_SHA256_1078]
    assert entities.expected_sha256 == CLIENT_SHA256_1078
    assert entities.root_rva == layout.entity_root_rva
    assert tuple(entities.pointer_offsets) == tuple(layout.entity_pointer_offsets)
    assert entities.collection_vtable_rva == layout.entity_collection_vtable_rva
    assert entities.monster_vtable_rva == layout.entity_actor_vtable_rva
    assert entities.attribute_pointer_offset == layout.entity_attribute_pointer_offset


def test_entity_reader_layout_loads_exact_1078_profile():
    selected = entity_reader_layout(SimpleNamespace(expected_sha256=CLIENT_SHA256_1078))
    assert selected == EntityLayout.model_validate(yaml.safe_load(PROFILE.read_text()))
