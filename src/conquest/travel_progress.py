"""Bound recovery by observed remaining route distance, never by clicks."""
import time


class TravelStalled(ValueError):
    def __init__(self,message,*,code='no_progress'):
        super().__init__(message);self.code=code


class ProgressDeadline:
    def __init__(self,*,clock=time.monotonic):
        self.clock=clock;self.last_progress=clock();self.best=None
        self.recovery_started=None;self.attempts=0

    def observe(self,remaining):
        now=self.clock()
        if self.best is None or remaining<self.best:
            self.best=remaining;self.last_progress=now
            self.recovery_started=None;self.attempts=0
            return False
        if now-self.last_progress<5:return False
        if self.recovery_started is None:self.recovery_started=now
        if self.attempts>=2 or now-self.recovery_started>=15:
            raise TravelStalled('Route made no improving progress after bounded recovery')
        self.attempts+=1
        self.last_progress=now  # Space corrections; it does not extend the wall deadline.
        return True
