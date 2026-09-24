"""Discover client windows and track one explicit launcher attempt."""

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time


def process_key(identity):
    return identity["pid"], identity["creation_time_100ns"]


def pinned_client(catalog, key):
    matches = [client for client in catalog.windows() if client.key == tuple(key)]
    if len(matches) != 1:
        raise ValueError(
            "The selected client closed or changed while the wrapper restarted"
        )
    return matches[0]


@dataclass(frozen=True)
class ClientWindow:
    identity: dict
    hwnd: int
    title: str

    @property
    def key(self):
        return (*process_key(self.identity), self.hwnd)

    @property
    def label(self):
        return f"{self.title or 'Conquer client'} · PID {self.identity['pid']}"


class ClientCatalog:
    def __init__(
        self, backend, executable="ImConquer.exe", *, image_path=None, title_prefix=None
    ):
        self.backend, self.executable = backend, executable
        self.image_path = (
            str(Path(image_path).resolve()).casefold() if image_path else None
        )
        self.title_prefix = title_prefix

    def identities(self):
        result = []
        for process in self.backend.processes(self.executable):
            try:
                identity = self.backend.identity(process["pid"])
            except OSError:
                continue  # Processes can exit between enumeration and query.
            if (
                self.image_path
                and str(Path(identity["path"]).resolve()).casefold() != self.image_path
            ):
                continue
            result.append(identity)
        return result

    def windows(self, *, include_hidden=False):
        """Return qualified game-client windows.

        Launcher and farmer selection deliberately see only visible clients.
        Merchant clients may be hosted in an embedded surface after a desktop
        restart, however, so the merchant runtime can opt in to hidden HWNDs
        and still prove the account identity from process memory before use.
        """
        result = []
        for identity in self.identities():
            for window in self.backend.windows(identity["pid"]):
                size = window.get("client_size") or [0, 0]
                if (
                    (not include_hidden and not window["visible"])
                    or size[0] < 200
                    or size[1] < 150
                ):
                    continue
                if window["title"].strip() == "ClassicConquer Loading":
                    continue  # Transient splash HWND is replaced by the login shell.
                if self.title_prefix and not window["title"].startswith(
                    self.title_prefix
                ):
                    continue
                result.append(ClientWindow(identity, window["hwnd"], window["title"]))
        return result


class LaunchWatch:
    def __init__(
        self, catalog, command, *, cwd, spawn=subprocess.Popen, clock=time.monotonic
    ):
        self.catalog, self.command, self.cwd = catalog, command, cwd
        self.spawn, self.clock = spawn, clock
        self.process = None
        self.before = set()
        self.deadline = 0
        self.state, self.note = "idle", ""

    @property
    def pending(self):
        return self.state == "waiting"

    def start(self):
        if self.pending:
            raise ValueError("The current client launch is still pending")
        self.before = {process_key(i) for i in self.catalog.identities()}
        self.process = self.spawn(self.command, cwd=self.cwd)
        self.deadline = self.clock() + 180
        self.state, self.note = "waiting", "Waiting for the client window…"

    def poll(self):
        if not self.pending:
            return None
        candidates = [
            w
            for w in self.catalog.windows()
            if process_key(w.identity) not in self.before
        ]
        if len(candidates) == 1:
            self.state, self.note = "ready", "Client window found"
            return candidates[0]
        if len(candidates) > 1:
            self.state, self.note = (
                "choose",
                "Several new windows opened; select a client to embed",
            )
        elif self.process.poll() not in (None, 0):
            self.state, self.note = (
                "failed",
                f"Launcher exited with code {self.process.returncode}",
            )
        elif self.clock() >= self.deadline:
            self.state, self.note = (
                "timed_out",
                "No client window appeared; select one when it is ready",
            )
        return None

    def cancel(self):
        # Cancel observation, not the game or its launcher.
        self.state, self.note = "canceled", "Stopped waiting for the client"
