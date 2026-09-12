"""One bounded flag interaction for live setup qualification, no confirmation."""
import struct
import time
from conquest.memory_life import read_life
from conquest.merchants.memory import unpack
from conquest.merchants.stalls import vacant_flags,owned_booth


def reconcile_interrupted_probe(driver,journal):
    """Clear a view-only probe only when no dialog, booth or resource change remains."""
    character=driver.observer.character;record=journal.get(character,'stall_probe',{})
    if record.get('phase')!='submitted':return record
    from conquest.conductress import read_dialog
    snapshot=driver.memory.read()
    if (snapshot['identity']!=record['identity'] or snapshot['map_id']!=1036
            or snapshot['booth_open'] or snapshot.get('trade') or snapshot.get('request')):
        raise ValueError('Stall interaction still needs live reconciliation')
    try:read_dialog(driver.observer)
    except ValueError as error:
        if str(error)!='NPC dialog is absent':raise
    else:raise ValueError('Stall dialog remains open; no repeat interaction')
    if (sorted(i['uid'] for i in snapshot['inventory'])!=sorted(record['inventory_uids'])
            or snapshot['silver']!=record['silver']):
        raise ValueError('Stall interaction inventory or silver differs')
    exact='inventory_before' in record
    if exact and (snapshot['inventory']!=record['inventory_before'] or snapshot['booth']!=record['booth_before']):
        raise ValueError('Stall interaction item attributes differ')
    record.update(phase='reconciled_cancelled',reconciled_at=time.time(),position_after=snapshot['position'],
                  no_open_interaction=True,inventory_uids_and_silver_unchanged=True,
                  exact_attributes_verified=exact,input_qualified=False)
    journal.set(character,'stall_probe',record)
    journal.event(character,'stall_probe_reconciled',evidence=record)
    return record


def inspect_flag(driver,travel,journal,check):
    character=driver.observer.character;s=driver.observer.adapter
    previous=journal.get(character,'stall_probe',{})
    if previous.get('phase')=='submitted':
        raise ValueError('Previous stall interaction needs reconciliation before another click')
    check();before=driver.memory.read()
    if before['map_id']!=1036 or before['booth_open'] or before.get('trade') or before.get('request'):
        raise ValueError('Stall inspection requires Market, a closed booth and no trade')
    from conquest.conductress import read_dialog
    try:read_dialog(driver.observer)
    except ValueError as error:
        if str(error)!='NPC dialog is absent':raise
    else:raise ValueError('Close the existing NPC dialog before inspecting a stall')
    # The flag name and proximity do not establish that nobody owns this stall.
    # Qualification of the vacancy field must precede even a diagnostic click.
    profile=driver.require_qualified('stall_occupancy')
    spec=profile.get('shop_setup',{})
    reopening=bool(before.get('own_booth_uid'))
    flags=[owned_booth(driver.observer,before)] if reopening else [f for f in vacant_flags(driver.observer,spec)
           if max(abs(a-b) for a,b in zip(f['position'],before['position']))<=3]
    if len(flags)!=1:raise ValueError('Stand beside exactly one memory-verified unattended stall flag')
    flag=flags[0];point=(flag['draw_position'][0],flag['draw_position'][1]-32)
    width,height=driver.memory.gui.viewport_size()
    if not (80<point[0]<width-80 and 170<point[1]<height-160):
        raise ValueError('Stall flag is outside the qualified scene')
    def uncovered(state):
        for window in state['windows']:
            x,y,w,h=window['geometry']
            if w>=width-10 and h>=height-10:continue
            if x<=point[0]<=x+w and y<=point[1]<=y+h:
                raise ValueError('A GUI panel covers the stall flag')
    uncovered(before)
    inventory=driver.memory.inventory.read();press_pending=False
    def unchanged():
        nonlocal press_pending
        check();life=read_life(s,driver.observer.health_layout,character)
        if life.map_id!=1036 or list(life.position)!=before['position'] or life.dead_candidate:
            raise ValueError('Merchant moved before stall inspection')
        if driver.require_qualified('stall_occupancy')!=profile:
            raise ValueError('Stall occupancy qualification changed before inspection')
        match=(owned_booth(driver.observer,driver.memory.read()) if reopening else
               next((f for f in vacant_flags(driver.observer,spec) if f['uid']==flag['uid']),None))
        if match!=flag:raise ValueError('Stall was occupied or changed before inspection')
        inv=driver.memory.inventory.read()
        if inv.items!=inventory.items or inv.silver!=inventory.silver:
            raise ValueError('Inventory changed before stall inspection')
        state=driver.memory.read()
        uncovered(state)
        if state['booth_open'] or state.get('trade') or state.get('request'):
            raise ValueError('Another interaction interrupted stall inspection')
        press_pending=True
        record['press_pending']=True
        journal.set(character,'stall_probe',record)
    record={'phase':'submitted','submitted_at':time.time(),'flag':flag,
            'operation':'open_owned_panel' if reopening else 'inspect_vacant_flag',
            'own_booth_uid_before':before.get('own_booth_uid',0),
            'identity':before['identity'],'position':before['position'],
            'silver':inventory.silver,'inventory_uids':[i.uid for i in inventory.items],
            'inventory_before':before['inventory'],'booth_before':before['booth'],
            'press_pending':False}
    journal.set(character,'stall_probe',record)
    try:travel.click(point,check,before_press=unchanged)
    except Exception:
        if not press_pending:
            record.update(phase='cancelled_before_press',cancelled_at=time.time(),retry_safe=True)
            journal.set(character,'stall_probe',record)
        raise
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        check();life=read_life(s,driver.observer.health_layout,character)
        inv=driver.memory.inventory.read()
        if (life.map_id!=1036 or life.dead_candidate or inv.items!=inventory.items
                or inv.silver!=inventory.silver):
            raise ValueError('Stall response needs inventory or position reconciliation')
        model=driver.memory.gui.model(25,0x5c27f8);raw=s.read_block(model,0x58)
        own=unpack(s,life.object_address+0x3258,'<I')[0]
        try:records=read_dialog(driver.observer)['records']
        except ValueError as error:
            if str(error)!='NPC dialog is absent':raise
            records=[]
        if raw[12] or records or (own and own!=before.get('own_booth_uid',0)):
            if raw[12] and (not own or struct.unpack_from('<I',raw,0x4c)[0]!=own):
                raise ValueError('Stall response opened a foreign booth; no qualification')
            record.update(phase='observed',observed_at=time.time(),position=list(life.position),
                          own_booth_uid=own,displayed_booth_uid=struct.unpack_from('<I',raw,0x4c)[0],
                          booth_open=bool(raw[12]),dialog=records,inventory_unchanged=True,
                          confirmation_submitted=False)
            journal.set(character,'stall_probe',record)
            return record
        time.sleep(.1)
    raise ValueError('No qualified stall response; do not repeat without reconciliation')
