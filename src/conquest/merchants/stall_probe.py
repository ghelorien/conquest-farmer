"""One bounded flag interaction for live setup qualification, no confirmation."""
import struct
import time
import json
from conquest.memory_life import read_life
from conquest.merchants.memory import unpack
from conquest.merchants.stalls import vacant_flags,owned_booth
from conquest.merchants.booth_target import owned_booth_target, CONTROL


def reconcile_interrupted_probe(driver,journal):
    """Clear a view-only probe only when no dialog, booth or resource change remains."""
    character=driver.observer.character;record=journal.get(character,'stall_probe',{})
    if record.get('phase')!='submitted':return record
    from conquest.conductress import read_dialog
    snapshot=driver.memory.read()
    if snapshot.get('booth_open') and snapshot.get('own_booth_uid'):
        return reconcile_manual_setup(driver,journal,record,snapshot)
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


def reconcile_manual_setup(driver,journal,record,snapshot):
    """A recorded manual stall setup supersedes an older view-only flag probe."""
    character=driver.observer.character
    flag=record.get('flag',{})
    if (record.get('operation') not in (None,'inspect_vacant_flag')
            or flag.get('name')!='ShopFlag' or (flag.get('type_id'),flag.get('model'))!=(0,1086)
            or snapshot['identity']!=record['identity'] or snapshot['map_id']!=1036
            or snapshot.get('trade') or snapshot.get('request')
            or journal.pending(character)
            or any(w['name']=='Add Item to Booth' for w in snapshot.get('windows',[]))):
        raise ValueError('Stall interaction still needs live reconciliation')
    with journal.db() as db:
        row=db.execute("SELECT id,timestamp,payload FROM events WHERE character=? AND "
                       "event='manual_stall_setup_adopted' AND timestamp>? ORDER BY id DESC LIMIT 1",
                       (character,record['submitted_at'])).fetchone()
    if row is None or json.loads(row['payload']).get('own_booth_uid')!=snapshot['own_booth_uid']:
        raise ValueError('No recorded manual setup supersedes this stall interaction')
    booth=owned_booth(driver.observer,snapshot)
    if booth['position']!=[flag['position'][0]+3,flag['position'][1]]:
        raise ValueError('The manually adopted booth belongs to another flag')
    from conquest.conductress import read_dialog
    try:read_dialog(driver.observer)
    except ValueError as error:
        if str(error)!='NPC dialog is absent':raise
    else:raise ValueError('Stall dialog remains open; no repeat interaction')
    from conquest.merchants.qualification import stock
    fresh=driver.memory.read()
    if (stock(fresh)!=stock(snapshot) or not fresh.get('booth_open')
            or fresh.get('trade') or fresh.get('request')
            or any(w['name']=='Add Item to Booth' for w in fresh.get('windows',[]))
            or any(fresh[k]!=snapshot[k] for k in ('identity','map_id','position','own_booth_uid'))
            or owned_booth(driver.observer,fresh)!=booth):
        raise ValueError('Manually adopted booth changed during reconciliation')
    record.update(phase='reconciled_manual_setup',reconciled_at=time.time(),
                  adopted_event_id=row['id'],adopted_at=row['timestamp'],own_booth_uid=booth['uid'],
                  original_inventory_result_verified=False,manual_setup_superseded_probe=True,
                  input_qualified=False)
    journal.set(character,'stall_probe',record)
    journal.event(character,'stall_probe_superseded_by_manual_setup',evidence=record)
    return record


def inspect_flag(driver,travel,journal,check,*,flag_uid=None):
    character=driver.observer.character;s=driver.observer.adapter
    previous=journal.get(character,'stall_probe',{})
    if previous.get('phase')=='submitted':
        return confirm_observed_flag(driver,travel,journal,previous,check)
    check();before=driver.memory.read()
    panel_close=None
    if before.get('booth_open') and before.get('own_booth_uid'):
        from conquest.merchants.booth_panel_probe import close_owned_panel
        driver.require_qualified('stall_occupancy')
        owned_booth_target(driver.observer,owned_booth(driver.observer,before))
        panel_close=close_owned_panel(driver,travel,journal,check)
        before=driver.memory.read()
        from conquest.merchants.qualification import stock
        if stock(before)!=stock(panel_close['before']):
            raise ValueError('Booth stock changed between panel close and reopen')
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
    if flag_uid is not None:
        flags=[f for f in flags if f['uid']==flag_uid]
    if len(flags)!=1:raise ValueError('Stand beside exactly one memory-verified unattended stall flag')
    flag=flags[0]
    from conquest.merchants.flag_target import flag_target,CONTROL as FLAG_CONTROL
    target=owned_booth_target(driver.observer,flag) if reopening else flag_target(driver.observer,flag)
    point=target['point']
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
        check();life=read_life(s,driver.observer.health_layout,character)
        if life.map_id!=1036 or list(life.position)!=before['position'] or life.dead_candidate:
            raise ValueError('Merchant moved before stall inspection')
        if driver.require_qualified('stall_occupancy')!=profile:
            raise ValueError('Stall occupancy qualification changed before inspection')
        match=(owned_booth(driver.observer,driver.memory.read()) if reopening else
               next((f for f in vacant_flags(driver.observer,spec) if f['uid']==flag['uid']),None))
        if match!=flag:raise ValueError('Stall was occupied or changed before inspection')
        if reopening and owned_booth_target(driver.observer,match)!=target:
            raise ValueError('Owned booth tile target changed before inspection')
        if not reopening and flag_target(driver.observer,match)!=target:
            raise ValueError('Vacant flag collision target changed before inspection')
        inv=driver.memory.inventory.read()
        if inv.items!=inventory.items or inv.silver!=inventory.silver:
            raise ValueError('Inventory changed before stall inspection')
        state=driver.memory.read()
        uncovered(state)
        if state['booth_open'] or state.get('trade') or state.get('request'):
            raise ValueError('Another interaction interrupted stall inspection')
    def prepare_press():
        nonlocal press_pending
        unchanged()
        from conquest.scene_pointer import wait_scene_pointer
        wait_scene_pointer(s,point,unchanged)
        press_pending=True
        record['press_pending']=True
        journal.set(character,'stall_probe',record)
    record={'phase':'submitted','submitted_at':time.time(),'flag':flag,
            'operation':'open_owned_panel' if reopening else 'inspect_vacant_flag',
            'control':dict(CONTROL if reopening else FLAG_CONTROL),
            'target':target,
            'panel_close':panel_close,
            'own_booth_uid_before':before.get('own_booth_uid',0),
            'identity':before['identity'],'position':before['position'],
            'silver':inventory.silver,'inventory_uids':[i.uid for i in inventory.items],
            'inventory_before':before['inventory'],'booth_before':before['booth'],
            'press_pending':False}
    journal.set(character,'stall_probe',record)
    try:travel.click(point,check,before_press=prepare_press)
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
        if not reopening and any(w['name']=='Open Booth###Confirm' for w in driver.memory.gui.windows()):
            return confirm_observed_flag(driver,travel,journal,record,check)
        if raw[12] or records or (own and own!=before.get('own_booth_uid',0)):
            if raw[12] and (not own or struct.unpack_from('<I',raw,0x4c)[0]!=own):
                raise ValueError('Stall response opened a foreign booth; no qualification')
            if not reopening and own:
                claimed=owned_booth(driver.observer,driver.memory.read())
                if claimed['position']!=[flag['position'][0]+3,flag['position'][1]]:
                    raise ValueError('Claimed booth belongs to another flag')
            record.update(phase='observed',observed_at=time.time(),position=list(life.position),
                          own_booth_uid=own,displayed_booth_uid=struct.unpack_from('<I',raw,0x4c)[0],
                          booth_open=bool(raw[12]),dialog=records,inventory_unchanged=True,
                          confirmation_submitted=False)
            journal.set(character,'stall_probe',record)
            return record
        time.sleep(.1)
    raise ValueError('No qualified stall response; do not repeat without reconciliation')


def confirm_observed_flag(driver,travel,journal,record,check):
    from conquest.merchants.booth_confirmation import submit,CONTROL as CONFIRM_CONTROL
    if record.get('operation')!='inspect_vacant_flag':
        raise ValueError('Previous stall interaction needs reconciliation before another click')
    check();before=driver.memory.read()
    if (any(before[k]!=record[k] for k in ('identity','silver'))
            or before['inventory']!=record.get('inventory_before') or before['booth']!=record.get('booth_before')):
        raise ValueError('Stall confirmation differs from journaled flag interaction')
    if record.get('confirmation_submitted'):
        if before['map_id']!=1036 or not before.get('own_booth_uid'):
            raise ValueError('Submitted booth confirmation needs reconciliation; no repeated confirmation')
        booth=owned_booth(driver.observer,before)
        if booth['position']!=[record['flag']['position'][0]+3,record['flag']['position'][1]]:
            raise ValueError('Claimed booth belongs to another flag')
        after=before
    else:
        if before['position']!=record['position']:
            raise ValueError('Merchant moved before booth confirmation')
        def submitted():
            record.update(confirmation_submitted=True,confirmation_at=time.time(),confirmation_control=dict(CONFIRM_CONTROL))
            journal.set(driver.observer.character,'stall_probe',record)
        after=submit(driver,travel,before,record['flag'],check,submitted)
    record.update(phase='observed',observed_at=time.time(),position=after['position'],
                  own_booth_uid=after['own_booth_uid'],displayed_booth_uid=after['own_booth_uid'] if after['booth_open'] else 0,
                  booth_open=after['booth_open'],inventory_unchanged=True,dialog=[],native_confirmation=True,
                  claim_verified=True)
    journal.set(driver.observer.character,'stall_probe',record)
    return record
