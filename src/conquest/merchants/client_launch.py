"""Launch the installed, fingerprint-qualified client during account recovery."""
import hashlib
from pathlib import Path
from conquest.memory_life import CLIENT_SHA256


def installed_client(root=Path(r'C:\Program Files\Classic Conquer 2.0')):
    root=Path(root)
    client=root/'bin'/'64'/'ImConquer.exe'
    with client.open('rb') as stream:
        fingerprint=hashlib.file_digest(stream,'sha256').hexdigest()
    if fingerprint!=CLIENT_SHA256:
        raise ValueError('Installed client changed; update its memory qualification before recovery')
    return [str(client)],root
