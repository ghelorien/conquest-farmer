"""Shared, read-only qualification of live memory-projected scene targets."""
from dataclasses import dataclass,asdict

from conquest.capture import CaptureUnavailable
from conquest.viewport import clear_scene,validate_size


BLOCKING_PANELS=frozenset({'Inventory','Shop','Warehouse','Booth','Dialog','Add Item to Booth',
                           'Trade','Trade###Confirm','Trade##TradeWindow','###Confirm'})


class TargetNotActionable(CaptureUnavailable):
    code='target_not_actionable'
    def __init__(self,result):
        self.result=result
        super().__init__(result['message'])


@dataclass(frozen=True)
class TargetActionability:
    actionable: bool
    reason: str
    message: str
    logical_point: tuple
    physical_point: tuple | None
    gui_size: tuple
    client_size: tuple
    blocking_panel: str | None = None

    def as_dict(self):
        data=asdict(self)
        for key in ('logical_point','physical_point','gui_size','client_size'):
            if data[key] is not None:data[key]=list(data[key])
        return data


def target_actionability(point,gui_size,client_size,windows=()):
    """Classify a projected target before any input intent is persisted."""
    gui=validate_size(gui_size);client=validate_size(client_size)
    if not isinstance(point,(list,tuple)) or len(point)!=2 or any(type(v) is not int for v in point):
        raise ValueError('Projected target point is invalid')
    logical=tuple(point)
    physical=tuple(round(v*p/g) for v,p,g in zip(logical,client,gui))
    reason=None;panel=None
    if not (0<=logical[0]<gui[0] and 0<=logical[1]<gui[1]):reason='outside_gui'
    elif not (0<=physical[0]<client[0] and 0<=physical[1]<client[1]):reason='outside_client'
    elif not clear_scene(logical,gui):reason='scene_control'
    else:
        for window in windows or ():
            name=window.get('name')
            if name not in BLOCKING_PANELS and not str(name).startswith('Trade##'):continue
            x,y,width,height=window.get('geometry',(0,0,0,0))
            if x<=logical[0]<x+width and y<=logical[1]<y+height:
                reason='panel_occlusion';panel=name;break
    messages={'outside_gui':'Receiver projection is outside the live GUI viewport',
              'outside_client':'Receiver projection is outside the native client',
              'scene_control':'Receiver projection overlaps reserved scene controls',
              'panel_occlusion':'Receiver projection is covered by a draggable panel'}
    return TargetActionability(reason is None,reason or 'actionable',
        'Receiver projection is actionable' if reason is None else messages[reason],
        logical,physical,gui,client,panel).as_dict()


def require_target_actionable(*args,**kwargs):
    result=target_actionability(*args,**kwargs)
    if not result['actionable']:raise TargetNotActionable(result)
    return result
