"""Read-only candidate ghost state for the pinned Classic Conquer client.

A visible ghost/Revive-button observation matched status bits 0x420 and
appearance 98 on the actual player object. A verified foreground revival changed
these to status 0x200 and appearance 0 alongside restored HP and the town spawn.
Other ghost appearances remain unvalidated. Positive HP is not proof of life,
and absence of this candidate does not prove life.
"""
from dataclasses import asdict, dataclass
import struct
import time
from types import SimpleNamespace

from conquest.addressing import resolve_player
from conquest.memory_health import MemoryHealthReader


CLIENT_SHA256='c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396'
STATUS_OFFSET=0x30
APPEARANCE_OFFSET=0xc0
REVIVE_GATE_OFFSET=0xae8
GHOST_STATUS_BITS=0x420
MAP_RVA=0x699564


@dataclass(frozen=True)
class LifeCandidate:
    character: str
    object_address: int
    status: int
    appearance: int
    map_id: int
    position: tuple[int,int]
    current_hp: int
    max_hp: int
    ghost_candidate: bool
    revive_gate_value: int
    revive_ready_candidate: bool
    started_at: float
    timestamp: float
    conservative_blocked: bool = False
    revive_input_supported: bool = True

    @property
    def dead_candidate(self):
        # Synchronized live death: 0x200 -> 0x20, ~3s before ghost 0x420.
        return self.conservative_blocked or bool(self.status & 0x20)


def ghost_candidate(status,appearance):
    """Both observed indications are required; appearance 99 is unvalidated."""
    return status & GHOST_STATUS_BITS == GHOST_STATUS_BITS and appearance == 98


def read_life(session,health_layout,character,*,clock=time.monotonic):
    """Worker-friendly entry point, resolving the actual player and pinned map."""
    if not hasattr(session,'read_block'):
        session=SimpleNamespace(expected_sha256=session.expected_sha256,
            modules=session.modules,identity=session.identity,read=session.read,
            read_block=session.read,assert_identity=session.assert_identity)
    player=health_layout.player.model_copy(update={'map_rva':MAP_RVA})
    return MemoryLifeReader(session,health_layout,player,character,clock=clock).read()


class MemoryLifeReader:
    def __init__(self,session,health_layout,player_layout,character,*,layout=None,clock=time.monotonic):
        if layout is None and any(fingerprint!=CLIENT_SHA256 for fingerprint in
                (session.expected_sha256,health_layout.player.expected_sha256,player_layout.expected_sha256)):
            raise ValueError('Life candidate offsets belong to a different client build')
        if layout is not None and any(fingerprint!=layout.expected_sha256 for fingerprint in
                (session.expected_sha256,health_layout.player.expected_sha256,player_layout.expected_sha256)):
            raise ValueError('Life candidate layout differs from the client')
        if player_layout.map_rva is None:
            raise ValueError('Life observations require the map field')
        self.session,self.health_layout,self.player_layout=session,health_layout,player_layout
        self.layout=layout
        self.character,self.clock=character,clock
        self.health=MemoryHealthReader(session,health_layout,character,clock=clock)

    @classmethod
    def for_session(cls,session,character,*,clock=time.monotonic):
        from conquest.memory_build_layout import health_reader_layout,read_build_layout
        layout=read_build_layout(session);health=health_reader_layout(session)
        player=health.player.model_copy(update={'map_rva':layout.map_rva})
        if not hasattr(session,'read_block'):
            session=SimpleNamespace(expected_sha256=session.expected_sha256,modules=session.modules,
                identity=session.identity,read=session.read,read_block=session.read,
                assert_identity=session.assert_identity,
                viewport_size=getattr(session,'viewport_size',None))
        return cls(session,health,player,character,layout=layout,clock=clock)

    def read(self):
        started=self.clock()
        session=self.session
        actual=resolve_player(session,self.health_layout.player)
        player=resolve_player(session,self.player_layout)
        if actual['position']!=player['position']:
            raise ValueError('Player and health profiles resolve different position fields')
        build=self.layout
        status_offset=build.life_status_offset if build is not None else STATUS_OFFSET
        appearance_offset=build.life_appearance_offset if build is not None else APPEARANCE_OFFSET
        revive_offset=build.life_revive_gate_offset if build is not None else REVIVE_GATE_OFFSET
        revive_supported=True
        if build and build.conservative_life_block:
            # Bracket the life sample after checking the actual loaded renderer.
            from conquest.native_revive import require_semantics
            try:require_semantics(session)
            except ValueError:revive_supported=False
        fields=((actual['object']+status_offset,8),
                (actual['object']+appearance_offset,4),
                (actual['position'],8),(player['map'],4),
                (actual['object']+revive_offset,8))
        before=[session.read_block(address,size) for address,size in fields]
        health=self.health.read()
        if [session.read_block(address,size) for address,size in fields]!=before:
            raise ValueError('Life state changed during observation')
        if (resolve_player(session,self.health_layout.player)!=actual
                or resolve_player(session,self.player_layout)!=player):
            raise ValueError('Player pointer changed during life observation')
        session.assert_identity()
        finished=self.clock()
        if not 0<=finished-started<=2:
            raise ValueError('Life observation expired')
        # 1078's adjoining high dword is unrelated data; retain the full block
        # for stability, but expose only the verified low status dword.
        status=(struct.unpack_from('<I',before[0])[0] if build and build.conservative_life_block
                else struct.unpack('<Q',before[0])[0])
        appearance=struct.unpack('<I',before[1])[0]
        position=struct.unpack('<II',before[2])
        map_id=struct.unpack('<I',before[3])[0]
        revive_gate=struct.unpack('<Q',before[4])[0]
        ghost=ghost_candidate(status,appearance)
        conservative=bool(build and build.conservative_life_block
                          and (health.current_hp==0 or status&0x420 or appearance!=0))
        return LifeCandidate(self.character,actual['object'],status,appearance,map_id,position,
            health.current_hp,health.max_hp,ghost,revive_gate,
            revive_supported and ghost and revive_gate==0,
            started,finished,conservative,revive_supported)

    def report(self):
        return {'qualified':False,'stage':'player_life_candidate','source':'read_only_memory',
                'process_identity':self.session.identity,'snapshot':asdict(self.read())}
