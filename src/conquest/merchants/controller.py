"""Merchant decisions and durable, verified transactions, independent of Windows."""
from conquest.valuables import require_marketable, storage_only
from dataclasses import asdict
import hashlib
import json
import time
import uuid
from conquest.merchants.pricing import OWNED, Listing, quote_item, validate_booth_price
from conquest.capture import CaptureUnavailable
from conquest.character_context import trusted_delivery


class ListingNotSubmitted(CaptureUnavailable):
    """Driver guarantees it did not attempt the final listing confirmation."""


def unsubmitted_listing_safe_to_retry(before, after):
    return (after.get('character')==before['snapshot']['character']
        and after.get('identity')==before['snapshot']['identity']
        and after.get('booth_open') and not after.get('request') and not after.get('trade')
        and not any(w['name']=='Add Item to Booth' for w in after.get('windows',[]))
        and not any(i['uid']==before['uid'] for i in after['booth'])
        and identities(after['inventory']).get(before['uid'])==identities([before['item']])[before['uid']])


def identities(items):
    result = {}
    for item in items:
        uid = item['uid']
        if type(uid) is not int or uid <= 0 or uid in result or type(item['quantity']) is not int or item['quantity'] <= 0:
            raise ValueError('Unreliable item identities or quantities')
        result[uid] = (item['type_id'],item['plus'],item['gem1'],item['gem2'],item['quantity'])
    return result


def offer_fingerprint(trade):
    return hashlib.sha256(json.dumps(trade,sort_keys=True).encode()).hexdigest()


def validate_trade(snapshot):
    trade = snapshot.get('trade')
    if not trade or not trusted_delivery(snapshot.get('character'),trade.get('participant'),trade.get('participant_uid')):
        raise ValueError('Only explicitly trusted, verified deliveries are accepted')
    if trade['own_items'] or trade['own_silver'] != 0:
        raise ValueError('Merchant-side items and silver must be zero')
    offered = identities(trade['items'])
    before = identities(snapshot['inventory'])
    if not offered or set(offered) & set(before):
        raise ValueError('Empty or ambiguous delivery')
    if len(offered) > snapshot['capacity']-len(before):
        raise ValueError('Insufficient merchant inventory space')
    if any(item.get('bound') for item in trade['items']):
        raise ValueError('Delivery contains a bound item')
    if any(storage_only(item) for item in trade['items']):
        raise ValueError('Rare Dragonballs must use storage, not merchant refill')
    if type(trade['other_silver']) is not int or trade['other_silver'] != 0:
        raise ValueError('Delivery silver must be zero on both sides')
    return trade


def received(before, after):
    trade = validate_trade(before)
    if after['character'] != before['character'] or after.get('trade'):
        return False
    old, new, offered = identities(before['inventory']), identities(after['inventory']), identities(trade['items'])
    return (new == {**old,**offered}
        and after['silver'] == before['silver']+trade['other_silver'])


def listing_received(before, after):
    return (not any(i['uid']==before['uid'] for i in after['inventory'])
        and any(i['uid']==before['uid'] and i['price']==before['price']
            and identities([i])==identities([before['item']]) for i in after['booth']))


class MerchantController:
    listing_purpose='listing'
    def __init__(self, character, journal, driver, coordinator, *, clock=time.time):
        self.character,self.journal,self.driver,self.coordinator,self.clock = character,journal,driver,coordinator,clock

    def active(self):
        return self.journal.get(self.character,'enabled',False) and not self.coordinator.stopped

    def check(self):
        if not self.active():
            raise CaptureUnavailable('Merchant is paused')
        self.coordinator.check()

    def check_listing(self):
        self.check()
        from conquest.merchants.delivery_reservation import active
        if active(self.journal,self.character):
            raise CaptureUnavailable('Reserved farmer delivery holds merchant listings until both inventories reconcile')

    def reconcile(self, snapshot):
        pending = self.journal.pending(self.character)
        for record in pending:
            before = json.loads(record['before_json'])
            if record['kind'] == 'delivery' and received(before,snapshot):
                self.journal.transition(record['id'],'verified',{'items':before['trade']['items']})
            elif record['kind'] == 'listing' and listing_received(before,snapshot):
                self.journal.transition(record['id'],'verified',{'uid':before['uid'],'price':before['price']})
            elif (record['kind']=='listing'
                  and json.loads(record.get('result_json') or '{}').get('confirmation_attempted') is False
                  and unsubmitted_listing_safe_to_retry(before,snapshot)):
                self.journal.transition(record['id'],'aborted',{'uid':before['uid'],
                    'note':'Confirmation was never attempted; memory verifies the item remains unlisted. Safe to replan.'})
            else:
                # Prepared intent is also ambiguous after a process crash.
                raise ValueError('Unfinished transaction requires reconciliation; input is paused')

    def accept_delivery(self):
        self.driver.require_qualified('trade')
        with self.coordinator.lease(self.character,purpose='trade'):
            self.check()
            before = self.driver.read()
            trade = validate_trade(before)
            from conquest.merchants.delivery_reservation import validate_receiver
            validate_receiver(self.journal,before,now=self.clock())
            accepted_request = self.journal.get(self.character,'accepted_request')
            if (not accepted_request or accepted_request.get('identity') != before['identity']
                    or accepted_request.get('participant_uid') != trade['participant_uid']
                    or not 0 <= self.clock()-accepted_request.get('opened_at',0) <= 120):
                raise ValueError('Trade was not opened by a verified incoming request')
            self.reconcile(before)
            if trade.get('accepted'):
                raise ValueError('Trade was already accepted outside this transaction')
            fingerprint = offer_fingerprint(trade)
            key = f'delivery:{self.character}:{uuid.uuid4().hex}'
            fresh = self.driver.read()
            if fresh['identity'] != before['identity'] or offer_fingerprint(validate_trade(fresh)) != fingerprint or identities(fresh['inventory']) != identities(before['inventory']) or fresh['silver'] != before['silver']:
                raise ValueError('Trade changed before confirmation')
            validate_receiver(self.journal,fresh,now=self.clock())
            self.journal.begin(key,self.character,'delivery',before)
            try:
                self.check()
                self.driver.accept_trade(fresh)
                self.journal.transition(key,'submitted')
                after = self.driver.wait_for(lambda s: received(before,s), self.check)
                self.journal.transition(key,'verified',{'items':trade['items'],'silver':trade['other_silver']})
                self.journal.set(self.character,'new_stock',True)
                self.journal.set(self.character,'accepted_request',None)
                return after
            except Exception:
                self.journal.transition(key,'uncertain')
                raise

    def accept_request(self, snapshot):
        self.driver.require_qualified('trade_request')
        with self.coordinator.lease(self.character,purpose='trade'):
            self.check()
            fresh = self.driver.read()
            if fresh['identity'] != snapshot['identity'] or fresh.get('request') != snapshot.get('request') or not fresh.get('request') or not trusted_delivery(self.character,fresh['request']['participant'],fresh['request'].get('participant_uid')):
                raise ValueError('Unverified or changed trade request')
            if len(fresh['inventory']) >= fresh['capacity']:
                raise ValueError('Inventory full; delivery deferred')
            from conquest.merchants.delivery_reservation import validate_receiver
            validate_receiver(self.journal,fresh,now=self.clock())
            self.driver.accept_request(fresh)
            opened = self.driver.wait_for(lambda s:bool(s.get('trade')) and
                trusted_delivery(self.character,s['trade']['participant'],s['trade'].get('participant_uid')),self.check)
            self.journal.set(self.character,'accepted_request',{
                'identity':opened['identity'],'participant_uid':opened['trade']['participant_uid'],
                'opened_at':self.clock()})

    def plan(self, snapshot, market, *, inventory_only=False, owned_snapshots=(), history=None):
        # Fresh, identity-verified memory supersedes the website's delayed
        # view of each owned booth, including listings that have been sold.
        live = {}
        for state in (*owned_snapshots, snapshot):
            seller = state['character'].casefold()
            if (seller in OWNED and state.get('server') == 'America'
                    and state.get('booth_open') and state.get('identity')
                    and 0 <= self.clock()-state.get('timestamp',0) <= 5):
                live[seller] = state
        owned = []
        for seller, state in live.items():
            for stock in state['booth']:
                try:
                    owned.append(Listing(seller,market.key_for(stock),stock['price'],stock['quantity']))
                except ValueError:
                    continue  # Incomplete attributes cannot provide a price anchor.
        plans = []
        for item in ([] if inventory_only else snapshot['booth'])+snapshot['inventory']:
            try:
                require_marketable(item)
                if item['bound']:
                    raise ValueError('Bound item')
                key = market.key_for(item)
                comparisons = [r for r in market.comparisons(item) if r.seller.casefold() not in live]
                decision = quote_item(key,comparisons+owned,quantity=item['quantity'],history=history,
                                      allow_plus_conversion=100000 <= item['type_id'] < 600000)
                plans.append({'uid':item['uid'],'name':item['name'],'old_price':item.get('price'),
                    'attributes':list(identities([item])[item['uid']]),
                    'observed_at':market.data['observed_at'],**asdict(decision)})
            except ValueError as error:
                plans.append({'uid':item['uid'],'name':item['name'],'old_price':item.get('price'),'price':None,'reason':str(error)})
        # One booth slot holds one listing: prioritize its verified total sale
        # price, including quantity, rather than inventory order or unit price.
        return sorted(plans,key=lambda p:(p['price'] is None,-(p['price'] or 0),p['uid']))

    def apply_price(self, plan):
        if plan['price'] is None or plan['price']==plan.get('old_price'):
            return
        validate_booth_price(plan['price'])
        with self.coordinator.lease(self.character,purpose=self.listing_purpose):
            self.check_listing()
            try:
                self.driver.require_qualified('booth_input')
                if hasattr(self.driver,'verify_listing_layout'):
                    self.driver.verify_listing_layout()
            except ValueError:
                from conquest.merchants.qualification import verify_booth_controls
                from conquest.desktop_runtime import physical_coordinates
                current = self.driver.read()
                if any(w['name']=='Add Item to Booth' for w in current.get('windows',[])):
                    raise ValueError('Existing price dialog needs reconciliation before automatic verification')
                with self.driver.observer.lock,physical_coordinates():
                    verify_booth_controls(self.driver,self.journal,self.check_listing)
                self.driver.require_qualified('booth_input')
            before = self.driver.read()
            self.reconcile(before)
            if before.get('request') or before.get('trade'):
                raise CaptureUnavailable('Close the trade before changing booth listings')
            if not before['booth_open']:
                raise ValueError('Merchant booth is not open; listing deferred')
            uid,target = plan['uid'],plan['price']
            found = [i for i in before['booth']+before['inventory'] if i['uid']==uid]
            if len(found)!=1 or found[0]['bound']:
                raise ValueError('Stock changed before pricing')
            item = found[0]
            require_marketable(item)
            if (not 0 <= self.clock()-plan.get('observed_at',0) <= 900
                    or list(identities([item])[uid]) != plan.get('attributes')):
                raise ValueError('Comparison expired or item changed; scan again')
            if item.get('price') != plan.get('old_price'):
                raise ValueError('Price changed since planning')
            if item.get('price') is None and len(before['booth'])>=32:
                return
            if item.get('price') is not None and len(before['inventory'])>=before['capacity']:
                raise ValueError('Need one inventory slot to reprice this item')
            self.driver.prepare_listing(before,item,self.check_listing)
            key = f'listing:{self.character}:{uuid.uuid4().hex}'
            self.journal.begin(key,self.character,'listing',{'uid':uid,'price':target,'item':item,'snapshot':before})
            try:
                self.driver.list_item(before,item,target,self.check_listing)
                self.journal.transition(key,'submitted')
                after = self.driver.wait_for(lambda s:listing_received({'uid':uid,'price':target,'item':item},s),self.check_listing)
                self.journal.transition(key,'verified',{'uid':uid,'name':item['name'],'old_price':item.get('price'),'price':target})
                return after
            except Exception as error:
                import traceback
                self.journal.transition(key,'uncertain',{
                    'confirmation_attempted':not isinstance(error,ListingNotSubmitted),
                    'error_type':type(error).__name__,
                    'note':str(error) if isinstance(error,(ValueError,CaptureUnavailable)) else 'Listing operation failed',
                    'frames':[{'file':f.filename.replace('\\','/').rsplit('/',1)[-1],
                               'function':f.name,'line':f.lineno}
                              for f in traceback.extract_tb(error.__traceback__)]})
                raise
