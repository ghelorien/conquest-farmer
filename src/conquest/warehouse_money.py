"""Ordinary warehouse money input, qualified by memory before and after."""
import ctypes as c
from ctypes import wintypes as w
import math
import struct
from conquest.memory_warehouse import WarehouseMoneyReader,money_received


def money_points(reader,snapshot):
    s=reader.session;base=reader.gui.base
    ctx=struct.unpack('<Q',s.read_block(base+0x6966f0,8))[0]
    font=struct.unpack('<Q',s.read_block(ctx+0x3c20,8))[0]
    size=struct.unpack('<f',s.read_block(ctx+0x3c28,4))[0]
    count,capacity,array=struct.unpack('<IIQ',s.read_block(font,16))
    native=struct.unpack('<f',s.read_block(font+0x14,4))[0]
    padding=struct.unpack('<2f',s.read_block(ctx+0x3834,8))
    spacing=struct.unpack('<2f',s.read_block(ctx+0x3844,8))
    if (not 128<=count<=capacity<=0x110000 or not 8<=size<=24 or not 8<=native<=24
            or padding!=(4.,3.) or spacing!=(8.,4.)
            or snapshot.window.size!=(312.,460.) or snapshot.grid.size!=(272.,326.)
            or snapshot.grid.scroll!=(0.,0.)
            or snapshot.grid.position!=(snapshot.window.position[0]+20,snapshot.window.position[1]+96)):
        raise ValueError('Warehouse money control geometry differs from renderer profile')
    advances=struct.unpack('<128f',s.read_block(array,512))
    def width(label):
        values=[advances[ord(ch)]*size/native for ch in label]
        if not all(math.isfinite(v) and 1<=v<=24 for v in values):raise ValueError('Warehouse font metrics changed')
        return sum(values)+2*padding[0]
    # Renderer 116c67: InputText width = FontSize * 8. Buttons use SameLine.
    x=snapshot.grid.position[0];height=size+2*padding[1]
    y=snapshot.grid.position[1]-spacing[1]-height/2
    field_width=size*8;deposit_width=width('Deposit');withdraw_width=width('Withdraw')
    result={'amount':(round(x+field_width/2),round(y)),
            'deposit':(round(x+field_width+spacing[0]+deposit_width/2),round(y)),
            'withdraw':(round(x+field_width+2*spacing[0]+deposit_width+withdraw_width/2),round(y))}
    if reader.gui.read('Warehouse')!=snapshot.window:raise ValueError('Warehouse moved before money input')
    return result


def type_amount(target,amount,*,expected_size=(1036,793),maximum=999999999):
    from conquest.foreground import Input,InputUnion,KeyboardInput,press_scan_sequence
    from conquest.win32 import bind
    from conquest.mouse_priority import guarded_send,require_idle
    from conquest.capture import CaptureUnavailable
    if type(maximum) is not int or not 1<=maximum<=2147483647 or type(amount) is not int or not 1<=amount<=maximum:
        raise ValueError('Invalid money amount')
    require_idle()
    def focus():
        view=target.snapshot()
        if view['minimized'] or view['client_size']!=list(expected_size) or target.backend.foreground()!=target.hwnd:
            raise CaptureUnavailable('Warehouse input lost focus')
    focus()
    keys=bind(target.backend.user,'GetAsyncKeyState',[c.c_int],c.c_short)
    if any(keys(vk)&0x8000 for vk in (0x10,0x11,0x12)):raise CaptureUnavailable('Physical modifiers held; no amount entered')
    send=guarded_send(bind(target.backend.user,'SendInput',[w.UINT,c.POINTER(Input),c.c_int],w.UINT))
    def key(event):
        if not event.data.ki.dwFlags&2:focus()
        if send(1,c.byref(event),c.sizeof(Input))!=1:raise OSError('Warehouse amount input incomplete')
    press_scan_sequence(key,[0x1d,0x1e])  # Select the existing amount through Ctrl+A.
    for ch in str(amount):
        for flags in (4,6):key(Input(type=1,data=InputUnion(ki=KeyboardInput(0,ord(ch),flags,0,0))))


def transfer(trade,direction,amount):
    if direction not in ('deposit','withdraw') or type(amount) is not int or not 1<=amount<=999999999:
        raise ValueError('Invalid warehouse transfer')
    npc=trade.vendor(0);reader=WarehouseMoneyReader(trade.observer.adapter)
    before=trade.inventory.read();bank=reader.read();points=money_points(reader,bank)
    available=before.silver if direction=='deposit' else bank.silver
    if amount>available:raise ValueError('Insufficient money for warehouse transfer')
    if trade.vendor(0)!=npc:raise ValueError('Warehouseman changed before transfer')
    trade.input_attempted=True
    trade.click(points['amount']);type_amount(trade.observer.operations.target,amount)
    typed=trade.verified_read(reader.read,lambda b:b.amount==str(amount),
        'Warehouse amount entry was not verified; transfer not submitted')
    current=trade.inventory.read()
    if (current.items!=before.items or current.equipped_ammo!=before.equipped_ammo
            or current.silver!=before.silver or typed.silver!=bank.silver
            or money_points(reader,typed)!=points or trade.vendor(0)!=npc):
        raise ValueError('Warehouse state changed before money submission')
    trade.click(points[direction])
    after,stored=trade.verified_read(lambda:(trade.inventory.read(),reader.read()),
        lambda pair:money_received(direction,amount,before,bank,*pair),
        'Warehouse money transfer unverified; no repeat input issued',timeout=5)
    return {'direction':direction,'amount':amount,'silver':after.silver,'stored_silver':stored.silver,'verified':True}
