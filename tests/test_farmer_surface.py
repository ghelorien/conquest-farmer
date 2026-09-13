import time
from types import SimpleNamespace as N
import pytest
from conquest.merchants.farmer_surface import focus_idle_farmer


def ui(enabled=False, safe=True):
    calls=[]
    state={'enabled':enabled,'revision':4}
    app=N(closing=False,control=N(snapshot=lambda:dict(state)),show_game=lambda:calls.append('focus') or True)
    return N(closed=False,app=app,coordinator=N(check=lambda:None,owner=None),safe_to_yield=lambda:safe),calls,state


def test_idle_focus_preserves_off():
    u,c,s=ui()
    assert focus_idle_farmer(u,queued_at=time.monotonic())=={'focused':True,'farming_enabled':False}
    assert c==['focus'] and s=={'enabled':False,'revision':4}


@pytest.mark.parametrize('enabled,safe',[(True,True),(False,False)])
def test_no_focus_during_active_work(enabled,safe):
    u,c,s=ui(enabled,safe)
    with pytest.raises(ValueError):focus_idle_farmer(u,queued_at=time.monotonic())
    assert not c


def test_expired_request_cannot_focus_later():
    u,c,s=ui()
    with pytest.raises(ValueError):focus_idle_farmer(u,queued_at=time.monotonic()-4)
    assert not c
