"""Exact-value candidate discovery. Matches are evidence, never validated offsets."""

import json
import math
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


KINDS = {"u16": "H", "u32": "I", "u64": "Q", "i32": "i", "f32": "f", "f64": "d",
         "xy_u16": "HH", "xy_u32": "II", "xy_f32": "ff", "xy_f64": "dd"}


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["u16", "u32", "u64", "i32", "f32", "f64", "xy_u16", "xy_u32", "xy_f32", "xy_f64", "utf8", "utf16"]
    value: int | float | str | list[int | float]

    def encoded(self) -> bytes:
        if self.kind in ("utf8", "utf16"):
            if not isinstance(self.value, str) or not 1 <= len(self.value) <= 64:
                raise ValueError("Text observations must contain 1 to 64 characters")
            return (self.value + "\0").encode("utf-8" if self.kind == "utf8" else "utf-16-le")
        values = self.value if isinstance(self.value, list) else [self.value]
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
            raise ValueError("Numeric observations must be finite numbers")
        try:
            return struct.pack("<" + KINDS[self.kind], *values)
        except (struct.error, OverflowError) as error:
            raise ValueError(f"Value does not fit {self.kind}") from error

    @model_validator(mode="after")
    def validate_encoding(self):
        self.encoded()
        return self


class ObservationSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    source: str = Field(min_length=1, max_length=500)
    observed_at: datetime
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observations: list[Observation] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_names(self):
        if len({item.name for item in self.observations}) != len(self.observations):
            raise ValueError("Observation names must be unique")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return self


def load_observations(path: Path) -> ObservationSet:
    if path.stat().st_size > 65536:
        raise ValueError("Observation YAML exceeds 64 KiB")
    return ObservationSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def find_matches(data: bytes, pattern: bytes, limit: int):
    """Include unaligned and overlapping matches without allocating an unbounded list."""
    position = 0
    for _ in range(limit):
        position = data.find(pattern, position)
        if position < 0:
            return
        yield position
        position += 1


def scan(session, observations: ObservationSet, *, max_bytes=512 * 1024 * 1024,
         max_seconds=20.0, max_candidates=2000, chunk_size=1024 * 1024, clock=time.monotonic):
    if not 1 <= max_bytes <= 4 * 1024**3 or not 0 < max_seconds <= 120:
        raise ValueError("Scan budget must be 1 byte to 4 GiB and at most 120 seconds")
    if not 1 <= max_candidates <= 10000 or not 256 <= chunk_size <= 1024 * 1024:
        raise ValueError("Invalid candidate or chunk limit")
    started = clock()
    deadline = started + max_seconds
    patterns = {item.name: item.encoded() for item in observations.observations}
    records = {item.name: {"kind": item.kind, "value": item.value, "addresses": [], "truncated": False}
               for item in observations.observations}
    attempted = read_bytes = failed_bytes = 0
    complete = True
    stop_reason = "eligible_regions_exhausted"
    overlap = max(map(len, patterns.values())) - 1
    for region in session.regions():
        session.assert_identity()
        for offset in range(0, region.size, chunk_size):
            if clock() >= deadline or attempted >= max_bytes:
                complete = False
                stop_reason = "time_budget" if clock() >= deadline else "byte_budget"
                break
            intended_primary_size = min(chunk_size, region.size - offset)
            primary_size = min(intended_primary_size, max_bytes - attempted)
            if primary_size < intended_primary_size:
                complete = False
                stop_reason = "byte_budget"
            size = min(primary_size + overlap, region.size - offset, max_bytes - attempted)
            address = region.base + offset
            attempted += size
            try:
                data = session.read(address, size)
                read_bytes += size
            except OSError:
                failed_bytes += size
                continue
            for name, pattern in patterns.items():
                record = records[name]
                remaining = max_candidates - len(record["addresses"])
                for index in find_matches(data, pattern, remaining + 1):
                    if index >= primary_size:
                        break  # The next chunk owns this starting address.
                    if remaining == 0:
                        record["truncated"] = True
                        break
                    record["addresses"].append(hex(address + index))
                    remaining -= 1
            if not complete:
                break
        if not complete:
            break
    session.assert_identity()
    return {
        "schema_version": 1, "stage": "candidate_scan", "qualified": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "process_identity": session.identity, "expected_sha256": observations.expected_sha256,
        "observations": observations.model_dump(mode="json"),
        "modules": session.modules, "candidates": records,
        "coverage": {"eligible_regions_complete": complete and failed_bytes == 0,
                     "attempted_bytes": attempted, "read_bytes": read_bytes,
                     "failed_bytes": failed_bytes, "stop_reason": stop_reason,
                     "elapsed_seconds": round(clock() - started, 3)},
        "note": "Session-specific matches only. Repeated, changing observations and restart-safe address resolution are required.",
    }


def refine(session, observations: ObservationSet, previous: dict):
    if previous.get("process_identity") != session.identity:
        raise ValueError("Candidate file belongs to a different process session; scan again")
    if previous.get("expected_sha256") != observations.expected_sha256:
        raise ValueError("Candidate fingerprint differs from the observation profile")
    results = {}
    session.assert_identity()
    for observation in observations.observations:
        old = previous.get("candidates", {}).get(observation.name)
        if old is None or old["kind"] != observation.kind:
            raise ValueError(f"No matching prior candidate type for {observation.name}")
        addresses = old["addresses"]
        if len(addresses) > 10000:
            raise ValueError("Candidate list exceeds bounds")
        matches = []
        failures = 0
        pattern = observation.encoded()
        for address in addresses:
            try:
                if session.read(int(address, 16), len(pattern)) == pattern:
                    matches.append(address)
            except OSError:
                failures += 1
        results[observation.name] = {
            "kind": observation.kind, "value": observation.value, "addresses": matches,
            "truncated": old.get("truncated", False), "read_failures": failures,
            "value_changed": old["value"] != observation.value,
        }
    session.assert_identity()
    return {
        "schema_version": 1, "stage": "candidate_refinement", "qualified": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "process_identity": session.identity, "expected_sha256": observations.expected_sha256,
        "observations": observations.model_dump(mode="json"), "modules": session.modules,
        "candidates": results,
        "note": "Even a unique matching address is unqualified until controlled changes and restart behavior are validated.",
    }


def scan_near(session, observations, previous, anchor_name, radius=2048, **scan_options):
    """Limit discovery to eligible data near previously observed, still-matching values."""
    from conquest.memory import Region

    if previous.get("process_identity") != session.identity:
        raise ValueError("Anchor file belongs to another process session")
    if previous.get("expected_sha256") != observations.expected_sha256:
        raise ValueError("Anchor fingerprint differs from the observation profile")
    if not 64 <= radius <= 65536:
        raise ValueError("Neighborhood radius must be between 64 and 65536 bytes")
    old = previous.get("candidates", {}).get(anchor_name)
    if not isinstance(old, dict) or not 1 <= len(old.get("addresses", [])) <= 100:
        raise ValueError("Neighborhood scanning requires 1 to 100 prior anchor candidates")
    anchor = Observation(name=anchor_name, kind=old["kind"], value=old["value"])
    pattern = anchor.encoded()
    ranges = []
    session.assert_identity()
    for encoded_address in old["addresses"]:
        address = int(encoded_address, 16)
        try:
            if session.read(address, len(pattern)) == pattern:
                ranges.append((max(0x10000, address - radius), address + len(pattern) + radius))
        except OSError:
            continue
    if not ranges:
        raise ValueError("None of the previous anchors still match; collect a fresh observation")
    merged = []
    for lower, upper in sorted(ranges):
        if merged and lower <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))

    class NeighborhoodSession:
        identity = session.identity
        modules = session.modules
        read = session.read
        assert_identity = session.assert_identity

        def regions(self):
            for region in session.regions():
                for lower, upper in merged:
                    start, end = max(lower, region.base), min(upper, region.base + region.size)
                    if start < end:
                        yield Region(start, end - start, region.allocation_base, region.kind, region.protection)

    report = scan(NeighborhoodSession(), observations, **scan_options)
    report["scope"] = {"kind": "anchor_neighborhoods", "anchor": anchor_name,
                       "matching_anchors": len(ranges), "radius": radius}
    return report
