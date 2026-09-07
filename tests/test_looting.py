from dataclasses import replace
import cv2
import numpy as np
from conquest.looting import Drop,PickupAttempt,nearby_drops
from conquest.memory_inventory import Item,InventorySnapshot


def test_pickup_requires_new_item_of_selected_type_and_quantity_gain():
    drop=Drop("Stancher",1000000,(900,450),(437,461))
    attempt=PickupAttempt(drop,frozenset({1}),1,10)
    before=InventorySnapshot(10,10,(Item(1,1000000,1,1,0),),None,0,40)
    assert attempt.outcome(before,10.5)=="waiting"
    assert attempt.outcome(replace(before,silver=500),12)=="unverified"
    other=Item(2,1050000,200,200,1)
    assert attempt.outcome(replace(before,items=before.items+(other,)),12)=="unverified"
    new=Item(3,1000000,1,1,1)
    assert attempt.outcome(replace(before,items=before.items+(new,)),10.5)=="verified"


def test_exact_drop_label_excludes_wrong_color_and_far_items():
    template=cv2.imread("profiles/templates/stancher-name.png",0)
    frame=np.zeros((861,1584,3),np.uint8)
    frame[431:444,893:956,1:3]=template[:,:,None]
    drops=nearby_drops(frame,template,(435,460),(405,442,440,472))
    assert len(drops)==1 and drops[0].point==(924,461)
    assert nearby_drops(frame,template,(435,460),(405,442,436,460))==[]
    assert nearby_drops(frame,template,(435,460),(405,442,440,472),max_distance=50)==[]
    frame[431:444,893:956,2]=0
    assert nearby_drops(frame,template,(435,460),(405,442,440,472))==[]
