"""Candidate HP observations from a bounded, read-only attribute table.

Decoding is not semantic qualification. This diagnostic never authorizes input
or replaces the unavailable-health monitor used by memory-only farming.
"""

import base64
import struct
import time
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from conquest.addressing import (
    PlayerLayout,
    WorkerPointerSession,
    checked_address,
    resolve_player,
)


class HealthLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    player: PlayerLayout
    attribute_pointer_offset: int = Field(ge=0, le=65536)
    hp_attribute_index: int = Field(ge=0, le=1023)
    max_attributes: int = Field(default=1024, ge=1, le=1024)
    max_sample_seconds: float = Field(default=1, gt=0, le=5)
    qualification: Literal["candidate"] = "candidate"


def decode_attribute(table: bytes, mode: int, count: int, index: int) -> int:
    """Mirror the inspected getter's index mapping and 32-bit rotations."""
    if mode not in (0, 1, 2, 3) or not 1 <= count <= 1024:
        raise ValueError("Unsupported attribute mode or count")
    if len(table) != count * 4 or not 0 <= index < count:
        raise ValueError("Attribute table size or index is invalid")
    if mode == 0:
        slot = index
    elif mode == 1:
        slot = count - 1 - index
    elif mode == 2:
        slot = index // 2 + (count // 2 + 1 if count & 1 else 0)
    else:
        slot = index // 2 + (count // 2 if not count & 1 else 0)
    if not 0 <= slot < count:
        raise ValueError("Decoded attribute index is outside the table")
    value = struct.unpack_from("<I", table, slot * 4)[0]
    shift = (slot if mode in (1, 3) else -slot) & 31
    return ((value << shift) | (value >> ((32 - shift) & 31))) & 0xFFFFFFFF


class HealthWorkerSession(WorkerPointerSession):
    def viewport_size(self):
        from conquest.viewport import validate_size

        return validate_size(self.request("health")["window"]["client_size"])

    def read_block(self, address, size):
        checked_address(address, size)
        if type(size) is not int or not 1 <= size <= 65536:
            raise ValueError("Memory block size is outside diagnostic bounds")
        result = self.request("read-block", {"address": hex(address), "size": size})
        if (
            result.get("address") != hex(address)
            or result.get("size") != size
            or result.get("encoding") != "base64"
        ):
            raise ValueError("Memory block response does not match the request")
        data = base64.b64decode(result["data"], validate=True)
        if len(data) != size:
            raise ValueError("Incomplete memory block response")
        return data


@dataclass(frozen=True)
class HealthCandidate:
    character: str
    current_hp: int
    max_hp: int
    started_at: float
    timestamp: float


class MemoryHealthReader:
    def __init__(
        self, session, layout: HealthLayout, character: str, *, clock=time.monotonic
    ):
        if not character or len(character.encode("utf-8")) > 63:
            raise ValueError("A character name of 1 to 63 UTF-8 bytes is required")
        self.session, self.layout, self.character, self.clock = (
            session,
            layout,
            character,
            clock,
        )

    def read(self):
        start = self.clock()
        s, layout = self.session, self.layout
        addresses = resolve_player(s, layout.player)
        name_bytes = s.read_block(addresses["name"], 64)
        name = name_bytes.split(b"\0", 1)[0].decode("utf-8")
        if name != self.character:
            raise ValueError("Character name differs from the selected character")
        max_bytes = s.read_block(addresses["max_hp"], 4)
        max_hp = struct.unpack("<I", max_bytes)[0]
        pointer_address = checked_address(
            addresses["object"] + layout.attribute_pointer_offset
        )
        pointer_bytes = s.read(pointer_address, 8)
        attributes = checked_address(struct.unpack("<Q", pointer_bytes)[0], 24)
        header = s.read_block(attributes, 24)
        mode, count = struct.unpack_from("<II", header, 8)
        if mode not in (0, 1, 2, 3) or not 1 <= count <= layout.max_attributes:
            raise ValueError("Attribute header is outside supported bounds")
        table_address = checked_address(
            struct.unpack_from("<Q", header, 16)[0], count * 4
        )
        table = s.read_block(table_address, count * 4)
        hp = decode_attribute(table, mode, count, layout.hp_attribute_index)
        if not 1 <= max_hp <= 100000000 or not 0 <= hp <= max_hp:
            raise ValueError("Candidate HP is outside current/maximum bounds")
        if (
            s.read_block(table_address, count * 4) != table
            or s.read_block(attributes, 24) != header
            or s.read(pointer_address, 8) != pointer_bytes
            or s.read_block(addresses["max_hp"], 4) != max_bytes
            or s.read_block(addresses["name"], 64) != name_bytes
            or resolve_player(s, layout.player) != addresses
        ):
            raise ValueError(
                "Health fields or pointer topology changed during sampling"
            )
        s.assert_identity()
        finish = self.clock()
        if not 0 <= finish - start <= layout.max_sample_seconds:
            raise ValueError("Health observation expired")
        return HealthCandidate(name, hp, max_hp, start, finish)

    def report(self):
        return {
            "schema_version": 1,
            "stage": "memory_health_candidate_sample",
            "source": "read_only_memory",
            "qualified": False,
            "restart_qualified": False,
            "autonomous_actions_enabled": False,
            "process_identity": self.session.identity,
            "snapshot": asdict(self.read()),
        }
