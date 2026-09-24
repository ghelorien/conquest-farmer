"""Exact-1078 Items and Trade HUD points from native renderer and GUI memory.

Read-only qualification. The caller owns foreground, input lease, Stop and
before-press hover checks; these functions never send input.
"""
import hashlib
import math
import struct

from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.trade_reader_1078 import assert_trade_code
from conquest.viewport import size_for

HUD_RVA=0x9B8E0
HUD_SIZE=640
HUD_SHA256='7612a144c66f3a3362a4ab59f08f1efa1caba6a88630c3a2fd324a035550cccf'
TABLE_ID=0x02A99238
TABLE_FLAGS=0x482010


def _buttons(gui):
    session=gui.session
    if session.expected_sha256!=CLIENT_SHA256_1078:
        raise ValueError('Trade HUD helper belongs to exact client 1078')
    base=assert_trade_code(session)
    read=getattr(session,'read_block',None) or session.read
    if (hashlib.sha256(read(base+HUD_RVA,HUD_SIZE)).hexdigest()!=HUD_SHA256
            or read(base+0x5DD998,10)!=b'##Buttons\0'
            or read(base+0x5E02A8,6)!=b'Items\0'
            or read(base+0x5DAC80,6)!=b'Trade\0'):
        raise ValueError('1078 Items/Trade HUD renderer changed')
    window=gui.read('##Control')
    viewport=size_for(session)
    context=struct.unpack('<Q',read(base+gui.context_rva,8))[0]
    header=read(context+0x4338,16)
    count,capacity,array=struct.unpack('<IIQ',header)
    if not 0<count<=capacity<=256:
        raise ValueError('1078 HUD table pool is invalid')
    matches=[]
    for index in range(count):
        address=array+index*536
        raw=read(address,536)
        if struct.unpack_from('<II',raw)==(TABLE_ID,TABLE_FLAGS):
            matches.append((address,raw))
    if len(matches)!=1:
        raise ValueError('1078 Items/Trade table is absent or ambiguous')
    address,raw=matches[0]
    frame=struct.unpack('<I',read(context+0x3E38,4))[0]
    last_frame,columns=struct.unpack_from('<II',raw,0x70)
    owners=struct.unpack_from('<2Q',raw,0x180)
    left,top,right,bottom=struct.unpack_from('<4f',raw,0xF0)
    clip=struct.unpack_from('<4f',raw,0x120)
    row_height=struct.unpack_from('<f',raw,0x1AC)[0]
    pointer=struct.unpack_from('<Q',raw,0x18)[0]
    column=read(pointer+104,104)
    x1,x2=struct.unpack_from('<2f',column,0x34)
    if (not 0<=frame-last_frame<=3 or columns!=6
            or owners!=(window.address,window.address)
            or not all(math.isfinite(v) for v in (left,top,right,bottom,*clip,row_height,x1,x2))
            or bottom-top!=40 or row_height!=40 or x2-x1!=71
            or not left<=x1<x2<=right or not clip[0]<=x1<x2<=clip[2]
            or not window.position[0]<=left<right<=window.position[0]+window.size[0]
            or not window.position[1]<=top<bottom<=window.position[1]+window.size[1]
            or not 0<=(x1+x2)/2<viewport[0] or not 0<top+30<viewport[1]):
        raise ValueError('1078 Items/Trade table geometry changed')
    current=read(address,536)
    if (read(context+0x4338,16)!=header
            or current[:0x20]!=raw[:0x20]
            or current[0x74:0x78]!=raw[0x74:0x78]
            or current[0xF0:0x130]!=raw[0xF0:0x130]
            or current[0x180:0x190]!=raw[0x180:0x190]
            or current[0x1AC:0x1B0]!=raw[0x1AC:0x1B0]
            or read(pointer+104,104)!=column
            or read(base+gui.context_rva,8)!=struct.pack('<Q',context)
            or gui.read('##Control')!=window):
        raise ValueError('1078 Items/Trade table changed during observation')
    session.assert_identity()
    center=round((x1+x2)/2)
    return (center,round(top+10)),(center,round(top+30))


def inventory_button(gui):
    return _buttons(gui)[0]


def trade_button(gui):
    return _buttons(gui)[1]
