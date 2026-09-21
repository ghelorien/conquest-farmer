"""Separate, input-denied 1078 manual-handoff reader registry.

This is intentionally outside EmbeddedObserver/MerchantDriver.  It discovers
only exact local profiles before an operator handoff and opens a short-lived
read-only session for each poll while that handoff is fenced.
"""
from dataclasses import dataclass
from pathlib import Path

from conquest.memory import MemorySession
from conquest.merchants.reader_1078 import CLIENT_SHA256_1078, open_read_only_1078


@dataclass(frozen=True)
class ManualReaderBinding1078:
    profile_id: str
    character: str
    role: str
    identity: dict


class ManualReaderIdentityChanged1078(ValueError):
    def __init__(self, target):
        self.target = target
        super().__init__("1078 manual process identity changed")


class ManualReaderRegistry1078:
    """Exact-profile discovery and polling; never supplies a game controller."""

    def __init__(self, catalog, *, session_factory=MemorySession):
        self.catalog, self.session_factory = catalog, session_factory
        self.bindings = {}
        self.read_only_build = False

    def exact_build_present(self):
        """Read the executable fingerprint only; do not open a game session."""
        from conquest.identity import fingerprint
        for identity in self.catalog.identities():
            try:
                if fingerprint(Path(identity["path"]))["sha256"] == CLIENT_SHA256_1078:
                    return True
            except (OSError, ValueError):
                continue
        return False

    def activate_if_present(self):
        self.read_only_build = self.exact_build_present()
        if not self.read_only_build:
            self.bindings = {}
        return self.read_only_build

    @staticmethod
    def _profiles():
        from conquest.character_context import registry
        profiles = registry()
        if profiles is None:
            raise ValueError("1078 manual observation requires configured local profiles")
        rows = [profile for profile in profiles.profiles()
                if profile.local_enabled and profile.server == "America" and profile.role in ("Farmer", "Merchant")]
        if not rows or len({profile.id for profile in rows}) != len(rows):
            raise ValueError("Configured Farmer and merchant profiles are required")
        if len({(profile.server.casefold(), profile.name.casefold()) for profile in rows}) != len(rows):
            raise ValueError("Configured manual profiles are ambiguous")
        if sum(profile.role == "Farmer" for profile in rows) != 1:
            raise ValueError("Exactly one configured local Farmer profile is required")
        return rows

    def discover(self, *, profile_ids=None, expected_identities=None):
        """Warm identity discovery only. It never becomes a handoff baseline."""
        profiles = self._profiles()
        if profile_ids is not None:
            expected=set(profile_ids)
            profiles=[profile for profile in profiles if profile.id in expected]
            if {profile.id for profile in profiles} != expected:
                raise ValueError("Frozen manual handoff profile is no longer configured")
        found = {}
        identity_mismatch = set()
        for client in self.catalog.windows(include_hidden=True):
            matches = []
            for profile in profiles:
                try:
                    with self.session_factory(client.identity["pid"], CLIENT_SHA256_1078) as session:
                        if session.identity != client.identity:
                            raise ValueError("Client identity changed during manual discovery")
                        snapshot = open_read_only_1078(session, profile.name).read_manual_ownership()
                    if (snapshot["character"] == profile.name and snapshot["server"] == profile.server
                            and (profile.character_uid is None or snapshot["character_uid"] == profile.character_uid)):
                        if (expected_identities and expected_identities.get(profile.id) is not None
                                and session.identity != expected_identities[profile.id]):
                            identity_mismatch.add(profile.id)
                        else:
                            matches.append(profile)
                except (ValueError, OSError):
                    continue
            if len(matches) == 1:
                found.setdefault(matches[0].id, []).append((matches[0], client.identity))
        if identity_mismatch:
            raise ManualReaderIdentityChanged1078(sorted(identity_mismatch)[0])
        if any(len(found.get(profile.id, ())) != 1 for profile in profiles):
            raise ValueError("Every configured Farmer and merchant needs one exact 1078 manual reader")
        if len({entry[0][1]["pid"] for entry in found.values()}) != len(profiles):
            raise ValueError("One game process cannot satisfy more than one configured manual profile")
        self.bindings = {profile_id: ManualReaderBinding1078(profile.id, profile.name, profile.role, dict(identity))
                         for profile_id, ((profile, identity),) in found.items()}
        return {binding.profile_id: binding.role for binding in self.bindings.values()}

    def covers(self, character):
        target = getattr(character, "profile_id", None)
        if target is None and character == "Farmer":
            return any(binding.role == "Farmer" for binding in self.bindings.values())
        return target in self.bindings

    def blocks_automation(self, character):
        # Before discovery we cannot map a title/PID to an account safely.
        # Exact-build presence still blocks the old-layout automation stack.
        return self.covers(character) or self.read_only_build

    def read_one(self, target):
        binding=self.bindings[target]
        with self.session_factory(binding.identity["pid"], CLIENT_SHA256_1078) as session:
            if session.identity != binding.identity:
                raise ManualReaderIdentityChanged1078(target)
            snapshot = open_read_only_1078(session, binding.character).read_manual_ownership()
        if snapshot["character"] != binding.character or snapshot["identity"] != binding.identity:
            raise ManualReaderIdentityChanged1078(target)
        # An open modal is valid observation evidence. The durable handoff
        # store records saw_window and waits for later closed ownership.
        return snapshot

    def detect_replacement(self, target):
        """Probe only a frozen profile after its previously bound PID fails."""
        binding=self.bindings[target]
        saved=self.bindings
        try:
            self.discover(profile_ids=[target], expected_identities={target:binding.identity})
        finally:
            self.bindings=saved
