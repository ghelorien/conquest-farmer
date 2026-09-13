"""Five-second progress deadline only after an unexpected connection loss."""
import time

KEY = 'recovery_safety'


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
