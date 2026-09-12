"""One process-wide input owner; an OS lock also excludes other app instances."""
from contextlib import contextmanager
from pathlib import Path
import threading
import time
from functools import wraps
from conquest.capture import CaptureUnavailable


class InputCoordinator:
    def __init__(self, safe_to_yield=lambda: False, manual_active=lambda: False, path='.runtime/merchant-input.lock'):
        self.safe_to_yield = safe_to_yield
        self.manual_active = manual_active
        self.path = Path(path)
        self.lock = threading.RLock()
        self.owner = None
        self.thread = None
        self.stopped = False
        self.handoff_until = 0
        self.owner_allowed = lambda character:True
        self.on_acquire = lambda character:None
        self.on_release = lambda character:None

    def stop(self):
        # Do not wait behind an in-flight transaction to record the stop.
        self.stopped = True

    def resume(self):
        self.stopped = False

    def check(self):
        if self.stopped or self.manual_active():
            raise CaptureUnavailable('Automation stopped or manual input active')
        if self.owner and self.thread != threading.get_ident():
            raise CaptureUnavailable('Another character owns game input')
        if self.owner and not self.owner_allowed(self.owner):
            raise CaptureUnavailable('Character was paused during input')
        if self.owner and self.owner != 'Farmer' and not self.safe_to_yield():
            raise CaptureUnavailable('Farmer handoff was revoked or expired')

    @contextmanager
    def lease(self, character):
        if not self.lock.acquire(blocking=False):
            raise CaptureUnavailable('Waiting for input owner')
        file = None
        owned = False
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
            owned = True
            self.on_acquire(character)
            self.check()
            yield self
        finally:
            if owned:
                self.owner = self.thread = None
            if file:
                file.close()
            self.lock.release()
            if owned:
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
    # Route controllers may run in another process. They honor the same lease.
    path = _coordinator.path if _coordinator else Path('.runtime/merchant-input.lock')
    if path.exists() and not getattr(_scope,'active',False):
        import msvcrt
        with path.open('r+b') as file:
            try:
                msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as error:
                raise CaptureUnavailable('Merchant owns foreground input; farmer must wait') from error
            msvcrt.locking(file.fileno(),msvcrt.LK_UNLCK,1)


@contextmanager
def input_scope():
    """Hold the shared lease across a complete farmer click/drag/key action."""
    if getattr(_scope,'active',False) or (_coordinator and _coordinator.owner and _coordinator.thread==threading.get_ident()):
        yield
        return
    coordinator = _coordinator
    if coordinator:
        coordinator.check()
        if not coordinator.lock.acquire(blocking=False):
            raise CaptureUnavailable('Waiting for the current input action')
    file = None
    owned = False
    try:
        path = coordinator.path if coordinator else Path('.runtime/merchant-input.lock')
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
        _scope.active = owned = True
        yield
    finally:
        if owned:
            _scope.active = False
            if coordinator:
                coordinator.owner = coordinator.thread = None
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
