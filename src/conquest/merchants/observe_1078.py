"""On-demand, app-owned merchant memory evidence; no input or saved state."""
import time
from types import SimpleNamespace

from conquest.addressing import resolve_player
from conquest.character_context import registry
from conquest.memory import MemorySession, UnsupportedClientBuildError
from conquest.memory_build_layout import (
    CLIENT_SHA256_1078, actual_player_layout, inventory_reader_layouts,
    read_build_layout,
)
from conquest.merchants.memory import character_uid
from conquest.merchants.reader_1078 import open_read_only_1078


def _actor_identity(session):
    """Identify a process without assuming its title, PID, or window order."""
    layout = read_build_layout(session)
    player = actual_player_layout(session)
    actor = resolve_player(session, player)
    modules = [m for m in session.modules if m['name'].casefold() == player.module.casefold()]
    if len(modules) != 1:
        raise ValueError('Ambiguous merchant module')
    base = modules[0]['base']
    adapter = SimpleNamespace(read_block=session.read)
    raw_name = session.read(actor['name'], 64)
    if b'\0' not in raw_name:
        raise ValueError('Merchant character name is not terminated')
    name = raw_name.split(b'\0', 1)[0].decode('utf-8')
    if not name:
        raise ValueError('Merchant character identity is unavailable')
    uid = character_uid(adapter, base, actor['object'], layout=layout)
    server = session.read(base + layout.merchant_server_rva, 64)
    if (resolve_player(session, player) != actor
            or session.read(actor['name'], 64) != raw_name
            or character_uid(adapter, base, actor['object'], layout=layout) != uid
            or session.read(base + layout.merchant_server_rva, 64) != server):
        raise ValueError('Merchant identity changed during discovery')
    session.assert_identity()
    return name, uid, server.split(b'\0', 1)[0]


def _processes(catalog):
    identities = catalog.identities()
    by_pid = {}
    for identity in identities:
        pid = identity['pid']
        if pid in by_pid and by_pid[pid] != identity:
            raise ValueError('Merchant process identity changed during enumeration')
        by_pid[pid] = dict(identity)
    if len(by_pid) > 16:
        raise ValueError('Too many client processes for bounded merchant discovery')
    return by_pid


def observe(runtime, character):
    """Called only behind the existing authenticated merchant bridge."""
    profiles = registry()
    if profiles is None:
        raise ValueError('Merchant observation requires configured local profiles')
    profile = profiles.resolve(getattr(character, 'profile_id', character),
                               role='Merchant', server='America')
    if not profile.local_enabled:
        raise ValueError('Merchant profile is not enabled on this PC')
    started = time.monotonic()
    candidates = _processes(runtime.catalog)
    matches = []
    for identity in candidates.values():
        if time.monotonic() - started > 4:
            raise ValueError('Merchant discovery expired; retry the read-only observation')
        try:
            with MemorySession(identity['pid'], CLIENT_SHA256_1078) as session:
                if session.identity != identity:
                    raise ValueError('Merchant process changed during discovery')
                name, uid, server = _actor_identity(session)
                if name == profile.name and server == b'Classic_US':
                    matches.append((identity, uid))
        except UnsupportedClientBuildError:
            continue  # Other builds remain outside this exact-1078 endpoint.
        # An unreadable exact-build candidate cannot safely be ruled out as a
        # second matching merchant. Propagate read/identity gaps, never guess.
    if len(matches) != 1:
        raise ValueError(f'{profile.name}: expected one memory-identified 1078 client, found {len(matches)}')
    identity, uid = matches[0]
    if profile.character_uid is not None and uid != profile.character_uid:
        raise ValueError('Merchant character UID differs from the configured profile')
    with MemorySession(identity['pid'], CLIENT_SHA256_1078) as session:
        if session.identity != identity:
            raise ValueError('Merchant process changed before stock observation')
        snapshot = open_read_only_1078(session, profile.name).read_manual_ownership()
        capacity = inventory_reader_layouts(session)[1].capacity
        if (snapshot['identity'] != identity or snapshot['character_uid'] != uid
                or snapshot['character'] != profile.name or snapshot['server'] != profile.server
                or snapshot['capacity'] != capacity):
            raise ValueError('Merchant identity or capacity changed before stock observation')
        owned = len(snapshot['inventory']) + len(snapshot['booth'])
        if owned > capacity:
            raise ValueError('Merchant stock exceeds qualified combined capacity')
        if _processes(runtime.catalog) != candidates:
            raise ValueError('Client processes changed during merchant observation')
        if profiles.resolve(profile.id, role='Merchant', server='America') != profile:
            raise ValueError('Merchant profile changed during observation')
        session.assert_identity()
    if time.monotonic() - started > 4:
        raise ValueError('Merchant observation expired; retry the read-only observation')
    fields = ('character', 'character_uid', 'identity', 'server', 'timestamp',
              'map_id', 'position', 'hp', 'silver', 'capacity', 'inventory',
              'booth', 'own_booth_uid', 'booth_open')
    return {**{key: snapshot[key] for key in fields},
            'profile_id': profile.id, 'client_sha256': CLIENT_SHA256_1078,
            'source': 'read_only_memory', 'read_only': True, 'observation_only': True,
            'profile_uid_verified': profile.character_uid == uid,
            'max_hp': snapshot['health']['max_hp_candidate'],
            'closed_modal': snapshot['trade'] is None and snapshot['request'] is None,
            'trade_open': snapshot['trade'] is not None,
            'request_open': snapshot['request'] is not None,
            'capacity_kind': 'combined_inventory_and_booth',
            'owned_free_slots': capacity - owned,
            'input_qualified': False, 'refill_input_ready': False}
