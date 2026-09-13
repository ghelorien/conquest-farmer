"""Memory-derived controls qualified by a reconciled native Trade exchange."""
from conquest.merchants.delivery_trade_controls import trade_grid,cell
from conquest.merchants.delivery_confirm_controls import confirm_control
from conquest.merchants.delivery_accept_probe import control as request_control

MODES=('native_trade_drop','native_trade_confirm','native_trade_request')


def locate(driver,snapshot,mode):
    if mode=='native_trade_request':
        w,point=request_control(driver,snapshot)
        return w,point,'Accept',None
    if mode=='native_trade_confirm':
        w,point,seed=confirm_control(driver.memory.gui,snapshot)
        return w,point,'Accept Trade',[seed]
    if mode=='native_trade_drop':
        trade=snapshot.get('trade')
        if not trade or trade['accepted'] or trade['other_accepted']:
            raise ValueError('Trade is absent or already confirmed')
        w,table=trade_grid(driver.memory.gui,snapshot)
        count=len(trade['own_items'])
        if count>=20:raise ValueError('Trade is full')
        return w,cell(table,count),None,None
    raise ValueError('Unknown native trade control')


def point(driver,snapshot,mode):
    _,point,_,_=locate(driver,snapshot,mode)
    gui=driver.memory.gui.viewport_size();size=driver.target.snapshot()['client_size']
    if not all(0<v<limit for v,limit in zip(point,gui)):
        raise ValueError('Native trade point is outside the client')
    return tuple(round(v*actual/logical) for v,actual,logical in zip(point,size,gui))


def hover(driver,snapshot,mode):
    w,_,label,seeds=locate(driver,snapshot,mode)
    if not label:raise ValueError('Native drop target is not a button')
    driver.memory.gui.assert_hovered(w,label,seeds=seeds)
