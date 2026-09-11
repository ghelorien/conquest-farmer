from types import SimpleNamespace as NS
import pytest
from conquest import market_services as m


def test_dialog_choice_uses_exact_memory_records_and_table(monkeypatch):
    records=[{'kind':0,'option':0,'text':'Pack Meteors?'},
             {'kind':1,'option':1,'text':'Pack ten Meteors'},
             {'kind':1,'option':2,'text':'Leave'}]
    d={'records':records,'window':NS(position=(20.,30.),size=(280.,160.),scroll=(0.,0.)),
       'table':(40.,100.,260.,22.)}
    monkeypatch.setattr(m,'read_dialog',lambda observer:d)
    assert m.dialog_point(None,'Pack ten Meteors',records)==(95,111)
    with pytest.raises(ValueError,match='changed'):m.dialog_point(None,'Pack ten Meteors',[])
    with pytest.raises(ValueError,match='absent'):m.dialog_point(None,'Unpack',records)
    d['table']=(40.,100.,270.,22.)
    with pytest.raises(ValueError,match='layout'):m.dialog_point(None,'Pack ten Meteors',records)


def test_withdrawal_requires_exact_meteor_uid_and_no_other_loss():
    from conquest.memory_inventory import Item
    from conquest.memory_warehouse import withdrawal_received,WarehouseSnapshot
    meteor=Item(42,1088001,1,1,0,0);other=Item(43,1088001,1,1,1,0)
    before=NS(items=(),silver=100);stash=WarehouseSnapshot((meteor,other),20)
    after=NS(items=(meteor,),silver=100);remaining=WarehouseSnapshot((other,),20)
    assert withdrawal_received(meteor,before,stash,after,remaining)
    assert not withdrawal_received(meteor,before,stash,before,stash)
    assert not withdrawal_received(meteor,before,stash,after,WarehouseSnapshot((),20))
    assert not withdrawal_received(meteor,before,stash,NS(items=(other,),silver=100),remaining)


def test_scrolled_packing_choices_must_be_fully_inside_dialog(monkeypatch):
    records=[{'kind':1,'option':0,'text':'Pack Meteors'}]
    window=NS(position=(378.,20.),size=(280.,198.),scroll=(0.,46.))
    data={'records':records,'window':window,'table':(398.,188.,618.,22.)}
    monkeypatch.setattr(m,'read_dialog',lambda observer:data)
    assert m.dialog_point(None,'Pack Meteors',records)==(453,199)
    data['table']=(398.,208.,618.,22.)
    with pytest.raises(ValueError,match='layout'):m.dialog_point(None,'Pack Meteors',records)


def test_ten_meteor_exchange_rejects_partial_or_unrelated_item_loss():
    from conquest.meteor_banking import batch,exchange_received
    meteors=[dict(uid=i,type_id=1088001,amount=1,limit=1,plus=0) for i in range(10)]
    ring=dict(uid=99,type_id=150009,amount=2300,limit=3000,plus=2)
    scroll=dict(uid=100,type_id=720027,amount=1,limit=1,plus=0)
    before={'items':meteors+[ring],'silver':200}
    after={'items':[ring,scroll],'silver':200}
    assert len(batch(meteors))==10 and not batch(meteors[:9])
    assert exchange_received(before,after)
    assert not exchange_received(before,{**after,'items':[scroll]})
    assert not exchange_received(before,{**after,'items':[ring,scroll,meteors[0]]})
    assert not exchange_received(before,{**after,'silver':199})


def test_service_discovery_tolerates_unrelated_scene_reordering(monkeypatch):
    layout=NS(begin_offset=0,end_offset=8,capacity_offset=16,entry_stride=16,
        entry_object_offset=8,max_objects=8,monster_vtable_rva=0x100,
        name_offset=0xa4,position_offset=0xe8,id_offset=0x78,kind_offset=0x80,draw_position_offset=0xf8)
    base,collection,entries,a,b=0x100000,0x200000,0x250000,0x300000,0x400000
    values={collection:entries,collection+8:entries+32,collection+16:entries+32,
        a:base+0x100,b:base+0x100,a+0x84:87,b+0x84:9999,
        a+0x7c:0,a+0xa4:'Warehouseman',a+0xe8:(182,180),a+0x78:42,
        a+0x80:0,a+0xf8:500,a+0xfc:400}
    calls=[0]
    def sample(session,fields):
        if fields==[(entries+8,'u64'),(entries+24,'u64')]:
            calls[0]+=1
            return [a,b] if calls[0]==1 else [b,a]
        return [values[address] for address,kind in fields]
    monkeypatch.setattr(m,'sample_fields',sample)
    entities=NS(session=NS(assert_identity=lambda:None),layout=layout,_resolve=lambda:(base,collection,[]))
    identity,npc=m.discover(entities,1036,'Warehouseman')
    assert npc.entity_id==42 and identity.position==(182,180)
    values[a+0xa4]='HelpNpc'
    with pytest.raises(ValueError,match='memory-identified'):m.discover(entities,1036,'Warehouseman')


def test_unqualified_exchange_and_unfinished_batch_never_withdraw(monkeypatch):
    from conquest import meteor_banking as banking
    stored={'items':[dict(uid=i,type_id=1088001,amount=1,limit=1) for i in range(10)]}
    monkeypatch.setattr(banking,'read_json',lambda path:{'enabled':False,'qualified':False})
    assert banking.consolidate(None,stored) is False
    monkeypatch.setattr(banking,'read_json',lambda path:
        {'enabled':True,'qualified':True,'exchange':{'fee':0}} if path==banking.POLICY else {'phase':'exchange_pending'})
    with pytest.raises(ValueError,match='reconciliation'):banking.consolidate(None,stored)


@pytest.mark.parametrize('type_id,plus',[(1088001,0),(720027,0),(1088000,0),(150009,0),(150006,2)])
def test_market_return_refuses_carried_valuables_before_movement(type_id,plus):
    from conquest.meteor_banking import trip
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036}}},
            town=lambda action:{'items':[{'uid':42,'type_id':type_id,'plus':plus,'slot':1}]})
    with pytest.raises(ValueError,match='Stay in Market'):
        trip(loop,{'source_map':1036,'destination_map':1011})
