"""Calibrated drop-label matching and evidence-based pickup accounting."""
from dataclasses import dataclass
import math

import cv2
import numpy as np


@dataclass(frozen=True)
class Drop:
    name: str
    type_id: int
    point: tuple[int, int]
    position: tuple[int, int]


def nearby_drops(frame, template, position, boundary, max_distance=180, *,
                 calibrated_size=(1584,861), player_anchor=(792,432)):
    width,height = calibrated_size
    if (frame.shape != (height,width,3) or template.shape != (13,63)
            or not (0 < player_anchor[0] < width and 0 < player_anchor[1] < height)):
        raise ValueError("Loot observation geometry is not calibrated")
    mask = cv2.inRange(frame,(0,150,150),(100,255,255))
    scores = cv2.matchTemplate(mask,template,cv2.TM_CCOEFF_NORMED)
    ys,xs = np.where(scores >= .97)
    found = []
    for x,y in zip(xs,ys):
        point = (int(x+31),int(y+30))
        dx,dy = point[0]-player_anchor[0],point[1]-player_anchor[1]
        mapped = (round(position[0]+(dx/32+dy/16)/2),
                  round(position[1]+(dy/16-dx/32)/2))
        if (math.hypot(dx,dy) > max_distance
                or not (100<point[0]<min(1380,width-80) and 180<point[1]<min(735,height-126))
                or (point[0]<615 and point[1]>550)
                or not (boundary[0]<=mapped[0]<=boundary[2] and boundary[1]<=mapped[1]<=boundary[3])):
            continue
        if not any(math.dist(point,d.point)<10 for d in found):
            found.append(Drop("Stancher",1000000,point,mapped))
    return sorted(found,key=lambda d: math.dist(d.point,player_anchor))


@dataclass(frozen=True)
class PickupAttempt:
    drop: Drop
    inventory_uids: frozenset[int]
    count_before: int
    issued_at: float

    def outcome(self, inventory, now, timeout=2):
        if (inventory.count(self.drop.type_id)>self.count_before
                and any(i.type_id==self.drop.type_id and i.uid not in self.inventory_uids for i in inventory.items)):
            return "verified"
        return "waiting" if 0<=now-self.issued_at<timeout else "unverified"
