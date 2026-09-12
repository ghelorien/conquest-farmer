"""The live, memory-identified Twin City Conductress."""
from conquest.memory_npcs import MemoryNpcReader,VendorIdentity
from conquest.memory_life import read_life
from conquest.addressing import checked_address
from conquest.memory_shop import MemoryGui
import struct
import json
from pathlib import Path
import time
from conquest.dialog_geometry import option_point as dialog_option_point

TRIPS=Path('profiles/conductress-routes.json')


def take_saved_trip(loop,destination_map):
    trips=json.loads(TRIPS.read_text(encoding='utf-8'))['trips'] if TRIPS.exists() else []
    matches=[t for t in trips if t['destination_map']==destination_map and t.get('verified') is True]
    if len(matches)!=1:return False
    trip=matches[0]
    before_life=loop.living()['embedded_controls']['life']
    if before_life['map_id']!=trip['source_map']:
        raise ValueError('Conductress trip must start in its saved source map')
    actor=before_life['object_address']
    from conquest.banking import ensure_transport
    ensure_transport(loop,minimum=trip['price']*2)
    loop.record('conductress_departing',destination_map=destination_map,
                activity=f'Heading to Conductress for {trip["option"]}')
    loop.travel((438,444))
    loop.town('conductress-open')
    prepare_destination(loop,trip['option'])
    before=loop.town('supplies')
    loop.town('conductress-travel',destination=trip['option'])
    for _ in range(30):
        health=loop.health();life=health['embedded_controls'].get('life')
        if (life and life['object_address']==actor and not life['dead_candidate']
                and 0<=time.time()-health['embedded_controls'].get('observed_at',0)<=1
                and life['map_id']==trip['arrival_map']
                and max(abs(a-b) for a,b in zip(life['position'],trip['arrival_position']))<=2):
            after=loop.town('supplies')
            if before['silver']-after['silver']==trip['price']:
                loop.record('conductress_arrived',destination_map=destination_map,position=life['position'],
                            activity=f'Conductress trip complete; entering {trip["option"]}')
                return True
        time.sleep(.1)
    raise ValueError('Conductress travel was not verified; no repeat payment issued')

TWIN_CONDUCTRESS=VendorIdentity(1002,0,'Conductress',280,(435,440))


def read_conductress(observer):
    life=read_life(observer.adapter,observer.health_layout,observer.character)
    if life.dead_candidate:raise ValueError('Living character required for Conductress travel')
    found=MemoryNpcReader(observer.entities,vendors=[TWIN_CONDUCTRESS]).read(life.map_id).npcs
    if len(found)!=1:raise ValueError('Conductress is not in the current memory scene')
    npc=found[0]
    if max(abs(a-b) for a,b in zip(life.position,npc.position))>18:
        raise ValueError('Travel closer to the Conductress')
    return npc


def read_dialog(observer):
    s=observer.adapter
    life=read_life(s,observer.health_layout,observer.character)
    if life.dead_candidate:raise ValueError('Living character required for NPC dialog')
    actor=life.object_address
    header=s.read_block(actor+0x1060,32)
    table,capacity,first,count=struct.unpack('<4Q',header)
    if not 1<=count<=32 or not count<=capacity<=128 or capacity&(capacity-1):
        raise ValueError('NPC dialog deque is invalid')
    pointers=s.read_block(checked_address(table),capacity*8)
    records=[];checks=[]
    for index in range(count):
        shared=checked_address(struct.unpack_from('<Q',pointers,((first+index)&(capacity-1))*8)[0])
        reference=s.read_block(shared,16)
        address=checked_address(struct.unpack_from('<Q',reference)[0])
        raw=s.read_block(address,0x30)
        kind,_,option=struct.unpack_from('<3I',raw)
        length,allocated=struct.unpack_from('<QQ',raw,0x20)
        if kind not in range(5) or not 0<=length<=2048 or not max(length,15)<=allocated<=4096:
            raise ValueError('NPC dialog record is invalid')
        content=(raw[0x10:0x20] if allocated<=15 else s.read_block(checked_address(struct.unpack_from('<Q',raw,0x10)[0]),length))[:length]
        records.append({'kind':kind,'option':option,'text':content.decode('utf-8')})
        checks.extend([(shared,reference),(address,raw)])
        if allocated>15 and length:checks.append((struct.unpack_from('<Q',raw,0x10)[0],content))
    window=MemoryGui(s).read('Dialog')
    dc=s.read_block(window.address+0xe0,0x38)
    right,top=struct.unpack_from('<2f',dc,8);left=struct.unpack_from('<f',dc,16)[0]
    height=struct.unpack_from('<f',dc,0x34)[0]
    if (s.read_block(actor+0x1060,32)!=header or s.read_block(table,capacity*8)!=pointers
            or any(s.read_block(a,len(raw))!=raw for a,raw in checks)
            or s.read_block(window.address+0xe0,0x38)!=dc):
        raise ValueError('NPC dialog changed during observation')
    return {'records':records,'window':window,'table':(left,top,right,height)}


def validate_destination(data,destination):
    choices={'Phoenix Castle','Desert City','Ape Mountain','Bird Island.'}
    if destination not in choices:raise ValueError('Unsupported leveling destination')
    records=data['records']
    options=[r for r in records if r['kind']==1]
    texts=[r['text'] for r in records if r['kind']==0]
    if (texts!=['Where are you heading? I can teleport you for a price of 100 silver.']
            or [r['text'] for r in options]!=['Phoenix Castle','Desert City','Ape Mountain','Bird Island.',
                'Mine Cave','Market','Just passing by.'] or [r['option'] for r in options]!=list(range(7))
            or any(r['kind']==2 for r in records)):
        raise ValueError('Conductress choices differ from the qualified dialog')


def prepare_destination(loop,destination):
    from conquest.dialog_geometry import scroll_direction
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        loop.check_stop()
        data=loop.town('service-dialog')
        validate_destination(data,destination)
        if not scroll_direction(data,destination,data.get('viewport')):return
        loop.town('service-scroll-dialog',name='Conductress',records=data['records'],option=destination)
        time.sleep(.15)
    raise ValueError('Conductress choice is still clipped; no fare submitted')


def destination_point(observer,destination):
    data=read_dialog(observer)
    validate_destination(data,destination)
    from conquest.viewport import size_for
    return dialog_option_point(data,destination,size_for(observer))
