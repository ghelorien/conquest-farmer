"""Recovery uses exact durable sales, not an inferred missing booth item."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conquest.merchants import booth_listing_once_1078, listing_plan_1078
from conquest.merchants.journal import Journal
from conquest.merchants.restoration_preview_1078 import _preview, sale_receipts
from conquest.merchants.sales import observe


ITEM = dict(uid=91, name='BambooBow', type_id=500004, plus=1,
            gem1=0, gem2=0, bound=False, quantity=1, price=1000000)
OLD_IDENTITY = dict(pid=1, creation_time_100ns=2, path='C:/old/ImConquer.exe')
NEW_IDENTITY = dict(pid=3, creation_time_100ns=4, path='C:/new/ImConquer.exe')


def stock(at, *, inventory=(), booth=(), silver=480428):
    return dict(character='Spiritual', server='America', identity=NEW_IDENTITY,
                timestamp=at, inventory=list(inventory), booth=list(booth),
                silver=silver, request=None, trade=None)


def incident(*, prior_booth):
    return dict(phase='recovering', started_at=99, before={
        **stock(99, inventory=() if prior_booth else (ITEM,),
                booth=(ITEM,) if prior_booth else ()),
        'identity': OLD_IDENTITY})


def live():
    return {**stock(102, silver=1450428), 'profile_id':'Spiritual',
            'character_uid':7, 'client_sha256':'1078-test-build',
            'closed_modal':True, 'map_id':1036, 'hp':100,
            'own_booth_uid':9, 'booth_open':True, 'capacity':40}


def verified_sale(journal):
    observe(journal, stock(100, booth=(ITEM,)))
    observe(journal, stock(101, silver=1450428))
    assert len(sale_receipts(journal.path, 'Spiritual', 99)) == 1


@pytest.mark.parametrize('change',['none','cash','attributes','unverified'])
def test_listing_plan_reconciles_prior_spiritual_booth_only_with_atomic_sale(
        tmp_path, monkeypatch, change):
    journal=Journal(tmp_path/'journal.sqlite3')
    if change=='unverified':
        observe(journal, stock(100, booth=(ITEM,)))
        observe(journal, stock(101))
    else:
        verified_sale(journal)
        if change in ('cash','attributes'):
            with journal.db() as db:
                if change=='cash':
                    event_id,payload=db.execute(
                        "SELECT id,payload FROM events WHERE character='Spiritual' "
                        "AND event='sale_verified'").fetchone()
                    event=json.loads(payload)
                    event['before_silver']+=1  # Row amount stays in valid net bounds.
                    db.execute('UPDATE events SET payload=? WHERE id=?',
                               (json.dumps(event),event_id))
                else:
                    altered={**ITEM,'plus':2}
                    db.execute("UPDATE sales SET items=? WHERE character='Spiritual'",
                               (json.dumps([altered]),))
    journal.set('Spiritual','shop_return',incident(prior_booth=True))
    profile=SimpleNamespace(id='Spiritual',name='Spiritual',character_uid=7)
    monkeypatch.setattr(booth_listing_once_1078,'_profile',lambda _:profile)
    monkeypatch.setattr(listing_plan_1078,'_owned_profiles',lambda:[profile])
    monkeypatch.setattr(listing_plan_1078,'_saved_prices',lambda _:({},{}))
    seen=[]
    monkeypatch.setattr(listing_plan_1078,'_queue',
                        lambda snapshot,catalog,quotes,restoration,**kw:
                        seen.append(restoration) or [])
    runtime=SimpleNamespace(journal=journal,market_path=Path(tmp_path/'market.json'))
    if change=='none':
        assert listing_plan_1078.plan(runtime,'Spiritual',live())==[]
        sold=seen[0]['prior_stock_sold_with_verified_receipts']
        assert [(row['uid'],row['origin'],row['receipt']['silver']) for row in sold] == [
            (91,'prior_booth',970000)]
    else:
        with pytest.raises(ValueError,match='ownership differs'):
            listing_plan_1078.plan(runtime,'Spiritual',live())
        assert not seen


def test_prior_inventory_sale_requires_native_listing_receipt_chain(tmp_path):
    journal=Journal(tmp_path/'journal.sqlite3')
    verified_sale(journal)
    snapshot=live()
    state={'shop_return':incident(prior_booth=False),
           'verified_sale_receipts':sale_receipts(journal.path,'Spiritual',99),
           'verified_listing_receipts_1078':[]}
    with pytest.raises(ValueError,match='ownership differs'):
        _preview(snapshot,state)
    state['verified_listing_receipts_1078']=[{
        'profile_id':'Spiritual', 'identity':NEW_IDENTITY,
        'character_uid':7, 'own_booth_uid':9, 'item':ITEM,
        'observed_at':99.5, 'request_id':'booth-list1078-proved'}]
    sold=_preview(snapshot,state)['prior_stock_sold_with_verified_receipts']
    assert [(row['uid'],row['origin'],row['listing_receipt']['request_id']) for row in sold] == [
        (91,'prior_inventory','booth-list1078-proved')]
    state['verified_listing_receipts_1078'][0]['item']={**ITEM,'price':999999}
    with pytest.raises(ValueError,match='ownership differs'):
        _preview(snapshot,state)
