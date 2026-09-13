import json
from conquest.loot_audit import append


def test_audit_preserves_attempt_manual_pause_and_inventory_only_receipt(tmp_path):
    rows=[('memory_pickup_attempt',{'uid':7,'type_id':1088001}),
          ('paused',{'reason':'Automation stopped or manual input active'}),
          ('memory_pickup_verified',{'inventory_uid':9,'source':'inventory_gain'})]
    for event,fields in rows:assert append(tmp_path,event,fields,now=1789260257)
    saved=[json.loads(line) for line in next((tmp_path/'loot-decisions').glob('*.jsonl')).read_text().splitlines()]
    assert [(r['event'],r['fields']) for r in saved]==rows
    assert not append(tmp_path,'health_observation',{},now=1789260257)
    assert len(saved)==3


def test_daily_journal_keeps_previous_day(tmp_path):
    append(tmp_path,'memory_loot_retry',{'detail':'scene changed'},now=0)
    append(tmp_path,'memory_loot_ready',{},now=86400)
    assert len(list((tmp_path/'loot-decisions').glob('*.jsonl')))==2
