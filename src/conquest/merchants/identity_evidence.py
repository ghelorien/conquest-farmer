"""Read-only cross-client identity evidence; never authorizes trade input."""
import struct
import time
from conquest.memory_entities import sample_fields


def peer_evidence(observer,peer):
    s=observer.adapter;e=observer.entities;p=e.layout
    base,collection,trace=e._resolve()
    fields=[(collection+o,'u64') for o in (p.begin_offset,p.end_offset,p.capacity_offset)]
    header=sample_fields(s,fields);begin,end,capacity=header
    if not (0<begin<=end<=capacity and (end-begin)%p.entry_stride==0
            and (capacity-begin)%p.entry_stride==0
            and (capacity-begin)//p.entry_stride<=p.max_objects):
        raise ValueError('Peer evidence scene bounds are invalid')
    entries=s.read_block(begin,end-begin) if end>begin else b''
    pointers=[struct.unpack_from('<Q',entries,i+p.entry_object_offset)[0]
              for i in range(0,len(entries),p.entry_stride)]
    if len(set(pointers))!=len(pointers):raise ValueError('Peer evidence scene is ambiguous')
    matches=[];started=time.monotonic()
    for address in pointers:
        raw=s.read_block(address,0x120)
        for uid_offset,name_offset,position_offset,draw_offset in (
                (0x68,0x94,0xd8,0xe8),(0x78,0xa4,0xe8,0xf8)):
            uid=struct.unpack_from('<I',raw,uid_offset)[0]
            if uid!=peer['character_uid']:continue
            name=raw[name_offset:name_offset+32].split(b'\0')[0]
            if name!=peer['character'].encode('utf-8'):continue
            position=list(struct.unpack_from('<2I',raw,position_offset))
            if position!=peer['position']:raise ValueError('Peer position differs between clients')
            fresh=s.read_block(address,0x120)
            for offset,size in ((0,8),(uid_offset,4),(name_offset,32),(position_offset,8),(draw_offset,8)):
                if raw[offset:offset+size]!=fresh[offset:offset+size]:
                    raise ValueError('Peer evidence changed during observation')
            matches.append({'uid':uid,'name':peer['character'],'position':position,
                'vtable_rva':struct.unpack_from('<Q',raw)[0]-base,
                'uid_offset':uid_offset,'name_offset':name_offset,'name_format':'inline_utf8',
                'name_capacity':32,'position_offset':position_offset,'draw_offset':draw_offset,
                'draw_format':'i32','draw_i32':list(struct.unpack_from('<2i',raw,draw_offset)),
                'draw_bytes':raw[draw_offset:draw_offset+8].hex()})
    if len(matches)!=1:raise ValueError('One matching peer identity was not found in the scene')
    if (sample_fields(s,fields)!=header
            or (end>begin and s.read_block(begin,end-begin)!=entries)
            or sample_fields(s,[(a,'u64') for a,_ in trace])!=[v for _,v in trace]
            or time.monotonic()-started>2):
        raise ValueError('Peer evidence expired or scene changed')
    s.assert_identity()
    return {'observer':observer.character,'peer':matches[0],'input_qualified':False,
            'source_identity':s.identity,'peer_identity':peer['identity'],'verified_at':time.time()}
