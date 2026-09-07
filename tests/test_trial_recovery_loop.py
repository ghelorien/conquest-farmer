import logging
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import yaml

from conquest import trial
from conquest.capture import Frame
from conquest.memory_inventory import InventorySnapshot, Item


@pytest.mark.parametrize("has_supplies",[True,False])
def test_live_loop_revives_before_supply_stop_and_returns_without_combat(tmp_path,monkeypatch,has_supplies):
    import win32api
    now, clicks = [0.],[]
    state = {"health":0,"position":[425,450]}
    profile = yaml.safe_load(open("profiles/pheasant-foreground-trial.yaml"))
    profile["route"] = []
    image_path = tmp_path / "button.png"
    cv2.imwrite(str(image_path),np.random.default_rng(1).integers(0,256,(20,70,3),dtype=np.uint8))
    profile["recovery"] = {"enabled":True,"revive_template":str(image_path),
                           "revive_region":[650,250,950,450],
                           "return_route":[[430,380],[430,400],[430,420],[425,450]]}
    path = tmp_path / "profile.yaml"
    profile["observation_mode"] = "legacy_visual"  # Exercise the historical loop with fakes.
    path.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(trial.time,"monotonic",lambda:now[0])
    monkeypatch.setattr(trial.time,"sleep",lambda n:now.__setitem__(0,now[0]+n))
    monkeypatch.setattr(win32api,"GetAsyncKeyState",lambda k:0)

    class Session:
        def __init__(self,*args): pass
        def request(self,name,args=None):
            if name == "health":
                return {"input_revision":6,"window":{"hwnd":1}}
            if name == "sample":
                values = dict(name="Parasite",position=state["position"],max_hp=[100],
                              kill_counter=[0],level=[10],map=[1002])
                return {"fields":[{"name":k,"value":v} for k,v in values.items()]}
            assert name == "foreground-click", "Dead characters must not receive potion keys"
            clicks.append((now[0],args["point"]))
            assert not args["control"], "The calibrated gate return must walk"
            if state["health"] == 0:
                assert now[0] >= 20 and args["point"] == [735,310]
                state.update(health=.9,position=[430,380])
            else:
                px,py = args["point"][0]-792,args["point"][1]-432
                dx,dy = (px/32+py/16)/2,(py/16-px/32)/2
                state["position"] = [round(state["position"][0]+dx),round(state["position"][1]+dy)]

    class Inventory:
        def __init__(self,*args): pass
        def read(self):
            now[0] += .1
            items = (Item(1,1000000,1,1,0),) if has_supplies else ()
            return InventorySnapshot(now[0],now[0],items,Item(99,1050000,100,200,None),0,40)

    camera = SimpleNamespace(geometry=lambda:(0,0),read=lambda:Frame(now[0],None,(0,0)),close=lambda:None)
    monkeypatch.setattr(trial,"WorkerPointerSession",Session)
    monkeypatch.setattr(trial,"MemoryInventoryReader",Inventory)
    monkeypatch.setattr(trial,"DesktopFrames",lambda *args:camera)
    monkeypatch.setattr(trial,"resolve_player",lambda *args:dict.fromkeys(
        ("name","position","max_hp","kill_counter","level","map"),1))
    monkeypatch.setattr(trial,"health_ratio",lambda frame:state["health"])
    monkeypatch.setattr(trial,"revive_button",lambda *args:(735,310))
    monkeypatch.setattr(trial.cv2,"imwrite",lambda *args:True)
    monkeypatch.setattr(trial,"targets",lambda *args:[])
    monkeypatch.setattr(trial,"nearby_drops",lambda *args:[])
    result = trial.run_trial(path,"unused",tmp_path / "output",50,logging.getLogger("test"))
    assert result["verified_revivals"] == result["deaths"] == 1
    assert result["attack_attempts"] == 0
    if has_supplies:
        assert result["reason"] == "duration_limit"
        assert len(clicks) > 1 and state["position"][1] >= 448
    else:
        assert result["reason"] == "potions_exhausted"
        assert len(clicks) == 1 and state["position"] == [430,380]
