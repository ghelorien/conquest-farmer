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

    def reserve(self, request_id, *, town=False):
        if not town and not self.due():
            return False
        now = self.clock()
        # Reserve before parking/input: crashes or failed safe-spot searches
        # cannot generate repeated interruptions of the hunting loop.
        write_json(self.path, {'request_id':request_id, 'last_check':now,
            'next_check':now+INTERVAL, 'phase':'preparing', 'town':town})
        return True

    def started(self):
        state = self.state()
        now = self.clock()
        state.update(phase='working', last_check=now, next_check=now+INTERVAL,
                     deadline=now+WORK_SECONDS)
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
        and character.get('qualification', {}).get('login')))


def service_window(loop, *, town=False):
    """Run on the existing route controller, retaining its exclusive ownership."""
    policy = read_json(POLICY)
    if not policy.get('parity_verified') or not policy.get('hunting_handoffs_enabled'):
        return False
    from conquest.merchants.bridge import request as merchant
    from conquest.worker import request
    from conquest.safe_reload import park, clear_observation
    from conquest.overnight import OvernightStopped
    windows = WorkWindows()
    if not town and not windows.due():
        return False
    try:
        status = merchant({'action':'status'})
    except (OSError, ValueError):
        return False
    if town and any(c.get('connected') for c in status.get('characters',{}).values()):
        request_id='restock-refill:'+str(time.time_ns())
        merchant({'action':'refill-check','request_id':request_id})
    else:
        request_id = status.get('handoff_requested')
    if not request_id or not any(service_candidate(c) for c in status.get('characters',{}).values()):
        return False
    before = loop.health()
    control = before['embedded_controls']['control']
    if before['embedded_controls'].get('manual_mouse'):
        return False
    if not windows.reserve(request_id, town=town):
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
            parked=park(loop,Cancellation(),lambda _:None,seconds=12,allow_town_retreat=False)
        except ValueError:
            windows.finish('unsafe_deferred')
            return False
        check()
        if not clear_observation(loop.health()):
            windows.finish('unsafe_deferred')
            return False
        deadline=windows.started()
        merchant({'action':'handoff-grant','request_id':request_id,'revision':revision,
                  'expires_at':deadline,'safe':True})
        granted=True
        loop.record('merchant_work_started',activity='Safe merchant refill · up to 15 seconds',deadline=deadline)
        while time.time()<deadline:
            check()
            health=loop.health()
            if not clear_observation(health):
                break
            status=merchant({'action':'status'})
            if not status.get('handoff_requested'):
                break
            time.sleep(.2)
        windows.finish('completed')
        return True
    finally:
        loop.check_stop=original_check
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
