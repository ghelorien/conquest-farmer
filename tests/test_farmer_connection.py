from types import SimpleNamespace as NS
import time
import pytest
from conquest.merchants.farmer_connection import attach


@pytest.mark.parametrize('case',['login','parasite','merchant','wrong_character','replaced','manual','expired','enabled','stopped'])
def test_exact_farmer_attachment_preserves_other_accounts_and_manual_control(monkeypatch,case):
    calls=[];identity={'pid':10,'creation_time_100ns':20}
    candidate=NS(identity=identity,hwnd=30)
    observer=NS(adapter=NS(identity=identity,assert_identity=lambda:None),health_layout=None,
                close=lambda:calls.append('close'))
    app=NS(closing=False,observer=None,host=NS(saved=None),
           control=NS(snapshot=lambda:{'enabled':case=='enabled'}),
           catalog=NS(windows=lambda:[candidate]),observer_factory=lambda *a:observer)
    def embed(c):
        assert c is candidate
        calls.append('embed');app.observer=observer
    app.embed=embed
    runtime=NS(connecting={},refilling={},observers={'Dutch':observer} if case=='merchant' else {},
               journal=NS(pending=lambda c:[]))
    ui=NS(app=app,closed=False,coordinator=NS(stopped=case=='stopped',owner=None),
          safe_to_yield=lambda:True,runtime=runtime)
    def idle():
        if case=='manual':raise ValueError('Manual input')
    def life(*args):
        calls.append('identity')
        if case=='wrong_character':raise ValueError('Wrong character')
    monkeypatch.setattr('conquest.mouse_priority.require_idle',idle)
    monkeypatch.setattr('conquest.reconnect.login_screen',lambda hwnd:case=='login')
    monkeypatch.setattr('conquest.memory_life.read_life',life)
    queued=time.monotonic()-(4 if case=='expired' else 0)
    if case in ('login','parasite'):
        result=attach(ui,10,20,queued_at=queued)
        assert result['attached'] and not result['farming_enabled']
        assert ('identity' in calls)==(case=='parasite')
    else:
        with pytest.raises(ValueError):attach(ui,10,21 if case=='replaced' else 20,queued_at=queued)
        assert 'embed' not in calls
