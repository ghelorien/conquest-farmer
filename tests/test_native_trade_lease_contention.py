"""Real local thread contention; no game process or gameplay input."""
import threading
from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants.coordination import InputCoordinator


@pytest.fixture
def coordinator(tmp_path):
    value=InputCoordinator(safe_to_yield=lambda:True,path=tmp_path/'input.lock')
    value.surface_blocks['Dutch']=True
    return value


def test_farmer_waiting_for_qualified_receiver_gets_retryable_contention(coordinator):
    entered=threading.Event();release=threading.Event();errors=[];checks=[]
    def qualified(character):
        checks.append(threading.get_ident())
        return character=='Dutch' and coordinator.thread==threading.get_ident()
    coordinator.native_trade1078_policy=qualified
    def receiver():
        try:
            with coordinator.lease('Dutch',purpose='trade'):
                entered.set()
                if not release.wait(3):raise AssertionError('Test receiver was not released')
        except BaseException as error:
            errors.append(error);entered.set()
    worker=threading.Thread(target=receiver)
    worker.start()
    try:
        assert entered.wait(3) and not errors
        before=list(checks)
        with pytest.raises(CaptureUnavailable,match='^Another character owns game input$'):
            coordinator.check()
        # The Farmer thread must not evaluate Dutch's thread-bound permission.
        assert checks==before and coordinator.owner=='Dutch'
    finally:
        release.set();worker.join(3)
    assert not worker.is_alive() and not errors
    with coordinator.lease('Farmer',purpose='farmer_delivery'):
        coordinator.check()
        assert coordinator.owner=='Farmer'
    assert coordinator.owner is None


@pytest.mark.parametrize('qualified',[False,True])
def test_same_thread_real_surface_qualification_still_required(coordinator,qualified):
    coordinator.owner='Dutch';coordinator.thread=threading.get_ident();coordinator.purpose='trade'
    coordinator.native_trade1078_policy=lambda character:qualified
    if qualified:
        coordinator.check()
    else:
        with pytest.raises(CaptureUnavailable,match='Client surface needs reattachment and input qualification'):
            coordinator.check()


@pytest.mark.parametrize('hold,reason',[
    ('fence','fence revoked'),('stop','Automation stopped'),
    ('manual_mouse','Automation stopped'),('manual_session','Manual visitor session')])
@pytest.mark.parametrize('foreign',[False,True])
def test_stop_fence_and_manual_holds_precede_surface_and_contention(coordinator,hold,reason,foreign):
    coordinator.owner='Dutch';coordinator.thread=-1 if foreign else threading.get_ident()
    coordinator.purpose='trade'
    def unexpected_surface(character):
        raise AssertionError('Safety hold must be checked before surface authorization')
    coordinator.native_trade1078_policy=unexpected_surface
    if hold=='fence':
        def revoked():raise CaptureUnavailable('fence revoked')
        coordinator.fence=NS(check=revoked)
        coordinator.stopped=True  # Fence remains the first failure.
    elif hold=='stop':coordinator.stopped=True
    elif hold=='manual_mouse':coordinator.manual_active=lambda:True
    else:coordinator.manual_sessions={'Dutch':{'holds_automation':True}}
    with pytest.raises(CaptureUnavailable,match=reason):coordinator.check()
