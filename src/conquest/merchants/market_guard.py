"""Independent memory-only merchant protection, including paused accounts."""
import time
import copy
import hashlib
import json
from conquest.character_context import state_path
from conquest.merchants.journal import CHARACTERS


def operator_override(runtime, character, *, operator_confirmed=False,
                      confirmation_reference=None, operator=None, fresh_evidence=None,
                      incident_digest=None):
    if operator_confirmed is not True:
        raise ValueError('Operator confirmation is required for this incident')
    if not isinstance(confirmation_reference,str) or not confirmation_reference.strip():
        raise ValueError('A non-empty incident confirmation reference is required')
    from conquest.merchants.journal import character_name
    character=character_name(character)
    with runtime.journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT value FROM state WHERE character=? AND name='market_safety'",(character,)).fetchone()
        state=json.loads(row[0]) if row else {}
        if state.get('phase')=='operator_overridden':
            if (state.get('operator_override') or {}).get('confirmation_reference')!=confirmation_reference.strip():
                raise ValueError('Incident was already overridden with a different confirmation')
            return state
        if not state:raise ValueError('No active Market safety hold is available')
        original=copy.deepcopy(state);digest=hashlib.sha256(json.dumps(original,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if incident_digest is not None and incident_digest != digest:
            raise ValueError('Incident evidence changed; recheck before overriding')
        state.update(phase='operator_overridden',replan_required=True,
                     operator_override={'operator_confirmed':True,'confirmation_reference':confirmation_reference.strip(),
                      'operator':operator,'confirmed_at':time.time(),'original_evidence_digest':digest,
                      'original_state':original,'fresh_evidence':fresh_evidence or {}})
        encoded=json.dumps(state)
        if row:
            db.execute("UPDATE state SET value=? WHERE character=? AND name='market_safety'",(encoded,character))
        else:
            db.execute("INSERT INTO state(character,name,value) VALUES(?,?,?)",(character,'market_safety',encoded))
        hold=db.execute("SELECT value FROM state WHERE character=? AND name='connect_hold'",(character,)).fetchone()
        attention=db.execute("SELECT value FROM state WHERE character=? AND name='attention'",(character,)).fetchone()
        attention_value=json.loads(attention[0]) if attention else None
        matching_attention = (attention_value is None or
                              isinstance(attention_value,dict) and
                              attention_value.get('kind')=='market_safety')
        # Do not clear a newer safety/attention incident that reused the
        # shared connection hold after this market incident was recorded.
        if hold and json.loads(hold[0]) is True and matching_attention:
            db.execute("UPDATE state SET value='false' WHERE character=? AND name='connect_hold' AND value=?",
                       (character,hold[0]))
            if attention and attention_value is not None:
                db.execute("UPDATE state SET value='null' WHERE character=? AND name='attention' AND value=?",
                           (character,attention[0]))
    runtime.journal.event(character,'market_safety_operator_overridden',
                           original_evidence_digest=digest,confirmation_reference=confirmation_reference.strip())
    return state


def protect(runtime, character, identity, reason, *, close=None):
    # Latest user instruction: never close a merchant client automatically.
    # Pausing input and reconnect intent preserves the current live connection.
    runtime.enable(character, False)
    runtime.set_refill_enabled(character, False)
    runtime.journal.set(character, 'connect_hold', True)
    cancel = runtime.connect_cancel.get(character)
    if cancel: cancel.set()
    runtime.journal.set(character, 'attention', {
        'kind': 'market_safety', 'note': reason + '; automation paused, client kept connected. User assistance required.'})
    result = {'time': time.time(), 'identity': identity, 'reason': reason,
              'disconnected': False, 'action': 'pause_only'}
    previous = runtime.journal.get(character, 'market_safety') or {}
    runtime.journal.set(character, 'market_safety', result)
    if previous.get('reason') != reason or previous.get('action') != 'pause_only':
        runtime.journal.event(character, 'market_safety', **result)
    return result


class MarketGuard:
    def __init__(self, runtime):
        self.runtime = runtime
        self.readers = {}
        self.unknown_since = {}
        self.observations = {}

    def check(self, character, observer, *, clock=time.monotonic, read=None, close=None):
        from conquest.memory_life import read_life
        read = read or read_life
        identity = observer.adapter.identity
        coordinator = getattr(self.runtime,'coordinator',None)
        if (coordinator and hasattr(coordinator,'manual_session_blocked')
                and coordinator.manual_session_blocked(character)):
            # Keep observing location but do not reinterpret manual movement
            # as permission to pause saved intent, reconnect, or disconnect.
            try:
                observer.adapter.assert_identity()
                life = read(observer.adapter,observer.health_layout,character)
                self.observations[character] = {'map_id':life.map_id,'observed_at':time.time()}
            except (ValueError,OSError):pass
            return
        from conquest.merchants.recovery_safety import arm
        from conquest.reconnect import login_screen
        # A known disconnected client is a recovery trigger, not an unsafe
        # manually positioned character. Never turn that into a pause-only hold.
        target = getattr(getattr(observer, 'operations', None), 'target', None)
        if target is not None and login_screen(target.hwnd):
            arm(self.runtime, character)
            from conquest.merchants.recovery_safety import active, observe
            if active(self.runtime, character):
                return observe(self.runtime, character, identity, close=close)
            return  # Never interpret stale player memory on the login screen.
        try:
            observer.adapter.assert_identity()
            life = read(observer.adapter, observer.health_layout, character)
        except Exception:
            from conquest.merchants.recovery_safety import active, observe
            if active(self.runtime, character):
                return observe(self.runtime, character, identity, close=close)
            started = self.unknown_since.setdefault(character, clock())
            if clock() - started < 2: return
            reason = 'Merchant location could not be verified for two seconds'
        else:
            self.unknown_since.pop(character, None)
            self.observations[character] = {'map_id':life.map_id, 'observed_at':time.time()}
            from conquest.merchants.recovery_safety import active, observe
            if active(self.runtime, character):
                return observe(self.runtime, character, identity, life, close=close)
            if life.map_id == 1036 and not life.dead_candidate: return
            reason = f'Merchant outside safe Market (map {life.map_id}) or death detected'
        return protect(self.runtime, character, identity, reason, close=close)

    def run(self):
        r = self.runtime
        while not r.stop_event.is_set():
            try:
                # Merchant clients can be deliberately hidden by their
                # embedded host.  Match only their saved exact identity; do
                # not broaden the normal visible-only farmer catalog.
                clients = r.merchant_windows()
                for character in CHARACTERS:
                    identity = r.journal.get(character, 'last_identity')
                    client = next((c for c in clients if c.identity == identity), None)
                    cached = self.readers.get(character)
                    if cached and (client is None or cached.adapter.identity != identity):
                        cached.close(); self.readers.pop(character, None)
                        self.unknown_since.pop(character, None)
                    if client is None: continue
                    if character not in self.readers:
                        # Separate handle: never wait on the automation's memory lock.
                        try:
                            self.readers[character] = r.observer_factory(client, character)
                        except Exception:
                            started = self.unknown_since.setdefault(character, time.monotonic())
                            if time.monotonic() - started >= 2:
                                from conquest.merchants.recovery_safety import active, observe
                                coordinator=getattr(r,'coordinator',None)
                                if (coordinator and hasattr(coordinator,'manual_session_blocked')
                                        and coordinator.manual_session_blocked(character)):continue
                                if active(r,character): observe(r,character,identity)
                                else: protect(r, character, identity, 'Merchant safety reader unavailable for two seconds')
                            continue
                    try:
                        self.check(character, self.readers[character])
                    except Exception:
                        # Preserve the failed-close record and retry next tick.
                        pass
            except Exception:
                pass
            from conquest.discord_notify import write_json
            try:
                write_json(state_path('reports/merchants/market-guard.json'), {'running':True, 'updated_at':time.time(),
                    'interval_seconds':0.25, 'unknown_grace_seconds':2, 'observations':self.observations})
            except OSError: pass
            r.stop_event.wait(0.25)
        for observer in self.readers.values(): observer.close()
