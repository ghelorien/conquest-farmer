"""Exact-1078 merchant stock observation with no game input surface."""

import threading
from types import SimpleNamespace

from conquest.memory import MemorySession
from conquest.memory_build_layout import CLIENT_SHA256_1078, health_reader_layout
from conquest.memory_life import MemoryLifeReader
from conquest.merchants.memory import MerchantMemory


class ReadOnlyMerchantObserver:
    """Foundation for stock/refill planning, never an automation observer.

    Deliberately does not construct Operations, a bridge, or a controller.
    A successful observation does not qualify any merchant input capability.
    """

    read_only_build = True
    automation_ready_build = False
    merchant_observation_only = True

    def __init__(self, client, character, *, context=None):
        self.lock = threading.RLock()
        self.character = character
        self.character_context = context
        # A verified native HWND is presentation data only.  Do not create an
        # Operations/MessageTarget object for this unqualified build.
        self.hwnd = client.hwnd
        self.session = MemorySession(
            client.identity["pid"], CLIENT_SHA256_1078
        ).__enter__()
        try:
            if self.session.identity != client.identity:
                raise ValueError(
                    "Client identity changed during merchant observation attachment"
                )
            self.adapter = SimpleNamespace(
                expected_sha256=self.session.expected_sha256,
                identity=dict(self.session.identity),
                modules=self.session.modules,
                read=self.session.read,
                read_block=self.session.read,
                assert_identity=self.session.assert_identity,
            )
            self.health_layout = health_reader_layout(self.adapter)
            self.memory = MerchantMemory.for_observer(self)
            self._verify_attachment()
        except BaseException:
            self.session.close()
            raise

    def _verify_attachment(self):
        # Verify the selected character in native memory before returning
        # an observer; a title or process alone never identifies a merchant.
        self.read_life()

    def read_life(self):
        with self.lock:
            return MemoryLifeReader.for_session(self.adapter, self.character).read()

    def read(self, *, max_seconds=3):
        with self.lock:
            return self.memory.read(max_seconds=max_seconds)

    def read_ownership(self):
        """Persistent full stock observation, including open manual modals."""
        from conquest.merchants.reader_1078 import open_read_only_1078

        with self.lock:
            snapshot = open_read_only_1078(
                self.session, self.character
            ).read_manual_ownership()
            if snapshot["identity"] != self.adapter.identity:
                raise ValueError(
                    "Merchant process changed during ownership observation"
                )
            profile = getattr(self.character_context, "profile", None)
            if profile is not None and (
                snapshot["character"] != profile.name
                or snapshot["server"] != profile.server
                or (
                    profile.character_uid is not None
                    and snapshot["character_uid"] != profile.character_uid
                )
            ):
                raise ValueError(
                    "Merchant ownership differs from the configured profile"
                )
            return snapshot

    def __call__(self):
        return self.read()

    def close(self):
        with self.lock:
            self.session.close()


class LoginMerchantObserver1078(ReadOnlyMerchantObserver):
    """Disconnect-recovery rebind of one pinned exact process at login.

    At the login screen no actor exists to identify. Only the full durable
    process identity (PID, creation time and path) recorded with the last
    healthy Market baseline or unresolved incident, plus the native Login GUI
    proof, admits this observer. In world, ``read_ownership`` still pins the
    configured name, server and UID before any snapshot is accepted.
    """

    login_rebound = True

    def __init__(self, client, character, *, context=None, pinned=None):
        if pinned is None or dict(client.identity) != dict(pinned):
            raise ValueError(
                "Login rebind requires the pinned merchant process identity"
            )
        self._pinned = dict(pinned)
        super().__init__(client, character, context=context)

    def _verify_attachment(self):
        from conquest.merchants.return_1078 import _login_memory

        if self.session.identity != self._pinned:
            raise ValueError("Pinned merchant process changed during login rebind")
        _login_memory(self.adapter)
