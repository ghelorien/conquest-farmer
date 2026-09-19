"""Offline, locked and atomic migration.  The legacy source is read-only."""
from contextlib import ExitStack, closing
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import yaml

from conquest.character_profiles import CharacterProfile, ProfileRegistry, write_json


COPY_SUFFIXES = frozenset(('.json', '.jsonl', '.sqlite', '.sqlite3', '.yaml', '.log'))
TRANSIENT_NAMES = ('worker', 'bridge', 'qualification', 'input-profile', 'reload-resume')
PROCESS_FILE_HINTS = ('worker', 'bridge', 'status', 'app-state', 'controller', 'handover', 'lifecycle')


def _copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in ('.sqlite', '.sqlite3'):
        # Opening a WAL database, even mode=ro, may update its shared-memory
        # sidecar.  Copy the raw stable snapshot into our staging area first;
        # SQLite may then recover/backup that private copy without touching the
        # legacy source.  _stable_copy validates every source sidecar around
        # this operation and the final manifest validates it again.
        with tempfile.TemporaryDirectory(dir=target.parent,
                                         prefix='.sqlite-source-snapshot-') as temporary:
            snapshot = Path(temporary) / source.name
            shutil.copy2(source, snapshot)
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = Path(str(source) + suffix)
                if sidecar.exists():
                    shutil.copy2(sidecar, Path(str(snapshot) + suffix))
            with closing(sqlite3.connect(snapshot)) as before, closing(sqlite3.connect(target)) as after:
                before.backup(after)
    else:
        shutil.copy2(source, target)


def _reparse(path):
    info = os.lstat(path)
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0) & 0x400))


def _inside(path, root):
    try:
        Path(path).resolve(strict=True).relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _safe_source(source):
    source = Path(os.path.abspath(source))
    if not source.is_dir() or _reparse(source):
        raise ValueError('Migration source must be a real local directory, not a reparse point')
    return source.resolve(strict=True)


def _safe_tree(root, relative):
    base = root / relative
    if not base.exists():
        return []
    if _reparse(base) or not _inside(base, root):
        raise ValueError('Migration source contains a junction, symlink or escaping path')
    found = []
    for current, directories, files in os.walk(base, followlinks=False):
        current = Path(current)
        if _reparse(current) or not _inside(current, root):
            raise ValueError('Migration source contains a junction, symlink or escaping path')
        for name in list(directories):
            path = current / name
            if _reparse(path) or not _inside(path, root):
                raise ValueError('Migration source contains a junction, symlink or escaping path')
        for name in files:
            path = current / name
            if _reparse(path) or not _inside(path, root):
                raise ValueError('Migration source contains a junction, symlink or escaping path')
            found.append(path)
    return found


def _fingerprint(path):
    before = os.lstat(path)
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    after = os.lstat(path)
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, field, None) != getattr(after, field, None) for field in fields):
        raise ValueError('Migration source file changed while it was inspected')
    return tuple(getattr(after, field, None) for field in fields), digest.digest()


def _source_token(path):
    """Content/identity token including SQLite's transaction sidecars."""
    try:
        token = [_fingerprint(path)]
        if Path(path).suffix.lower() in ('.sqlite', '.sqlite3'):
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = Path(str(path) + suffix)
                if sidecar.exists():
                    if _reparse(sidecar):
                        raise ValueError('Migration source contains a reparse point')
                    token.append((suffix, _fingerprint(sidecar)))
                else:
                    token.append((suffix, None))
        return tuple(token)
    except OSError:
        raise ValueError('Migration source file changed while it was inspected') from None


def _stable_copy(source, target, copy_file):
    if _reparse(source):
        raise ValueError('Migration source contains a reparse point')
    before = _source_token(source)
    copy_file(source, target)
    if _reparse(source) or _source_token(source) != before:
        raise ValueError('Migration source file changed during copy')


def _selected_source_paths(source):
    selected = set()
    for folder in ('.runtime', 'reports'):
        for path in _safe_tree(source, folder):
            if (path.is_file() and path.suffix.lower() in COPY_SUFFIXES
                    and not any(word in path.name.lower() for word in TRANSIENT_NAMES)):
                selected.add(path)
    for relative in (
        '.runtime/account.dpapi',
        '.runtime/merchants/spiritual/account.dpapi',
        '.runtime/merchants/dutch/account.dpapi',
        '.runtime/discord-webhook.dpapi',
        '.runtime/merchants/shops-webhook.dpapi',
        'profiles/desktop-foreground.local.yaml',
    ):
        path = source / relative
        if path.exists():
            if _reparse(path) or not _inside(path, source):
                raise ValueError('Selected migration source escapes the source root')
            selected.add(path)
    for route in _safe_tree(source, 'profiles/routes'):
        if route.is_file() and route.suffix.lower() == '.yaml':
            selected.add(route)
    return selected


def _source_manifest(source):
    return {path: _source_token(path) for path in _selected_source_paths(source)}


def _validate_manifest(source, manifest):
    if _selected_source_paths(source) != set(manifest):
        raise ValueError('Selected migration source files changed before activation')
    for path, token in manifest.items():
        if _reparse(path) or _source_token(path) != token:
            raise ValueError('Selected migration source file changed before activation')


def _identity_records(value):
    """Yield only durable controller/worker identities, never a bare PID."""
    if isinstance(value, list):
        for child in value:
            yield from _identity_records(child)
        return
    if not isinstance(value, dict):return
    if (type(value.get('pid')) is int and value['pid'] > 0
            and (value.get('creation_time_100ns') or value.get('path'))):
        yield value
    for child in value.values():
        if isinstance(child, (dict, list)):
            yield from _identity_records(child)


def _same_process(recorded, actual):
    if actual.get('pid') != recorded.get('pid'):
        return False
    creation = recorded.get('creation_time_100ns')
    # PID existence is deliberately insufficient.  A durable creation time is
    # the minimum reuse fence; compare the image path too whenever recorded.
    if type(creation) is not int or creation <= 0 or actual.get('creation_time_100ns') != creation:
        return False
    path = recorded.get('path')
    if path is not None:
        if not isinstance(path, str) or not isinstance(actual.get('path'), str):
            return False
        if os.path.normcase(os.path.abspath(actual['path'])) != os.path.normcase(os.path.abspath(path)):
            return False
    return True


def require_offline(source, *, backend=None):
    """Reject matching live controller identities; stale/reused PIDs are inert."""
    source = _safe_source(source)
    if backend is None:
        from conquest.win32 import WindowsBackend
        backend = WindowsBackend()
    paths = _safe_tree(source, '.runtime') + _safe_tree(source, 'reports')
    for path in paths:
        if path.suffix.lower() != '.json':
            continue
        hinted = any(hint in path.name.lower() for hint in PROCESS_FILE_HINTS)
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            if hinted:
                raise ValueError('A durable Conquest controller receipt is unreadable') from None
            continue
        for identity in _identity_records(value):
            # Memory evidence can retain an independently running game client;
            # it is not a Conquest controller or worker.
            executable = Path(str(identity.get('path',''))).name.casefold()
            if executable == 'imconquer.exe':
                continue
            try:
                actual = backend.identity(identity['pid'])
            except OSError as error:
                if getattr(error, 'winerror', None) not in (None, 6, 87, 1168):
                    raise ValueError('Cannot verify whether a recorded Conquest process is still live') from None
                continue
            if _same_process(identity, actual):
                raise ValueError('Close the existing Conquest app and workers before importing local state')


def _diagnostics_only(root):
    entries = list(root.iterdir())
    if not entries:
        return True
    return all(entry.name == 'diagnostics' or
               (entry.is_file() and entry.name.lower().startswith('diagnostic')
                and entry.suffix.lower() in ('.json', '.log')) for entry in entries)


def _validate_existing_diagnostics(root):
    if _reparse(root):
        raise ValueError('Destination is a reparse point')
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            if _reparse(Path(current) / name):
                raise ValueError('Diagnostics destination contains a reparse point')


def _remap_journal(journal, by_name):
    with closing(sqlite3.connect(journal)) as db, db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        if ('manual_sessions' in tables and db.execute(
                "SELECT 1 FROM manual_sessions WHERE phase NOT IN "
                "('completed','request_withdrawn','declined_verified','operator_overridden') "
                "LIMIT 1").fetchone()):
            raise ValueError('Reconcile nonterminal manual sessions in the legacy installation before migration')
        affected = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = {row[1] for row in db.execute(f'PRAGMA table_info({quoted})')}
            selected = tuple(column for column in ('character', 'target_profile_id')
                             if column in columns)
            if selected:
                affected[table] = selected
        # Wave 1 manual-session triggers intentionally make identity rows
        # immutable at runtime.  Migration rewrites only the target profile key
        # in the staged copy, then restores the exact trigger definitions.
        triggers = list(db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL "
            "AND tbl_name IN (%s)" % ','.join('?' for _ in affected),
            tuple(affected))) if affected else []
        for name, _sql in triggers:
            db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        for table, selected in affected.items():
            quoted = '"' + table.replace('"', '""') + '"'
            for column in selected:
                qcolumn = '"' + column + '"'
                for name, profile in by_name.items():
                    db.execute(f'UPDATE {quoted} SET {qcolumn}=? WHERE {qcolumn}=?',
                               (profile.id, name))
        for _name, sql in triggers:
            db.execute(sql)
        if 'state' in tables:
            for profile in by_name.values():
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',
                           (profile.id, 'profile_initialized', 'true'))


def _import_legacy_trust(registry, journal, farmer, merchants):
    with closing(sqlite3.connect(journal)) as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'transactions' not in tables:
            return
        changes = {}
        for profile in merchants:
            sources = set()
            for row in db.execute(
                    "SELECT before_json FROM transactions WHERE character=? AND kind='delivery'",
                    (profile.id,)):
                try:
                    trade = json.loads(row[0]).get('trade', {})
                except (ValueError, TypeError, AttributeError):
                    continue
                uid = trade.get('participant_uid')
                if trade.get('participant') == farmer.name and type(uid) is int and uid > 0:
                    sources.add(uid)
            if len(sources) == 1:
                changes[profile.id] = [{'name': farmer.name, 'server': farmer.server,
                                        'character_uid': sources.pop()}]
    if not changes:
        return
    # Initial construction under the migration/app lock, not a live edit.
    with registry.edit() as value:
        for raw in value['profiles']:
            if raw['id'] in changes:
                raw['trusted_sources'] = changes[raw['id']]
                CharacterProfile(**raw)


def migrate_legacy(source, root, *, check_offline=require_offline, copy_file=_copy,
                   lock_timeout=0):
    source_arg = Path(os.path.abspath(source))
    # Keep the lexical destination until its reparse status has been checked.
    # Path.resolve() here would follow the very destination junction/symlink
    # that migration is required to reject.
    root = Path(os.path.abspath(root))
    from conquest.profile_bootstrap import managed_root_owner, legacy_app_owner_if_present
    with ExitStack() as owners:
        owners.enter_context(managed_root_owner(root, timeout=lock_timeout))
        if root.exists() and _reparse(root):
            raise ValueError('Destination is a reparse point')
        owners.enter_context(legacy_app_owner_if_present(root, timeout=lock_timeout))
        marker = root / 'migration.json'
        if marker.exists():
            if _reparse(marker):
                raise ValueError('Destination migration marker is a reparse point')
            try:
                completed = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                raise ValueError('Destination has an invalid migration marker') from None
            if not isinstance(completed, dict) or completed.get('state') != 'complete':
                raise ValueError('Destination already contains an incomplete managed installation')
            return completed
        if os.path.normcase(str(source_arg)) == os.path.normcase(str(root)):
            raise ValueError('Migration source and destination must be different directories')
        # The sibling fence is derivable without opening the source.  Acquire
        # it before validating or traversing the legacy tree so a current build
        # cannot start while migration is inspecting it.
        owners.enter_context(managed_root_owner(source_arg, timeout=lock_timeout))
        source = _safe_source(source_arg)
        if source == root.resolve():
            raise ValueError('Migration source and destination must be different directories')
        owners.enter_context(legacy_app_owner_if_present(source, timeout=lock_timeout))
        diagnostics_backup = None
        if root.exists():
            if not _diagnostics_only(root):
                raise ValueError('Destination already contains a managed installation')
            _validate_existing_diagnostics(root)
            diagnostics_backup = root.with_name(
                root.name + '.diagnostics-before-migration-' + uuid.uuid4().hex)
            os.rename(root, diagnostics_backup)
        stage = root.with_name(root.name + '.migration-' + uuid.uuid4().hex)
        try:
            check_offline(source)
            manifest = _source_manifest(source)
            registry = ProfileRegistry(stage)
            config_path = source / 'profiles/desktop-foreground.local.yaml'
            if config_path.exists():
                if _reparse(config_path) or not _inside(config_path, source):
                    raise ValueError('Migration config escapes the source')
                config = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
            else:
                config = {}
            farmer = registry.add(config.get('character', 'Parasite'))
            merchants = [registry.add(name, role='Merchant') for name in ('Spiritual', 'Dutch')]
            by_name = {profile.name: profile for profile in merchants}

            for folder in ('.runtime', 'reports'):
                for path in _safe_tree(source, folder):
                    if not path.is_file() or path.suffix.lower() not in COPY_SUFFIXES:
                        continue
                    relative = path.relative_to(source).as_posix()
                    if any(word in path.name.lower() for word in TRANSIENT_NAMES):
                        continue
                    shared = relative.startswith(('.runtime/merchants/', 'reports/merchants/',
                                                   '.runtime/shop-', '.runtime/shops-'))
                    destination = stage / ('machine-state' if shared
                                           else 'characters/' + farmer.id) / relative
                    _stable_copy(path, destination, copy_file)

            # Generic traversal excludes all .dpapi because COPY_SUFFIXES does
            # not contain it.  Only these fixed, semantically known secrets
            # can enter managed credential locations.
            credentials = {
                source / '.runtime/account.dpapi':
                    stage / 'accounts' / farmer.account_id / 'account.dpapi',
                source / '.runtime/merchants/spiritual/account.dpapi':
                    stage / 'accounts' / by_name['Spiritual'].account_id / 'account.dpapi',
                source / '.runtime/merchants/dutch/account.dpapi':
                    stage / 'accounts' / by_name['Dutch'].account_id / 'account.dpapi',
                source / '.runtime/discord-webhook.dpapi':
                    stage / 'characters' / farmer.id / '.runtime/discord-webhook.dpapi',
                source / '.runtime/merchants/shops-webhook.dpapi':
                    stage / 'machine-state/.runtime/merchants/shops-webhook.dpapi',
            }
            for original, destination in credentials.items():
                if original.exists():
                    if _reparse(original) or not _inside(original, source):
                        raise ValueError('Credential path escapes the migration source')
                    _stable_copy(original, destination, copy_file)
            if config_path.exists():
                _stable_copy(config_path,
                             stage / 'characters' / farmer.id / 'farmer.local.yaml', copy_file)

            packaged = Path(__file__).resolve().parents[2] / 'profiles/routes'
            for route in _safe_tree(source, 'profiles/routes'):
                if not route.is_file() or route.suffix.lower() != '.yaml':
                    continue
                builtin = packaged / route.name
                if not builtin.exists() or builtin.read_bytes() != route.read_bytes():
                    _stable_copy(route, stage / 'characters' / farmer.id / 'routes' / route.name,
                                 copy_file)

            journal = stage / 'machine-state/reports/merchants/journal.sqlite3'
            if journal.exists():
                _copy(journal, stage / 'migration-backup/merchant-journal.sqlite3')
                _remap_journal(journal, by_name)
                _import_legacy_trust(registry, journal, farmer, merchants)
            result = {
                'schema_version': 1, 'source': str(source),
                'farmer_profile_id': farmer.id,
                'profiles': {profile.name: profile.id for profile in (farmer, *merchants)},
                'state': 'complete',
                'rollback': 'Original source data is unchanged. Keep old and new app instances mutually exclusive.',
            }
            if diagnostics_backup is not None:
                result['diagnostics_rollback'] = str(diagnostics_backup)
            write_json(stage / 'migration.json', result)
            _validate_manifest(source, manifest)
            # The destination remains absent until every copy, validation and
            # journal rewrite has succeeded.
            os.rename(stage, root)
            return result
        except Exception:
            if diagnostics_backup is not None and diagnostics_backup.exists() and not root.exists():
                os.rename(diagnostics_backup, root)
            raise
