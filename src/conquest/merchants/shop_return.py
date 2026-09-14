"""Durable reconnect itinerary; never resume trading before returning to Market."""
import json
import time
import copy
import hashlib
from conquest.capture import CaptureUnavailable
from conquest.merchants.controller import identities


class ShopReturn:
    def __init__(self, character, journal, *, clock=time.time):
        self.character,self.journal,self.clock=character,journal,clock

    def state(self):
        return self.journal.get(self.character,'shop_return')

    def save(self, state, phase=None, **values):
        if phase:state['phase']=phase
        state.update(values,updated_at=self.clock())
        self.journal.set(self.character,'shop_return',state)

    def remember(self, snapshot):
        if snapshot.get('map_id')==1036 and snapshot.get('booth_open') and snapshot.get('position'):
            home={'map_id':1036,'position':snapshot['position']}
            if self.journal.get(self.character,'shop_home')!=home:
                self.journal.set(self.character,'shop_home',home)

    def begin(self):
        if self.state() and self.state()['phase'] not in ('complete','operator_overridden'):return
        # Sales observations already persist the final pre-disconnect inventory.
        with self.journal.db() as db:
            row=db.execute('SELECT snapshot FROM sales_baseline WHERE character=?',(self.character,)).fetchone()
        before=json.loads(row['snapshot']) if row else None
        state={'phase':'returning','started_at':self.clock(),'moves':0,'stalls':0,
               'before':before,'home':self.journal.get(self.character,'shop_home')}
        self.save(state)
        self.journal.event(self.character,'shop_return_started')

    def block(self, state, note):
        self.save(state,'needs_attention',note=note)
        self.journal.set(self.character,'attention',{'kind':'shop_return','note':note})
        raise ValueError(note)

    def step(self, snapshot, controller, travel):
        state=self.state()
        if not state or state['phase'] in ('complete','operator_overridden'):return True
        # Check this even after login/arrival so manual pause never becomes a resume.
        if not controller.active():
            raise CaptureUnavailable('Paused; recovery will not change manual intent')
        if state['phase']=='needs_attention':raise ValueError(state['note'])
        if snapshot.get('trade') or snapshot.get('request'):
            raise CaptureUnavailable('Return to Market waits for the open trade to be resolved')
        if state['moves']>=300 or state['stalls']>=6:
            self.block(state,'Return to Market is obstructed; route retry requires attention')
        world=snapshot['map_id']
        if world not in (1002,1036):
            self.block(state,'Unexpected map after reconnect; no unverified route will be used')
        # An uncertain paid transfer must never be issued a second time.
        if state['phase']=='transfer_submitted':
            if world==1036 and state['transfer_silver']-snapshot['silver']==100:
                self.save(state,'returning')
            elif self.clock()-state['submitted_at']<=10:
                return False
            else:
                self.block(state,'Conductress transfer has an uncertain result; no repeat fare was sent')
        if world==1002:
            if max(abs(a-b) for a,b in zip(snapshot['position'],(438,444)))>2:
                self.move(state,snapshot,controller,travel,(438,444));return False
            with controller.coordinator.lease(self.character):
                controller.check()
                fresh=travel.read()
                if fresh['identity']!=snapshot['identity'] or fresh['map_id']!=1002:
                    raise ValueError('Merchant changed before the Market transfer')
                # Preparation validates the live NPC, dialog and exact 100-silver fare.
                records=travel.prepare_transfer(fresh,controller.check)
                self.save(state,'transfer_submitted',transfer_silver=fresh['silver'],submitted_at=self.clock())
                travel.transfer(records,controller.check)
            return False
        # Closing the panel does not surrender a stall. Never seek another flag
        # while memory still identifies a booth owned by this merchant.
        if snapshot.get('own_booth_uid') and not snapshot['booth_open']:
            if state['phase']=='panel_submitted':
                if self.clock()-state['submitted_at']<=10:return False
                self.block(state,'Owned booth panel did not open; verify it before retrying')
            with controller.coordinator.lease(self.character):
                controller.check()
                travel.driver.require_qualified('booth_panel')
                self.save(state,'opening_panel',own_booth_uid=snapshot['own_booth_uid'])
                travel.open_owned_booth(snapshot,controller.check,before_press=lambda:
                    self.save(state,'panel_submitted',submitted_at=self.clock()))
            return False
        home=state.get('home')
        if not home and not snapshot.get('own_booth_uid'):
            self.block(state,'No memory-verified Market booth location was saved before disconnect')
        if not snapshot['booth_open']:
            if state['phase']=='shop_submitted':
                if self.clock()-state['submitted_at']<=10:return False
                self.block(state,'Shop setup has an uncertain result; verify the stall before retrying')
            chosen=travel.stall(home['position'])
            if max(abs(a-b) for a,b in zip(snapshot['position'],chosen['position']))>1:
                self.move(state,snapshot,controller,travel,tuple(chosen['position']));return False
            with controller.coordinator.lease(self.character):
                controller.check()
                prepared=travel.prepare_shop(chosen,controller.check)
                self.save(state,'shop_submitted',submitted_at=self.clock())
                travel.start_shop(prepared,controller.check)
            return False
        # Route first, stock accounting second: do not strand valuables in Twin City.
        # Reconciliation runs before this method; ambiguous transactions never move.
        before=state.get('before')
        if not before:
            self.block(state,'Pre-disconnect stock is unavailable; reconcile inventory before restoring listings')
        expected=identities(before['inventory']+before['booth'])
        current=identities(snapshot['inventory']+snapshot['booth'])
        if expected!=current:
            self.block(state,'Stock changed across disconnect; reconcile missing/new items before restoring the shop')
        if not any(w['name']=='Inventory' for w in snapshot.get('windows',[])):
            if state['phase']=='inventory_submitted':
                if self.clock()-state['submitted_at']<=10:return False
                self.block(state,'Inventory panel did not open; verify it before retrying')
            with controller.coordinator.lease(self.character):
                controller.check()
                travel.driver.require_qualified('inventory_panel')
                self.save(state,'opening_inventory')
                travel.open_inventory(snapshot,controller.check,before_press=lambda:
                    self.save(state,'inventory_submitted',submitted_at=self.clock()))
            return False
        self.save(state,'restoring_listings')
        wanted=sorted(before['booth'],key=lambda i:(-i['price'],i['uid']))
        for item in wanted:
            from conquest.valuables import storage_only
            if storage_only(item):continue
            actual=next(i for i in snapshot['booth']+snapshot['inventory'] if i['uid']==item['uid'])
            if actual.get('price')==item['price']:continue
            if actual.get('price') is None and len(snapshot['booth'])>=32:
                self.block(state,'Booth capacity changed during restoration; remaining valuables are queued')
            # Restore the last verified asking price; scans may reprice it later.
            plan={'uid':item['uid'],'name':item['name'],'price':item['price'],
                  'old_price':actual.get('price'),'observed_at':self.clock(),
                  'attributes':list(current[item['uid']]),'reason':'Restore verified pre-disconnect listing'}
            controller.apply_price(plan)
            return False
        trial=self.journal.get(self.character,'recovery_trial',{})
        from conquest.merchants.qualification import stock
        trial_before=trial.get('before')
        matching_trial=bool(trial.get('phase')=='recovering' and trial_before
            and trial_before.get('character_uid')==before.get('character_uid')
            and stock(trial_before)==stock(before))
        interventions=state.get('manual_interventions',[]) or (trial.get('manual_interventions',[]) if matching_trial else [])
        self.save(state,'complete',completed_at=self.clock(),automatic_recovery_verified=not bool(interventions),
                  completion_kind='assisted_recovery' if interventions else 'automatic_recovery',
                  manual_interventions=interventions)
        if matching_trial:
            trial.update(phase='assisted' if interventions else 'verified',verified_at=self.clock(),
                         automatic_recovery_verified=not bool(interventions),after=snapshot)
            self.journal.set(self.character,'recovery_trial',trial)
        self.journal.set(self.character,'new_stock',True)
        self.journal.event(self.character,'shop_return_verified',position=snapshot['position'],restored=len(wanted))
        return True

    def recheck(self, snapshot):
        """Return the current memory snapshot without changing the hold."""
        return {'observed_at':self.clock(),'snapshot':snapshot}

    def operator_override(self, snapshot, *, operator_confirmed=False,
                          confirmation_reference=None, operator=None, incident_digest=None):
        if operator_confirmed is not True:
            raise ValueError('Operator confirmation is required for this incident')
        if not isinstance(confirmation_reference,str) or not confirmation_reference.strip():
            raise ValueError('A non-empty incident confirmation reference is required')
        from conquest.merchants.journal import character_name
        character=character_name(self.character)
        with self.journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM state WHERE character=? AND name='shop_return'",(character,)).fetchone()
            state=json.loads(row[0]) if row else {}
            if state.get('phase')=='operator_overridden':
                if (state.get('operator_override') or {}).get('confirmation_reference')!=confirmation_reference.strip():
                    raise ValueError('Incident was already overridden with a different confirmation')
                return state
            if not state or state.get('phase')=='complete':
                raise ValueError('No unresolved shop recovery hold is active')
            original=copy.deepcopy(state)
            digest=hashlib.sha256(json.dumps(original,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if incident_digest is not None and incident_digest != digest:
                raise ValueError('Incident evidence changed; recheck before overriding')
            override={'operator_confirmed':True,'confirmation_reference':confirmation_reference.strip(),
                      'operator':operator,'confirmed_at':self.clock(),
                      'original_phase':state.get('phase'),'original_evidence_digest':digest,
                      'original_state':original,'fresh_evidence':{'snapshot':snapshot}}
            state.update(operator_override=override,phase='operator_overridden',
                         replan_required=True,updated_at=self.clock())
            encoded=json.dumps(state)
            if row:
                db.execute("UPDATE state SET value=? WHERE character=? AND name='shop_return'",(encoded,character))
            else:
                db.execute("INSERT INTO state(character,name,value) VALUES(?,?,?)",(character,'shop_return',encoded))
            # The old shop baseline may contain assets that are no longer
            # explainable after the disconnect. Mark current known inventory
            # for an independent ordinary scan.
            db.execute("INSERT OR REPLACE INTO state(character,name,value) VALUES(?,?,?)",
                       (character,'new_stock','true'))
        self.journal.event(self.character,'shop_return_operator_overridden',
                           original_evidence_digest=digest,
                           confirmation_reference=confirmation_reference.strip())
        return state

    def move(self, state, snapshot, controller, travel, destination):
        with controller.coordinator.lease(self.character):
            controller.check()
            travel.qualify_movement()
            self.save(state,moves=state['moves']+1)
            after=travel.move(snapshot,destination,controller.check)
        progressed=after['map_id']==snapshot['map_id'] and after['position']!=snapshot['position']
        self.save(state,stalls=0 if progressed else state['stalls']+1)
