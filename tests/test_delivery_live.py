from types import SimpleNamespace

import pytest

from conquest.merchants import delivery_live, delivery_probe, farmer_preferences
from conquest import recovery_override


def setup_stage(monkeypatch,tmp_path):
    path=tmp_path/'probe.json'
    monkeypatch.setattr(delivery_probe,'JOURNAL',path)
    monkeypatch.setattr(delivery_live,'JOURNAL',path)
    monkeypatch.setattr(farmer_preferences,'permits_new_delivery',lambda _:None)
    def no_stage_import(*args):
        pytest.fail('A rejected stage must not import or start an input worker')
    monkeypatch.setattr(delivery_live.importlib,'import_module',no_stage_import)
    ui=SimpleNamespace(coordinator=SimpleNamespace(check=lambda:None),safe_to_yield=lambda:True,
                       app=SimpleNamespace())
    return ui,path


@pytest.mark.parametrize('phase,stage',[('request_verified','accept'),('offer_verified','confirm')])
def test_stage_completes_durable_override_before_phase_gating(tmp_path,monkeypatch,phase,stage):
    ui,path=setup_stage(monkeypatch,tmp_path)
    before={'phase':phase,'character':'Spiritual','historical_input':'retained'}
    delivery_probe.write_probe(path,before)
    digest=recovery_override.evidence_digest(before)
    complete=recovery_override._complete
    def crash(*args):raise OSError('crash after durable override intent')
    monkeypatch.setattr(recovery_override,'_complete',crash)
    with pytest.raises(OSError,match='durable override intent'):
        recovery_override.operator_override(path,pending_phases={phase},operator_confirmed=True,
            confirmation_reference=digest,incident_digest=digest,
            fresh_evidence={'historical_outcome':'unknown'})
    assert delivery_probe.read_json(path)==before
    assert delivery_probe.Path(str(path)+'.override-intent.json').exists()
    monkeypatch.setattr(recovery_override,'_complete',complete)
    with pytest.raises(ValueError,match='Reconcile the previous delivery stage'):
        delivery_live.start(ui,stage)
    after=delivery_probe.read_json(path)
    assert after['phase']=='operator_overridden'
    assert after['operator_override']['original_state']==before
    assert not delivery_probe.Path(str(path)+'.override-intent.json').exists()
    assert not hasattr(ui,'delivery_probe_thread')


@pytest.mark.parametrize('contents',['{','[]','null','"not a receipt"','{}',None])
@pytest.mark.parametrize('stage',['accept','confirm'])
def test_stage_fails_closed_without_valid_dictionary_journal(tmp_path,monkeypatch,contents,stage):
    ui,path=setup_stage(monkeypatch,tmp_path)
    if contents is not None:path.write_text(contents)
    with pytest.raises(ValueError):delivery_live.start(ui,stage)
    assert not hasattr(ui,'delivery_probe_thread')


@pytest.mark.parametrize('contents',['{','{}','[]','null','{"record": {}}'])
def test_invalid_override_intent_cannot_reauthorize_original_stage(tmp_path,monkeypatch,contents):
    ui,path=setup_stage(monkeypatch,tmp_path)
    delivery_probe.write_probe(path,{'phase':'request_verified'})
    delivery_probe.Path(str(path)+'.override-intent.json').write_text(contents)
    with pytest.raises(ValueError,match='override evidence is unreadable'):
        delivery_live.start(ui,'accept')
    assert delivery_probe.read_json(path)['phase']=='request_verified'
    assert not hasattr(ui,'delivery_probe_thread')
