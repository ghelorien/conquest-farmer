"""Native viewport loot matching keeps label pixels and map projection intact."""
import cv2
import numpy as np
import pytest

from conquest.looting import nearby_drops


SIZE, ANCHOR = (1036,793), (518,396)
BOUNDARY = (400,400,480,480)


def drops_at(*points, **options):
    template = cv2.imread('profiles/templates/stancher-name.png',0)
    frame = np.zeros((SIZE[1],SIZE[0],3),np.uint8)
    for x,y in points:
        frame[y-30:y-17,x-31:x+32,1:3] = template[:,:,None]
    return nearby_drops(frame,template,(435,449),BOUNDARY,
                        calibrated_size=SIZE,player_anchor=ANCHOR,**options)


def test_native_labels_project_one_tile_without_visual_stretching():
    drops = drops_at((550,412))
    assert len(drops) == 1
    assert drops[0].point == (550,412)
    assert drops[0].position == (436,449)


def test_native_anchor_controls_distance_order():
    drops = drops_at((646,460),(550,412))
    assert [drop.point for drop in drops] == [(550,412),(646,460)]
    assert drops_at((646,460),max_distance=100) == []


@pytest.mark.parametrize('point',[(960,412),(700,680),(550,570),(550,160)])
def test_native_viewport_excludes_right_hud_bottom_hud_chat_and_top(point):
    assert drops_at(point,max_distance=600) == []


def test_scene_beside_chat_stays_available():
    assert [drop.point for drop in drops_at((700,570),max_distance=400)] == [(700,570)]


def test_native_frame_requires_explicit_matching_calibration():
    frame = np.zeros((793,1036,3),np.uint8)
    template = cv2.imread('profiles/templates/stancher-name.png',0)
    with pytest.raises(ValueError,match='geometry'):
        nearby_drops(frame,template,(435,449),BOUNDARY)
    with pytest.raises(ValueError,match='geometry'):
        nearby_drops(frame,template,(435,449),BOUNDARY,
                     calibrated_size=SIZE,player_anchor=(1100,396))
