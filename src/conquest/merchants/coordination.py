"""One process-wide input owner; an OS lock also excludes other app instances."""
from conquest.character_context import state_path, is_farmer_owner, ProfileMap
from contextlib import contextmanager, nullcontext
from pathlib import Path
import threading
import time
from functools import wraps
from conquest.capture import CaptureUnavailable

INPUT_LOCK = Path(state_path('.runtime/merchant-input.lock'))


class InputAcquisitionBusy(CaptureUnavailable):
    """The local input mutex was unavailable; the input body never started."""


class InputCoordinator:
    def __init__(self, safe_to_yield=lambda: False, manual_active=lambda: False, path=None):
        self.safe_to_yield = safe_to_yield
        self.manual_active = manual_active
        self.path = INPUT_LOCK if path is None else Path(path)
        self.lock = threading.RLock()
        self.owner = None
        self.thread = None
        self.purpose = None
        self.stopped = False
        self.surface_blocks=ProfileMap()
        self.handoff_until = 0
        self.owner_allowed = lambda character:True
        self.on_acquire = lambda character:None
        self.on_release = lambda character:None
        # Production enables this explicitly. Legacy callers retain their
        # existing idle/manual policy; fenced workers also pin its generation.
        self.fence = None
        # Durable manual-session state is installed by MerchantRuntime. It is
        # separate from physical mouse ownership and never changes saved intent.
        self.manual_sessions = {}
        self.manual_journal = None
        self.manual_farmer_target = 'Farmer'
        self._probe_abort_capability = None

    @contextmanager
    def probe_abort_scope(self, validate):
        """Ephemeral close-only worker scope; a purpose string grants nothing.

        The abort module supplies a validator bound to its fsynced journal,
        exact two holds, operator digest and control revision. It is never
        retained on restart or shared with another thread.
        """
        with self.lock:
            if self._probe_abort_capability is not None:
                raise CaptureUnavailable('Another probe abort is active')
            self._probe_abort_capability = (threading.get_ident(), validate)
            try:
                validate()
                yield
            finally:
                self._probe_abort_capability = None

    def probe_abort_authorized(self, target):
        capability = self._probe_abort_capability
        if capability is None or capability[0] != threading.get_ident():return False
        try:return target in capability[1]()
        except (ValueError,OSError,KeyError,TypeError,AttributeError):return False

    def set_manual_sessions(self, sessions):
        self.manual_sessions = {row['target_profile_id']: row for row in sessions}

    def manual_session_blocked(self, character=None, *, purpose=None):
        rows = self.manual_sessions
        if any(row.get('ever_approved') and row.get('holds_automation') for row in rows.values()):
            return True
        key = getattr(character, 'profile_id', character)
        if is_farmer_owner(character):
            from conquest.character_context import current
            context=current()
            key=context.profile.id if context and context.profile.role=='Farmer' else self.manual_farmer_target
        row = rows.get(key)
        if not row or not row.get('holds_automation'):
            return False
        if purpose == 'delivery_probe_abort' and self.probe_abort_authorized(key):
            return False
        # The only exception is the independently qualified native decline of
        # a still-unapproved request with a durable timeout/rejection intent.
        return not (purpose == 'manual_decline' and row.get('phase') == 'approval_pending'
                    and row.get('request_state') == 'decline_pending')

    def stop(self):
        # Do not wait behind an in-flight transaction to record the stop.
        self.stopped = True
        if self.fence is not None:
            self.fence.invalidate()

    def resume(self):
        self.stopped = False

    def check(self):
        if self.fence is not None:
            self.fence.check()
        if self.owner and self.surface_blocks.get(self.owner):
            raise CaptureUnavailable('Client surface needs reattachment and input qualification')
        if self.stopped or self.manual_active():
            raise CaptureUnavailable('Automation stopped or manual input active')
        if self.manual_session_blocked(self.owner, purpose=self.purpose):
            raise CaptureUnavailable('Manual visitor session holds automation input')
        if self.owner and self.thread != threading.get_ident():
            raise CaptureUnavailable('Another character owns game input')
        if self.owner and not self.owner_allowed(self.owner):
            raise CaptureUnavailable('Character was paused during input')
        if self.owner and not is_farmer_owner(self.owner) and not self.safe_to_yield():
            raise CaptureUnavailable('Farmer handoff was revoked or expired')

    @contextmanager
    def lease(self, character, *, purpose=None):
        with self.fence.input_action() if self.fence is not None else nullcontext():
            with self._lease(character, purpose=purpose) as lease:
                yield lease

    @contextmanager
    def _lease(self, character, *, purpose=None):
        if not self.lock.acquire(blocking=False):
            raise CaptureUnavailable('Waiting for input owner')
        file = None
        owned = False
        prepared = False
        try:
            self.check()
            if self.owner is not None or not self.safe_to_yield():
                raise CaptureUnavailable('Waiting for a safe farmer handoff')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            file = self.path.open('a+b')
            file.seek(0,2)
            if file.tell() == 0:
                file.write(b'0');file.flush()
            file.seek(0)
            import msvcrt
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise CaptureUnavailable('Another app owns merchant input') from error
            self.owner, self.thread = character, threading.get_ident()
            self.purpose = purpose
            owned = True
            self.check()  # Denied work must not focus or restore a merchant surface.
            prepared = True
            self.on_acquire(character)
            self.check()
            yield self
        finally:
            if owned:
                self.owner = self.thread = None
                self.purpose = None
            if file:
                file.close()
            self.lock.release()
            if prepared:
                self.on_release(character)


_coordinator = None
_scope = threading.local()


def install(coordinator):
    global _coordinator
    _coordinator = coordinator


def check_input():
    if _coordinator:
        _coordinator.check()
        if _coordinator.owner:
            return
        if _coordinator.manual_session_blocked('Farmer'):
            raise CaptureUnavailable('Manual visitor session holds farmer input')
    # Route controllers may run in another process. They honor the same lease.
    path = _coordinator.path if _coordinator else INPUT_LOCK
    if path.exists() and not getattr(_scope,'active',False):
        import msvcrt
        with path.open('r+b') as file:
            try:
                msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as error:
                raise CaptureUnavailable('Merchant owns foreground input; farmer must wait') from error
            msvcrt.locking(file.fileno(),msvcrt.LK_UNLCK,1)


@contextmanager
def input_scope(*, purpose=None):
    fence = getattr(_coordinator, 'fence', None)
    with fence.input_action() if fence is not None else nullcontext():
        with _input_scope(purpose=purpose):
            yield


@contextmanager
def _input_scope(*, purpose=None):
    """Hold the shared lease across a complete farmer click/drag/key action."""
    if getattr(_scope,'active',False) or (_coordinator and _coordinator.owner and _coordinator.thread==threading.get_ident()):
        if _coordinator:_coordinator.check()
        yield
        return
    coordinator = _coordinator
    if coordinator:
        coordinator.check()
        if coordinator.manual_session_blocked('Farmer',purpose=purpose):
            raise CaptureUnavailable('Manual visitor session holds farmer input')
        if not coordinator.lock.acquire(blocking=False):
            # This is the sole retryable acquisition boundary: no owner/file
            # lease, cursor move or input body has been entered. Other input
            # failures may be post-submission and must not use this type.
            raise InputAcquisitionBusy('Waiting for the current input action')
    file = None
    owned = False
    try:
        path = coordinator.path if coordinator else INPUT_LOCK
        path.parent.mkdir(parents=True,exist_ok=True)
        file = path.open('a+b')
        file.seek(0,2)
        if file.tell()==0:
            file.write(b'0');file.flush()
        file.seek(0)
        import msvcrt
        try:
            msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as error:
            raise CaptureUnavailable('Another character owns foreground input') from error
        if coordinator:
            coordinator.owner,coordinator.thread = 'Farmer',threading.get_ident()
            coordinator.purpose = purpose
        _scope.active = owned = True
        if coordinator:coordinator.check()
        yield
    finally:
        if owned:
            _scope.active = False
            if coordinator:
                coordinator.owner = coordinator.thread = None
                coordinator.purpose = None
        if file:
            file.close()
        if coordinator:
            coordinator.lock.release()


def coordinated_input(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        with input_scope():
            return function(*args,**kwargs)
    return wrapped


def manual_session_blocked(character=None):
    return bool(_coordinator and _coordinator.manual_session_blocked(character))


def manual_replan_journal():
    return getattr(_coordinator, 'manual_journal', None)


def observe_manual_farmer(observer):
    callback=getattr(_coordinator,'manual_farmer_boundary',None)
    return callback(observer) if callback else False
