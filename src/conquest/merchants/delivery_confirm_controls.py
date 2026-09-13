"""Pinned confirmation controls for the native Trade window."""
from conquest.merchants.qualification import window
from conquest.merchants.delivery_trade_controls import nested_table


def confirm_control(gui,snapshot):
    for rva,code in ((0x10fa38,'488d0dd9b74b00'),(0x10fa48,'41c6869800000001'),(0x10fa7a,'e8311a0700')):
        if gui.session.read_block(gui.base+rva,len(bytes.fromhex(code)))!=bytes.fromhex(code):
            raise ValueError('Native trade confirmation handler changed')
    w=window(snapshot,'Trade##TradeWindow')
    outer=gui.table(w,'##TradeWindowGrid')
    bar=nested_table(gui,w,'##MyTradeBar',outer['id'])
    if len(outer['columns'])!=3 or len(bar['columns'])!=2 or bar['row_height']!=18:
        raise ValueError('Trade confirmation layout changed')
    l,t,r,b=bar['outer'];point=(round((l+r)/2),round(b+4+9))
    left,top,right,bottom=outer['clip']
    if not left<point[0]<right or not top<point[1]<bottom:
        raise ValueError('Trade confirmation is clipped')
    return w,point,outer['id']
