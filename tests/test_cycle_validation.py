from conquest.cycle_validation import evaluate
import copy
import hashlib
import json
from pathlib import Path
import pytest
import runpy
import sqlite3


def data():
    items=[{'uid':1,'type_id':123,'plus':1,'gem1':0,'gem2':0,'quantity':1,'bound':False}]
    return dict(config={'started_at':100,'duration_seconds':7200},now=7300,
        kill_events=[(200,4800)],visits=[{'town_visit_id':'v','phase':'complete','required_at':300,
            'farmer_profile_id':'farmer-a','return_started_at':490,
            'completed_at':500,'first_verified_resume_kill':{'rowid':99,'count':1,'time':499}}],
        deliveries=[{'request_id':'trade','town_visit_id':'v','phase':'verified','outcome':'transferred',
            'operation_id':'trade','visit_id':'market','farmer_profile_id':'farmer-a','character':'Dutch',
            'proof_digest':'proof','items':items,'delivered':copy.deepcopy(items),'verified_at':400,
            'next_action':'release_route','started_at':350}],
        refill_events=[{'town_visit_id':'v','visit_id':'market','character':'Dutch','observed_at':450,
            'status':'completed','listed':2}],interruptions=[])


def test_full_window_includes_all_downtime_and_rejects_missing_cycle():
    values=data();assert evaluate(**values)['qualified']
    values['visits']=[]
    assert not evaluate(**values)['qualified']


def test_burst_or_early_success_does_not_qualify():
    values=data();values['now']=1000
    assert not evaluate(**values)['qualified']
    values['now']=7300;values['kill_events']=[(7299,600),(7400,10000)]
    result=evaluate(**values)
    assert result['total_kills']==600 and result['overall_kills_per_minute']==5
    assert not result['qualified']


@pytest.mark.parametrize('fields',[
    {'reasons':['merchant_acceptance']}, {'reasons':['restock','merchant_acceptance']},
    {'validation_cycle':True}, {'acceptance_scope':{'run_id':'trial'}},
])
def test_forced_acceptance_visits_never_qualify_as_natural_cycles(fields):
    values=data();values['visits'][0].update(fields)
    result=evaluate(**values)
    assert not result['qualified'] and result['covered_cycles']==[]
    assert result['excluded_forced_visit_ids']==['v']


def test_assisted_recovery_pending_work_and_old_cycle_never_pass():
    values=data();values['interruptions']=['App process changed during qualification']
    assert not evaluate(**values)['qualified']
    values['interruptions']=[];values['refill_events'][0]['status']='paused_budget'
    assert not evaluate(**values)['qualified']
    values=data();values['visits'][0]['required_at']=50
    assert not evaluate(**values)['qualified']
    values=data();values['deliveries'].append({'request_id':'ambiguous','started_at':600,'phase':'uncertain'})
    assert evaluate(**values)['unresolved_operations']==['ambiguous']


def test_unrelated_or_earlier_refill_cannot_complete_transfer_cycle():
    for changes in ({'character':'Spiritual','status':'no_stock'}, {'observed_at':399},
                    {'visit_id':'earlier-market'},{'town_visit_id':'other'},{'observed_at':491}):
        values=data();values['refill_events'][0].update(changes)
        assert not evaluate(**values)['qualified']


def test_every_transferred_recipient_needs_subsequent_evaluation():
    values=data();other=copy.deepcopy(values['deliveries'][0])
    other.update(character='Spiritual',request_id='other-trade',operation_id='other-trade')
    values['deliveries'].append(other)
    result=evaluate(**values)
    assert not result['qualified']
    assert result['incomplete_cycles'][0]['unmatched_transfers']==['other-trade']
    values['refill_events'].append({**values['refill_events'][0],'character':'Spiritual','status':'booth_full'})
    result=evaluate(**values)
    assert result['qualified']
    assert result['covered_farmer_profile_ids']==['farmer-a']
    assert result['covered_merchant_record_ids']==['Dutch','Spiritual']


def test_missing_exact_receipt_or_wrong_profile_or_resume_evidence_blocks():
    for change in ({'proof_digest':None},{'farmer_profile_id':'other'}, {'operation_id':'other'},
                   {'delivered':[]},{'verified_at':None},{'cleanup_pending':['farmer']}):
        values=data();values['deliveries'][0].update(change)
        assert not evaluate(**values)['qualified']
    values=data();values['deliveries'][0]['delivered'][0]['bound']=True
    assert not evaluate(**values)['qualified']
    values=data();values['visits'][0]['first_verified_resume_kill']['time']=480
    assert not evaluate(**values)['qualified']


def test_required_identity_coverage_is_explicit():
    values=data();values['config'].update(required_farmer_profile_ids=['farmer-a','farmer-b'],
        required_merchant_record_ids=['Dutch','Spiritual'])
    result=evaluate(**values)
    assert not result['qualified']
    assert result['missing_farmer_profile_ids']==['farmer-b']
    assert result['missing_merchant_record_ids']==['Spiritual']


def test_monitor_source_integrity_accepts_complete_release_shape_and_rejects_changes(tmp_path):
    monitor=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/monitor_unified_validation.py'))
    relative_files=['src/conquest/code.py','scripts/start.py','docs/operations.md',
                    'tests/test_code.py','AGENTS.md','README.md','pyproject.toml']
    files={}
    for relative in relative_files:
        target=tmp_path/relative;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text('original '+relative);files[relative]=hashlib.sha256(target.read_bytes()).hexdigest()
    manifest=tmp_path/'RELEASE-MANIFEST.json'
    manifest.write_text(json.dumps({'version':1,'created_at':1,'source_commit':'abc','files':files,
        'data_mode':'explicit_legacy','live_qualification_profiles':['Parasite','Spiritual','Dutch']}))
    config={'release_root':str(tmp_path),'release_manifest_path':str(manifest),
            'release_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
    assert monitor['source_integrity'](config)
    source=tmp_path/'src/conquest/code.py'
    source.write_text('changed');assert not monitor['source_integrity'](config)
    assert not monitor['source_integrity']({})
    manifest.write_text(json.dumps({'version':1,'files':{'reports/secret.json':'anything'}}))
    config['release_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert not monitor['source_integrity'](config)


@pytest.mark.parametrize('private',[
    'reports/secret.json','.runtime/token.json','profiles/private.json','docs/../reports/secret.json'])
def test_monitor_source_integrity_rejects_private_or_traversing_manifest_path(tmp_path,private):
    monitor=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/monitor_unified_validation.py'))
    target=tmp_path/private;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('secret')
    manifest=tmp_path/'RELEASE-MANIFEST.json'
    manifest.write_text(json.dumps({'version':1,'files':{private:hashlib.sha256(target.read_bytes()).hexdigest()}}))
    config={'release_root':str(tmp_path),'release_manifest_path':str(manifest),
            'release_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
    assert not monitor['source_integrity'](config)


def test_readonly_monitor_collects_recipient_time_and_exact_identity(tmp_path,monkeypatch):
    values=data();delivery=values['deliveries'][0];items=delivery['items']
    bank=tmp_path/'reports/banking';bank.mkdir(parents=True)
    merchants=tmp_path/'reports/merchants';merchants.mkdir(parents=True)
    source=bank/'merchant-deliveries.sqlite3';receiver=merchants/'journal.sqlite3'
    before={**{key:delivery[key] for key in ('operation_id','town_visit_id','visit_id','farmer_profile_id')},
        'items':items,'farmer':{'character':'Farmer A','character_uid':12},
        'merchant':{'character':'Dutch','character_uid':34}}
    result={'outcome':'delivered','delivered':items,'remaining':[],'proof_digest':'proof'}
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE transactions(id,character,kind,phase,before_json,result_json,created,updated)')
        db.execute('CREATE TABLE transaction_steps(id,transaction_id,stage,status,payload)')
        db.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?)',('trade','dutch-record','farmer_delivery',
            'verified',json.dumps(before),json.dumps(result),350,400))
        db.execute('INSERT INTO transaction_steps VALUES(1,?,?,?,?)',('trade','receiver_receipt','observed','{}'))
    with sqlite3.connect(receiver) as db:
        db.execute('CREATE TABLE events(id,character,event,payload,timestamp)')
        db.execute('CREATE TABLE transactions(id,created,phase)')
        refill={**values['refill_events'][0],'character':'wrong-payload','observed_at':1}
        db.execute('INSERT INTO events VALUES(1,?,?,?,?)',('dutch-record','refill_checked',json.dumps(refill),450))
    (bank/'town-visit.json').write_text(json.dumps(values['visits'][0]))
    original={path:path.read_bytes() for path in (source,receiver)}
    monkeypatch.setattr('conquest.character_context.state_path',lambda path:str(tmp_path/path))
    monitor=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/monitor_unified_validation.py'))
    monkeypatch.setitem(monitor['qualification'].__globals__,'source_integrity',lambda config:True)
    config={**values['config'],'app_pid':1,'route_pid':2,'kill_session_started_at':50}
    result=monitor['qualification'](config,7300,values['kill_events'],
        {'pid':1,'kill_session_started_at':50,'updated_at':7300},{'pid':2,'phase':'hunting','updated_at':7300},{})
    assert result['qualified']
    assert result['covered_merchant_record_ids']==['dutch-record']
    match=result['covered_cycles'][0]['transfer_refill_matches'][0]
    assert match['merchant']['character']=='Dutch' and match['merchant']['character_uid']==34
    assert match['refill']['character']=='dutch-record' and match['refill']['observed_at']==450
    assert all(path.read_bytes()==raw for path,raw in original.items())
    with sqlite3.connect(receiver) as db:db.execute("INSERT INTO transactions VALUES('listing',600,'uncertain')")
    pending=monitor['qualification'](config,7300,values['kill_events'],
        {'pid':1,'kill_session_started_at':50,'updated_at':7300},{'pid':2,'phase':'hunting','updated_at':7300},{})
    assert not pending['qualified'] and pending['unresolved_merchant_operations']==['listing']
