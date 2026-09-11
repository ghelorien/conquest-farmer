"""Read the experience HUD's candidate fields; measure complete route episodes."""
from dataclasses import dataclass
import struct
import time


@dataclass(frozen=True)
class Experience:
    level:int
    current:int
    required:int
    cumulative:int
    timestamp:float

    @property
    def percent(self):
        return 100*self.current/self.required


def read_experience(session,actual_player):
    # Caller resolves the actual player through the pinned, read-only life reader.
    session.assert_identity()
    def fields():
        return (struct.unpack('<I',session.read(actual_player+0x6e8,4))[0],
                struct.unpack('<Q',session.read(actual_player+0x708,8))[0])
    level,current=fields()
    if not 1<=level<130:
        raise ValueError('Experience HUD changes formula at level 130')
    table=session.read(actual_player+0x1120,level*4)
    requirements=struct.unpack('<'+'I'*level,table)
    if not all(requirements) or current>=requirements[-1]:
        raise ValueError('Experience fields are outside the current level bounds')
    if fields()!=(level,current) or session.read(actual_player+0x1120,level*4)!=table:
        raise ValueError('Experience changed during the sample')
    return Experience(level,current,requirements[-1],sum(requirements[:-1])+current,time.monotonic())


class ExperienceRate:
    def __init__(self):
        self.first=None

    def add(self,reading):
        if self.first is None or reading.level<self.first.level or reading.timestamp<=self.first.timestamp:
            self.first=reading
            return None
        elapsed=reading.timestamp-self.first.timestamp
        # Net experience includes death losses and all automatic travel time.
        return 3600*(reading.cumulative-self.first.cumulative)/elapsed if elapsed>=10 else None
