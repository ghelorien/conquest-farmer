"""Durable farmer work windows; never accumulate missed merchant intervals."""
from conquest.character_context import state_path
import time
from pathlib import Path
from conquest.discord_notify import read_json, write_json

INTERVAL = 900
WORK_SECONDS = 15
POLICY = Path('profiles/merchant-deliveries.json')
STATE = Path(state_path('.runtime/merchant-handoff.json'))


class WorkWindows:
    def __init__(self, path=STATE, *, clock=time.time):
        self.path, self.clock = Path(path), clock

    def state(self):
        return read_json(self.path)

    def due(self):
        return self.clock() >= self.state().get('next_check', 0)

    def reserve(self, request_id, *, town=False, urgent=False, visit=None):
        if not town and not urgent and not self.due():
            return False
        now = self.clock()
        old=self.state()
        if visit and (not town or not now<visit['deadline']<=now+60):
            return False
        same_visit=visit and old.get('visit_id')==visit['visit_id']
        # Reserve before parking/input: crashes or failed safe-spot searches
        # cannot generate repeated interruptions of the hunting loop.
        state={'request_id':request_id, 'last_check':old.get('last_check',now) if same_visit else now,
            'next_check':old['next_check'] if same_visit else now+INTERVAL,
            'phase':'preparing', 'town':town,'last_attempt_at':old.get('last_attempt_at',now) if same_visit else now}
        if visit:state.update(visit_id=visit['visit_id'],deadline=visit['deadline'],
                              farmer_profile_id=visit['farmer_profile_id'],scope='market_visit')
        write_json(self.path,state)
        return True

    def started(self):
        state = self.state()
        now = self.clock()
        state.update(phase='working',deadline=state.get('deadline',now+WORK_SECONDS))
        write_json(self.path, state)
        return state['deadline']

    def finish(self, phase):
        state = self.state()
        state.update(phase=phase, finished_at=self.clock())
        write_json(self.path, state)


def resumable(health, proof, revision):
    """A manual control change or a different client always wins."""
    data = health.get('embedded_controls', {})
    control = data.get('control', {})
    return (health.get('target') == proof.get('target')
        and control.get('revision') == revision
        and not control.get('enabled') and not control.get('paused'))


def service_candidate(character):
    """Recovery needs an input window before it can produce a fresh snapshot."""
    return bool(character.get('connected') or (
        character.get('enabled') and character.get('credentials_saved')
        and (character.get('qualification', {}).get('login') or
             (character.get('recovery_safety') or {}).get('active'))))


def service_window(loop, *, town=False):
    """Run on the existing route controller, retaining its exclusive ownership."""
    policy = read_json(POLICY)
    if not policy.get('parity_verified') or (not town and not policy.get('hunting_handoffs_enabled')):
        return False
    from conquest.merchants.bridge import request as merchant
    from conquest.worker import request
    from conquest.safe_reload import park, clear_observation
    from conquest.overnight import OvernightStopped
    windows = WorkWindows()
    try:
        status = merchant({'action':'status'})
    except (OSError, ValueError):
        return False
    urgent = urgent_recovery(status)
    if not town and not urgent and not windows.due():
        return False
    before = loop.health()
    control = before['embedded_controls']['control']
    if before['embedded_controls'].get('manual_mouse'):
        return False
    visit = None
    if town and (before['embedded_controls'].get('life') or {}).get('map_id') == 1036:
        from conquest.merchants.service_visit import MarketVisit, parent_visit
        visit = MarketVisit().begin(parent=parent_visit())
        if time.time() >= visit['deadline']:
            return False  # A refill-only entry cannot renew a used delivery visit.
    if town and any(c.get('connected') for c in status.get('characters',{}).values()):
        request_id='restock-refill:'+str(time.time_ns())
        merchant({'action':'refill-check','request_id':request_id})
    else:
        request_id = status.get('handoff_requested')
    if not request_id or not any(service_candidate(c) for c in status.get('characters',{}).values()):
        return False
    if not windows.reserve(request_id, town=town, urgent=urgent, visit=visit):
        return False
    was_enabled, phase = control['enabled'], loop.phase
    loop.stop_farm()
    stopped = loop.health()
    revision = stopped['embedded_controls']['control']['revision']
    proof = {'target':stopped['target']}
    granted = False
    released = True
    manually_cancelled = False
    original_check = loop.check_stop
    previous_deadline = getattr(loop, 'market_service_deadline', None)
    if visit:loop.market_service_deadline = visit['deadline']

    def check():
        nonlocal manually_cancelled
        original_check()
        import ctypes
        if ctypes.windll.user32.GetAsyncKeyState(0x7a)&0x8000:
            manually_cancelled=True
            raise OvernightStopped('Merchant work paused with F11')
        current = request(loop.info,'health')
        if not resumable(current, proof, revision):
            raise OvernightStopped('Manual control changed during merchant work')

    class Cancellation:
        def is_set(self):
            check()
            return False

    loop.check_stop = check
    try:
        loop.phase='merchant_handoff'
        loop.record('merchant_safe_spot', activity='Finding a safe spot for merchant refill')
        try:
            seconds=min(12,max(0,visit['deadline']-time.time())) if visit else 12
            if seconds<=0:
                windows.finish('paused_budget')
                return False
            parked=park(loop,Cancellation(),lambda _:None,seconds=seconds,allow_town_retreat=False)
        except ValueError:
            windows.finish('unsafe_deferred')
            return False
        check()
        if not clear_observation(loop.health()):
            windows.finish('unsafe_deferred')
            return False
        deadline=windows.started()
        if deadline<=time.time():
            windows.finish('paused_budget')
            return False
        command={'action':'handoff-grant','request_id':request_id,'revision':revision,
                 'expires_at':deadline,'safe':True}
        if visit:command.update(scope='market_visit',visit_id=visit['visit_id'])
        # A lost acknowledgement may still have granted input. Revoke in finally.
        granted=True
        merchant(command)
        loop.record('merchant_work_started',activity=('Safe merchant refill within this Market visit'
                    if visit else 'Safe merchant refill · up to 15 seconds'),deadline=deadline)
        while time.time()<deadline:
            check()
            health=loop.health()
            if not clear_observation(health):
                break
            status=merchant({'action':'status'})
            if not status.get('handoff_requested'):
                break
            time.sleep(.2)
        windows.finish('paused_budget' if time.time()>=deadline else 'released')
        return True
    finally:
        loop.check_stop=original_check
        loop.market_service_deadline=previous_deadline
        if granted:
            # Revocation blocks further input. Read-only reconciliation can
            # finish before the input lease is released; never race the farmer.
            released=False
            until=time.monotonic()+12
            while time.monotonic()<until:
                result=merchant({'action':'handoff-release','request_id':request_id})
                if result.get('released'):
                    released=True
                    break
                original_check()
                time.sleep(.1)
        loop.phase=phase
        original_check()
        current=loop.health()
        if not released:
            raise ValueError('Merchant input did not release; farmer remains protected and stopped')
        if was_enabled and not manually_cancelled and resumable(current,proof,revision):
            loop.focus(current)
            request(loop.info,'controls',{'enabled':True})
            loop.record('merchant_work_finished',activity='Hunting resumed after merchant work')


def urgent_recovery(status):
    request=str(status.get('handoff_requested',''))
    if not request.startswith(('merchant-recovery:', 'merchant-return:')):return False
    parts=request.split(':')
    state=status.get('characters',{}).get(parts[1] if len(parts)>1 else '',{})
    if (state.get('recovery_safety') or {}).get('active'):return True
    returning=state.get('shop_return') or {}
    return bool(returning.get('phase') not in (None,'complete','operator_overridden','needs_attention')
                and (state.get('snapshot') or {}).get('map_id')!=1036)
