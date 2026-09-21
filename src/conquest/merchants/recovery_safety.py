"""Five-second progress deadline only after an unexpected connection loss."""
import time
import copy
import hashlib
import json

KEY = 'recovery_safety'


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
        row=db.execute('SELECT value FROM state WHERE character=? AND name=?',(character,KEY)).fetchone()
        state=json.loads(row[0]) if row else {}
        if state.get('phase')=='operator_overridden':
            if (state.get('operator_override') or {}).get('confirmation_reference')!=confirmation_reference.strip():
                raise ValueError('Incident was already overridden with a different confirmation')
            return state
        hold_row=db.execute("SELECT value FROM state WHERE character=? AND name='connect_hold'",(character,)).fetchone()
        held=bool(hold_row and json.loads(hold_row[0]))
        if not state or not (state.get('active') or held):
            raise ValueError('No active recovery safety hold is available')
        original=copy.deepcopy(state);digest=hashlib.sha256(json.dumps(original,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if incident_digest is not None and incident_digest != digest:
            raise ValueError('Incident evidence changed; recheck before overriding')
        state.update(active=False,phase='operator_overridden',replan_required=True,
                     operator_override={'operator_confirmed':True,'confirmation_reference':confirmation_reference.strip(),
                      'operator':operator,'confirmed_at':time.time(),'original_phase':original.get('phase'),
                      'original_evidence_digest':digest,'original_state':original,
                      'fresh_evidence':fresh_evidence or {}})
        encoded=json.dumps(state)
        if row:
            db.execute('UPDATE state SET value=? WHERE character=? AND name=?',(encoded,character,KEY))
        else:
            db.execute('INSERT INTO state VALUES(?,?,?)',(character,KEY,encoded))
        hold=db.execute("SELECT value FROM state WHERE character=? AND name='connect_hold'",(character,)).fetchone()
        attention=db.execute("SELECT value FROM state WHERE character=? AND name='attention'",(character,)).fetchone()
        attention_value=json.loads(attention[0]) if attention else None
        # Clear only the hold and attention created by this safety incident.
        # A newer attention record remains authoritative and keeps the global
        # connection hold in place.
        matching_attention = (attention_value is None or
                              isinstance(attention_value,dict) and
                              attention_value.get('kind')=='recovery_stalled')
        if hold and json.loads(hold[0]) is True and matching_attention:
            db.execute("UPDATE state SET value='false' WHERE character=? AND name='connect_hold' AND value=?",
                       (character,hold[0]))
            if attention and attention_value is not None:
                db.execute("UPDATE state SET value='null' WHERE character=? AND name='attention' AND value=?",
                           (character,attention[0]))
    runtime.journal.event(character,'recovery_safety_operator_overridden',
                           original_evidence_digest=digest,
                           confirmation_reference=confirmation_reference.strip())
    return state


def active(runtime, character):
    return bool((runtime.journal.get(character, KEY) or {}).get('active'))


def arm(runtime, character, *, now=None):
    if runtime.journal.get(character, 'connect_hold', False): return
    if active(runtime, character): return
    runtime.journal.set(character, KEY, {'active':True, 'source':'unexpected_connection_loss',
        'phase':'waiting_for_input', 'started_at':time.time() if now is None else now})


def submitted(runtime, character, *, now=None):
    state = runtime.journal.get(character, KEY) or {}
    if state.get('active') and state.get('last_progress') is None:
        state.update(phase='logging_in', last_progress=time.time() if now is None else now)
        runtime.journal.set(character, KEY, state)


def observe(runtime, character, identity, life=None, *, now=None, close=None):
    state = runtime.journal.get(character, KEY) or {}
    if not state.get('active'): return False
    coordinator = getattr(runtime, 'coordinator', None)
    if (coordinator and hasattr(coordinator, 'manual_session_blocked')
            and coordinator.manual_session_blocked(character)):
        return True  # Manual ownership cannot authorize a protective close.
    now = time.time() if now is None else now
    if life is None and state.get('last_progress') is None:
        return True  # Still at login awaiting exclusive input, not exposed in town.
    if life is not None:
        if life.map_id == 1036 and not life.dead_candidate:
            state.update(active=False, phase='market_arrived', arrived_at=now)
            runtime.journal.set(character, KEY, state)
            return True
        # Only improving distance counts, not oscillation or attempted clicks.
        distance = max(abs(life.position[0]-438), abs(life.position[1]-444))
        if (life.map_id == 1002 and not life.dead_candidate and
                (state.get('best_distance') is None or distance < state['best_distance'])):
            state.update(best_distance=distance, last_progress=now, phase='returning_to_market')
    if state.get('last_progress') is None: state['last_progress'] = now
    runtime.journal.set(character, KEY, state)
    if now - state['last_progress'] < 5: return True
    # Explicitly authorized exception: failed recovery, not routine/manual exit.
    from conquest.storage_halt import disconnect_exact_client
    close = close or disconnect_exact_client
    runtime.enable(character, False)
    runtime.set_refill_enabled(character, False)
    runtime.journal.set(character, 'connect_hold', True)
    cancel = runtime.connect_cancel.get(character)
    if cancel: cancel.set()
    state.update(phase='disconnect_pending', reason='Recovery made no verified progress for five seconds',
                 identity=identity, disconnected=False)
    runtime.journal.set(character, KEY, state)
    runtime.journal.set(character, 'attention', {'kind':'recovery_stalled',
        'note':'Recovery stalled for five seconds; protective disconnect pending. Automatic retries stopped.'})
    state['disconnected'] = close(identity) is True
    state.update(active=not state['disconnected'], phase='disconnected' if state['disconnected'] else 'disconnect_pending')
    runtime.journal.set(character, KEY, state)
    if state['disconnected']:
        runtime.journal.set(character, 'attention', {'kind':'recovery_stalled',
            'note':'Recovery stalled for five seconds; merchant disconnected for protection. Approval needed before retrying.'})
        runtime.journal.event(character, 'recovery_safety_disconnect', reason=state['reason'])
    return True
