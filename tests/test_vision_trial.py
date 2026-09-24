from pathlib import Path

import cv2
import numpy as np
import pytest

from conquest.trial import TrialConfig, choose_target
from conquest.vision import Target, health_ratio, targets
from conquest.trial import visible_movement_delta


@pytest.mark.parametrize("delta", [(4, 4), (6, -6), (-6, -6), (2, 1)])
def test_jump_is_shortened_to_avoid_interface(delta):
    dx, dy = visible_movement_delta(*delta)
    assert 100 < round(792 + (dx - dy) * 32) < 1100
    assert 140 < round(432 + (dx + dy) * 16) < 550
    assert dx * delta[1] == pytest.approx(dy * delta[0])


def config():
    return TrialConfig(
        character="Parasite",
        player_profile="unused",
        inventory_profile="unused",
        template="unused",
        client_size=(1584, 861),
        boundary=(405, 442, 435, 472),
    )


@pytest.mark.parametrize("fraction", [0, 0.25, 0.5, 0.98, 1])
def test_health_fill_including_death(fraction):
    frame = np.zeros((861, 1584, 3), np.uint8)
    frame[772:775, 335:788] = (75, 48, 30)
    count = round(453 * fraction)
    frame[772:775, 335 : 335 + count] = (34, 22, 148)
    assert health_ratio(frame) == pytest.approx(fraction, abs=0.002)


def test_obscured_health_rejected():
    with pytest.raises(ValueError, match="unrecognizable"):
        health_ratio(np.zeros((861, 1584, 3), np.uint8))


def test_stale_health_and_wrong_monsters_cannot_trigger_attack():
    target = Target("Pheasant", 830, 450, 0.99)
    c = config()
    assert choose_target([target], 0.99, (425, 460), c, 0.01) == target
    assert choose_target([target], 0.39, (425, 460), c, 0.01) is None
    assert choose_target([target], 0.99, (425, 460), c, 0.36) is None
    assert choose_target([target], 0.99, (100, 100), c, 0.01) is None
    assert (
        choose_target([Target("Player", 830, 450, 1)], 1, (425, 460), c, 0.01) is None
    )


def test_template_requires_health_bar_and_excludes_chat():
    template = cv2.imread(
        str(Path(__file__).parents[1] / "profiles/templates/pheasant-name.png"), 0
    )
    frame = np.zeros((861, 1584, 3), np.uint8)
    frame[300:313, 800:863] = template[:, :, None]
    assert targets(frame, template) == []
    frame[319:323, 806:857] = (0, 0, 200)
    found = targets(frame, template)
    assert len(found) == 1 and found[0].name == "Pheasant"
    frame[300:313, 800:863] = 0
    frame[300:313, 800:863, 1] = template
    assert len(targets(frame, template)) == 1
    frame[:] = 0
    frame[600:613, 300:363] = template[:, :, None]
    frame[619:623, 306:357] = (0, 0, 200)
    assert targets(frame, template) == []


def test_turtledove_calibration_filters_by_selected_monster():
    template = cv2.imread("profiles/templates/turtledove-name.png", 0)
    frame = np.zeros((861, 1584, 3), np.uint8)
    frame[300:313, 800:877] = template[:, :, None]
    frame[319:323, 813:864] = (0, 0, 200)
    found = targets(frame, template, 0.94, "Turtledove")
    assert len(found) == 1 and found[0].name == "Turtledove"
    c = config()
    assert choose_target(found, 0.9, (425, 460), c, 0.1) is None
    c.monster = "Turtledove"
    assert choose_target(found, 0.9, (425, 460), c, 0.1) == found[0]
