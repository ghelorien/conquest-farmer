"""Wait for the native actor hit-test pointer, without pixels or extra input."""
import struct
import time

from conquest.capture import CaptureUnavailable
from conquest.memory_life import CLIENT_SHA256


def wait_scene_pointer(session, point, check, *, timeout=.35, clock=time.monotonic, sleep=time.sleep):
    if (len(point)!=2 or any(type(v) is not int for v in point)
            or not 0<timeout<=.5):
        raise ValueError('Invalid native scene pointer check')
    if session.expected_sha256!=CLIENT_SHA256:
        raise ValueError('Scene pointer client fingerprint is not qualified')
    modules=[m for m in session.modules if m['name'].casefold()=='imconquer.exe']
    if len(modules)!=1 or modules[0]['size']<0x6985b0:
        raise ValueError('Scene pointer module is absent or invalid')
    base=modules[0]['base']
    # Actor::hit_test loads Y, then X, from these native input fields.
    for rva,expected in ((0x19fcb1,bytes.fromhex('8b2df5884f00')),
                         (0x19fcbc,bytes.fromhex('8b35e6884f00'))):
        if session.read_block(base+rva,len(expected))!=expected:
            raise ValueError('Scene pointer accessor differs from the pinned client')
    deadline=clock()+timeout
    while True:
        check()
        session.assert_identity()
        current=struct.unpack('<2i',session.read_block(base+0x6985a8,8))
        if clock()>=deadline:
            raise CaptureUnavailable('Game has not confirmed the scene pointer; no button pressed')
        if all(abs(a-b)<=1 for a,b in zip(current,point)):
            return
        sleep(.01)
