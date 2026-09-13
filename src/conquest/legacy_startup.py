"""Explicit legacy data namespace for a separately staged source release.

Validate before importing behavior modules: their default paths are evaluated
at import time. This does not migrate data or silently discard managed context.
"""
from dataclasses import dataclass
from pathlib import Path
import os


ENVIRONMENT = 'CONQUEST_LEGACY_DATA_ROOT'
MANAGED_ENVIRONMENT = ('CONQUEST_DATA_ROOT', 'CONQUEST_PROFILE_ID')
MANAGED_OPTIONS = ('--data-root', '--profile-id', '--manage-profiles', '--migrate-from')


@dataclass(frozen=True)
class StartupContext:
    legacy_root: Path | None
    arguments: tuple[str, ...]


def configure(argv, *, environ=None):
    """Consume the launch-only option and publish its validated inherited env.

    Call for import checks too. No filesystem writes or directory creation are
    performed; default managed launch keeps its original arguments/environment.
    """
    environ = os.environ if environ is None else environ
    remaining = []
    requested = []
    args = iter(argv)
    for argument in args:
        if argument == '--legacy-data-root':
            value = next(args, None)
            if not value or value.startswith('--'):
                raise ValueError('--legacy-data-root requires an existing absolute local directory')
            requested.append(value)
        elif argument.startswith('--legacy-data-root='):
            requested.append(argument.split('=', 1)[1])
        else:
            remaining.append(argument)
    if len(requested) > 1:
        raise ValueError('Specify --legacy-data-root only once')
    inherited = environ.get(ENVIRONMENT)
    if not requested and not inherited:
        return StartupContext(None, tuple(remaining))
    if any(environ.get(name) for name in MANAGED_ENVIRONMENT):
        raise ValueError('Legacy data mode conflicts with the managed profile environment')
    if any(argument.split('=', 1)[0] in MANAGED_OPTIONS for argument in remaining):
        raise ValueError('Legacy data mode conflicts with managed profile startup options')

    def resolve(value):
        path = Path(value)
        if not value or value.startswith(('\\\\', '//')) or not path.is_absolute():
            raise ValueError('Legacy data root must be an absolute local directory')
        resolved = path.resolve(strict=True)
        if not resolved.is_dir() or str(resolved).startswith(('\\\\', '//')):
            raise ValueError('Legacy data root must resolve to a local directory')
        if not all((resolved / name).is_dir() for name in ('profiles', 'reports', '.runtime')):
            raise ValueError('Legacy data root must contain the existing profiles, reports and .runtime directories')
        return resolved

    destination = resolve(requested[0] if requested else inherited)
    if inherited and resolve(inherited) != destination:
        raise ValueError('Explicit legacy data root differs from the inherited legacy data root')
    environ[ENVIRONMENT] = str(destination)
    return StartupContext(destination, tuple(remaining))
