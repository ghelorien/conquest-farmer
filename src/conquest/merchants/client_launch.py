"""Launch the installed, fingerprint-qualified client during account recovery."""

import hashlib
from pathlib import Path

# Installed builds whose unattended recovery launch has been live-qualified.
# Only the retired 1074 client ever was, so no installed build is accepted;
# qualify a build's launch before adding its fingerprint here.
LAUNCH_QUALIFIED_SHA256 = frozenset()


def installed_client(root=Path(r"C:\Program Files\Classic Conquer 2.0")):
    root = Path(root)
    client = root / "bin" / "64" / "ImConquer.exe"
    with client.open("rb") as stream:
        fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    if fingerprint not in LAUNCH_QUALIFIED_SHA256:
        raise ValueError(
            "Installed client changed; update its memory qualification before recovery"
        )
    return [str(client)], root
