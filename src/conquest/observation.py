"""Timestamped read-only candidate observations. Read success is not validation.

Candidate addresses are only usable in the original process session. Sequential
reads are not an atomic game snapshot, even when every individual read succeeds.
This module deliberately has no input backend.
"""

import json
import math
import struct
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from conquest.calibration import KINDS


@dataclass(frozen=True)
class Candidate:
    name: str
    address: int
    kind: str


@dataclass(frozen=True)
class CandidateValue:
    name: str
    address: str
    kind: str
    value: list | None
    read_ok: bool
    error: str | None = None


@dataclass(frozen=True)
class Observation:
    started_at: str
    started_monotonic: float
    finished_monotonic: float
    process_identity: dict
    fields: tuple[CandidateValue, ...]
    read_ok: bool
    error: str | None = None
    qualified: bool = False
    atomic: bool = False

    def to_dict(self):
        return asdict(self)


class StateReader(Protocol):
    def read(self) -> Observation: ...


def load_candidates(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Candidate report exceeds 16 MiB")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise ValueError("Unsupported candidate report")
    return result


class CandidateReader:
    def __init__(self, session, report, names=("hp_u32", "position_u32"), clock=time.monotonic):
        if report.get("process_identity") != session.identity:
            raise ValueError("Candidate report belongs to another process session")
        if report.get("expected_sha256") != session.expected_sha256:
            raise ValueError("Candidate fingerprint differs from the selected client")
        if not names or len(set(names)) != len(names):
            raise ValueError("Select at least one field, without duplicates")
        self.session, self.clock = session, clock
        self.identity = dict(session.identity)
        self.candidates = []
        fields = report.get("candidates", {})
        for name in names:
            field = fields.get(name)
            if not isinstance(field, dict) or field.get("truncated") is not False:
                raise ValueError(f"{name} is missing or truncated")
            kind, addresses = field.get("kind"), field.get("addresses")
            if kind not in KINDS:
                raise ValueError(f"{name} is not a supported numeric candidate")
            if not isinstance(addresses, list) or not 1 <= len(addresses) <= 64:
                raise ValueError(f"{name} needs 1 to 64 candidate addresses")
            parsed = []
            for address in addresses:
                if not isinstance(address, str):
                    raise ValueError("Candidate addresses must be hexadecimal strings")
                number = int(address, 16)
                if not 0x10000 <= number <= 0x7FFFFFFFFFFF - 64:
                    raise ValueError("Candidate address is outside user memory bounds")
                parsed.append(number)
                self.candidates.append(Candidate(name, number, kind))
            if len(set(parsed)) != len(parsed):
                raise ValueError(f"{name} contains duplicate addresses")
        if len(self.candidates) > 128:
            raise ValueError("Observation is limited to 128 candidate addresses")

    def read(self):
        start = self.clock()
        wall_time = datetime.now(timezone.utc).isoformat()
        values = []
        error = None
        try:
            self.session.assert_identity()
            for candidate in self.candidates:
                try:
                    fmt = "<" + KINDS[candidate.kind]
                    value = list(struct.unpack(fmt, self.session.read(candidate.address, struct.calcsize(fmt))))
                    if any(isinstance(item, float) and not math.isfinite(item) for item in value):
                        raise ValueError("Non-finite candidate value")
                    values.append(CandidateValue(candidate.name, hex(candidate.address), candidate.kind, value, True))
                except (OSError, ValueError, struct.error) as failure:
                    values.append(CandidateValue(candidate.name, hex(candidate.address), candidate.kind,
                                                 None, False, str(failure)))
            self.session.assert_identity()
        except (OSError, ValueError) as failure:
            error = str(failure)
            # A changed process invalidates the entire sample, including earlier reads.
            values = [CandidateValue(item.name, item.address, item.kind, None, False, error) for item in values]
        if len(values) != len(self.candidates):
            values = [CandidateValue(item.name, hex(item.address), item.kind, None, False, error)
                      for item in self.candidates]
        read_ok = error is None and all(item.read_ok for item in values)
        if not read_ok and error is None:
            error = "One or more candidate reads failed"
        return Observation(wall_time, start, self.clock(), self.identity, tuple(values), read_ok, error)
