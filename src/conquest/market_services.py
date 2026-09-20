"""Memory-qualified Market services and their ordinary dialog input."""
from dataclasses import asdict
import time
from conquest.memory_npcs import VendorIdentity,NpcObservation
from conquest.memory_entities import sample_fields
from conquest.addressing import checked_address
from conquest.conductress import read_dialog,dialog_option_point

# Models from the installed npc.json, checked again in the live actor scene.
MODELS={'Conductress':{280,287},'MillionaireLee':{4290,4294,4297},
        'Warehouseman':{80,87,200,210},'Mark.Controller':{417}}


def discover(entities,map_id,name,*,stable_identity_only=False):
    started=time.monotonic()
    if name not in MODELS:raise ValueError('Unsupported Market service')
    s,p=entities.session,entities.layout
    base,collection,trace=entities._resolve()
    headers=[(collection+o,'u64') for o in (p.begin_offset,p.end_offset,p.capacity_offset)]
    bounds=sample_fields(s,headers);begin,end,capacity=bounds
    if not(begin<=end<=capacity and (end-begin)%p.entry_stride==0
           and (capacity-begin)%p.entry_stride==0 and (capacity-begin)//p.entry_stride<=p.max_objects):
        raise ValueError('Service scene bounds changed')
    entries=[(a+p.entry_object_offset,'u64') for a in range(begin,end,p.entry_stride)]
    objects=sample_fields(s,entries)
    vtables=sample_fields(s,[(checked_address(a),'u64') for a in objects])
    actors=[a for a,v in zip(objects,vtables) if v==base+p.monster_vtable_rva]
    models=sample_fields(s,[(a+0x84,'u32') for a in actors])
    matches=[]
    for a,model in zip(actors,models):
        if model not in MODELS[name]:continue
        fields=[(a+0x7c,'u32'),(a+p.name_offset,'utf8'),(a+p.position_offset,'xy_u32'),
                (a+p.id_offset,'u32'),(a+p.kind_offset,'u32'),
                (a+p.draw_position_offset,'i32'),(a+p.draw_position_offset+4,'i32')]
        values=sample_fields(s,fields)
        fresh_values=sample_fields(s,fields)
        # Warehouse item input targets the verified inventory grid, not the
        # NPC's animated draw coordinates. Keep strict draw sampling for every
        # ordinary NPC interaction; this narrower mode is only for identity.
        if (fresh_values[:5] if stable_identity_only else fresh_values)!=(values[:5] if stable_identity_only else values):
            raise ValueError('Service changed during observation')
        values=fresh_values
        kind,found,position,uid,species,dx,dy=values
        if found==name:
            if not uid or species!=0 or any(not 0<=v<2048 for v in position):
                raise ValueError('Service actor identity is invalid')
            matches.append((VendorIdentity(map_id,kind,found,model,tuple(position)),
                            NpcObservation(a,uid,kind,found,map_id,tuple(position),(dx,dy))))
    if len(matches)!=1:raise ValueError('One memory-identified '+name+' is required in the scene')
    # Other players reorder this scene continuously in Market. Require the
    # selected NPC to remain in a fresh bounded scene and retain every pinned
    # identity field; unrelated player movement does not invalidate the NPC.
    fresh=sample_fields(s,headers);fb,fe,fc=fresh
    if not(fb<=fe<=fc and (fe-fb)%p.entry_stride==0
           and (fc-fb)%p.entry_stride==0 and (fc-fb)//p.entry_stride<=p.max_objects):
        raise ValueError('Service scene bounds changed')
    members=sample_fields(s,[(v+p.entry_object_offset,'u64') for v in range(fb,fe,p.entry_stride)])
    identity,npc=matches[0];a=npc.object_address
    fields=[(a,'u64'),(a+0x84,'u32'),(a+0x7c,'u32'),(a+p.name_offset,'utf8'),
            (a+p.position_offset,'xy_u32'),(a+p.id_offset,'u32'),(a+p.kind_offset,'u32'),
            (a+p.draw_position_offset,'i32'),(a+p.draw_position_offset+4,'i32')]
    expected=[base+p.monster_vtable_rva,identity.model,identity.type_id,identity.name,
              identity.position,npc.entity_id,0,*npc.draw_position]
    if stable_identity_only:fields=fields[:7];expected=expected[:7]
    if members.count(a)!=1 or sample_fields(s,fields)!=expected:
        raise ValueError('Service identity changed during observation')
    if sample_fields(s,[(a,'u64') for a,_ in trace])!=[v for _,v in trace]:
        raise ValueError('Service scene owner changed')
    s.assert_identity()
    if time.monotonic()-started>1:raise ValueError('Service observation expired')
    return matches[0]


def dialog_snapshot(observer):
    from conquest.viewport import size_for
    d=read_dialog(observer)
    return {'records':d['records'],'window':asdict(d['window']),'table':d['table'],
            'viewport':size_for(observer)}


def dialog_point(observer,option,expected_records):
    d=read_dialog(observer)
    if d['records']!=expected_records:raise ValueError('Service dialog changed before selection')
    from conquest.viewport import size_for
    return dialog_option_point(d,option,size_for(observer))


def execute(trade,body):
    action=body.get('action')
    if action=='service-close-panel' and set(body)=={'action','window'}:
        if body['window'] not in ('Inventory','Dialog'):raise ValueError('Unsupported service panel')
        from conquest.memory_shop import MemoryGui
        gui=MemoryGui(trade.observer.adapter);window=gui.read(body['window'])
        if gui.read(body['window'])!=window:raise ValueError('Service panel moved')
        trade.input_attempted=True
        trade.click((round(window.position[0]+window.size[0]-18),round(window.position[1]+18)))
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            try:gui.read(body['window'])
            except ValueError as error:
                if 'not active' in str(error):return {'closed':True}
                raise
            time.sleep(.05)
        raise ValueError('Service panel close was not verified')
    if action=='service-dismiss' and set(body)=={'action'}:
        data=dialog_snapshot(trade.observer)
        point=dialog_point(trade.observer,'Just passing by.',data['records'])
        trade.input_attempted=True
        trade.click(point)
        return {'dismissed':True}
    if action=='service-dialog' and set(body)=={'action'}:
        return dialog_snapshot(trade.observer)
    if action not in ('service-locate','service-open','service-select','service-scroll-dialog'):return None
    expected={'action','name'}|({'option','records'} if action in ('service-select','service-scroll-dialog') else set())
    if set(body)!=expected:raise ValueError('Unsupported service arguments')
    life=trade.life(any_map=True)
    identity,npc=discover(trade.observer.entities,life.map_id,body['name'])
    if action=='service-locate':return {'identity':asdict(identity),'npc':asdict(npc)}
    if max(abs(a-b) for a,b in zip(life.position,npc.position))>18:raise ValueError('Travel closer to service NPC')
    if action=='service-open':
        from conquest.memory_shop import MemoryGui
        try:MemoryGui(trade.observer.adapter).read('Dialog')
        except ValueError as error:
            if not any(t in str(error) for t in ('not active','absent')):raise
        else:execute(trade,{'action':'service-close-panel','window':'Dialog'})
        point=(npc.draw_position[0],npc.draw_position[1]-32)
    elif action=='service-scroll-dialog':
        from conquest.dialog_geometry import scroll_direction
        from conquest.foreground import foreground_scroll
        from conquest.viewport import size_for
        if npc!=getattr(trade,'service_npc',None):raise ValueError('Open this service before scrolling its dialog')
        data=read_dialog(trade.observer);w=data['window'];x,y=w.position
        viewport=size_for(trade.observer)
        direction=scroll_direction(data,body['option'],viewport)
        if data['records']!=body['records'] or not direction:
            raise ValueError('Dialog does not require scrolling or its records changed')
        point=(round(x+w.size[0]/2),round((y+24+min(y+w.size[1]-4,viewport[1]-4))/2))
        if not(0<point[0]<viewport[0] and y+20<point[1]<min(y+w.size[1],viewport[1])):
            raise ValueError('Dialog scroll target is outside the client')
        if read_dialog(trade.observer)!=data:raise ValueError('Dialog changed before scrolling')
    else:
        if npc!=getattr(trade,'service_npc',None):raise ValueError('Open this service before selecting its dialog')
        point=dialog_point(trade.observer,body['option'],body['records'])
    if discover(trade.observer.entities,life.map_id,body['name'])[1]!=npc:
        raise ValueError('Service NPC changed before input')
    if action=='service-select' and dialog_point(trade.observer,body['option'],body['records'])!=point:
        raise ValueError('Service dialog moved before input')
    trade.input_attempted=True
    if action=='service-scroll-dialog':
        foreground_scroll(trade.observer.operations.target,point,direction,viewport)
    else:trade.click(point)
    trade.service_npc=npc
    return {'interacted':True,'npc_id':npc.entity_id,'option':body.get('option')}
