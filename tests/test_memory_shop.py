import struct
from types import SimpleNamespace
import pytest

from conquest.memory_life import CLIENT_SHA256
from conquest.memory_shop import MemoryGui,GuiWindow,ShopProduct,ShopSnapshot


def test_ground_diagnostics_do_not_shadow_supply_snapshot_serializer():
    from conquest.town_trade import TownTrade
    from conquest.memory_inventory import InventorySnapshot
    trade=TownTrade.__new__(TownTrade)
    trade.life=lambda *args,**kwargs:SimpleNamespace(map_id=1011,position=(341,441))
    trade.inventory=SimpleNamespace(read=lambda:InventorySnapshot(1,1,(),None,200,40))
    assert trade.execute({'action':'supplies'})['silver']==200


def gui_fixture():
    base,context,array,window,name=0x140000000,0x100000,0x200000,0x300000,0x400000
    record=bytearray(0x250)
    struct.pack_into('<Q',record,0,name)
    struct.pack_into('<4f',record,0x18,51,158,288,396)
    record[0x97]=1
    struct.pack_into('<I',record,0x248,1000)
    blobs={base+0x6966f0:struct.pack('<Q',context),context+0x3e58:struct.pack('<IIQ',1,1,array),
           context+0x3e38:struct.pack('<I',1000),array:struct.pack('<Q',window),window:record,
           name:b'Shop\0'+bytes(91)}
    s=SimpleNamespace(expected_sha256=CLIENT_SHA256,modules=[{'name':'ImConquer.exe','base':base}],
        assert_identity=lambda:None,read_block=lambda address,size:bytes(blobs[address][:size]))
    return MemoryGui(s),record


def test_shop_geometry_is_read_from_active_memory_window():
    reader,_=gui_fixture()
    assert reader.read('Shop').position==(51,158)


@pytest.mark.parametrize('hidden,frame',[(True,1000),(False,990)])
def test_cached_closed_or_old_shop_window_cannot_authorize_input(hidden,frame):
    reader,record=gui_fixture()
    record[0x97]=not hidden
    struct.pack_into('<I',record,0x248,frame)
    with pytest.raises(ValueError,match='not active'):
        reader.read('Shop')


def test_shop_item_coordinates_follow_observed_grid_and_selected_memory_index():
    window=GuiWindow(1,'Shop',(51.,158.),(288.,396.),(0.,0.))
    grid=GuiWindow(2,'Shop/grid',(71.,196.),(248.,328.),(0.,0.))
    potion=ShopProduct(2,3,1000020,'Painkiller',60,250)
    arrow=ShopProduct(0,4,1050000,'LuckyArrow',200,0)
    snapshot=ShopSnapshot(100123,(potion,arrow),window,grid)
    assert snapshot.point(potion)==(186,228) and snapshot.point(arrow)==(90,228)
    other=ShopProduct(6,5,500005,'BambooBow',204,0)
    assert ShopSnapshot(100123,(other,),window,grid).point(other)==(138,308)
    far=ShopProduct(28,6,500045,'HardBow',4000,0)
    with pytest.raises(ValueError,match='qualified visible'):
        ShopSnapshot(100123,(far,),window,grid).point(far)
