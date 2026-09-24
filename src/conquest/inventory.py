"""Observations of the calibrated open inventory, never inferred from clicks."""

from dataclasses import dataclass
from pathlib import Path
import cv2
import numpy as np


@dataclass(frozen=True)
class Inventory:
    potions: tuple[tuple[int, int], ...]
    occupied: int
    capacity: int = 40


class InventoryReader:
    def __init__(self, directory):
        directory = Path(directory)
        self.title = cv2.imread(str(directory / "inventory-title.png"))
        self.potion = cv2.imread(str(directory / "stancher-icon.png"))
        if self.title is None or self.potion is None:
            raise ValueError("Inventory calibration templates missing")

    def visible(self, frame):
        if frame.shape != (861, 1584, 3):
            return False
        title = frame[293:310, 1285:1435]
        return (
            float(cv2.matchTemplate(title, self.title, cv2.TM_CCOEFF_NORMED)[0, 0])
            > 0.98
        )

    def read(self, frame):
        if not self.visible(frame):
            raise ValueError("Calibrated inventory is not visible")
        potions, occupied = [], 0
        for row in range(4):
            for col in range(10):
                x, y = 1158 + col * 40, 327 + row * 40
                cell = frame[y : y + 37, x : x + 37]
                if np.count_nonzero(cell.max(axis=2) > 70) > 8:
                    occupied += 1
                score = float(
                    cv2.matchTemplate(cell, self.potion, cv2.TM_CCOEFF_NORMED)[0, 0]
                )
                if score > 0.95:
                    potions.append((x + 18, y + 18))
        return Inventory(tuple(potions), occupied)
