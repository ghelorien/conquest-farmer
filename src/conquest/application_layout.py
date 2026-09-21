"""Explicit application-code authority, independent of CWD and site-packages."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import re
import stat
import sys
import threading
import time

APP_ROOT = 'CONQUEST_APP_ROOT'
MANIFEST_PIN = 'CONQUEST_RELEASE_MANIFEST_SHA256'
_VERIFY_BACKOFF_SECONDS = 45
_verification_registry_lock = threading.Lock()
_verification_flights = {}


def _verify_release_for_launch(root, pin):
    """Serialize full reads and retain only failures, never successful proofs."""
    key = (os.path.normcase(str(root)), pin)
    with _verification_registry_lock:
        flight = _verification_flights.setdefault(key, {'lock': threading.Lock()})
    with flight['lock']:
        failure = flight.get('failure')
        if failure and time.monotonic() - failure[0] < _VERIFY_BACKOFF_SECONDS:
            _at, error_type, arguments = failure
            raise error_type(*arguments)
        from conquest.release import verify_release
        try:
            verify_release(root, expected_manifest_sha256=pin)
        except (OSError, ValueError) as error:
            flight['failure'] = (time.monotonic(), type(error), error.args)
            raise
        else:
            flight.pop('failure', None)


def real_path(value):
    path = Path(os.path.abspath(value))
    for part in (path, *path.parents):
        if os.path.lexists(part):
            info = os.lstat(part)
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Application paths must not contain reparse points')
    return path


def application_root(explicit=None, *, verify=True):
    configured = os.environ.get(APP_ROOT)
    pin = os.environ.get(MANIFEST_PIN)
    if pin and not configured:
        raise ValueError('A release manifest pin requires CONQUEST_APP_ROOT')
    if configured:
        root = real_path(configured)
        if explicit is not None and real_path(explicit) != root:
            raise ValueError('Explicit application root differs from CONQUEST_APP_ROOT')
    elif explicit is not None:
        root = real_path(explicit)
    else:
        module = Path(__file__).absolute()
        if module.parent.name != 'conquest' or module.parent.parent.name != 'src':
            raise ValueError('Installed Conquest requires an explicit CONQUEST_APP_ROOT')
        root = real_path(module.parent.parent.parent)
    for relative, directory in (('pyproject.toml', False), ('src/conquest', True),
                                ('scripts', True), ('profiles/routes', True)):
        path = real_path(root / relative)
        if not (path.is_dir() if directory else path.is_file()):
            raise ValueError('Application root is missing ' + relative)
    if pin or (root / 'release-manifest.json').exists():
        if not pin or not re.fullmatch('[0-9a-f]{64}', pin):
            raise ValueError('Immutable release requires a valid manifest pin')
        manifest = real_path(root / 'release-manifest.json')
        if not manifest.is_file() or hashlib.sha256(manifest.read_bytes()).hexdigest() != pin:
            raise ValueError('Release manifest differs from the activated manifest')
        if verify:
            _verify_release_for_launch(root, pin)
    return root


@dataclass(frozen=True)
class RuntimeLayout:
    root: Path
    immutable: bool

    @classmethod
    def resolve(cls, explicit=None, *, verify=True):
        root = application_root(explicit, verify=verify)
        immutable = bool(os.environ.get(MANIFEST_PIN))
        if immutable:
            state = os.environ.get('CONQUEST_DATA_ROOT')
            if not state or not Path(state).is_absolute():
                raise ValueError('Immutable release requires an absolute managed state root')
            state = real_path(state)
            if state == root or root in state.parents or state in root.parents:
                raise ValueError('Managed state and immutable application roots must be separate')
        return cls(root, immutable)

    def verify_for_launch(self):
        application_root(self.root)

    def script(self, name):
        if Path(name).name != name or not name.endswith('.py'):
            raise ValueError('Invalid application entry script')
        path = real_path(self.root / 'scripts' / name)
        if not path.is_file():
            raise ValueError('Application entry script is missing: ' + name)
        return path

    def python(self, *, windowed=False):
        if self.immutable:
            name = 'Scripts/pythonw.exe' if windowed and os.name == 'nt' else (
                'Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            path = real_path(self.root / '.venv' / name)
            if not path.is_file():
                raise ValueError('Release-local Python is missing')
            return path
        path = Path(sys.executable)
        if windowed and path.with_name('pythonw.exe').is_file():
            path = path.with_name('pythonw.exe')
        return path

    def environment(self):
        environment = os.environ.copy()
        environment[APP_ROOT] = str(self.root)
        if self.immutable:
            environment['PYTHONDONTWRITEBYTECODE'] = '1'
        return environment
