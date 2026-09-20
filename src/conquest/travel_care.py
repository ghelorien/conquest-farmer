"""Memory-driven healing and revival while a standalone route is moving."""
from conquest.character_context import farmer_name
from pathlib import Path
import time
import yaml

from conquest.addressing import WorkerPointerSession,PlayerLayout,resolve_player
from conquest.memory_inventory import InventoryLayout,MemoryInventoryReader
from conquest.worker import request


class TravelStateChanged(ValueError):
    pass


class PanelTravelChanged(TravelStateChanged):
    code='panel_intercepted'
    def __init__(self,panel):
        self.panel=panel
        super().__init__('Closed a shop panel; rechecking the route')


class TravelCare:
    def __init__(self,worker_info,notify=lambda event:None):
        self.info,self.notify=worker_info,notify
        self.layout=PlayerLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-player-candidate.yaml').read_text()))
        from conquest.memory_health import HealthWorkerSession
        self.session=HealthWorkerSession(worker_info,self.layout.expected_sha256)
        self.inventory=MemoryInventoryReader(self.session,self.layout,InventoryLayout.model_validate(
            yaml.safe_load(Path('profiles/classic-1074-inventory-candidate.yaml').read_text())))
        self.health_layout=yaml.safe_load(Path('profiles/classic-1074-health-candidate.yaml').read_text())
        self.pending=None
        self.last_heal=-float('inf')
        self.last_revive=-float('inf')

    def check(self,health):
        if health['embedded_controls'].get('manual_mouse'):
            raise TravelStateChanged('Mouse control is yours; waiting for idle')
        life=health['embedded_controls'].get('life')
        if life is None:
            return
        if health['embedded_controls']['control']['enabled']:
            raise ValueError('Travel care cannot share input with farming')
        now=time.monotonic()
        if life['dead_candidate']:
            self.pending=None
            if life['revive_ready_candidate'] and now-self.last_revive>=2:
                try:
                    request(self.info,'revive-click',{'health_profile':self.health_layout,'character':farmer_name(),
                        'expected_size':health.get('window',{}).get('client_size',[1036,793]),'expires_at':time.time()+4,'input_mode':'foreground'})
                except ValueError as error:
                    if (str(error)=='Recovery waiting for game focus; no input sent'
                            or str(error).startswith('Mouse control is yours')):
                        raise TravelStateChanged(str(error)) from error
                    raise
                self.last_revive=now
                self.notify({'event':'travel_revive','death_position':life['position']})
            raise TravelStateChanged('Waiting for living route position')
        if now>=getattr(self,'next_panel_check',0):
            if getattr(self,'panel_close_uncertain',False):
                raise ValueError('Town panel close remains uncertain; reconcile before route input')
            self.next_panel_check=now+1
            try:
                result=request(self.info,'town',{'action':'clear-travel-panels','expires_at':time.time()+4})
            except ValueError as error:
                from conquest.panel_events import panel_close_unverified
                if not panel_close_unverified(error):raise
                # The close may have been submitted. Do not issue the same
                # request again without a read-only panel-instance receipt.
                self.panel_close_uncertain=True
                raise TravelStateChanged('Rechecking an unconfirmed town panel close') from error
            self.panel_close_uncertain=False
            if result.get('closed_panel'):
                self.notify({'event':'travel_panel_closed','panel':result['closed_panel'],
                             'activity':'Closed '+result['closed_panel']+' panel; continuing travel'})
                raise PanelTravelChanged(result['closed_panel'])
        if self.pending:
            before,hp,issued=self.pending
            after=self.inventory.read()
            if after.count(1000020)<before and life['current_hp']>hp:
                self.notify({'event':'travel_heal_verified','hp':life['current_hp'],'potions':after.count(1000020)})
                self.pending=None
            elif now-issued<2:
                return
            else:
                # A consumed potion can be masked by incoming damage. Do not
                # strand the character; continue escape and recheck after cooldown.
                self.notify({'event':'travel_heal_unconfirmed','hp':life['current_hp'],
                    'consumed':after.count(1000020)<before,
                    'activity':'Healing result unclear; continuing toward safety'})
                self.pending=None
                self.last_heal=now
                return
        if life['current_hp']>=life['max_hp']*.75 or now-self.last_heal<1:
            self.xp_step(health)
            return
        inventory=self.inventory.read()
        if inventory.count(1000020)<=0:
            if not getattr(self,'empty_healing_reported',False):
                self.empty_healing_reported=True
                self.notify({'event':'travel_healing_empty','activity':'No potions left; continuing toward town'})
            self.xp_step(health)
            return  # Keep escaping toward supplies; stopping cannot restore health.
        self.empty_healing_reported=False
        potion=next(i for i in inventory.items if i.type_id==1000020 and i.amount>0)
        try:
            receipt=request(self.info,'town',{'action':'consume-healing','uid':potion.uid,'expires_at':time.time()+4})
        except ValueError as error:
            from conquest.town_trade import TownObservationUnavailable
            if isinstance(error,TownObservationUnavailable) or str(error) in ('Game lost focus; no key sent', 'Game did not receive focus; no key sent'):
                raise TravelStateChanged('Regaining focus before travel healing') from error
            if (str(error)=='Healing consumption unverified; no repeat input issued'
                    and self.inventory.read().count(1000020)==inventory.count(1000020)-1):
                self.last_heal=now
                self.notify({'event':'travel_heal_unconfirmed','consumed':True,
                             'activity':'Potion consumed; continuing toward safety while checking HP'})
                return
            raise
        finally:
            import sys
            failed=sys.exc_info()[0] is not None
            try:request(self.info,'town',{'action':'close','window':'Inventory','expires_at':time.time()+4})
            except (ValueError,OSError):
                if not failed:raise
        self.last_heal=now
        if receipt['consumed']:
            self.notify({'event':'travel_heal_verified','hp':receipt['hp_after'],'potions':receipt['remaining']})

    def xp_step(self,health):
        # Normal travel uses the same memory-qualified popup as combat.
        # Avoid a remote GUI scan until the ready/flying status is present.
        life=health['embedded_controls']['life']
        status=life.get('status',0)
        verifying=bool(status&0x8000000 and getattr(getattr(self,'_xp_skill',None),'pending',None))
        if not status&0x10 and not verifying:return
        from types import SimpleNamespace
        from conquest.xp_skill import XpSkill
        from conquest.memory_health import HealthWorkerSession,HealthLayout
        if not hasattr(self,'_xp_skill'):
            observer=SimpleNamespace(adapter=HealthWorkerSession(self.info,self.layout.expected_sha256),
                health_layout=HealthLayout.model_validate(self.health_layout),character=farmer_name())
            self._xp_skill=XpSkill(observer,lambda event,fields:self.notify({'event':event,**fields}))
        def click(point):
            fresh=request(self.info,'health')['embedded_controls']
            current=fresh.get('life')
            if (fresh['control']['enabled'] or fresh.get('manual_mouse') or not current
                    or current['dead_candidate'] or current['object_address']!=life['object_address']):
                raise TravelStateChanged('Travel XP input state changed')
            addresses=resolve_player(self.session,self.layout)
            request(self.info,'foreground-click',{'point':list(point),'button':'left','control':False,
                'expected_size':health.get('window',{}).get('client_size',[1036,793]),'require_foreground':True,'expires_at':time.time()+4,
                'guard':{'name_address':hex(addresses['name']),'name':farmer_name(),
                         'hp_address':hex(addresses['max_hp']),'max_hp':current['max_hp']}})
        from conquest.merchants.coordination import InputAcquisitionBusy
        try:
            activated=self._xp_skill.step(click)
        except InputAcquisitionBusy as error:
            # The coordinator denied entry before any XP input. Abandon this
            # care pass, not just the click: the route must recheck Stop/client
            # identity/life and plan again, then reread XP and its popup point.
            # Generic focus/OS/SendInput errors can be uncertain; never catch
            # them here or retry the current dispatch with its old evidence.
            raise TravelStateChanged('XP input busy; reobserve before continuing travel') from error
        if activated:raise TravelStateChanged('XP full; activating Fly before continuing travel')
