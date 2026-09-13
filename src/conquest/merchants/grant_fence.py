"""Fence action-capable work across grant changes without holding input locks.

Capture at worker/queue creation, not when delayed work finally executes. Idle
work has its own generation too: it cannot cross activation or revocation.
This is an input permission boundary, not a transaction completion receipt.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import math
import threading
import time
import uuid

from conquest.capture import CaptureUnavailable


@dataclass(frozen=True)
class GrantToken:
    epoch: str
    generation: int
    request_id: str | None
    revision: int | None
    expires_at: float | None
    scope: str
    farmer_profile_id: str | None = None


class GrantFence:
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self.epoch = uuid.uuid4().hex
        self.generation = 0
        self.lock = threading.RLock()  # Never the coordinator's action lock.
        self.local = threading.local()
        self.active = None
        self.requests = {}
        self.workers = {}
        self.actions = {}

    def _stack(self, name):
        stack = getattr(self.local, name, None)
        if stack is None:
            stack = []
            setattr(self.local, name, stack)
        return stack

    def _bound(self):
        actions = self._stack('actions')
        if actions:
            return actions[-1]
        workers = self._stack('workers')
        return workers[-1]['token'] if workers else None

    def capture(self):
        """Preserve a caller's old binding; never silently upgrade its grant."""
        with self.lock:
            return self._bound() or self.active or GrantToken(
                self.epoch, self.generation, None, None, None, 'idle')

    def activate(self, request_id, revision, expires_at, *, scope='hunting', farmer_profile_id=None):
        if (not isinstance(request_id, str) or not request_id
                or type(revision) is not int or type(expires_at) not in (int, float)
                or not math.isfinite(expires_at) or expires_at <= self.clock()):
            raise ValueError('Invalid current input grant')
        with self.lock:
            previous = self.requests.get(request_id)
            if previous:
                token = previous['token']
                if (not previous['revoked'] and token == self.active
                        and (token.revision, token.expires_at, token.scope, token.farmer_profile_id)
                        == (revision, expires_at, scope, farmer_profile_id)):
                    return token
                raise ValueError('Input grant request cannot be reused or extended')
            if self.active is not None:
                raise CaptureUnavailable('Release the current input grant first')
            if self.actions or any(not self._quiescence(row['token'])['released']
                                   for row in self.requests.values() if row['revoked']):
                raise CaptureUnavailable('Previous input work has not quiesced')
            self.generation += 1
            token = GrantToken(self.epoch, self.generation, request_id, revision,
                               expires_at, scope, farmer_profile_id)
            self.active = token
            self.requests[request_id] = {'token': token, 'revoked': False}
            return token

    def _valid(self, token):
        if not isinstance(token, GrantToken) or token.epoch != self.epoch:
            raise CaptureUnavailable('Input grant belongs to another app session')
        if token.request_id is None:
            if self.active is not None or token.generation != self.generation:
                raise CaptureUnavailable('Idle input permission changed')
        elif token != self.active or token.expires_at <= self.clock():
            raise CaptureUnavailable('Input grant was revoked or expired')

    def check(self, token=None):
        with self.lock:
            workers = self._stack('workers')
            if any(not worker['action_capable'] for worker in workers):
                raise CaptureUnavailable('Read-only reconciliation cannot send input')
            token = self.capture() if token is None else token
            try:
                self._valid(token)
                # Explicit callback tokens cannot escape an outer stale binding.
                bound = self._bound()
                if bound is not None and bound != token:
                    self._valid(bound)
            except CaptureUnavailable:
                for worker in workers:
                    worker['acknowledged'] = True
                raise
            return token

    @contextmanager
    def bind_worker(self, token, *, action_capable=True):
        """Register a worker iteration; read-only recovery may use expired tokens."""
        worker = {'token': token, 'action_capable': action_capable,
                  'acknowledged': not action_capable}
        with self.lock:
            if action_capable:
                self.check(token)
            self.workers[id(worker)] = worker
            self._stack('workers').append(worker)
        try:
            yield token
        finally:
            with self.lock:
                self._stack('workers').pop()
                self.workers.pop(id(worker), None)

    def mark_read_only(self):
        """Permanently downgrade current bindings before a read-only tail."""
        with self.lock:
            for worker in self._stack('workers'):
                worker.update(action_capable=False, acknowledged=True)

    @contextmanager
    def input_action(self, token=None):
        """Keep release pending through native release events and UI callbacks."""
        with self.lock:
            token = self.capture() if token is None else token
            self.check(token)
            action = object()
            self.actions[id(action)] = token
            self._stack('actions').append(token)
        try:
            yield token
        finally:
            with self.lock:
                self._stack('actions').pop()
                self.actions.pop(id(action), None)

    def guard_callback(self, token, callback):
        """Guard both execution and quiescence of a queued surface mutation."""
        @wraps(callback)
        def guarded(*args, **kwargs):
            with self.input_action(token):
                return callback(*args, **kwargs)
        return guarded

    def _resolve(self, token_or_request_id):
        key = (token_or_request_id.request_id if isinstance(token_or_request_id, GrantToken)
               else token_or_request_id)
        row = self.requests.get(key)
        if row is None or (isinstance(token_or_request_id, GrantToken)
                           and row['token'] != token_or_request_id):
            raise ValueError('Unknown input grant')
        return row

    def revoke(self, token_or_request_id):
        """Idempotent, nonblocking fence; stale release never touches a new grant."""
        with self.lock:
            row = self._resolve(token_or_request_id)
            if not row['revoked']:
                row['revoked'] = True
                if self.active == row['token']:
                    self.active = None
                    self.generation += 1
            return self._quiescence(row['token'])

    def invalidate(self):
        """Manual Stop/control changes also invalidate queued idle callbacks."""
        with self.lock:
            if self.active is not None:
                return self.revoke(self.active)
            self.generation += 1
            return None

    def _quiescence(self, token):
        actions = sum(value == token for value in self.actions.values())
        workers = sum(worker['token'] == token and worker['action_capable']
                      and not worker['acknowledged'] for worker in self.workers.values())
        revoked = self.requests[token.request_id]['revoked']
        return {'request_id': token.request_id, 'released': revoked and not actions and not workers,
                'revoked': revoked, 'active_actions': actions, 'waiting_workers': workers}

    def quiescence(self, token_or_request_id):
        with self.lock:
            return self._quiescence(self._resolve(token_or_request_id)['token'])
