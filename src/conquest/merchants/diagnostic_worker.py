"""Read-only diagnostic of the exact previously attached merchant process."""

import sys
from conquest.character_context import state_path
from conquest.merchants.journal import Journal, character_name
from conquest.memory import MemorySession
from conquest.memory_life import CLIENT_SHA256
from conquest.win32 import WindowsBackend
from conquest.worker import serve


def main(character):
    character = character_name(character)
    identity = Journal().get(character, "last_identity")
    if not identity:
        raise ValueError("No previously attached merchant")
    with MemorySession(identity["pid"], CLIENT_SHA256) as session:
        if session.identity != identity:
            raise ValueError("Previously attached client was replaced")
    windows = [
        w
        for w in WindowsBackend().windows(identity["pid"])
        if min(w.get("client_size") or [0, 0]) >= 200
    ]
    if len(windows) != 1:
        raise ValueError("Merchant diagnostic window is absent or ambiguous")
    serve(
        identity["pid"],
        windows[0]["hwnd"],
        CLIENT_SHA256,
        state_path(f".runtime/account-diagnostic-{character.lower()}.json"),
        lifetime=1800,
        read_only=True,
    )


if __name__ == "__main__":
    main(sys.argv[1])
