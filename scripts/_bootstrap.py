"""Validate an entry script's code root before importing Conquest behavior."""
from pathlib import Path
import hashlib
import os
import stat
import sys


def activate(entrypoint):
    entrypoint = Path(os.path.abspath(entrypoint))
    root = entrypoint.parent.parent
    if entrypoint.parent.name != 'scripts':
        raise ValueError('Conquest entry script is outside its application root')
    for path in (entrypoint, root, *root.parents):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Conquest entry paths must not contain reparse points')
    configured = os.environ.get('CONQUEST_APP_ROOT')
    if configured and Path(os.path.abspath(configured)) != root:
        raise ValueError('Entry script differs from CONQUEST_APP_ROOT')
    pin = os.environ.get('CONQUEST_RELEASE_MANIFEST_SHA256')
    manifest = root / 'release-manifest.json'
    if pin or manifest.exists():
        if not pin or not manifest.is_file() or hashlib.sha256(manifest.read_bytes()).hexdigest() != pin:
            raise ValueError('Entry script release manifest pin is missing or changed')
        sys.dont_write_bytecode = True
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['CONQUEST_APP_ROOT'] = str(root)
    sys.path.insert(0, str(root / 'src'))
    from conquest.application_layout import application_root
    application_root(root)
    os.environ['PYTHONPATH'] = str(root / 'src')
    os.chdir(root)
    return root
