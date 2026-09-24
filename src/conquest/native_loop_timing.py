"""In-memory timing totals; observes existing work without reading game state."""
from contextlib import contextmanager
from functools import wraps
import time


def optional(method):
    @wraps(method)
    def guarded(self,*args,**kwargs):
        if not self.enabled:return None
        try:return method(self,*args,**kwargs)
        except Exception:
            self.disable()
            return None
    return guarded


class NativeLoopTiming:
    def __init__(self, *, clock=None, interval=60):
        self.clock=clock or time.monotonic
        self.interval=interval
        self.enabled=True
        try:self.started=self.clock()
        except Exception:self.enabled=False;self.started=0
        self.values={}
        self.current=None
        self.moving=False
        self.arrival=None
        self.verified=None

    def disable(self):
        self.enabled=False
        self.current=None

    @optional
    def sample(self,name,seconds):
        row=self.values.setdefault(name,{'count':0,'total_seconds':0.,'max_seconds':0.})
        row['count']+=1
        row['total_seconds']+=seconds
        row['max_seconds']=max(row['max_seconds'],seconds)

    @optional
    def stage(self,name):
        now=self.clock()
        if self.current:
            previous,started=self.current
            self.sample(previous,now-started)
            if self.moving:self.sample('moving_'+previous,now-started)
        self.current=(name,now) if name else None

    @optional
    def begin(self,moving):
        self.stage(None)
        self.moving=bool(moving)
        self.stage('loop_guards')

    @contextmanager
    def measure(self,name):
        previous=None;started=None
        if self.enabled:
            try:
                previous=self.current[0] if self.current else None
                self.stage(None)
                started=self.clock()
            except Exception:self.disable()
        try:
            yield
        finally:
            # Only telemetry is caught. Exceptions from the guarded read/input
            # body must propagate unchanged, including when timing also fails.
            if self.enabled and started is not None:
                try:
                    elapsed=self.clock()-started
                    self.sample(name,elapsed)
                    if self.moving:self.sample('moving_'+name,elapsed)
                    self.stage(previous)
                except Exception:self.disable()

    @optional
    def observe_arrival(self,moving,position):
        if not moving:return
        _,issued,expected=moving
        if tuple(position)==tuple(expected) and (self.arrival is None or self.arrival[0]!=issued):
            now=self.clock()
            self.arrival=(issued,now)
            self.sample('first_exact_arrival_seconds',now-issued)

    @optional
    def verifier(self,moving):
        issued=moving[1]
        if self.arrival and self.arrival[0]==issued and self.verified!=issued:
            now=self.clock()
            self.sample('exact_arrival_to_verifier_seconds',now-self.arrival[1])
            self.sample('exact_arrival_verifier_elapsed_seconds',now-issued)
            self.verified=issued

    @optional
    def finish(self):
        """Call in the iteration finally so early returns/retries are included."""
        self.stage(None)
        now=self.clock()
        if now-self.started<self.interval:return None
        result={'window_seconds':round(now-self.started,6),'timings':self.values}
        self.values={}
        self.started=now
        return result

    @optional
    def flush(self,emit):
        summary=self.finish()
        if summary:emit(summary)
