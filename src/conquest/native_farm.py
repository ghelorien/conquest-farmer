"""Run the existing foreground combat loop with the hosted client's life reader."""
from conquest.character_context import state_path
from contextlib import contextmanager
from dataclasses import asdict,replace
import ctypes
import time

from conquest.capture import CaptureUnavailable
from conquest.memory_life import read_life
from conquest.viewport import size_for,clear_scene
from conquest.valuables import SPECIAL_LOOT_TYPES


@contextmanager
def logical_coordinates():
    setter=ctypes.windll.user32.SetThreadDpiAwarenessContext
    setter.argtypes, setter.restype=[ctypes.c_void_p],ctypes.c_void_p
    previous=setter(ctypes.c_void_p(-1))
    if not previous:
        raise ctypes.WinError()
    try:
        yield
    finally:
        setter(previous)


class NativeFarmSupervisor:
    def __init__(self,observer,control,recovery,notify):
        self.observer,self.control,self.recovery,self.notify=observer,control,recovery,notify
        self.recovery.delegate_return=True
        self.last_state=None
        self.revision=control.snapshot()['revision']
        from conquest.experience import ExperienceRate
        self.experience_rate=ExperienceRate()
        self.last_metrics=0
        self.last_health_position=None
        self.defend_until=0
        self.last_damage_at=-float("inf")
        self.escape_damage_consumed_at=-float("inf")
        self.escape_context={}
        self.defending=False
        self.position=None
        self.map_id=1002
        self.excluded_targets={}
        self.last_target=None
        self.scene_monsters=()
        self.chase_monsters=()
        self.escape_monsters=()
        self.targets_observation_available=False
        self.scene_timestamp=0
        self.ground=None
        self.pending_loot=None
        self.loot_cooldowns={}
        self.loot_wait_until=0
        self.pickups=0
        self.last_loot_error=None
        self.discarder=None
        self.movement_obstructions={}
        self.movement_run_until=0
        self.recent_movement_progress=[]
        self.patrol_chase=None
        self.recovery_death_seen=False

    def observe_inventory(self, inventory):
        """Record new valuable item identities independently of ground-read races."""
        from conquest.memory_ground import wanted_drop
        items={i.uid:i for i in inventory.items}
        known=getattr(self,'inventory_seen',None)
        if known is None:
            self.inventory_seen=set(items)
            self.inventory_reported=set()
            return
        new=set(items)-known
        known.update(items)
        for uid in sorted(new):
            item=items[uid]
            if not wanted_drop(item):continue
            self.inventory_reported.add(uid)
            self.pickups+=1
            pending=self.pending_loot
            linked=(pending and pending[0].type_id==item.type_id
                    and pending[0].plus==getattr(item,'plus',None))
            self.notify('memory_pickup_verified',{
                'uid':pending[0].uid if linked else uid,'inventory_uid':uid,
                'type_id':item.type_id,'plus':getattr(item,'plus',None),
                'silver':False,'increase':1,'total':self.pickups,
                'position':pending[0].position if linked else None,
                'map_id':self.map_id,'timestamp':time.time(),
                'source':'ground_pickup' if linked else 'inventory_gain',
                'timestamp_kind':'observed_at'})

    def urgent_banking(self,inventory):
        from conquest.banking import urgent_valuables
        return bool(urgent_valuables(inventory.items))

    def start_runback(self,destination):
        from conquest.runback_monitor import RunbackMonitor
        if getattr(self,'runback_watch',None):self.runback_watch.finish('replanned')
        self.runback_watch=RunbackMonitor(destination,self.map_id,'hunt',self.notify)

    def finish_runback(self,result):
        if getattr(self,'runback_watch',None):
            self.runback_watch.finish(result);self.runback_watch=None

    def reload_arrows(self,inventory,ammo_type):
        matches=[i for i in inventory.items if i.type_id==ammo_type and i.amount>0]
        if not matches:raise CaptureUnavailable('No reserve stack of the selected arrows')
        item=max(matches,key=lambda i:i.amount)
        def reload():
            # Inventory geometry is read in the embedded client's logical
            # coordinates; the combat thread otherwise uses physical pixels.
            with logical_coordinates():
                trade=self.observer.town_trade
                try:return trade({'action':'equip-arrows','uid':item.uid})
                except ValueError as error:
                    if str(error)=='Inventory opening unverified':
                        # This fails before the equipment click. Reobserve the
                        # bag and life next tick rather than stopping the route.
                        raise CaptureUnavailable('Arrow reload: waiting for Inventory to open') from error
                    raise  # An uncertain equipment receipt must not be replayed.
                finally:trade({'action':'close','window':'Inventory'})
        return self.dispatch(reload)

    def heal_potion(self,uid):
        def consume():
            from conquest.town_trade import TownObservationUnavailable
            with logical_coordinates():
                trade=self.observer.town_trade
                self.supply_panel_pending=True
                try:
                    try:return trade({'action':'consume-healing','uid':uid})
                    except TownObservationUnavailable as error:
                        raise CaptureUnavailable('Healing: reobserving before item use: '+str(error)) from error
                finally:
                    # Cleanup is reversible and retried separately. Never mask
                    # a verified receipt or an uncertain consumption error.
                    try:
                        trade({'action':'close','window':'Inventory'})
                        self.supply_panel_pending=False
                    except (ValueError,OSError):pass
        return self.dispatch(consume)

    def attack_strategy(self):
        from conquest.attack_strategy import AttackStrategy,equipment_context
        from conquest.equipment import read_equipment
        from conquest.combat_ranges import read_combat_ranges
        if not hasattr(self,'_attack_strategy'):
            self._attack_strategy=AttackStrategy(state_path('.runtime/attack-strategy.json'),self.notify)
            self._next_strategy_check=0
        if time.monotonic()>=self._next_strategy_check:
            self._next_strategy_check=time.monotonic()+5
            try:
                with self.observer.lock:
                    state=read_equipment(self.observer)
                    ranges=read_combat_ranges(self.observer)
                self._attack_strategy.set_context(equipment_context(state,ranges['scatter']))
            except (ValueError,OSError):
                pass  # A failed gear read must not reset an established decision.
        return self._attack_strategy

    def player_anchor(self,position):
        from conquest.scene_input import memory_player_anchor
        with self.observer.lock:
            life=self.read_life()
            if tuple(life.position)!=tuple(position):
                raise CaptureUnavailable('Player moved before projection')
            try:return memory_player_anchor(self.observer,life)
            except ValueError as error:raise CaptureUnavailable(str(error)) from error

    def xp_step(self,dispatch):
        from conquest.xp_skill import XpSkill
        if not hasattr(self,'_xp_skill'):self._xp_skill=XpSkill(self.observer,self.notify)
        with self.observer.lock:
            return self._xp_skill.step(dispatch)

    def scatter_selection_step(self,dispatch):
        from conquest.scatter_selection import ScatterSelection
        if not hasattr(self,'_scatter_selection'):
            self._scatter_selection=ScatterSelection(self.observer,self.notify)
        with self.observer.lock:
            try:return self._scatter_selection.step(dispatch)
            except ValueError as error:raise CaptureUnavailable(str(error)) from error

    def read_life(self):
        from conquest.reconnect import login_screen
        hwnd=getattr(self.observer.operations.target,'hwnd',None)
        if hwnd is not None and login_screen(hwnd):
            raise CaptureUnavailable('Disconnected; waiting for automatic reconnection')
        try:
            return read_life(self.observer.adapter,self.observer.health_layout,self.observer.character)
        except ValueError as error:
            if str(error) in ('Life state changed during observation',
                              'Player pointer changed during life observation',
                              'Life observation expired',
                              'Health fields or pointer topology changed during sampling'):
                raise CaptureUnavailable(str(error)) from error
            raise

    def observe(self):
        from conquest.mouse_priority import require_idle
        require_idle()
        with logical_coordinates(),self.observer.lock:
            life=self.read_life()
            if hasattr(life,'position'):
                self.position=tuple(life.position)
                self.map_id=life.map_id
                previous=self.last_health_position
                if previous and life.current_hp<previous[0] and not life.dead_candidate:
                    self.last_damage_at=time.monotonic()
                if previous and previous[1]==self.position and life.current_hp<previous[0] and not life.dead_candidate:
                    self.defend_until=time.monotonic()+8
                self.last_health_position=(life.current_hp,self.position)
            self.defending=not life.dead_candidate and time.monotonic()<self.defend_until
            if time.monotonic()-self.last_metrics>=2:
                from conquest.experience import read_experience
                try:
                    xp=read_experience(self.observer.adapter,life.object_address)
                    self.notify('experience_sample',{'level':xp.level,'experience_candidate':xp.current,
                        'experience_required':xp.required,
                        'percent_candidate':xp.percent,'xp_per_hour_candidate':self.experience_rate.add(xp),
                        'validated':False})
                except (AttributeError,ValueError,OSError):
                    pass  # XP telemetry must not delay combat or death recovery.
                self.last_metrics=time.monotonic()
            window=self.observer.operations.target.snapshot()
            intent=self.control.snapshot()
            if intent['revision']!=self.revision:
                return {'stop':True,'waiting':True,'health_ratio':life.current_hp/life.max_hp}
            focused=window['foreground']==window['root_hwnd'] and not window['minimized']
            recovery_events=[]
            if (life.dead_candidate or life.ghost_candidate) and not self.recovery_death_seen:
                self.recovery_death_seen=True
                recovery_events.append({'event':'death_detected','position':list(life.position),
                    'map_id':life.map_id,'health_ratio':life.current_hp/life.max_hp,'source':'native_memory'})
            status=self.recovery.step({**asdict(life),'dead_candidate':life.dead_candidate},focused)
            phase=(getattr(self.recovery,'episode',None) or {}).get('phase')
            if (self.recovery_death_seen and not life.dead_candidate and not life.ghost_candidate
                    and phase in ('returning_with_farmer','returning_after_revive','completed')):
                # RouteRecovery has confirmed revival across fresh life samples.
                self.recovery_death_seen=False
                recovery_events.append({'event':'revival_verified','position':list(life.position),
                    'map_id':life.map_id,'health_ratio':life.current_hp/life.max_hp,'source':'native_memory'})
            waiting=not intent['enabled'] or not focused or life.dead_candidate or bool(status)
            if not waiting and getattr(self,'supply_panel_pending',False):
                try:
                    self.dispatch(lambda:self.observer.town_trade({'action':'close','window':'Inventory'}))
                    self.supply_panel_pending=False
                except (ValueError,OSError) as error:
                    raise CaptureUnavailable('Waiting to close healing inventory: '+str(error)) from error
            if getattr(self,'runback_watch',None):
                self.runback_watch.observe({**asdict(life),'dead_candidate':life.dead_candidate},paused=waiting)
            if waiting:
                self.pending_loot=None
                self.loot_wait_until=0
            note=(status['note'] if status else 'Waiting for game focus' if not focused else
                  'Farming is off' if not intent['enabled'] else 'Hunting')
            state=status['state'] if status else 'paused' if waiting else 'farming'
            self.control.publish(intent['revision'],state,note)
            if (state,note)!=self.last_state:
                self.notify('farm_state',{'state':state,'note':note})
                self.last_state=(state,note)
            result={'waiting':waiting,'health_ratio':life.current_hp/life.max_hp}
            if recovery_events:result['recovery_events']=recovery_events
            if (getattr(self.recovery,'episode',None) or {}).get('phase')=='returning_with_farmer':
                result['returning_after_revive']=True
            if self.defending:
                result['defending']=True
            return result

    def match_targets(self,targets,*,refresh=False):
        with self.observer.lock:
            try:
                monsters=self.observer.entities.read().monsters
            except ValueError:
                return []
            intent=self.control.snapshot()
            matched=[]
            for target in targets:
                from conquest.routes import boss_name
                candidates=[m for m in monsters if not boss_name(m.name) and m.name==target.name
                    and (target.entity_id is None or (m.entity_id==target.entity_id and m.object_address==target.object_address))
                    and (m.entity_id in intent['target_ids'] or m.type_id in intent['target_type_ids']
                         or (self.defending and self.position is not None
                             and max(abs(a-b) for a,b in zip(m.position,self.position))<=3))
                    and ((refresh and target.entity_id is not None and target.object_address is not None
                          and target.world_position is not None
                          and max(abs(a-b) for a,b in zip(m.position,target.world_position))<=2)
                         or (not refresh and abs(m.draw_position[0]-target.x)<=30 and abs(m.draw_position[1]-target.y)<=35))]
                if len(candidates)==1:
                    from conquest.monster_health import read_monster_health
                    try:
                        hp=read_monster_health(self.observer.adapter,self.observer.entities.layout,candidates[0])
                        if hp<=0:
                            continue
                    except (ValueError,OSError):
                        continue
                    monster=candidates[0]
                    matched.append(replace(target,entity_id=monster.entity_id,object_address=monster.object_address,
                        **({'x':monster.draw_position[0],'y':monster.draw_position[1],
                            'world_position':tuple(monster.position),'current_hp':hp} if refresh else {})))
            return matched

    def memory_targets(self, size=(1036,793)):
        """Selected scene IDs and draw coordinates; no pixel observations.

        Scene presence does not prove life. Attack progress and the player's
        kill counter are checked separately; no per-ID kill claim is inferred.
        """
        from conquest.vision import Target
        with self.observer.lock:
            self.chase_monsters=()
            self.escape_monsters=()
            self.scatter_scene_targets=()
            self.targets_observation_available=False
            try:
                monsters=self.observer.entities.read().monsters
            except ValueError:
                return []
            self.targets_observation_available=True
            self.scene_monsters=monsters
            self.scene_timestamp=time.monotonic()
            intent=self.control.snapshot()
            accepted=[];scatter_scene=[]
            chase=[]
            escape=[]
            for monster in monsters:
                selected=(monster.entity_id in intent['target_ids'] or monster.type_id in intent['target_type_ids'])
                close=(self.defending and self.position is not None
                       and max(abs(a-b) for a,b in zip(monster.position,self.position))<=3)
                adjacent=(self.position is not None and max(abs(a-b) for a,b in zip(monster.position,self.position))<=1)
                threat=(self.position is not None and
                        max(abs(a-b) for a,b in zip(monster.position,self.position))<=12)
                if not (selected or close or adjacent or threat) or monster.alive is False or monster.current_hp == 0:
                    continue
                key=(monster.entity_id,monster.object_address)
                from conquest.monster_health import read_monster_health
                try:
                    monster_hp=read_monster_health(self.observer.adapter,self.observer.entities.layout,monster)
                    if monster_hp<=0:
                        continue
                except (ValueError,OSError):
                    self.targets_observation_available=False
                    continue
                # Retain the qualified HP used for this target decision. Region
                # occupancy must not see the original scene record's unknown
                # HP and mistake offscreen but living targets for an empty area.
                monster=replace(monster,current_hp=monster_hp)
                escape.append(monster)
                from conquest.routes import boss_name
                if boss_name(monster.name) or not (selected or close) or self.excluded_targets.get(key,0)>time.monotonic():
                    continue
                chase.append(monster)
                x,y=map(int,monster.draw_position)
                scatter_scene.append(Target(monster.name,x,y,1.0,monster.entity_id,monster.object_address,tuple(monster.position),monster_hp))
                if not (80<x<size[0]-80 and 140<y<size[1]-126):
                    continue
                if not clear_scene((x,y),size):
                    continue
                accepted.append(Target(monster.name,x,y,1.0,monster.entity_id,monster.object_address,tuple(monster.position),monster_hp))
            self.chase_monsters=tuple(chase)
            self.escape_monsters=tuple(escape)
            self.scatter_scene_targets=tuple(scatter_scene)
            return accepted

    def ground_items(self):
        from conquest.memory_ground import MemoryGroundReader
        if self.ground is None:
            self.ground=MemoryGroundReader(self.observer.entities)
        return self.ground.read()

    @property
    def discard_panel_pending(self):
        return bool(self.discarder and getattr(self.discarder,'cleanup_pending',False))

    def discard_step(self,inventory):
        from conquest.discard_loot import DiscardLoot, discard_candidate
        if self.discard_panel_pending:
            def close():
                with logical_coordinates():
                    try:self.discarder.close_inventory()
                    except (ValueError,OSError) as error:
                        # Keep the panel cleanup pending across fresh memory,
                        # focus and revival checks; never terminate the route.
                        raise CaptureUnavailable('Waiting to close Inventory: '+str(error)) from error
            self.dispatch(close)
            return True
        # Finish confirming the pickup before removing that inventory UID.
        if self.pending_loot or self.defending:
            return False
        # Optional housekeeping must not displace a live combat opportunity.
        # Unknown or stale scene memory is not evidence that opening the bag
        # is safe. Closing an already-open bag above remains unconditional.
        now=time.monotonic()
        if (not getattr(self,'targets_observation_available',False)
                or not 0<=now-getattr(self,'scene_timestamp',0)<=.5
                or getattr(self,'position',None) is None):
            return False
        if any(monster.alive is not False and monster.current_hp!=0
               and max(abs(a-b) for a,b in zip(monster.position,self.position))<=16
               for monster in self.scene_monsters):
            return False
        if not any(discard_candidate(i) for i in inventory.items):
            return False
        if self.discarder is None:
            self.discarder=DiscardLoot(self.observer.town_trade)
        if time.monotonic()<getattr(self.discarder,'next_attempt_at',0):
            return False
        candidate=next((i for i in inventory.items if discard_candidate(i)
                        and i.uid not in self.discarder.attempted),None)
        if candidate is None:
            return False
        self.notify('discarding_loot',{'uid':candidate.uid,'type_id':candidate.type_id,
                                      'activity':'Dropping unwanted +0 loot'})
        def discard():
            # Direct input runs on the combat thread rather than the HTTP
            # bridge; use the same logical coordinate context as the GUI reader.
            with logical_coordinates():
                return self.discarder.discard(candidate.uid)
        result=self.dispatch(discard)
        event={'verified':'loot_discarded','deferred':'loot_discard_deferred'}.get(
            result['state'],'loot_discard_unverified')
        self.notify(event,result)
        return True

    def ownership_guard(self):
        from conquest.loot_ownership import LootOwnership
        if not hasattr(self,'_loot_ownership'):
            self._loot_ownership=LootOwnership(self.observer.adapter)
        return self._loot_ownership

    def combat_loot_step(self,inventory,position,dispatch):
        # Valuable drops must not wait for a constantly respawning scene to
        # empty. The memory allowlist excludes currency; care runs first.
        return self.loot_step(inventory,position,dispatch,max_distance=12)

    def loot_step(self,inventory,position,dispatch,*,money_only=False,max_distance=12):
        from conquest.memory_ground import pickup_delta, wanted_drop
        now=time.monotonic()
        try:
            with self.observer.lock:
                ownership=self.ownership_guard()
                if self.pending_loot and ownership is not None and getattr(self,'pending_loot_feedback',None) is not None:
                    from conquest.loot_ownership import ownership_rejected
                    if ownership_rejected(self.pending_loot_feedback,ownership.snapshot()):
                        drop,_,_=self.pending_loot
                        ownership.reject(drop,self.map_id)
                        self.pending_loot=None;self.pending_loot_feedback=None
                        self.notify('memory_pickup_owned',{'uid':drop.uid,'type_id':drop.type_id,
                            'position':drop.position,'timestamp':time.time(),
                            'activity':"Skipping another player's loot"})
                        return False
                drops=self.ground_items()
        except (ValueError,OSError) as error:
            if str(error)!=self.last_loot_error:
                self.notify('memory_loot_retry',{'detail':str(error)})
                self.last_loot_error=str(error)
            if self.pending_loot and now-self.pending_loot[2]>=2:
                drop,_,_=self.pending_loot
                self.loot_cooldowns[(drop.uid,drop.object_address)]=now+60
                self.pending_loot=None
            return bool(self.pending_loot) or now<self.loot_wait_until
        if self.last_loot_error is not None:
            self.notify('memory_loot_ready',{})
        self.last_loot_error=None
        if self.pending_loot:
            drop,before,issued=self.pending_loot
            exists=drop in drops
            delta=pickup_delta(drop,before,inventory)
            if not exists and delta>0:
                previous={i.uid for i in before.items}
                gained=[i for i in inventory.items if i.uid not in previous and i.type_id==drop.type_id]
                already_reported=(gained and all(i.uid in getattr(self,'inventory_reported',set()) for i in gained))
                if not already_reported:
                    self.pickups+=1
                    fields={'uid':drop.uid,'type_id':drop.type_id,
                        'silver':drop.silver,'plus':drop.plus,'position':drop.position,
                        'map_id':self.map_id,'increase':delta,'total':self.pickups,'timestamp':time.time()}
                    if len(gained)==1:fields['inventory_uid']=gained[0].uid
                    self.notify('memory_pickup_verified',fields)
                self.pending_loot=None
                if money_only:return False
            elif now-issued<1.5:
                return True
            else:
                self.notify('memory_pickup_unverified',{'uid':drop.uid,'type_id':drop.type_id})
                self.loot_cooldowns[(drop.uid,drop.object_address)]=now+60
                self.pending_loot=None
        self.loot_cooldowns={k:v for k,v in self.loot_cooldowns.items() if v>now}
        candidates=[];approaches=[];anchor=None
        from conquest.discard_loot import ignored_drop, JOURNAL
        from conquest.discord_notify import read_json
        discarder=getattr(self,'discarder',None)
        ignored=(discarder.records if discarder is not None else read_json(JOURNAL,[]))
        for drop in drops:
            if (money_only and not drop.silver) or not wanted_drop(drop):
                continue
            if ownership is not None and ownership.blocked(drop,self.map_id):
                continue
            if ignored_drop(drop,getattr(self,'map_id',1002),ignored):
                continue
            if (drop.uid,drop.object_address) in self.loot_cooldowns:
                continue
            if not drop.silver and len(inventory.items)>=inventory.capacity:
                continue
            dx,dy=drop.position[0]-position[0],drop.position[1]-position[1]
            distance=max(abs(dx),abs(dy))
            if distance>40:
                continue
            viewport=size_for(self.observer)
            if anchor is None:anchor=self.player_anchor(position)
            point=(anchor[0]+(dx-dy)*32,anchor[1]+(dx+dy)*16)
            rank=(drop.type_id not in SPECIAL_LOOT_TYPES,not bool(drop.plus),dx*dx+dy*dy)
            if distance>max_distance or not clear_scene(point,viewport):
                approaches.append((rank,drop))
                continue
            candidates.append((rank,drop,point))
        if candidates or approaches:
            summary=[{'uid':drop.uid,'type_id':drop.type_id,'plus':drop.plus,'position':drop.position,
                      'action':'approach'} for _,drop in approaches]
            summary += [{'uid':drop.uid,'type_id':drop.type_id,'plus':drop.plus,'position':drop.position,
                         'action':'pickup'} for _,drop,_ in candidates]
            if summary!=getattr(self,'last_valuable_summary',None):
                self.last_valuable_summary=summary
                self.notify('memory_loot_observed',{'valuable_drops':summary,'timestamp':time.time()})
        if approaches and (not candidates or min(row[0] for row in approaches)<min(row[0] for row in candidates)):
            for _,drop in sorted(approaches,key=lambda row:row[0]):
                if self.approach_loot(drop,position,dispatch):return True
        if candidates:
            _,drop,point=min(candidates,key=lambda row:row[0])
            try:
                with self.observer.lock:
                    baseline=ownership.snapshot() if ownership is not None else None
            except (ValueError,OSError) as error:
                self.loot_cooldowns[(drop.uid,drop.object_address)]=time.monotonic()+2
                self.notify('memory_loot_retry',{'detail':'System pickup feedback unavailable: '+str(error)})
                return False
            try:
                dispatch(point,drop=drop)
            except CaptureUnavailable as error:
                if str(error) not in ('Ground item changed before pickup','Ground scene changed during sampling'):
                    raise
                # No click was sent: give combat a turn instead of repeatedly
                # selecting the same changing ground record on every frame.
                self.loot_cooldowns[(drop.uid,drop.object_address)]=time.monotonic()+1
                self.notify('memory_pickup_deferred',{'uid':drop.uid,'detail':str(error)})
                return False
            self.pending_loot=(drop,inventory,time.monotonic())
            self.pending_loot_feedback=baseline
            self.notify('memory_pickup_attempt',{'uid':drop.uid,'type_id':drop.type_id,'position':drop.position,
                'silver':drop.silver,'point':point})
            return True
        return now<self.loot_wait_until

    def approach_loot(self,drop,position,dispatch):
        """Reposition toward a freshly observed valuable instead of skipping it."""
        now=time.monotonic()
        if now<getattr(self,'loot_approach_ready',0):return True
        terrain=getattr(self.recovery,'terrain',None)
        if terrain is None or not hasattr(terrain,'path'):return False
        from conquest.navigation import native_waypoint
        boundary=getattr(self,'loot_boundary',(0,0,terrain.width-1,terrain.height-1))
        try:
            path=terrain.path(position,drop.position)
            if len(path)<2 or len(path)>100:return False
            if any(not(boundary[0]<=x<=boundary[2] and boundary[1]<=y<=boundary[3]) for x,y in path):return False
            destination=native_waypoint(path,viewport=size_for(self.observer))
            dx,dy=destination[0]-position[0],destination[1]-position[1]
            viewport=size_for(self.observer)
            anchor=self.player_anchor(position)
            from conquest.scene_input import visible_route_delta
            from conquest.viewport import scene_bounds
            delta=visible_route_delta((dx,dy),anchor,scene_bounds(viewport))
            if delta is None:return False
            dx,dy=delta;destination=(position[0]+dx,position[1]+dy)
            point=(anchor[0]+(dx-dy)*32,anchor[1]+(dx+dy)*16)
            # Planning never authorizes a stale identity or stale player tile.
            with self.observer.lock:
                if drop not in self.ground_items():return False
                if tuple(self.read_life().position)!=tuple(position):return False
            dispatch(point,control=max(abs(dx),abs(dy))>=8)
            self.loot_approach_ready=time.monotonic()+.6
            self.notify('memory_pickup_approach',{'uid':drop.uid,'type_id':drop.type_id,
                'position':drop.position,'destination':destination,'timestamp':time.time(),
                'activity':'Moving closer to valuable loot'})
            return True
        except ValueError as error:
            self.notify('memory_loot_retry',{'detail':'Valuable approach: '+str(error)})
            return False

    def movement_failed(self,position,destination):
        # A failed landing is dynamic evidence, not a permanent terrain edit.
        # Avoid its first step as well so A* chooses another departure direction.
        now=time.monotonic()
        dx,dy=destination[0]-position[0],destination[1]-position[1]
        first=(position[0]+(1 if dx>0 else -1 if dx<0 else 0),
               position[1]+(1 if dy>0 else -1 if dy<0 else 0))
        for point in (first,tuple(destination)):
            if point!=tuple(position):
                self.movement_obstructions[(self.map_id,point)]=now+30
        self.movement_run_until=now+6
        self.notify('movement_recovery',{'position':list(position),'blocked_landing':list(destination),
            'activity':'Blocked movement; taking another path'})

    def movement_succeeded(self,source,destination,*,arrived):
        # One verified detour clears slow walking mode; keep the failed tiles
        # excluded so returning to long jumps cannot replay the blocked edge.
        if max(abs(a-b) for a,b in zip(source,destination))>=3:
            self.movement_run_until=0
            if arrived:
                now=time.monotonic()
                self.recent_movement_progress=[entry for entry in self.recent_movement_progress if now-entry[0]<=12]
                self.recent_movement_progress.append((now,self.map_id,tuple(source),tuple(destination)))

    def patrol_step(self,position,fallback,boundary,*,chase=True,alternatives=()):
        self.patrol_destination=None
        from conquest.navigation import native_waypoint
        terrain=self.recovery.terrain
        intent=self.control.snapshot()
        x0,y0,x1,y1=boundary
        now=time.monotonic()
        self.movement_obstructions={key:until for key,until in self.movement_obstructions.items() if until>now}
        avoid={point for (map_id,point) in self.movement_obstructions
               if map_id==self.map_id and point!=tuple(position)}
        candidates=[]
        fresh={}
        if chase and now-self.scene_timestamp<=.5:
            for monster in self.chase_monsters:
                key=(monster.entity_id,monster.object_address)
                from conquest.routes import boss_name
                if boss_name(monster.name) or monster.alive is False or monster.current_hp==0:continue
                if (monster.entity_id not in intent['target_ids'] and monster.type_id not in intent['target_type_ids']):continue
                if self.excluded_targets.get(key,0)>now:continue
                mx,my=monster.position
                if x0<=mx<=x1 and y0<=my<=y1 and max(abs(mx-position[0]),abs(my-position[1]))>1:
                    fresh[key]=tuple(monster.position)
            candidates=sorted(fresh.values(),key=lambda p:abs(p[0]-position[0])+abs(p[1]-position[1]))
        lease=getattr(self,'patrol_chase',None)
        if chase and lease:
            key,point,until=lease
            if key in fresh:
                point=fresh[key]
            if (self.excluded_targets.get(key,0)<=now and (key in fresh or until>now)
                    and x0<=point[0]<=x1 and y0<=point[1]<=y1
                    and max(abs(a-b) for a,b in zip(point,position))>3):
                # A briefly unavailable target read must not reverse travel back
                # to the patrol point. This is scouting a last-seen location;
                # attack dispatch still requires fresh identity and positive HP.
                candidates.insert(0,point)
            else:
                self.patrol_chase=None
        if not chase:self.patrol_chase=None
        # An obstructed approach waypoint must still permit a local detour.
        escapes=[(position[0]+dx,position[1]+dy) for dx,dy in ((4,0),(0,4),(-4,0),(0,-4))] if avoid else []
        for destination in dict.fromkeys([*candidates[:4],tuple(fallback),*map(tuple,alternatives),*escapes]):
            if destination==tuple(position):
                continue
            if not (x0<=destination[0]<=x1 and y0<=destination[1]<=y1):
                continue
            try:
                # Long inter-area travel can exceed the small local patrol budget.
                # Reuse a checked path only while fresh memory stays on it and the
                # destination, terrain, boundary and temporary obstructions agree.
                key=(id(terrain),self.map_id,destination,tuple(boundary),frozenset(avoid))
                cached=getattr(self,'travel_path_cache',None) if not chase else None
                if cached and cached[0]==key and tuple(position) in cached[1]:
                    path=cached[1][cached[1].index(tuple(position)):]
                else:
                    planner=getattr(terrain,'travel_path',getattr(terrain,'straight_path',terrain.path)) if not chase else terrain.path
                    path=planner(position,destination,limit=250000 if not chase else 10000,
                                      **({'avoid':avoid} if avoid else {}))
                if not chase:
                    self.travel_path_cache=(key,path)
                if any(not(x0<=px<=x1 and y0<=py<=y1) for px,py in path):
                    continue
                chosen=next((key for key,point in fresh.items() if point==destination),None)
                if chosen is not None:
                    self.patrol_chase=(chosen,destination,now+2)
                    stand_off=getattr(self,'scatter_standoff',0)
                    if stand_off:
                        # Approach until the group is inside Scatter reach;
                        # do not jump onto the target and trigger an escape.
                        monster=next(m for m in self.chase_monsters
                                     if (m.entity_id,m.object_address)==chosen)
                        def aim_visible(p):
                            # Being in world range is insufficient when the target
                            # is outside the clear input area. Do not creep one tile
                            # at a time toward a target that remains obscured.
                            dx,dy=p[0]-position[0],p[1]-position[1]
                            px=monster.draw_position[0]-(dx-dy)*32
                            py=monster.draw_position[1]-(dx+dy)*16
                            return clear_scene((px,py),size_for(self.observer))
                        stop=next((i for i,p in enumerate(path) if i>0 and
                            max(abs(a-b) for a,b in zip(p,destination))<=stand_off
                            and aim_visible(p)),len(path)-1)
                        path=path[:stop+1]
                if not chase and hasattr(terrain,'travel_path'):
                    from conquest.navigation import travel_waypoint
                    step=travel_waypoint(terrain,path,4 if now<self.movement_run_until else 12,avoid=avoid,viewport=size_for(self.observer))
                else:step=native_waypoint(path,4 if now<self.movement_run_until else 12,viewport=size_for(self.observer))
                repeats=sum(now-stamp<=12 and map_id==self.map_id and source==tuple(position)
                            and landing==tuple(step)
                            for stamp,map_id,source,landing in self.recent_movement_progress)
                if repeats>=2:
                    # Reaching a landing is insufficient if fresh memory keeps
                    # returning to the same source before the next identical move.
                    # Use the existing bounded detour; do not guess whether this
                    # was server correction, auto-chasing, or a dynamic obstacle.
                    self.movement_failed(position,step)
                    self.recent_movement_progress=[]
                    self.notify('movement_reversed',{'position':list(position),'landing':list(step),
                        'repetitions':repeats,'activity':'Repeated return to the same tile; taking another path'})
                    raise CaptureUnavailable('Patrol progress reversed; taking another path')
                if destination not in candidates:
                    self.patrol_destination=destination
                return step
            except CaptureUnavailable:
                raise
            except ValueError:
                continue
        raise CaptureUnavailable('Waiting for a traversable patrol step')

    def ranged_escape(self,position,boundary):
        """A bounded clear jump away from memory-verified nearby living monsters."""
        from conquest.navigation import native_movement_delta
        now=time.monotonic()
        if now<getattr(self,'escape_ready_at',0) or now-self.scene_timestamp>.5:
            return None
        living=[m.position for m in self.escape_monsters]
        adjacent=sum(max(abs(a-b) for a,b in zip(p,position))<=1 for p in living)
        damaged=(now-self.last_damage_at<=1.25 and self.last_damage_at>self.escape_damage_consumed_at)
        if adjacent<2 and not damaged:
            return None
        threats=[p for p in living if max(abs(a-b) for a,b in zip(p,position))<=(12 if damaged else 1)]
        if not threats:
            return None
        x,y=position;left,top,right,bottom=boundary
        terrain=self.recovery.terrain
        candidates=[]
        for length in (12,10,8):
            for dx,dy in ((length,0),(-length,0),(0,length),(0,-length)):
                dx,dy=native_movement_delta(dx,dy,viewport=size_for(self.observer))
                distance=max(abs(dx),abs(dy))
                if distance<8:continue
                point=(x+dx,y+dy)
                if not(left<=point[0]<=right and top<=point[1]<=bottom):continue
                sx,sy=dx//distance,dy//distance
                if not all(terrain.walkable((x+sx*i,y+sy*i)) for i in range(distance+1)):continue
                separation=min(max(abs(point[0]-mx),abs(point[1]-my)) for mx,my in threats)
                if separation<6:continue
                distances=[max(abs(point[0]-mx),abs(point[1]-my)) for mx,my in living]
                nearby=sum(d<=4 for d in distances)
                if nearby>=len(threats):continue
                # Prefer fewer nearby enemies, including those outside the
                # original surround, then more clearance and longer jumps.
                candidates.append((-nearby,min(distances),separation,distance,point))
        if not candidates:return None
        self.escape_context={'adjacent_enemies':adjacent,'recent_damage':damaged,
                             'reason':'recent_damage' if damaged else 'enemies_within_one_tile'}
        return max(candidates)[-1]

    def finish_target(self, reason):
        self.patrol_chase=None
        if reason=='kill_counter_increased':
            self.loot_wait_until=time.monotonic()+.45
        # Scatter can kill a different monster from the one used to aim.
        # A player-counter increase never proves that the aimed target died.
        if self.last_target is not None and reason!='kill_counter_increased':
            target=self.last_target
            self.excluded_targets[(target.entity_id,target.object_address)]=time.monotonic()+8
            self.notify('target_cooldown',{'entity_id':target.entity_id,'reason':reason,'seconds':8})
        self.last_target=None
        now=time.monotonic()
        self.excluded_targets={key:expiry for key,expiry in self.excluded_targets.items() if expiry>now}

    def dispatch(self,callback,*,target=None,drop=None,expected_position=None,retarget=None,attack_range=None):
        with self.observer.lock,self.control.lock:
            with logical_coordinates():
                life=self.read_life()
            if (not self.control.enabled or self.control.revision!=self.revision
                    or life.dead_candidate or life.ghost_candidate):
                raise CaptureUnavailable('Farming paused or character died before input')
            if expected_position is not None and tuple(life.position)!=tuple(expected_position):
                raise CaptureUnavailable('Player moved before movement input; reobserving')
            if target is not None:
                matched=self.match_targets([target],refresh=True) if retarget else self.match_targets([target])
                if not matched:
                    raise CaptureUnavailable('Selected monster moved before input')
                if retarget:
                    target=matched[0]
                    if (not isinstance(attack_range,(int,float)) or not 0<attack_range<=20
                            or max(abs(a-b) for a,b in zip(life.position,target.world_position))>attack_range
                            or not clear_scene((target.x,target.y),size_for(self.observer))):
                        raise CaptureUnavailable('Refreshed monster aim is outside attack range or clear scene')
                    retarget(target)
            if drop is not None:
                try:
                    if drop not in self.ground_items():
                        raise CaptureUnavailable('Ground item changed before pickup')
                except ValueError as error:
                    raise CaptureUnavailable(str(error)) from error
            result=callback()
            if target is not None:
                self.last_target=target
            return result
