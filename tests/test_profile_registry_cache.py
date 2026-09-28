"""The profile registry is parsed once per file version, not once per lookup.

Live 2026-09-28 (Suicide's app, thread_sampler): every journal call resolves
its character through the registry, and every journal row with a character
column does too (profile_row -> database_character). merchant-ui-data spent
19% of its samples re-reading and re-validating profiles.json, once per row,
and the same read sat under each journal.get of every other thread.

Failure modes, written before the change:
1. A settled registry is read and validated again on every lookup.
2. An edit through the registry (add, update, bind) is not seen at once.
3. A file written in the last two seconds is served from the cache, so a
   same-size rewrite within one clock tick is missed.
4. A caller mutating a returned value changes what the next caller gets.
5. edit() starts from a cached copy instead of the file on disk.
6. A removed registry file still returns cached profiles.
"""

import json
import os
import time

import pytest

from conquest import character_profiles
from conquest.character_profiles import ProfileRegistry


def settle(registry, age=3600):
    old = time.time() - age
    os.utime(registry.path, (old, old))


@pytest.fixture
def loads(monkeypatch):
    calls = []
    real = json.loads

    def counting(text, *args, **kwargs):
        calls.append(1)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(character_profiles.json, "loads", counting)
    return calls


def test_a_settled_registry_is_parsed_once(tmp_path, loads):
    # 1
    registry = ProfileRegistry(tmp_path)
    farmer = registry.add("Varric")
    registry.add("Kalhiam", role="Merchant")
    settle(registry)
    loads.clear()
    for _ in range(5):
        assert [p.name for p in registry.profiles()] == ["Varric", "Kalhiam"]
        assert registry.resolve("kalhiam", role="Merchant").role == "Merchant"
        assert registry.read()["profiles"][0]["id"] == farmer.id
    assert len(loads) == 1


def test_edits_through_the_registry_are_seen_at_once(tmp_path, loads):
    # 2, 5
    registry = ProfileRegistry(tmp_path)
    farmer = registry.add("Varric")
    settle(registry)
    assert registry.resolve(farmer.id).label == ""
    other = ProfileRegistry(tmp_path)  # another app instance on the same file
    other.update(farmer.id, {"label": "main"})
    assert registry.resolve(farmer.id).label == "main"
    settle(registry)
    registry.resolve(farmer.id)
    registry.bind(farmer.id, "Varric", "America", 4242)
    assert registry.resolve(farmer.id).character_uid == 4242


def test_a_fresh_file_is_read_every_time(tmp_path, loads):
    # 3
    registry = ProfileRegistry(tmp_path)
    registry.add("Varric")
    text = registry.path.read_text(encoding="utf-8")
    loads.clear()
    registry.profiles()
    registry.path.write_text(text.replace("Varric", "Varrik"), encoding="utf-8")
    assert [p.name for p in registry.profiles()] == ["Varrik"]
    assert len(loads) == 2


def test_returned_values_are_independent(tmp_path, loads):
    # 4
    registry = ProfileRegistry(tmp_path)
    farmer = registry.add("Varric")
    settle(registry)
    registry.read()["profiles"][0]["name"] = "Mallory"
    registry.profiles()[0].overrides["heal_below"] = 0.1
    registry.resolve(farmer.id).trusted_sources.append({"name": "x"})
    fresh = registry.resolve(farmer.id)
    assert (fresh.name, fresh.overrides, fresh.trusted_sources) == ("Varric", {}, [])


def test_a_removed_registry_is_empty(tmp_path, loads):
    # 6
    registry = ProfileRegistry(tmp_path)
    registry.add("Varric")
    settle(registry)
    assert registry.profiles()
    registry.path.unlink()
    assert registry.profiles() == []
    assert registry.read()["profiles"] == []
