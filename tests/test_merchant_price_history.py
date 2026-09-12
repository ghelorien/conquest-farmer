from dataclasses import replace
import pytest
from conquest.merchants.pricing import ItemKey, Listing, price_item, quote_item
from conquest.merchants.market import MarketSnapshot
from conquest.merchants.price_history import PriceHistory

KEY = ItemKey('Trojan Armor','Normal',2,('No socket','No socket'))


def market(at, *, present=True):
    return MarketSnapshot(dict(source='https://conqueronline.net/market',server='America',complete=True,
        observed_at=at,total=1,equipment_categories={'130':'Trojan Armor'} if present else {'500':'Bow'},
        listings=[dict(name='Coat' if present else 'Bow',category='Trojan Armor' if present else 'Bow',
            quality='Normal',plus=2,sockets=['No socket','No socket'],seller='Outside',price=130000,
            quantity=1,server='America')]),now=at)


@pytest.mark.parametrize('count',[1,2,3])
def test_one_comparable_is_enough(count):
    decision=price_item(KEY,[Listing(str(i),KEY,130000) for i in range(count)])
    assert decision.price==128700 and decision.sellers==count


def test_outliers_only_rejected_with_three_other_sellers():
    assert price_item(KEY,[Listing(str(i),KEY,p) for i,p in enumerate([10,100,100])]).price==9
    assert price_item(KEY,[Listing(str(i),KEY,p) for i,p in enumerate([10,100,100,100])]).price==99


def test_only_owned_offer_is_matched_without_discount():
    assert quote_item(KEY,[Listing('Dutch',KEY,130000)]).price==130000


def test_plus_two_uses_three_times_raw_plus_one_value_only_without_exact_quote():
    base=Listing('Outside',replace(KEY,plus=1),5_000_000)
    assert quote_item(KEY,[base]).price==15_000_000
    assert quote_item(KEY,[base,Listing('Other',KEY,10_000_000)]).price==9_900_000
    history={KEY:{'unit_price':'12000000','observed_at':100}}
    assert quote_item(KEY,[base],history=history).price==12_000_000
    assert quote_item(KEY,[replace(base,key=replace(base.key,quality='Super'))]).price is None
    assert quote_item(KEY,[replace(base,key=replace(base.key,sockets=('Empty','No socket')))]).price is None
    assert quote_item(KEY,[base],allow_plus_conversion=False).price is None
    assert quote_item(replace(KEY,plus=3),[base]).price is None


def test_historical_fallback_persists_without_repeated_undercuts_or_age_reset(tmp_path):
    path=tmp_path/'history.sqlite3'
    old=market(100)
    history=PriceHistory(path);history.remember(old)
    empty=market(200,present=False);history.remember(empty)
    restarted=PriceHistory(path)
    for _ in range(3):
        quote=quote_item(KEY,empty.listings,history=restarted.quotes())
        assert quote.price==130000 and quote.source_observed_at==100
        restarted.remember(empty)
    restarted.remember(market(50))
    assert restarted.quotes()[KEY]['observed_at']==100
    empty.history_catalog=restarted.catalog()
    stock=dict(type_id=130805,name='UnseenLevelCoat',plus=2,gem1=0,gem2=0)
    assert empty.key_for(stock)==KEY
    # Current contradictory categories cannot be hidden by historical mapping.
    empty.data['ambiguous_equipment_types']=['130']
    with pytest.raises(ValueError):empty.key_for(stock)


def test_history_retains_reference_instead_of_discounted_target(tmp_path):
    history=PriceHistory(tmp_path/'history.sqlite3');history.remember(market(100))
    assert history.quotes()[KEY]['unit_price']=='130000'
    assert quote_item(KEY,[Listing('Now',KEY,120000)],history=history.quotes()).price==118800


def test_plus_one_history_and_quantity_and_range():
    history={replace(KEY,plus=1):{'unit_price':'5000000','observed_at':10}}
    quote=quote_item(KEY,[],quantity=2,history=history)
    assert quote.price==30_000_000 and quote.source_observed_at==10
    assert quote_item(KEY,[],quantity=100,history=history).price is None
    assert quote_item(KEY,[]).price is None


def test_history_keeps_non_equipment_names_separate(tmp_path):
    snapshot=market(100)
    snapshot.rows[0].update(name='Meteor',category='Stones',quality='Normal',plus=0)
    snapshot.data['equipment_categories']={}
    snapshot=MarketSnapshot(snapshot.data,now=100)
    history=PriceHistory(tmp_path/'history.sqlite3');history.remember(snapshot)
    key=snapshot.key_for(dict(type_id=1088001,name='Meteor',plus=0,gem1=0,gem2=0))
    assert quote_item(key,[],history=history.quotes()).price==130000
    assert quote_item(replace(key,category='Stones:DragonBall'),[],history=history.quotes()).price is None


@pytest.mark.parametrize('owned_quality',['Fixed','Normal','Refined','Unique','Elite'])
@pytest.mark.parametrize('market_quality',['Fixed','Normal','Refined','Unique','Elite'])
def test_user_composition_quality_group(owned_quality,market_quality):
    key=replace(KEY,quality=owned_quality)
    assert quote_item(key,[Listing('Outside',replace(KEY,quality=market_quality),100)]).price==99
    assert quote_item(key,[Listing('Dutch',replace(KEY,quality=market_quality),100)]).price==100


@pytest.mark.parametrize('sockets',[('Empty','No socket'),('Empty','Empty'),('Normal DragonGem','No socket')])
def test_composition_group_never_crosses_socket_layouts(sockets):
    row=Listing('Outside',replace(KEY,quality='Elite',sockets=sockets),100)
    assert quote_item(KEY,[row]).price is None
    assert quote_item(replace(KEY,sockets=sockets),[row]).price==99
    assert quote_item(KEY,[replace(row,key=replace(row.key,plus=1))]).price is None
    history={row.key:{'unit_price':'100','observed_at':100}}
    assert quote_item(KEY,[],history=history).price is None


def test_quality_group_does_not_include_super_or_unplussed_items():
    assert quote_item(KEY,[Listing('Outside',replace(KEY,quality='Super'),100)]).price is None
    assert quote_item(replace(KEY,plus=0),[Listing('Outside',replace(KEY,plus=0,quality='Elite'),100)]).price is None
    row=Listing('Outside',replace(KEY,quality='Fixed',plus=1),100)
    assert quote_item(KEY,[row]).price==300
    assert quote_item(KEY,[],history={row.key:{'unit_price':'100','observed_at':100}}).price==300
