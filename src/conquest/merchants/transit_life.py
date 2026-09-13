"""Retry only a torn life observation; never relax character or alive checks."""
import time
from conquest.capture import CaptureUnavailable


def stable_life(*args,**kwargs):
    from conquest.memory_life import read_life
    deadline=time.monotonic()+.3
    while True:
        try:return read_life(*args,**kwargs)
        except ValueError as error:
            if str(error)!='Life state changed during observation':raise
            if time.monotonic()>=deadline:
                raise CaptureUnavailable('Waiting for stable merchant life observation') from error
            time.sleep(.02)
