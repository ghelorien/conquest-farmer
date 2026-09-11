"""Read XP charge and the ready Fly popup; activate only through normal input."""
import struct
import time
from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256,read_life
from conquest.memory_shop import MemoryGui


def read_xp(observer):
    s=observer.adapter
    if s.expected_sha256!=CLIENT_SHA256:raise ValueError('XP client profile differs')
    life=read_life(s,observer.health_layout,observer.character)
    if life.dead_candidate:raise ValueError('Living character required for XP skill')
    actor=life.object_address
    raw=s.read_block(actor+0x3cc,4)
    charge=struct.unpack('<I',raw)[0]
    status=struct.unpack('<Q',s.read_block(actor+0x30,8))[0]
    if not 0<=charge<=100:raise ValueError('XP charge outside HUD bounds')
    latest=read_life(s,observer.health_layout,observer.character)
    if (latest.object_address!=actor or latest.dead_candidate
            or s.read_block(actor+0x3cc,4)!=raw):
        raise ValueError('XP state changed during observation')
    s.assert_identity()
    return {'charge':charge,'ready':bool(status&0x10),'flying':bool(status&0x8000000),
            'actor':actor,'source':'read_only_memory'}


def fly_point(observer,state):
    if state['charge']!=100 or not state['ready'] or state['flying']:
        raise ValueError('Fly requires full XP and the ready status')
    s=observer.adapter;actor=state['actor']
    base=next(m['base'] for m in s.modules if m['name'].lower()=='imconquer.exe')
    # The XP popup iterates this vector (separate from the learned-skill list).
    header=s.read_block(actor+0x1998,24)
    start,end,capacity=struct.unpack('<3Q',header)
    if end-start!=16 or not end<=capacity<=start+128*16:
        raise ValueError('Fly popup requires the qualified single XP skill layout')
    entry=s.read_block(checked_address(start),16)
    pointer=checked_address(struct.unpack_from('<Q',entry)[0])
    raw=s.read_block(pointer,0x68)
    if (struct.unpack_from('<Q',raw)[0]!=base+0x5cff78
            or struct.unpack_from('<I',raw,8)[0]!=1
            or struct.unpack_from('<I',raw,0x10)[0]!=8002
            or raw[0x18:0x1c]!=b'Fly\0'
            or struct.unpack_from('<QQ',raw,0x28)!=(3,15)
            or struct.unpack_from('<I',raw,0x44)[0]!=2):
        raise ValueError('Ready XP entry is not the self-target Fly skill')
    window=MemoryGui(s).read('##SkillsPopup')
    if window.size!=(56.,56.) or window.scroll!=(0.,0.):
        raise ValueError('Fly popup geometry changed')
    if (s.read_block(actor+0x1998,24)!=header or s.read_block(start,16)!=entry
            or s.read_block(pointer,0x68)!=raw or read_xp(observer)!=state):
        raise ValueError('Fly readiness changed before activation')
    return tuple(round(p+size/2) for p,size in zip(window.position,window.size))


class XpSkill:
    def __init__(self,observer,notify):
        self.observer,self.notify=observer,notify
        self.last=None
        self.pending=None
        self.attempts=0
        self.next_attempt=0

    def step(self,dispatch):
        now=time.monotonic()
        try:state=read_xp(self.observer)
        except (ValueError,OSError):return False
        telemetry={k:state[k] for k in ('charge','ready','flying','source')}
        if telemetry!=self.last:
            self.notify('xp_skill_state',telemetry)
            self.last=telemetry
        if self.pending and state['actor']!=self.pending['actor']:
            self.pending=None
        if self.pending and state['flying']:
            self.notify('xp_fly_verified',{'activity':'Fly active; continuing combat',
                'charge':state['charge'],'source':'read_only_memory'})
            self.pending=None
        if not state['ready'] or state['charge']<100:
            self.attempts=0
            if self.pending and now-self.pending['at']>3:self.pending=None
            return False
        if state['flying'] or now<self.next_attempt or self.attempts>=3:return False
        try:point=fly_point(self.observer,state)
        except (ValueError,OSError):return False
        dispatch(point)
        self.attempts+=1
        self.next_attempt=now+1.5
        self.pending={'actor':state['actor'],'at':now}
        self.notify('xp_fly_attempt',{'activity':'XP full; activating Fly','point':point,
                                    'attempt':self.attempts,'charge':100})
        return True
