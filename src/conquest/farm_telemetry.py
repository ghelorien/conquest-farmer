"""Persistent confirmed pickup history and human-readable route activity."""
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import time

ITEM_NAMES = {1000020:'Painkiller',1050000:'LuckyArrow',1000000:'Stancher',1000010:'Resolutive',
              1001000:'Agrypnotic',1088000:'DragonBall',1088001:'Meteor'}
from conquest.valuables import DRAGONBALL_NAMES
ITEM_NAMES.update(DRAGONBALL_NAMES)
# Local client definitions supply display names; live memory supplies identity.
try:
    definitions=json.loads(Path(r'C:\Program Files\Classic Conquer 2.0\ini\itemtype.json').read_text(encoding='utf-8'))
    ITEM_NAMES.update({item['id']:item['name'] for item in definitions})
except (OSError,ValueError,KeyError,TypeError):
    pass


def item_label(fields):
    if fields.get('silver'):
        return 'Silver'
    kind=fields['type_id']
    name=ITEM_NAMES.get(kind,f"Item {kind}")
    if 100000<=kind<600000:
        quality={6:'Refined',7:'Unique',8:'Elite',9:'Super'}.get(kind%10)
        if quality:name=f'{quality} {name}'
        plus=fields.get('plus')
        if type(plus) is int and plus>0:name+=f' +{plus}'
    return name


class PickupHistory:
    def __init__(self,path,limit=200):
        self.path=Path(path)
        self.rows=deque(maxlen=limit)
        self.inventory_ids=set()
        if self.path.exists():
            with self.path.open(encoding='utf-8') as source:
                for line in source:
                    try:
                        row=json.loads(line)
                        if row['increase']>0 and isinstance(row['timestamp'],(int,float)):
                            self.rows.append(row)
                            if row.get('inventory_uid'):self.inventory_ids.add((row['inventory_uid'],row['type_id']))
                    except (ValueError,KeyError,TypeError):
                        continue

    def add(self,fields):
        if fields.get('increase',0)<=0:
            raise ValueError('Pickup history requires a verified positive increase')
        key=(fields.get('inventory_uid'),fields.get('type_id'))
        if key[0] and key in self.inventory_ids:return None
        row={k:fields[k] for k in ('uid','type_id','silver','increase')}
        row.update({k:fields[k] for k in ('plus','position','map_id','inventory_uid','source','timestamp_kind') if k in fields})
        row['timestamp']=fields.get('timestamp',time.time())
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a',encoding='utf-8') as out:
            out.write(json.dumps(row)+'\n')
        self.rows.append(row)
        if key[0]:self.inventory_ids.add(key)
        return row


def pickup_values(row):
    return (datetime.fromtimestamp(row['timestamp']).astimezone().strftime('%m/%d %H:%M:%S'),
            item_label(row)+(' (recovered; time unknown)' if row.get('source')=='recovered_inventory' else ''),f"+{row['increase']}" if row.get('silver') else f"×{row['increase']}")


def activity_text(route,app,control,life,*,now=None):
    now=time.time() if now is None else now
    if life and life.get('dead_candidate'):
        return 'Dead — reviving, then returning to the farm route'
    if app.get('state')=='Reconnect needs attention':
        return 'Reconnect needs attention; use Retry reconnect after resolving the login error'
    if app.get('state')=='Reconnecting':
        status=(app.get('reconnection') or {}).get('state')
        if status=='waiting_for_login_input':return 'Reconnect waiting for client focus or mouse release'
        return 'Disconnected; retrying login to Conquer'
    phase=route.get('phase')
    fresh=0<=now-route.get('updated_at',0)<12
    if app.get('reload_preparing') or (phase=='reloading' and fresh):
        return 'Moving to a safe spot for app reload'
    if phase=='merchant_handoff' and fresh:
        return route.get('activity','Safe merchant refill · up to 15 seconds')
    if phase=='needs_attention' and not control.get('enabled'):
        return 'Route stopped: '+route.get('detail','Needs attention')
    if phase in ('restocking','changing_route','recovering_route','visiting_town'):
        if not fresh:
            return 'Waiting for a current route update'
        message=route.get('activity','Restocking supplies in town')
        watch=route.get('runback',{})
        if watch.get('result')=='travelling' and 0<=now-watch.get('updated_at',0)<4:
            message+=f" · {watch['elapsed_seconds']:.0f}s · {watch['stalls']} stalls · lowest HP {watch['minimum_hp_percent']:.0f}%"
        return message
    if not control.get('enabled'):
        return 'Farming is off'
    watch=app.get('runback',{})
    if watch.get('result')=='travelling' and 0<=now-watch.get('updated_at',0)<4:
        return f"Returning to farm · {watch['elapsed_seconds']:.0f}s · {watch['stalls']} stalls · lowest HP {watch['minimum_hp_percent']:.0f}%"
    if control.get('execution_state') not in (None,'farming','off'):
        return control.get('note') or app.get('state','Waiting for the client')
    if app.get('navigation_blocked'):
        return 'Navigation blocked — looking for another patrol path'
    if now-app.get('activity_at',0)<3 and app.get('activity'):
        return app['activity']
    state=app.get('state')
    return {'Farming':'Attacking selected monsters','Patrolling':'Patrolling for monsters',
            'Travelling to hunting area':'Heading back to the hunting area',
            'Paused â€” waiting for Conquer':'Waiting for Conquer to regain focus'}.get(state,state or 'Hunting')



def farm_stats(app,enabled,*,now=None):
    import math
    now=time.time() if now is None else now
    xp=app.get('experience') or {}
    disconnected=app.get('state') in ('Reconnecting','Reconnect needs attention','Waiting for Conquer')
    level=xp.get('level') if not disconnected else None
    rate=app.get('kills_per_hour') if enabled else 0
    first=f"Kills {app.get('kills',0):,}"
    if isinstance(rate,(int,float)) and math.isfinite(rate):first+=f"  ·  {rate:,.0f}/h"
    eta='Estimating…'
    if disconnected:eta='—'
    elif not enabled:eta='Paused'
    elif 0<=now-app.get('experience_observed_at',0)<=15:
        current=xp.get('experience_candidate');required=xp.get('experience_required')
        speed=xp.get('xp_per_hour_candidate')
        if (all(isinstance(v,(int,float)) and math.isfinite(v) for v in (current,required,speed))
                and 0<=current<required and speed>0):
            seconds=3600*(required-current)/speed
            minutes=math.ceil(seconds/60)
            eta='~<1 min' if seconds<60 else f'~{minutes//60}h {minutes%60:02d}m' if minutes>=60 else f'~{minutes} min'
    return first+f"\nLevel {level if level is not None else '—'}  ·  Next level {eta}"


def pause_message(reason):
    if not reason:
        return 'Paused with F11'
    if 'traversable patrol' in reason:
        return 'Navigation blocked — looking for another patrol path'
    if any(word in reason.lower() for word in ('focus','foreground','minimized')):
        return 'Waiting for Conquer to regain focus'
    return 'Waiting: '+reason
