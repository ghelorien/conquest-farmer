"""Drop qualified unwanted carried items and remember their ground records."""
from dataclasses import asdict
from pathlib import Path
import time
import struct

from conquest.capture import CaptureUnavailable
from conquest.discord_notify import read_json, write_json
from conquest.foreground import foreground_drag, foreground_click
from conquest.memory_ground import MemoryGroundReader
from conquest.town_trade import sale_candidate
from conquest.viewport import size_for
from conquest.scene_input import memory_player_anchor

JOURNAL=Path('.runtime/discarded-loot.json')


def discard_candidate(item):
    # Keep equipped gear, +/unknown gear, rare-quality gear, valuables and
    # route supplies. sale_candidate also identifies unwanted weak consumables.
    get=item.get if isinstance(item,dict) else lambda k,d=None:getattr(item,k,d)
    return (get('slot') is not None and type(get('plus')) is int
            and get('plus')==0 and sale_candidate(item))


def ground_key(drop,map_id):
    return [map_id,drop.uid,drop.type_id,*drop.position,drop.spawn_tick]


def ignored_drop(drop,map_id,records):
    return any(row.get('ground_key')==ground_key(drop,map_id) for row in records)


def removal_received(item,before,after):
    identity=lambda i:(i.uid,i.type_id,i.amount,i.limit,i.plus)
    return (after.silver==before.silver and
            {identity(i) for i in after.items}=={identity(i) for i in before.items if i.uid!=item.uid})


def inventory_button(gui):
    """Items button: renderer 09a740, second column/top of ##Control's table."""
    s=gui.session
    window=gui.read('##Control')
    context=struct.unpack('<Q',s.read_block(gui.base+0x6966f0,8))[0]
    header=s.read_block(context+0x4338,16)
    count,capacity,table=struct.unpack('<IIQ',header)
    if not 0<count<=capacity<=128:
        raise ValueError('Invalid GUI table pool')
    matches=[]
    for i in range(count):
        address=table+i*536
        record=s.read_block(address,536)
        if struct.unpack_from('<II',record)==(0x02a99238,0x482010):matches.append((address,record))
    if len(matches)!=1:raise ValueError('Inventory button table is absent or ambiguous')
    address,_=matches[0]
    record=s.read_block(address,536)
    frame=struct.unpack('<I',s.read_block(context+0x3e38,4))[0]
    if not 0<=frame-struct.unpack_from('<I',record,0x70)[0]<=3 or struct.unpack_from('<I',record,0x74)[0]!=6:
        raise ValueError('Inventory button table is not current')
    left,top,right,bottom=struct.unpack_from('<4f',record,0xf0)
    columns=struct.unpack_from('<Q',record,0x18)[0]
    column=s.read_block(columns+104,104)
    x1,x2=struct.unpack_from('<2f',column,0x34)
    if (bottom-top!=40 or not left<=x1<x2<=right or x2-x1!=71
            or not window.position[1]<=top<bottom<=window.position[1]+window.size[1]):
        raise ValueError('Inventory button geometry differs from the qualified layout')
    fresh=s.read_block(address,536)
    if (s.read_block(context+0x4338,16)!=header or fresh[:0x20]!=record[:0x20]
            or fresh[0xf0:0x100]!=record[0xf0:0x100]
            or s.read_block(columns+104+0x34,8)!=column[0x34:0x3c]
            or gui.read('##Control')!=window):
        raise ValueError('Inventory button moved during observation')
    s.assert_identity()
    return round((x1+x2)/2),round(top+(bottom-top)/4)


class DiscardLoot:
    def __init__(self,trade):
        self.trade=trade
        self.observer=trade.observer
        self.ground=MemoryGroundReader(self.observer.entities)
        self.records=read_json(JOURNAL,[])
        self.attempted={row['uid'] for row in self.records}
        self.cleanup_pending=False
        self.next_attempt_at=0
        self.deferred_attempts=0

    def discard(self,uid):
        if uid in self.attempted:
            raise ValueError('Item was already attempted for discard')
        try:
            result=self._discard(uid)
            self.deferred_attempts=0
        except (ValueError,OSError,CaptureUnavailable) as error:
            row=next((r for r in self.records if r['uid']==uid),None)
            if row is None:
                # Optional cleanup must not end combat when the scene changes
                # before a drag. Retry observation later, with a global backoff
                # so multiple carried items cannot monopolize the combat loop.
                self.deferred_attempts=min(getattr(self,'deferred_attempts',0)+1,6)
                delay=min(300,10*2**(self.deferred_attempts-1))
                self.next_attempt_at=time.monotonic()+delay
                result={'uid':uid,'state':'deferred','detail':str(error),'retry_after':delay}
            else:
                # The drag may have succeeded. Quarantine this UID and preserve
                # uncertainty without terminating combat or sending another drag.
                row.update(state='unverified',detail=str(error))
                write_json(JOURNAL,self.records)
                result=dict(row)
        finally:
            if self.cleanup_pending:
                try:self.close_inventory()
                except (ValueError,OSError,CaptureUnavailable):
                    pass  # Native supervisor finishes cleanup after focus/revival.
        return result

    def close_inventory(self):
        t=self.trade
        try:window=t.shop.gui.read('Inventory')
        except ValueError as error:
            if 'not active' in str(error) or 'absent' in str(error):
                self.cleanup_pending=False
                return
            raise CaptureUnavailable(str(error)) from error
        # Closing a panel is safe at low HP; do not wait for the 80% discard
        # threshold while the open bag obstructs combat. Death is still guarded.
        t.life(0)
        point=(round(window.position[0]+window.size[0]-18),round(window.position[1]+18))
        foreground_click(self.observer.operations.target,*point,size_for(self.observer),require_foreground=True)
        def closed():
            try:t.shop.gui.read('Inventory')
            except ValueError as error:
                if 'not active' in str(error) or 'absent' in str(error):return True
                raise
            return False
        t.verified_read(closed,bool,'Inventory cleanup is not yet confirmed',timeout=1)
        self.cleanup_pending=False

    def _discard(self,uid):
        t=self.trade
        before=t.inventory.read()
        choices=[i for i in before.items if i.uid==uid and discard_candidate(i)]
        if len(choices)!=1 or uid in self.attempted:
            raise ValueError('Item is protected, absent, or already attempted for discard')
        item=choices[0]
        life=t.life(.8)
        # Town panels can receive dragged items; never accidentally sell/store.
        for name in ('Shop','Warehouse'):
            try:t.shop.gui.read(name)
            except ValueError as error:
                if 'not active' in str(error) or 'absent' in str(error):continue
                raise
            raise CaptureUnavailable('Close town panels before discarding loot')
        # Check the required ground reader before opening the bag. A failed
        # observation must not repeatedly flash Inventory during combat.
        ground_before=t.verified_read(self.ground.read,lambda value:True,
            "Ground observation unavailable before discard; no drag sent")
        self.cleanup_pending=True
        try:t.shop.gui.read('Inventory')
        except ValueError as error:
            if 'not active' not in str(error) and 'absent' not in str(error):raise
            t.click(inventory_button(t.shop.gui))
            t.verified_read(lambda:t.shop.gui.read('Inventory'),lambda value:True,
                            'Inventory opening was not verified; no discard issued')
        grid=t.shop.gui.read('Inventory/##ItemGrid_')
        if grid.size!=(407.,175.) or grid.scroll!=(0.,0.):
            raise ValueError('Inventory grid differs from calibrated discard layout')
        source=(round(grid.position[0]+20+40*(item.slot%10)),
                round(grid.position[1]+20+40*(item.slot//10)))
        # The same memory-qualified world projection used by movement/loot.
        viewport=size_for(self.observer)
        destination=memory_player_anchor(self.observer,life)
        window=t.shop.gui.read('Inventory')
        if (window.position[0]<=destination[0]<=window.position[0]+window.size[0]
                and window.position[1]<=destination[1]<=window.position[1]+window.size[1]):
            raise ValueError('Inventory overlaps the world discard target')
        ground_before=t.verified_read(self.ground.read,lambda value:True,
            "Ground observation unavailable before discard; no drag sent")
        fresh=t.inventory.read()
        latest=t.life(.8)
        if (fresh.items!=before.items or fresh.silver!=before.silver
                or latest.position!=life.position or latest.map_id!=life.map_id
                or t.shop.gui.read('Inventory/##ItemGrid_')!=grid
                or t.shop.gui.read('Inventory')!=window
                or size_for(self.observer)!=viewport
                or memory_player_anchor(self.observer,latest)!=destination):
            raise CaptureUnavailable('Inventory or player moved before discard; no drag sent')
        # Persist intent BEFORE input. Ambiguous results never trigger another
        # drag for this UID, even after an application restart.
        row={'uid':uid,'type_id':item.type_id,'plus':item.plus,'map_id':life.map_id,
             'position':list(life.position),'timestamp':time.time(),'state':'attempted'}
        self.records.append(row)
        write_json(JOURNAL,self.records)
        self.attempted.add(uid)
        foreground_drag(self.observer.operations.target,source,destination,viewport)
        before_keys={tuple(ground_key(d,life.map_id)) for d in ground_before}
        def receipt():
            bag=t.inventory.read()
            drops=self.ground.read()
            found=[d for d in drops if d.type_id==item.type_id
                   and max(abs(a-b) for a,b in zip(d.position,life.position))<=2
                   and tuple(ground_key(d,life.map_id)) not in before_keys]
            row.update(item_still_carried=any(i.uid==uid for i in bag.items),
                       inventory_removal_verified=removal_received(item,before,bag),
                       ground_candidates=[ground_key(d,life.map_id) for d in found])
            if len(found)==1:
                row['ground_key']=ground_key(found[0],life.map_id)
            return bag,found
        bag,found=t.verified_read(receipt,lambda pair:removal_received(item,before,pair[0])
                                  and len(pair[1])==1,
                                  'Discard not verified in bag and ground memory; no repeat drag issued',timeout=3)
        row.update(state='verified',ground_key=ground_key(found[0],life.map_id))
        write_json(JOURNAL,self.records)
        return dict(row)
