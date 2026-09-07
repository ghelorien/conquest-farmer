"""Bounded death/revival/return decisions; all input needs fresh observations."""
from dataclasses import dataclass
from enum import StrEnum
import math

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    revive_template: str | None = None
    revive_region: tuple[int, int, int, int] | None = None
    town_position: tuple[int, int] = (430, 380)
    town_tolerance: float = Field(default=10, gt=0, le=20)
    map_id: int = 1002
    return_route: tuple[tuple[int, int], ...] = ()
    revive_wait: float = Field(default=20, ge=20, le=120)
    revive_timeout: float = Field(default=8, ge=3, le=30)
    calibration_timeout: float = Field(default=120, ge=20, le=300)
    return_timeout: float = Field(default=300, ge=30, le=900)
    attempt_limit: int = Field(default=3, ge=1, le=3)
    death_limit: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def validate_calibration(self):
        if bool(self.revive_template) != (self.revive_region is not None):
            raise ValueError("Revive template and its observed region must be provided together")
        if self.revive_region:
            l,t,r,b = self.revive_region
            if not 0 <= l < r <= 1584 or not 0 <= t < b <= 861:
                raise ValueError("Revive search region is outside the calibrated client")
        if self.enabled and not self.return_route:
            raise ValueError("Enabled revival needs a return route")
        if any(not (0 <= x <= 1024 and 0 <= y <= 1024) for x,y in self.return_route):
            raise ValueError("Return route coordinates are outside the supported map")
        return self


def revive_button(frame, template, region):
    """Recognize a calibrated complete button, never the word in chat alone."""
    if frame.shape != (861,1584,3):
        raise ValueError("Uncalibrated revive frame geometry")
    if template is None or template.ndim != 3:
        raise ValueError("A full-color Revive button calibration is required")
    h,w = template.shape[:2]
    if not (15 <= h <= 80 and 40 <= w <= 240) or np.std(template) < 5:
        raise ValueError("Revive button template is not a complete textured button")
    l,t,r,b = region
    if not (0 <= l < r <= 1584 and 0 <= t < b <= 861 and r-l >= w and b-t >= h):
        raise ValueError("Revive search region is invalid")
    scores = cv2.matchTemplate(frame[t:b,l:r],template,cv2.TM_CCOEFF_NORMED)
    _,score,_,point = cv2.minMaxLoc(scores)
    if score < .995:
        return None
    # More than one full button match is ambiguous; do not select one at random.
    x,y = point
    scores[max(0,y-h//2):y+h//2+1,max(0,x-w//2):x+w//2+1] = -1
    if np.max(scores) >= .98:
        return None
    return (l+x+w//2,t+y+h//2)


class RecoveryPhase(StrEnum):
    WAITING = "waiting_for_revive"
    REVIVING = "verifying_revive"
    RETURNING = "returning_to_route"
    COMPLETE = "recovered"
    FAILED = "recovery_failed"


@dataclass(frozen=True)
class RecoveryAction:
    kind: str
    point: tuple[int, int]
    expires_at: float


class DeathRecovery:
    def __init__(self, config, death_position, started, boundary):
        self.config, self.death_position, self.started, self.boundary = config, death_position, started, boundary
        self.phase, self.reason = RecoveryPhase.WAITING, None
        self.attempts = self.waypoint = self.movement_failures = 0
        self.issued, self.return_started, self.moving = None, None, None
        self.revival_verified = False

    def fail(self, reason):
        self.phase, self.reason = RecoveryPhase.FAILED, reason
        return None

    def decide(self, *, health, position, map_id, timestamp, now, button=None):
        if self.phase in (RecoveryPhase.FAILED, RecoveryPhase.COMPLETE):
            return None
        if not 0 <= now-timestamp <= .35:
            return None
        if not math.isfinite(health) or not 0 <= health <= 1:
            return self.fail("invalid_recovery_health")
        if map_id != self.config.map_id:
            return self.fail("unexpected_revival_map")
        if self.phase in (RecoveryPhase.WAITING, RecoveryPhase.REVIVING):
            if health > 0:
                l,t,r,b = self.boundary
                if math.dist(position,self.death_position) <= 3 and l <= position[0] <= r and t <= position[1] <= b:
                    # An observed on-site revival needs no town-return trip.
                    self.revival_verified = True
                    self.phase = RecoveryPhase.COMPLETE
                    return None
                if math.dist(position,self.config.town_position) > self.config.town_tolerance:
                    return self.fail("unexpected_revival_position")
                self.revival_verified = True
                self.phase, self.return_started = RecoveryPhase.RETURNING, now
            else:
                if self.phase == RecoveryPhase.REVIVING:
                    if now-self.issued < self.config.revive_timeout:
                        return None
                    if self.attempts >= self.config.attempt_limit:
                        return self.fail("revive_attempt_limit")
                    self.phase = RecoveryPhase.WAITING
                if now-self.started < self.config.revive_wait:
                    return None
                if button is None:
                    if now-self.started >= self.config.calibration_timeout:
                        return self.fail("revive_button_not_verified")
                    return None
                self.attempts += 1
                self.issued, self.phase = now, RecoveryPhase.REVIVING
                return RecoveryAction("revive",button,now+.3)
        if health <= 0:
            return self.fail("death_during_return")
        if now-self.return_started > self.config.return_timeout:
            return self.fail("return_timeout")
        if health < .4:
            return None  # The shared healing path runs before return movement.
        if self.moving:
            before,issued = self.moving
            if now-issued < 1.5:
                return None
            self.movement_failures = self.movement_failures+1 if math.dist(before,position)<.5 else 0
            self.moving = None
            if self.movement_failures >= 3:
                return self.fail("return_movement_failure_limit")
        while self.waypoint < len(self.config.return_route) and math.dist(position,self.config.return_route[self.waypoint]) <= 2:
            self.waypoint += 1
        if self.waypoint == len(self.config.return_route):
            l,t,r,b = self.boundary
            if not (l <= position[0] <= r and t <= position[1] <= b):
                return self.fail("return_route_ends_outside_farm")
            self.phase = RecoveryPhase.COMPLETE
            return None
        dx,dy = (v-p for v,p in zip(self.config.return_route[self.waypoint],position))
        scale = min(1,6/max(abs(dx),abs(dy),1))
        dx,dy = dx*scale,dy*scale
        if self.movement_failures == 1: dx+=1; dy-=1
        if self.movement_failures == 2: dx-=1; dy+=1
        scale = min(1,280/max(abs((dx-dy)*32),1),110/max(abs((dx+dy)*16),1))
        point = (round(792+(dx-dy)*scale*32),round(432+(dx+dy)*scale*16))
        if not (100 < point[0] < 1100 and 170 < point[1] < 550):
            return self.fail("return_point_obscured")
        self.moving = (position,now)
        return RecoveryAction("return_walk",point,now+.3)
