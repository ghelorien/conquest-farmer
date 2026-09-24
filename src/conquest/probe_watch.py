"""Observe a small, session-pinned set of candidates during an input probe.

This is diagnostic evidence, not a validated game-state reader.
"""

import struct
import time


def read_probe_watch(session, candidates):
    if candidates.get("process_identity") != session.identity:
        raise ValueError("Watch candidates belong to another process session")
    if candidates.get("expected_sha256") != session.expected_sha256:
        raise ValueError("Watch candidate fingerprint differs from the client")
    result = {
        "sampled_at_monotonic": time.monotonic(),
        "qualified": False,
        "fields": {},
    }
    for name, kind, fmt in [("hp_u32", "u32", "<I"), ("position_u32", "xy_u32", "<II")]:
        field = candidates.get("candidates", {}).get(name)
        if (
            not isinstance(field, dict)
            or field.get("kind") != kind
            or field.get("truncated")
            or not 1 <= len(field.get("addresses", [])) <= 16
        ):
            raise ValueError(
                f"Probe watch requires 1 to 16 untruncated {name} candidates"
            )
        values = []
        for address in field["addresses"]:
            value = struct.unpack(
                fmt, session.read(int(address, 16), struct.calcsize(fmt))
            )
            values.append({"address": address, "value": list(value)})
        result["fields"][name] = values
    session.assert_identity()
    return result


def require_observed_values(watch, candidates):
    """An input test must begin with all watched values matching the live reference."""
    for name, values in watch["fields"].items():
        expected = candidates["candidates"][name]["value"]
        if not isinstance(expected, list):
            expected = [expected]
        if any(value["value"] != expected for value in values):
            raise ValueError(
                f"{name} no longer matches its reference; refresh calibration before input"
            )
    if any(value["value"][0] <= 0 for value in watch["fields"]["hp_u32"]):
        raise ValueError("Health watch contains zero HP; no input allowed")


def watch_changes(before, after):
    return [
        name
        for name in before["fields"]
        if before["fields"][name] != after["fields"].get(name)
    ]
