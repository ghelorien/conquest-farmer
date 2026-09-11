"""Expand an idle hunt within a reusable route's search limits."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from conquest.region_rotation import HuntingRegion


def patrol_step(supervisor,position,route,index,boundary,*,chase=True):
    """Keep the sweep cursor on the reachable destination selected by routing."""
    ordered=route[index+1:]+route[:index]
    step=supervisor.patrol_step(position,route[index],boundary,alternatives=ordered,**({'chase':False} if not chase else {}))
    destination=getattr(supervisor,'patrol_destination',None)
    if destination is not None:
        index=next((i for i,p in enumerate(route) if tuple(p)==tuple(destination)),index)
    return step,index


class PatrolSearchConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    idle_seconds: float = Field(default=7.5,ge=5,le=10)
    expansion_tiles: int = Field(default=12,ge=1,le=24)
    maximum_expansions: int = Field(default=4,ge=1,le=8)
    sweep_spacing_tiles: int = Field(default=24,ge=8,le=32)
    regions: tuple[HuntingRegion,...] = ()

    @model_validator(mode='after')
    def validate_regions(self):
        if self.regions and (not 2<=len(self.regions)<=8 or len({r.name for r in self.regions})!=len(self.regions)):
            raise ValueError('Rotation needs two to eight distinctly named regions')
        return self


class AdaptivePatrol:
    def __init__(self,boundary,policy,now,map_size):
        self.boundary=tuple(boundary)
        self.policy,self.map_size=policy,map_size
        self.last_activity=now
        self.expansions=0

    def attacked(self,now):
        self.last_activity=now

    def expand(self,now):
        if (now-self.last_activity<self.policy.idle_seconds
                or self.expansions>=self.policy.maximum_expansions):
            return None
        x0,y0,x1,y1=self.boundary
        step=self.policy.expansion_tiles
        width,height=self.map_size
        new=(max(0,x0-step),max(0,y0-step),min(width-1,x1+step),min(height-1,y1+step))
        self.last_activity=now
        if new==self.boundary:
            return None
        self.boundary=new
        self.expansions+=1
        return new

    def patrol_points(self,terrain):
        x0,y0,x1,y1=self.boundary
        # Cover the interior too: a perimeter circuit can permanently miss
        # every spawn more than attack range from the boundary.
        def axis(low,high):
            if high-low<=8:return [(low+high)//2]
            values=list(range(low+4,high-3,self.policy.sweep_spacing_tiles))
            if values[-1]!=high-4:values.append(high-4)
            return values
        xs,ys=axis(x0,x1),axis(y0,y1)
        sweep=[(x,y) for row,y in enumerate(ys) for x in (xs if row%2==0 else xs[::-1])]
        points=[]
        for x,y in sweep:
            for radius in range(8):
                candidates=((x+dx,y+dy) for dx in range(-radius,radius+1)
                            for dy in range(-radius,radius+1) if max(abs(dx),abs(dy))==radius)
                point=next((p for p in candidates if x0<=p[0]<=x1 and y0<=p[1]<=y1 and terrain.walkable(p)),None)
                if point is not None:
                    if point not in points:points.append(point)
                    break
        return tuple(points)
