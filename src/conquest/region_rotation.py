"""Rotate saved hunting regions using qualified memory target observations."""
from collections import deque
from pydantic import BaseModel, ConfigDict, Field, model_validator


class HuntingRegion(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    name: str
    boundary: tuple[int,int,int,int]
    patrol: tuple[tuple[int,int],...] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_points(self):
        if not all(self.contains(p) for p in self.patrol):
            raise ValueError('Region patrol must stay inside its boundary')
        return self

    def contains(self,position):
        l,t,r,b=self.boundary;x,y=position
        return l<=x<=r and t<=y<=b


class RegionRotation:
    def __init__(self,regions,*,window=20,empty_fraction=.6,idle_seconds=8):
        self.regions=regions;self.index=0;self.samples=deque()
        self.window=window;self.threshold=empty_fraction;self.idle_seconds=idle_seconds
        self.entered=None;self.last_occupied=None

    @property
    def region(self):return self.regions[self.index]

    def observe(self,now,position,occupied,*,available=True):
        if self.samples and now-self.samples[-1][0]>2:
            self.samples.clear();self.entered=None;self.last_occupied=None
        # Unknown reads and inter-region travel never count as empty hunting.
        if not available or not self.region.contains(position):
            self.samples.clear();self.entered=None;self.last_occupied=None
            return None
        if self.entered is None:self.entered=self.last_occupied=now
        if occupied:self.last_occupied=now
        if not self.samples or now-self.samples[-1][0]>=.25:
            self.samples.append((now,not occupied))
        while self.samples and self.samples[0][0]<now-self.window:self.samples.popleft()
        if occupied or now-self.entered<self.idle_seconds or len(self.samples)<8:return None
        fraction=sum(empty for _,empty in self.samples)/len(self.samples)
        idle=now-self.last_occupied
        if fraction<self.threshold and idle<self.idle_seconds:return None
        previous=self.region.name
        self.index=(self.index+1)%len(self.regions)
        self.samples.clear();self.entered=None;self.last_occupied=None
        return {'from_region':previous,'to_region':self.region.name,
                'empty_fraction':fraction,'idle_seconds':idle,
                'activity':f'Rotating from {previous} to {self.region.name}: few nearby targets'}
