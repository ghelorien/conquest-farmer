"""Memory-only visitor routing; permission never grants automated trade input."""
import json
import time
from contextlib import contextmanager
from conquest.character_context import is_farmer_owner

from conquest.merchants.journal import CHARACTERS, character_name
from conquest.merchants.manual_sessions import ManualSessionError, ManualSessionStore, PROBE_HISTORY_CHECKS


OBSERVATION_DEFERRED = object()


def _probe_history_failure(error):
    """Only fixed invariant labels and bounded counters, never traceback data."""
    result={}
    trace=error.__traceback__
    while trace is not None:
        if trace.tb_frame.f_code is ManualSessionStore._probe_pair.__code__:
            scope=trace.tb_frame.f_locals
            check,role,index=(scope.get(key) for key in ('history_check','role','index'))
            if isinstance(check,str) and check in PROBE_HISTORY_CHECKS:result['history_check']=check
            if isinstance(role,str) and role in ('farmer','merchant'):result['history_role']=role
            if type(index) is int and 0<=index<=10000000:result['history_index']=index
        trace=trace.tb_next
    return result


def probe_attempt_projection(value):
    """Bounded metadata only; never echo exception text or cached evidence."""
    if not isinstance(value,dict):return None
    enums={
        'stage':{'probe_read','profile_binding','ownership','probe_recheck','hold_read','manual_history','final_probe_recheck','fence_sync'},
        'reason':{'probe_read_failed','profile_binding_failed','ownership_unverified','open_trade_required','probe_changed',
                  'hold_read_failed','reader_or_decline_hold','manual_history_validation_failed','exact_bot_history',
                  'manual_history_unverified','fence_sync_failed'},
        'outcome':{'error','protected','validated','reconciled'},'checked_through':{'final_probe_recheck'},
        'history_check':PROBE_HISTORY_CHECKS,'history_role':{'farmer','merchant'},
        'error_type':{'ValueError','OSError','TypeError','KeyError','AttributeError','RuntimeError','OperationalError',
                      'DatabaseError','IntegrityError','PermissionError','FileNotFoundError','CaptureUnavailable',
                      'ManualSessionError','BindingMismatch','JSONDecodeError'}}
    result={key:value[key] for key,allowed in enums.items() if isinstance(value.get(key),str) and value[key] in allowed}
    if type(value.get('bot_owned')) is bool:result['bot_owned']=value['bot_owned']
    for key in ('probe_digest','evidence_digest'):
        item=value.get(key)
        if isinstance(item,str) and len(item)==64 and all(c in '0123456789abcdef' for c in item):result[key]=item
    for key in ('target_profile_id','farmer_profile_id'):
        item=value.get(key)
        if isinstance(item,str) and 0<len(item)<=128 and item.isascii() and all(c.isalnum() or c in '_-.' for c in item):result[key]=item
    for key in ('active_session_count','eligible_sessions','outage_intervals','retracted_sessions','history_index'):
        item=value.get(key)
        if type(item) is int and 0<=item<=10000000:result[key]=item
    return result


class ManualRuntime:
    @contextmanager
    def _manual_observation_scope(self, snapshot, *, now=None):
        """Serialize one observation, distinguishing queued reads from bad input.

        Complete evidence fresh when submitted must be reread if it waited for
        another input owner: even a short action can change modal ownership.
        This grants no incident proof, changes no fence, and requires a new
        read on the next observer poll.
        """
        from conquest.merchants.manual_sessions import _fresh, _digest
        entered_at = time.time() if now is None else now
        digest = None
        try:
            _fresh(snapshot, entered_at)
            digest = _digest(snapshot)
        except (ValueError, KeyError, TypeError, AttributeError):
            pass  # Missing/already-stale evidence follows ordinary fail-closed routing.
        waited = not self.coordinator.lock.acquire(blocking=False)
        if waited:self.coordinator.lock.acquire()
        try:
            deferred = False
            if waited and digest is not None:
                try:
                    deferred = _digest(snapshot) == digest
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
            yield deferred
        finally:
            self.coordinator.lock.release()

    def init_manual_sessions(self):
        from conquest.merchants.manual_recovery import SCHEMA
        with self.journal.db() as db:db.executescript(SCHEMA)
        self.manual_sessions = ManualSessionStore(self.journal.path, on_settlement=self._manual_settlement)
        self.manual_farmer_provider = lambda:None
        self.manual_farmer_control = lambda:{'enabled':False}
        self.manual_farmer_observation = {'available':False,'reason':'Farmer observer is not configured'}
        self.manual_farmer_controller = None
        self.coordinator.manual_journal = self.journal
        self.coordinator.manual_farmer_target = self.manual_target('Farmer')
        self.coordinator.manual_farmer_boundary = self.observe_manual_farmer
        self._sync_manual_fence()

    @staticmethod
    def manual_target(character):
        from conquest.character_context import is_farmer_owner,current
        if is_farmer_owner(character):
            context=current()
            return context.profile.id if context and context.profile.role=='Farmer' else 'Farmer'
        character = character_name(character)
        return getattr(character, 'profile_id', str(character))

    def _manual_settlement(self, db, session, snapshot, receipt):
        from conquest.merchants.manual_recovery import settlement
        from conquest.character_context import registry
        profiles=registry()
        role=(profiles.resolve(session['target_profile_id']).role if profiles else
              'Farmer' if session['target_profile_id']=='Farmer' else 'Merchant')
        settlement(db,session,snapshot,receipt,target_role=role)

    def _manual_get(self, character, name, default=None):
        with self.journal.db() as db:
            row=db.execute('SELECT value FROM state WHERE character=? AND name=?',(self.manual_target(character),name)).fetchone()
        return json.loads(row[0]) if row else default

    def _manual_set(self, character, name, value):
        with self.journal.db() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(self.manual_target(character),name,json.dumps(value)))

    def _manual_rows(self):
        # Include archived/disabled target profiles: a pending manual interval
        # cannot be forgotten merely by changing the profile roster.
        with self.manual_sessions.db() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM manual_sessions WHERE phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden')")]
        rows = [self.manual_sessions.get(key) for key in ids]
        with self.journal.db() as db:
            holds=[json.loads(row[0]) for row in db.execute("SELECT value FROM state WHERE name='manual_reader_hold' AND value!='null'")]
        for hold in holds:
            if hold and not any(row['target_profile_id'] == hold['target_profile_id'] for row in rows):
                rows.append(hold)
        from conquest.merchants.manual_recovery import rebaseline_views
        for hold in rebaseline_views(self.journal):
            if not any(row['target_profile_id']==hold['target_profile_id'] for row in rows):rows.append(hold)
        return rows

    def _sync_manual_fence(self):
        with self.coordinator.lock:
            self.coordinator.set_manual_sessions(self._manual_rows())

    def manual_status(self, character=None):
        rows = self._manual_rows()
        if character is not None:
            rows = [next((row for row in rows if row['target_profile_id'] == self.manual_target(character)), None)]
        def view(row):
            if row is None:return None
            return {**row, 'fence_scope': 'global' if row['ever_approved'] else 'target',
                    'deadline': row['expires_at']}
        return [view(row) for row in rows] if character is None else view(rows[0])

    def _manual_character(self, target):
        if target==self.manual_target('Farmer'):return 'Farmer'
        matches = [character for character in CHARACTERS if self.manual_target(character) == target]
        if len(matches) != 1:raise ManualSessionError('Manual target is not an attached profile')
        return matches[0]

    def approve_manual(self, binding, *, operator='local', now=None):
        if not isinstance(binding,dict):raise ManualSessionError('Exact displayed approval binding is required')
        character = self._manual_character(binding.get('target_profile_id'))
        # Exclude native input without a gameplay lease, focus or handoff.
        with self.coordinator.lock:
            from conquest.merchants.delivery_reservation import active
            farmer=is_farmer_owner(character)
            if (self.farmer_bot_owned() if farmer else active(self.journal, character) or self.journal.pending(character)):
                raise ManualSessionError('Bot transaction must reconcile before manual approval')
            observer = self.manual_farmer_provider() if farmer else self.observers.get(character)
            controller = self.manual_farmer_controller if farmer else self.controllers.get(character)
            if observer is None or (not farmer and controller is None):
                raise ManualSessionError('Fresh attached target memory is required for approval')
            if farmer:
                from conquest.character_context import registry,current,farmer_name
                context=current()
                if (observer.character!=farmer_name() or registry() is not None
                        and (context is None or context.profile.role!='Farmer')):
                    raise ManualSessionError('Fresh exact selected Farmer profile is required')
            if not observer.lock.acquire(blocking=False):
                raise ManualSessionError('Memory observer is busy; retry approval with fresh evidence')
            try:
                observer.adapter.assert_identity()
                if farmer:
                    from conquest.merchants.memory import MerchantMemory
                    snapshot=MerchantMemory(observer).read(farmer_preflight=True)
                else:snapshot = controller.driver.read()
            finally:observer.lock.release()
            if self.process_probe_owned(character, snapshot, now=now):
                raise ManualSessionError('Supervised bot probe owns this exact request; manual approval is unavailable')
            try:return self.manual_sessions.allow_and_activate(binding, snapshot, operator=operator, now=now)
            finally:self._sync_manual_fence()

    def reject_manual(self, binding, *, operator='local', now=None):
        with self.coordinator.lock:
            try:return self.manual_sessions.reject(binding, operator=operator, now=now)
            finally:self._sync_manual_fence()

    def revoke_manual(self, visitor, *, operator='local', now=None):
        return self.manual_sessions.revoke(visitor, operator=operator, now=now)

    def override_manual(self, session_id, *, confirmation_reference, operator, reason, now=None):
        if not all(isinstance(value,str) and value.strip() for value in (confirmation_reference,operator,reason)):
            raise ManualSessionError('Explicit confirmation, operator and reason are required')
        from conquest.merchants.manual_recovery import start_rebaseline, reset_rebaseline
        with self.coordinator.lock:
            try:
                if session_id.startswith('rebaseline:'):
                    reset_rebaseline(self.journal,session_id,operator=operator,
                        confirmation_reference=confirmation_reference,reason=reason,now=now)
                    return next(row for row in self._manual_rows() if row['id']==session_id)
                for character in (*CHARACTERS,'Farmer'):
                    hold = self._manual_get(character, 'manual_reader_hold')
                    if hold and hold['id'] == session_id:
                        start_rebaseline(self.journal,hold['target_profile_id'],session_id,now=now)
                        with self.journal.db() as db:
                            db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                                (hold['target_profile_id'],'manual_reader_operator_overridden',json.dumps({
                                    'confirmation_reference':confirmation_reference,'operator':operator,'reason':reason,'hold':hold}),
                                 time.time() if now is None else now))
                            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(hold['target_profile_id'],'manual_reader_hold','null'))
                        return {**hold,'phase':'operator_overridden','holds_automation':False}
                row = self.manual_sessions.get(session_id)
                if not row['holds_automation']:
                    # Let the domain validate an idempotent terminal command;
                    # a rejected command must not create a new recovery hold.
                    return self.manual_sessions.operator_override(session_id,
                        confirmation_reference=confirmation_reference,operator=operator,reason=reason,now=now)
                # Persist the target fence first. A crash before disposition
                # leaves the original (possibly global) domain hold intact.
                start_rebaseline(self.journal,row['target_profile_id'],session_id,now=now)
                return self.manual_sessions.operator_override(session_id,
                    confirmation_reference=confirmation_reference, operator=operator, reason=reason, now=now)
            finally:self._sync_manual_fence()

    def manual_unavailable(self, character, reason):
        with self.coordinator.lock:
            row = self.manual_sessions.active(self.manual_target(character))
            if row:
                self.manual_sessions.observe(row['id'], {'reader_error': reason})
            else:
                from conquest.merchants.manual_recovery import observe_rebaseline
                row = observe_rebaseline(self.journal,self.manual_target(character),{'reader_error':reason},
                                         target_role='Farmer' if is_farmer_owner(character) else 'Merchant')
            self._sync_manual_fence()
        return bool(row or self._manual_get(character,'manual_reader_hold'))

    def _structural_request_probe_owned(self, character, snapshot, state, *, now):
        """Conservatively reserve a still-visible exact probe request.

        This is deliberately weaker than :func:`ownership`: it is used only
        while the peer observer is briefly unavailable, to prevent that
        transient observation failure from creating a *new* manual admission.
        It never retracts a session, changes a fence, or grants input.
        """
        import math
        from conquest.merchants.delivery_probe_ownership import REQUEST_PHASES
        from conquest.merchants.delivery import validate_snapshot, exact_items
        from conquest.merchants.delivery_request_reconciliation import ownership as request_ownership
        from conquest.merchants.manual_sessions import canonical_ownership
        from conquest.character_context import farmer_name
        from conquest.recovery_override import evidence_digest
        try:
            request=snapshot.get('request')
            intent=state['intent'];merchant=intent['merchant'];farmer=intent['farmer']
            if (not isinstance(state,dict) or state.get('phase') not in REQUEST_PHASES
                    or snapshot.get('trade') is not None or not isinstance(request,dict)
                    or state.get('character')!=str(character)
                    or state.get('target_profile_id',state.get('character'))!=self.manual_target(character)
                    or state.get('farmer_profile_id','Farmer')!=self.manual_target('Farmer')
                    or farmer.get('character')!=farmer_name()):
                return False
            started=state.get('started_at')
            if type(started) not in (int,float) or not math.isfinite(started) or not 0<=started<=now:
                return False
            for field in ('updated_at','finished_at','accepted_at'):
                value=state.get(field,started)
                if (type(value) not in (int,float) or not math.isfinite(value)
                        or not started<=value<=now):return False
            for participant in (farmer,merchant):
                timestamp=participant.get('timestamp') if isinstance(participant,dict) else None
                if (participant.get('map_id')!=1036 or type(timestamp) not in (int,float)
                        or not math.isfinite(timestamp) or not 0<=started-timestamp<=5):
                    return False
            if farmer.get('identity')==merchant.get('identity'):return False
            # Both the attached merchant and the incoming requester must be
            # exactly the durable participants; a same-named visitor is not
            # enough to suppress manual routing.
            if any(snapshot.get(key)!=merchant.get(key) for key in
                   ('identity','character','character_uid','server','position')):
                return False
            if (request.get('participant')!=farmer.get('character')
                    or type(request.get('participant_uid')) is not int
                    or request['participant_uid']!=farmer.get('character_uid')
                    or request.get('server',snapshot.get('server'))!=farmer.get('server')
                    or request.get('message')!=f"{farmer.get('character')} wishes to trade with you."):
                return False
            recipient=state.get('recipient',{})
            if any(recipient.get(left)!=merchant.get(right) for left,right in
                   (('uid','character_uid'),('name','character'),('position','position'))):
                return False
            selected=state.get('selected_uids');items=exact_items(intent['items'])
            farmer_items=exact_items(farmer['inventory'])
            if (not isinstance(selected,list) or not selected or any(type(uid) is not int or uid<=0 for uid in selected)
                    or len(set(selected))!=len(selected) or set(selected)!=set(items)
                    or any(farmer_items.get(uid)!=item for uid,item in items.items())):
                return False
            validate_snapshot(snapshot,merchant['character'],now)
            # request_verified is an unresolved durable receipt.  Its saved
            # merchant observation must bind the same modal before it can
            # provide this narrow structural precedence.
            if state['phase']!='request_submitted':
                saved=state.get('merchant_after')
                if not isinstance(saved,dict) or any(saved.get(key)!=snapshot.get(key) for key in
                        ('identity','character','character_uid','server','position')):
                    return False
                saved_request=saved.get('request')
                if not isinstance(saved_request,dict) or saved_request!=request:
                    return False
                saved_farmer=state.get('farmer_after')
                # Saved observations are a durable receipt, not current
                # memory.  Validate them at their own boundary so an old,
                # recoverable error cannot reopen manual admission.
                if not isinstance(saved_farmer,dict):return False
                if state['phase']=='request_verified':
                    verified_at=state.get('updated_at')
                    if (type(verified_at) not in (int,float) or not math.isfinite(verified_at)
                            or not started<=verified_at<=now):return False
                    for receipt in (saved_farmer,saved):
                        observed_at=receipt.get('timestamp')
                        if (type(observed_at) not in (int,float) or not math.isfinite(observed_at)
                                or not started<=observed_at<=verified_at):return False
                saved_now=max(saved_farmer.get('timestamp',float('inf')),saved.get('timestamp',float('inf')))
                request_ownership(intent,saved_farmer,saved,now=saved_now)
                expected=saved
            else:
                expected=merchant
            live=canonical_ownership(snapshot,require_closed=False)
            expected_proof=canonical_ownership(expected,require_closed=False)
            if ({**live,'request':None}!={**expected_proof,'request':None}
                    or snapshot.get('position')!=expected.get('position')
                    or snapshot.get('map_id')!=expected.get('map_id')
                    or any({item['uid']:item.get('slot') for item in snapshot[field]}!=
                           {item['uid']:item.get('slot') for item in expected[field]}
                               for field in ('inventory','booth'))):
                return False
            # Do not let a journal replacement race turn an unrelated request
            # into a suppressed one.
            from conquest.merchants import delivery_probe
            return evidence_digest(delivery_probe.read_probe())==evidence_digest(state)
        except (KeyError, TypeError, ValueError, OSError):
            return False

    def _structural_trade_probe_owned(self, character, snapshot, state, *, now):
        """Defer one observation only; never retract or synchronize a fence."""
        from conquest.merchants import delivery_probe
        from conquest.merchants.delivery_probe_ownership import local_ownership
        from conquest.recovery_override import evidence_digest
        try:
            merchant = character_name(state['character'])
            proof = local_ownership(state, merchant, self.manual_target(merchant), self.manual_target('Farmer'),
                'farmer' if is_farmer_owner(character) else 'merchant', snapshot, now=now)
            return evidence_digest(delivery_probe.read_probe()) == proof['probe_digest']
        except (KeyError, TypeError, ValueError, OSError):
            return False

    def process_probe_owned(self, character, snapshot, *, farmer_snapshot=None, now=None,
                            require_bilateral=False):
        """Keep exact supervised input under its own worker, never auto-accept."""
        if not (snapshot.get('request') or snapshot.get('trade')):
            return False
        from conquest.merchants import delivery_probe
        from conquest.merchants.delivery_probe_ownership import ownership, REQUEST_PHASES, TRADE_PHASES
        from conquest.merchants.memory import MerchantMemory
        from conquest.recovery_override import evidence_digest
        from conquest.capture import CaptureUnavailable
        from conquest.character_context import farmer_name
        with self._manual_observation_scope(snapshot, now=now) as deferred:
            if deferred:return False if require_bilateral else OBSERVATION_DEFERRED
            state = None
            try:
                state = delivery_probe.read_probe()
                farmer_side = is_farmer_owner(character)
                if (not state or not farmer_side and state.get('character') != str(character)
                        or state.get('phase') not in REQUEST_PHASES | TRADE_PHASES):
                    return False
                merchant_character = character_name(state['character']) if farmer_side else character
                def defer():
                    if require_bilateral:return False
                    observed_now = time.time() if now is None else now
                    if state['phase'] in TRADE_PHASES and self._structural_trade_probe_owned(
                            character, snapshot, state, now=observed_now):return True
                    return not farmer_side and self._structural_request_probe_owned(
                        character, snapshot, state, now=observed_now)
                # A request captured before acceptance can reach this mutex
                # after the durable open-trade write. It is old observation,
                # never evidence for a new manual admission or for input.
                if farmer_snapshot is not None and snapshot.get('request') and state['phase'] not in REQUEST_PHASES:
                    return False
                if farmer_snapshot is not None:
                    # Delivery acceptance has just read this exact bilateral
                    # pair under both observer locks.  Reacquiring the farmer
                    # lock here creates a false failure window and can mint a
                    # competing manual admission.  Supplied evidence remains
                    # read-only and must pass the same full ownership/digest
                    # proof below; it never bypasses a manual-session fence.
                    if farmer_side:return False
                    farmer,merchant=farmer_snapshot,snapshot
                else:
                    source = self.manual_farmer_provider()
                    if source is None:
                        # On restart the merchant can attach before the farmer.
                        # Only an already verified bilateral receipt may reserve
                        # this exact still-visible request during that gap. This
                        # early return cannot retract a session, change a fence,
                        # or provide the full proof required for any input.
                        if snapshot.get('request') is not None:
                            return (not require_bilateral and not farmer_side and state.get('phase')=='request_verified'
                                    and self._structural_request_probe_owned(character,snapshot,state,
                                        now=time.time() if now is None else now))
                        # Do not fall through to the request fallback merely
                        # because accept/cancel are also trade-capable phases.
                        return (not require_bilateral and state['phase'] in TRADE_PHASES
                                and self._structural_trade_probe_owned(character,snapshot,state,
                                    now=time.time() if now is None else now))
                    intent=state.get('intent');farmer_intent=intent.get('farmer') if isinstance(intent,dict) else None
                    if (source.character != farmer_name() or not isinstance(farmer_intent,dict)
                            or getattr(getattr(source,'adapter',None),'identity',None)!=farmer_intent.get('identity')):
                        return False
                    if snapshot.get('request') and state['phase'] not in REQUEST_PHASES:
                        return defer()
                    observer = self.observers.get(merchant_character) if farmer_side else source
                    if observer is None:return defer() if state['phase'] in TRADE_PHASES else False
                    peer_role = 'merchant' if farmer_side else 'farmer'
                    if getattr(getattr(observer, 'adapter', None), 'identity', None) != intent[peer_role]['identity']:
                        return False
                    if not observer.lock.acquire(blocking=False):
                        return defer()
                    try:
                        try:observer.adapter.assert_identity()
                        except (OSError, CaptureUnavailable, ValueError):return False
                        if farmer_side:
                            farmer = snapshot
                            merchant = self.controllers[merchant_character].driver.read()
                        else:
                            farmer = MerchantMemory(observer).read(farmer_preflight=True)
                            merchant = snapshot
                    except (OSError, CaptureUnavailable, ValueError, KeyError, TypeError, AttributeError):
                        # Only the peer acquisition/read path may use the
                        # observation-only structural fallback.  Once both
                        # snapshots exist, a failed bilateral proof is evidence
                        # of a changed incident and must route manually.
                        return defer()
                    finally:observer.lock.release()
                # Native snapshots are timestamped when their reads finish.
                # Taking now before the peer read makes valid fresh evidence
                # look future-dated and incorrectly admits the bot request as
                # manual. Keep explicit caller-supplied test/evidence time fixed.
                now = time.time() if now is None else now
                source_target = self.manual_target('Farmer')
                proof = ownership(state, merchant_character, self.manual_target(merchant_character), source_target,
                                  farmer, merchant, now=now)
                if evidence_digest(delivery_probe.read_probe()) != proof['probe_digest']:
                    return False
            except (OSError, CaptureUnavailable, ValueError, KeyError, TypeError, AttributeError):
                return False
            if proof['modal'] == 'trade':
                return self.reconcile_probe_pair(merchant_character, farmer, merchant, now=now)
            row = self.manual_sessions.active(self.manual_target(character))
            decline = self._manual_get(character, 'unrelated_request_decline') or {}
            if row and not farmer_side and decline.get('phase') != 'submitted':
                try:
                    self.manual_sessions.retract_probe_admission(row['id'], snapshot, state, farmer,
                                                                farmer_profile_id=source_target, now=now)
                except ManualSessionError:
                    pass  # Genuine/uncertain manual intervals retain their fence.
            self._sync_manual_fence()
            return True

    def reconcile_probe_pair(self, character, farmer, merchant, *, now=None):
        """Full bilateral proof and all-or-nothing false-admission cleanup."""
        result=self._probe_reconciliation(character,farmer,merchant,now=now,read_only=False)
        self.last_probe_reconciliation=result
        return result['bot_owned'] and result['outcome']!='error'

    def inspect_probe_reconciliation(self, character, farmer, merchant, *, now=None):
        """Query-only diagnosis: never retract, synchronize fences or send input."""
        return self._probe_reconciliation(character,farmer,merchant,now=now,read_only=True)

    def _probe_reconciliation(self, character, farmer, merchant, *, now, read_only):
        from conquest.merchants import delivery_probe
        from conquest.merchants.delivery_probe_ownership import ownership
        from conquest.recovery_override import evidence_digest
        result={'read_only':read_only,'input_authorized':False,'bot_owned':False,
                'outcome':'error','stage':'probe_read','reason':'probe_read_failed'}
        read=(lambda:delivery_probe.read_probe(read_only=True)) if read_only else delivery_probe.read_probe
        with self.coordinator.lock:
            try:
                state = read()
                now = time.time() if now is None else now
                result.update(stage='profile_binding',reason='profile_binding_failed')
                target, source = self.manual_target(character), self.manual_target('Farmer')
                result.update(target_profile_id=target,farmer_profile_id=source,
                              stage='ownership',reason='ownership_unverified')
                proof = ownership(state, str(character), target, source, farmer, merchant, now=now)
                if proof['modal']!='trade':
                    result['reason']='open_trade_required'
                    return result
                result.update(probe_digest=proof['probe_digest'],evidence_digest=proof['evidence_digest'],
                              stage='probe_recheck',reason='probe_changed')
                if evidence_digest(read())!=proof['probe_digest']:return result
                result.update(bot_owned=True,stage='hold_read',reason='hold_read_failed')
                # Use a query-only connection even for the diagnostic's hold
                # projection; no evidence append, WAL-mode change or fence sync.
                with self.manual_sessions._probe_connection(True) as db:
                    db.execute('BEGIN')
                    rows=db.execute('SELECT name,value FROM state WHERE character IN (?,?) AND name IN '
                                    "('unrelated_request_decline','manual_reader_hold')",(target,source)).fetchall()
                    held=any((value and (row['name']=='manual_reader_hold' or value.get('phase')=='submitted'))
                             for row in rows for value in (json.loads(row['value']),))
                    result['active_session_count']=db.execute('SELECT COUNT(*) FROM manual_sessions WHERE target_profile_id IN (?,?) '
                        "AND phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden')",(target,source)).fetchone()[0]
                if held:
                    result.update(outcome='protected',reason='reader_or_decline_hold')
                else:
                    result.update(stage='manual_history',reason='manual_history_validation_failed')
                    method=(self.manual_sessions.inspect_probe_pair if read_only else self.manual_sessions.retract_probe_pair)
                    try:
                        details=method(state,farmer,merchant,target_profile_id=target,farmer_profile_id=source,
                                       now=now,current_probe=read)
                        result.update(outcome='validated' if read_only else 'reconciled',reason='exact_bot_history')
                        if read_only:result.update(details)
                        else:result['retracted_sessions']=len(details)
                    except ManualSessionError as error:
                        # A historical gap/divergence is not a failed current
                        # bot proof. It retains both manual fences for review.
                        result.update(outcome='protected',reason='manual_history_unverified',**_probe_history_failure(error))
                decisive_stage=result['stage'];result['stage']='final_probe_recheck'
                if evidence_digest(read())!=proof['probe_digest']:
                    result.update(bot_owned=False,outcome='error',reason='probe_changed')
                else:result.update(stage=decisive_stage,checked_through='final_probe_recheck')
            except Exception as error:
                # No arbitrary exception text/snapshots in diagnostics. Storage
                # and programming failures remain explicit, bounded and closed.
                result.update(outcome='error',error_type=type(error).__name__,**_probe_history_failure(error))
            finally:
                if not read_only:
                    try:self._sync_manual_fence()
                    except Exception as error:
                        result.update(outcome='error',stage='fence_sync',reason='fence_sync_failed',error_type=type(error).__name__)
            return result

    def reconcile_probe_owned(self, character, farmer, merchant, *, now=None):
        """Reconcile an already-read exact pair without reacquiring observers.

        This is a read-only, full bilateral ownership proof plus the existing
        false-admission retraction/fence sync.  It deliberately grants no
        input: callers must still pass the coordinator's manual fence and
        purpose-scoped lease checks.
        """
        with self.coordinator.lock:
            return self.process_probe_owned(character,merchant,farmer_snapshot=farmer,
                                            now=now,require_bilateral=True)

    def process_manual(self, character, snapshot, *, decline_enabled=False, now=None):
        """Called after bot reservation/transaction routing, before normal work."""
        with self._manual_observation_scope(snapshot, now=now) as deferred:
            if deferred:return True
            return self._process_manual_locked(character, snapshot, decline_enabled=decline_enabled, now=now)

    def _process_manual_locked(self, character, snapshot, *, decline_enabled=False, now=None):
        """Routing and any resulting admission share one observation mutex."""
        if self.process_probe_owned(character, snapshot, now=now):return True
        now = time.time() if now is None else now
        target = self.manual_target(character)
        store = self.manual_sessions
        with self.coordinator.lock:
            if self._manual_get(character,'manual_reader_hold'):
                self._sync_manual_fence()
                return True
            row = store.active(target)
            if row is None:
                from conquest.merchants.manual_recovery import observe_rebaseline
                if observe_rebaseline(self.journal,target,snapshot,now=now,
                                     target_role='Farmer' if is_farmer_owner(character) else 'Merchant'):
                    self._sync_manual_fence()
                    return True
            request = snapshot.get('request')
            try:
                if row:
                    if request and row['phase'] != 'needs_attention':
                        try:
                            row = store.begin_request(target, snapshot, session_id=row['id'], now=now)
                            row = store.observe(row['id'], snapshot, now=now)
                        except ManualSessionError:
                            row = store.observe(row['id'], snapshot, now=now)
                    else:row = store.observe(row['id'], snapshot, now=now)
                elif request:
                    row = store.begin_request(target, snapshot, now=now)
                elif snapshot.get('trade'):
                    row = store.observe_target(target, snapshot, now=now)
                else:return False
                if row['phase'] == 'approval_pending' and row['request_state'] == 'pending':
                    row = store.expire(row['id'], now=now)
                    if row['request_state'] == 'pending' and store.allowed(row['visitor']):
                        row = store.activate_allowed(row['approval_binding'], snapshot, now=now)
            except ManualSessionError as error:
                # Incomplete identity is not permission to use the old path.
                self.manual_reader_failure(character,snapshot,'Manual visitor memory requires attention: '+str(error),now=now)
                return True
            finally:self._sync_manual_fence()
        if (row['phase'] == 'approval_pending' and row['request_state'] == 'decline_pending'
                and not row['ever_approved'] and decline_enabled and self.can_start_work()):
            if not is_farmer_owner(character) and not self.coordinator.safe_to_yield():
                with self.lock:
                    if self.handoff is None:self.handoff = f'manual-decline:{row["id"]}'
            from conquest.merchants.unrelated_request import decline_unrelated_request
            try:
                controller=self.manual_farmer_controller if is_farmer_owner(character) else self.controllers[character]
                decline_unrelated_request(controller, snapshot,
                    operations_enabled=True, manual_session=(store, row['id']))
            finally:self._sync_manual_fence()
        return True

    def configure_manual_farmer(self, observer_provider, control_provider):
        self.manual_farmer_provider=observer_provider
        self.manual_farmer_control=control_provider

    def manual_reader_failure(self, character, evidence, reason, *, now=None):
        now=time.time() if now is None else now
        target=self.manual_target(character)
        with self.coordinator.lock:
            self._manual_set(character,'manual_reader_hold',{
                'id':'unbound:'+target, 'target_profile_id':target, 'phase':'needs_attention',
                'reason':reason, 'created_at':now, 'updated_at':now, 'visitor':None, 'ever_approved':False,
                'holds_automation':True, 'approval_binding':None, 'request_state':None,
                'expires_at':None, 'evidence':json.loads(json.dumps(evidence))})
            self._sync_manual_fence()

    def manual_farmer_status(self):
        return {'session':self.manual_status('Farmer'), 'observation':dict(self.manual_farmer_observation),
                'input_fenced':self.coordinator.manual_session_blocked('Farmer')}

    def farmer_bot_owned(self):
        from conquest.merchants.delivery_reservation import active
        if getattr(self,'delivery_window',None) or any(active(self.journal,c) for c in CHARACTERS):return True
        from conquest.merchants.delivery_operation import JOURNAL
        import sqlite3
        if not JOURNAL.exists():return False
        with sqlite3.connect(JOURNAL.resolve().as_uri()+'?mode=ro',uri=True) as db:
            return bool(db.execute("SELECT 1 FROM transactions WHERE kind='farmer_delivery' AND phase NOT IN ('verified','aborted','operator_overridden') LIMIT 1").fetchone())

    def observe_manual_farmer(self, observer=None):
        from conquest.merchants.manual_farmer import observe
        return observe(self,observer)

    def run_manual_farmer(self):
        while not self.stop_event.is_set():
            try:self.observe_manual_farmer()
            except (ValueError,OSError):
                reason='Farmer manual observation unavailable'
                if not self.manual_unavailable('Farmer',reason):
                    self.manual_reader_failure('Farmer',{'reader_error':reason},reason)
            except Exception:
                self.manual_farmer_observation={'available':False,'reason':'Unexpected farmer manual observer failure'}
                reason='Farmer manual observer failed; inspect diagnostics before overriding'
                if not self.manual_unavailable('Farmer',reason):
                    self.manual_reader_failure('Farmer',{'reader_error':'Unexpected observer failure'},reason)
            self.stop_event.wait(.5)
