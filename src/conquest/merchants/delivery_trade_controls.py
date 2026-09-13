"""Pinned native Trade grids; positions come from current window-owned tables."""
import math
import struct
import zlib
from conquest.addressing import checked_address
from conquest.merchants.memory import unpack,GuiObservationChanged
from conquest.merchants.qualification import window

def nested_table(self, window, label, seed):
    for attempt in range(3):
        try:
            return _table(self,window,label,seed)
        except GuiObservationChanged:
            if attempt==2:
                raise

def _table(self, window, label, seed):
    """Read the rendered table layout, including its scroll/clip bounds.

    This client uses 536-byte ImGuiTable records and 104-byte columns.
    The window-seeded table ID and both owning-window pointers must agree;
    an unrelated or old table cannot supply merchant input coordinates.
    """
    s = self.session
    context = unpack(s,self.base+0x6966f0,'<Q')[0]
    window_id = seed
    expected_id = zlib.crc32(label.encode('utf-8'),window_id)
    header = s.read_block(context+0x4338,16)
    count,capacity,array = struct.unpack('<IIQ',header)
    if not 0 < count <= capacity <= 256:
        raise ValueError('Invalid GUI table pool')
    frame_before = unpack(s,context+0x3e38,'<I')[0]
    records = s.read_block(checked_address(array),count*536)
    matches = [i for i in range(count) if struct.unpack_from('<I',records,i*536)[0]==expected_id]
    if len(matches)!=1:
        raise ValueError('Expected one window-owned GUI table')
    address = array+matches[0]*536
    raw = records[matches[0]*536:(matches[0]+1)*536]
    frame_after = unpack(s,context+0x3e38,'<I')[0]
    last_frame,columns = struct.unpack_from('<II',raw,0x70)
    if frame_after < frame_before or not frame_before-3 <= last_frame <= frame_after:
        raise ValueError('GUI table is not currently rendered')
    if not 1 <= columns <= 64 or struct.unpack_from('<2Q',raw,0x180)!=(window['address'],window['address']):
        raise ValueError('GUI table ownership or column count changed')
    pointer = checked_address(struct.unpack_from('<Q',raw,0x18)[0])
    column_data = s.read_block(pointer,columns*104)
    outer = struct.unpack_from('<4f',raw,0xf0)
    clip = struct.unpack_from('<4f',raw,0x120)
    row_height = struct.unpack_from('<f',raw,0x1ac)[0]
    cells = [{'minimum':struct.unpack_from('<f',column_data,i*104+8)[0],
              'maximum':struct.unpack_from('<f',column_data,i*104+12)[0],
              'content_x':struct.unpack_from('<f',column_data,i*104+52)[0]}
             for i in range(columns)]
    values = (*outer,*clip,row_height,*(v for cell in cells for v in cell.values()))
    if (not all(math.isfinite(v) and -8192 <= v <= 8192 for v in values)
            or not 4 <= row_height <= 512
            or outer[2]<=outer[0] or outer[3]<=outer[1]
            or clip[2]<=clip[0] or clip[3]<=clip[1]
            or any(not c['minimum']<=c['content_x']<c['maximum'] for c in cells)):
        raise GuiObservationChanged('Invalid GUI table geometry')
    after = s.read_block(address,536)
    stable = ((0,4),(0x18,8),(0x74,4),(0xf0,64),(0x180,16),(0x1ac,4))
    if (s.read_block(context+0x4338,16)!=header
            or any(after[o:o+n]!=raw[o:o+n] for o,n in stable)
            or s.read_block(pointer,columns*104)!=column_data):
        raise GuiObservationChanged('GUI table changed during observation')
    return {'id':expected_id,'address':address,'columns':cells,'outer':outer,
            'clip':clip,'row_height':row_height}

def trade_grid(gui,snapshot):
    for rva,code in ((0x10f44d,'488d0d34bd4b00'),(0x10f606,'488d0d93314b00'),(0x10f623,'e848fa0a00')):
        if gui.session.read_block(gui.base+rva,len(bytes.fromhex(code)))!=bytes.fromhex(code):
            raise ValueError('Native trade placement handler changed')
    w=window(snapshot,'Trade##TradeWindow')
    outer=gui.table(w,'##TradeWindowGrid')
    table=nested_table(gui,w,'##TradeWindowGrid1',outer['id'])
    if len(outer['columns'])!=3 or len(table['columns'])!=5 or table['row_height']!=44:
        raise ValueError('Trade item grid dimensions changed')
    return w,table


def cell(table,slot,offset=(20,20)):
    columns=table['columns']
    if type(slot) is not int or slot<0:raise ValueError('Invalid grid slot')
    x=columns[slot%len(columns)]['content_x']+offset[0]
    y=table['outer'][1]+slot//len(columns)*table['row_height']+offset[1]
    l,t,r,b=table['clip']
    if not l+2<x<r-2 or not t+2<y<b-2:raise ValueError('Item cell is clipped')
    return round(x),round(y)


def endpoints(gui,snapshot,item,offered):
    inventory=window(snapshot,'Inventory/##ItemGrid_A800F95C')
    table=gui.table(inventory,'##ItemTable')
    if len(table['columns'])!=10 or table['row_height']!=40:
        raise ValueError('Inventory grid dimensions changed')
    trade,grid=trade_grid(gui,snapshot)
    if len(offered)>=20:raise ValueError('Trade grid is full')
    return inventory,trade,cell(table,item['slot']),cell(grid,len(offered))
