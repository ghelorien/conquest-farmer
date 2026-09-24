"""Calibrated foreground observations. No entity identity is inferred from colour alone."""

from dataclasses import dataclass
import cv2
import numpy as np


@dataclass(frozen=True)
class Target:
    name: str
    x: int
    y: int
    confidence: float
    entity_id: int | None = None
    object_address: int | None = None
    world_position: tuple[int, int] | None = None
    current_hp: int | None = None


def health_ratio(frame, calibrated_size=(1584, 861)):
    """Read the unobstructed bottom edge of the calibrated HP bar, not its text."""
    width, height = calibrated_size
    if frame.shape != (height, width, 3) or width < 1200 or height < 700:
        raise ValueError("Uncalibrated frame geometry")
    strip = frame[height - 89 : height - 86, width // 2 - 457 : width // 2 - 4].astype(
        np.int16
    )
    red = (strip[:, :, 2] > 100) & (strip[:, :, 1] < 55) & (strip[:, :, 0] < 80)
    blue = (
        (strip[:, :, 0] > 50)
        & (strip[:, :, 0] < 100)
        & (strip[:, :, 1] < 75)
        & (strip[:, :, 2] < 55)
    )
    if np.mean(red | blue) < 0.98:
        raise ValueError("HP bar is covered or unrecognizable")
    columns = np.mean(red, axis=0) > 0.5
    count = int(np.count_nonzero(columns))
    if np.any(columns[count:]) or not np.all(columns[:count]):
        raise ValueError("HP bar does not have a contiguous fill")
    return count / len(columns)


def targets(frame, template, threshold=0.94, name="Pheasant"):
    mask = cv2.inRange(frame, (200, 200, 200), (255, 255, 255))
    # The same Pheasant label turns green once the Archer outlevels it.
    mask |= cv2.inRange(frame, (0, 180, 0), (100, 255, 100))
    scores = cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(scores >= threshold)
    result = []
    centre = template.shape[1] // 2
    for x, y in sorted(zip(xs, ys), key=lambda p: -float(scores[p[1], p[0]])):
        cx, cy = int(x + centre), int(y + 62)
        if not (
            80 < cx < min(1380, frame.shape[1] - 80)
            and 120 < cy < min(735, frame.shape[0] - 126)
        ):
            continue
        if cx < 615 and (cy > 550 or cy < 170):  # Chat intercepts game clicks.
            continue
        bar = frame[y + 19 : y + 24, x + centre - 25 : x + centre + 26].astype(np.int16)
        if bar.shape != (5, 51, 3):
            continue
        red = (bar[:, :, 2] > 140) & (bar[:, :, 1] < 80) & (bar[:, :, 0] < 80)
        if np.count_nonzero(red) < 30:
            continue  # A name without a live health bar cannot be attacked.
        if any(abs(cx - t.x) < 35 and abs(cy - t.y) < 25 for t in result):
            continue
        result.append(Target(name, cx, cy, float(scores[y, x])))
    return result
