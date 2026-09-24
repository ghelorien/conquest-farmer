"""Resolve candidate objects afresh from a fingerprint-specific module pointer.

This proves only that a configured pointer path can be read. It never promotes
candidate field semantics or restart compatibility to qualified status.
"""

import struct
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


Offset = Annotated[int, Field(ge=0, le=0x1000000)]


class PlayerLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    module: str = Field(min_length=1, max_length=128)
    root_rva: int = Field(ge=0, le=0x7FFFFFFF)
    pointer_offsets: tuple[Offset, ...] = Field(min_length=1, max_length=8)
    vtable_rva: int = Field(ge=0, le=0x7FFFFFFF)
    name_offset: Offset
    max_hp_offset: Offset
    position_offset: Offset
    kill_counter_offset: Offset | None = None
    level_offset: Offset | None = None
    map_rva: int | None = Field(default=None, ge=0, le=0x7FFFFFFF)
    qualification: Literal["candidate"] = "candidate"


def checked_address(address, size=8):
    if not 0x10000 <= address <= 0x7FFFFFFFFFFF - size:
        raise ValueError("Pointer is null or outside supported user address space")
    return address


def resolve_player(session, layout: PlayerLayout):
    if session.expected_sha256 != layout.expected_sha256:
        raise ValueError("Player layout fingerprint differs from the client")
    session.assert_identity()
    modules = [
        module
        for module in session.modules
        if module["name"].casefold() == layout.module.casefold()
    ]
    if len(modules) != 1:
        raise ValueError("Expected exactly one matching loaded module")
    module = modules[0]
    if layout.root_rva + 8 > module["size"] or layout.vtable_rva >= module["size"]:
        raise ValueError("Layout RVA is outside the loaded module")
    address = checked_address(module["base"] + layout.root_rva)
    # Re-read every pointer per observation; never cache heap addresses.
    trace = []
    for offset in layout.pointer_offsets:
        pointer = struct.unpack("<Q", session.read(address, 8))[0]
        checked_address(pointer)
        trace.append((address, pointer))
        address = checked_address(pointer + offset)
    expected_vtable = module["base"] + layout.vtable_rva
    if struct.unpack("<Q", session.read(address, 8))[0] != expected_vtable:
        raise ValueError("Candidate player object type changed")
    for location, pointer in reversed(trace):
        if struct.unpack("<Q", session.read(location, 8))[0] != pointer:
            raise ValueError("Player pointer path changed during resolution")
    session.assert_identity()
    result = {
        "object": address,
        "name": checked_address(address + layout.name_offset, 64),
        "max_hp": checked_address(address + layout.max_hp_offset, 4),
        "position": checked_address(address + layout.position_offset, 8),
    }
    for name, offset in (
        ("kill_counter", layout.kill_counter_offset),
        ("level", layout.level_offset),
    ):
        if offset is not None:
            result[name] = checked_address(address + offset, 4)
    if layout.map_rva is not None:
        if layout.map_rva + 4 > module["size"]:
            raise ValueError("Map RVA is outside module")
        result["map"] = checked_address(module["base"] + layout.map_rva, 4)
    return result


def resolve_object(
    session, *, expected_sha256, module, root_rva, pointer_offsets, vtable_rva
):
    """Resolve a typed object without inventing player/life field semantics."""
    if session.expected_sha256 != expected_sha256:
        raise ValueError("Object layout fingerprint differs from the client")
    session.assert_identity()
    modules = [
        entry
        for entry in session.modules
        if entry["name"].casefold() == module.casefold()
    ]
    if len(modules) != 1:
        raise ValueError("Expected exactly one matching loaded module")
    selected = modules[0]
    if root_rva + 8 > selected["size"] or vtable_rva >= selected["size"]:
        raise ValueError("Object layout RVA is outside the loaded module")
    address = checked_address(selected["base"] + root_rva)
    trace = []
    for offset in pointer_offsets:
        pointer = struct.unpack("<Q", session.read(address, 8))[0]
        checked_address(pointer)
        trace.append((address, pointer))
        address = checked_address(pointer + offset)
    if (
        struct.unpack("<Q", session.read(address, 8))[0]
        != selected["base"] + vtable_rva
    ):
        raise ValueError("Candidate object type changed")
    for location, pointer in reversed(trace):
        if struct.unpack("<Q", session.read(location, 8))[0] != pointer:
            raise ValueError("Object pointer path changed during resolution")
    session.assert_identity()
    return address


class WorkerPointerSession:
    """Read only eight-byte pointer values through an existing authenticated worker."""

    def __init__(self, info_path, expected_sha256):
        from conquest.worker import request

        self.request = lambda operation, body=None: request(info_path, operation, body)
        health = self.request("health")
        if (
            health.get("protocol_version") != 2
            or health.get("expected_sha256") != expected_sha256
        ):
            raise ValueError(
                "Worker protocol or pinned executable fingerprint differs from player layout"
            )
        self.identity, self.modules = health["target"], health["modules"]
        # The worker's MemorySession already pins the exact image fingerprint.
        # Verify it independently here against the selected process's disk image.
        from pathlib import Path
        from conquest.identity import fingerprint

        if fingerprint(Path(self.identity["path"]))["sha256"] != expected_sha256:
            raise ValueError("Worker client fingerprint differs from player layout")
        self.expected_sha256 = expected_sha256

    def assert_identity(self):
        if self.request("health")["target"] != self.identity:
            raise ValueError("Worker target exited or restarted")

    def read(self, address, size):
        if size != 8:
            raise ValueError("Worker pointer adapter reads eight-byte pointers only")
        checked_address(address)
        sample = self.request(
            "sample",
            {"fields": [{"name": "pointer", "address": hex(address), "kind": "u64"}]},
        )
        return struct.pack("<Q", sample["fields"][0]["value"][0])


def sample_player(info_path, layout):
    session = WorkerPointerSession(info_path, layout.expected_sha256)
    addresses = resolve_player(session, layout)
    sample = session.request(
        "sample",
        {
            "fields": [
                {"name": name, "address": hex(addresses[name]), "kind": kind}
                for name, kind in (
                    ("name", "utf8"),
                    ("max_hp", "u32"),
                    ("position", "xy_u32"),
                )
            ]
        },
    )
    if resolve_player(session, layout) != addresses:
        raise ValueError("Player object changed during sampling")
    return {
        "schema_version": 1,
        "stage": "player_candidate_sample",
        "process_identity": session.identity,
        "addresses": {key: hex(value) for key, value in addresses.items()},
        "sample": sample,
        "qualified": False,
        "autonomous_actions_enabled": False,
    }
