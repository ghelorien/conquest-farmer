from types import SimpleNamespace as NS
from dataclasses import replace
import struct
import pytest
from conquest.memory_inventory import Item
from conquest.memory_warehouse import MemoryWarehouseReader, WarehouseSnapshot, deposit_received


def test_deposit_receipt_requires_exact_item_in_warehouse_without_other_losses():
    meteor=Item(42,1088001,1,1,3,0)
    other=Item(43,1088000,1,1,4,0)
    before=NS(items=(meteor,other),silver=100)
    stash=WarehouseSnapshot((),20)
    after=NS(items=(replace(other,slot=0),),silver=100)
    receipt=WarehouseSnapshot((replace(meteor,slot=0),),20)
    assert deposit_received(meteor,before,stash,after,receipt)
    assert not deposit_received(meteor,before,stash,after,stash)
    assert not deposit_received(meteor,before,stash,NS(items=(),silver=100),receipt)
    assert not deposit_received(meteor,before,stash,NS(items=after.items,silver=99),receipt)
    assert not deposit_received(meteor,before,stash,after,WarehouseSnapshot((replace(meteor,uid=99),),20))


def reader_fixture(monkeypatch,count=1):
    from conquest import memory_warehouse as module
    base=0x140000000;shared=0x500000;owner=0x600000;table=0x700000;entry=0x800000;item=0x900000
    data={base+0x69c730:struct.pack('<Q',shared),shared:struct.pack('<Q',owner),
          owner+0x1008:struct.pack('<4Q',table,8,0,count),owner+0x1030:struct.pack('<I',20),
          table:struct.pack('<Q',entry),entry:struct.pack('<Q',item),item:struct.pack('<Q',base+0x5cf220),
          item+8:struct.pack('<I',42),item+0x10:struct.pack('<I',1088001),
          item+0x62:struct.pack('<HH',1,1),item+0x6b:b'\0'}
    s=NS(read_block=lambda address,size:data[address],assert_identity=lambda:None)
    monkeypatch.setattr(module,'MemoryGui',lambda session:NS(base=base,read=lambda name:'active'))
    return MemoryWarehouseReader(s),data,owner


def test_warehouse_reads_exact_uid_and_type(monkeypatch):
    reader,data,owner=reader_fixture(monkeypatch)
    assert reader.read()==WarehouseSnapshot((Item(42,1088001,1,1,0,0),),20)


def test_warehouse_rejects_invalid_count(monkeypatch):
    reader,data,owner=reader_fixture(monkeypatch,21)
    with pytest.raises(ValueError,match='deque'):reader.read()


def test_warehouse_rejects_changed_topology(monkeypatch):
    reader,data,owner=reader_fixture(monkeypatch)
    old=reader.session.read_block; calls=[]
    def read(address,size):
        if address==owner+0x1008:
            calls.append(1)
            if len(calls)>1:return bytes(32)
        return old(address,size)
    reader.session.read_block=read
    with pytest.raises(ValueError,match='changed'):reader.read()


@pytest.mark.parametrize('case',['full','unsupported','ambiguous_receipt'])
def test_deposit_guards_and_ambiguous_receipt_never_repeat_drag(monkeypatch,case):
    from conquest import town_trade as module
    item=Item(42,1088001 if case!='unsupported' else 500005,1,1,0,0)
    bag=NS(items=(item,),silver=100)
    stash=WarehouseSnapshot((),0 if case=='full' else 20)
    grid=NS(size=(407.,175.),scroll=(0.,0.),position=(577.,435.))
    target=NS(size=(272.,326.),scroll=(0.,0.),position=(71.,195.))
    reader=NS(read=lambda:stash,gui=NS(read=lambda name:target))
    monkeypatch.setattr(module,'MemoryWarehouseReader',lambda adapter:reader)
    trade=module.TownTrade.__new__(module.TownTrade)
    trade.observer=NS(adapter=None,operations=NS(target=None))
    trade.vendor=lambda kind:123
    trade.inventory=NS(read=lambda:bag)
    trade.shop=NS(gui=NS(read=lambda name:grid))
    trade.life=lambda **kwargs:None
    drags=[]
    monkeypatch.setattr(module,'foreground_drag',lambda *args:drags.append(args))
    def verify(read,accept,failure,**kwargs):
        for _ in range(3):assert not accept(read())
        raise ValueError(failure)
    trade.verified_read=verify
    with pytest.raises(ValueError):trade({'action':'warehouse-deposit','uid':42})
    assert len(drags)==(1 if case=='ambiguous_receipt' else 0)
