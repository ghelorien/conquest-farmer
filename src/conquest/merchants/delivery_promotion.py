"""Promote only the app's current verified staged exchange; never send input."""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from conquest.character_context import current, registry, merchant_directory, state_path
from conquest.discord_notify import read_json
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants import delivery_probe, delivery_qualification
from conquest.merchants.delivery import reconcile, exact_items, exact_listings, validate_snapshot
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery_confirm_probe import _merchant_key
from conquest.merchants.farmer_qualification import qualification_path
from conquest.merchants.journal import character_name
from conquest.recovery_override import evidence_digest


@contextmanager
def available(lock):
    if not lock.acquire(blocking=False):
        raise ValueError('Wait for the current merchant operation before qualification promotion')
    try:yield
    finally:lock.release()


def validate_candidate(observer,candidate,farmer,merchant,state):
    """The mutable layout file must still describe the live pinned client."""
    from conquest.merchants.farmer_trade import _recipient_record
    from conquest.merchants.memory import GuiReader
    from conquest.merchants.farmer_trade import targeting_state
    intent=state.get('intent',{});saved=state.get('recipient',{});expected=intent.get('merchant',{})
    def point(value):
        return isinstance(value,list) and len(value)==2 and all(type(axis) is int for axis in value)
    if (type(saved.get('address')) is not int or saved['address']<=0
            or type(saved.get('uid')) is not int or saved['uid']<=0
            or not isinstance(saved.get('name'),str) or not saved['name']
            or not point(saved.get('position')) or not point(saved.get('point'))
            or saved['uid']!=expected.get('character_uid')
            or saved['name']!=expected.get('character')
            or saved['position']!=expected.get('position')):
        raise ValueError('Verified recipient receipt is incomplete or differs from the delivery merchant')
    gui_size=GuiReader.for_session(observer.adapter).viewport_size()
    if observer.operations.target.snapshot()['client_size']!=gui_size:
        raise ValueError('Trade layout requires matching native and memory GUI dimensions')
    # Promotion reads the layout only. An Inventory panel left open after the
    # completed offer need not leave this actor's point clickable.
    actor=_recipient_record(observer,{**candidate,'gui_size':gui_size},merchant)
    # Scene-object addresses are heap allocations, not player identities.  A
    # completed trade can legitimately rebuild the receiver's scene object;
    # promotion sends no target input and therefore only the immutable UID and
    # name bind the historical recipient to this fresh, qualified projection.
    if any(actor.get(key)!=saved.get(key) for key in ('uid','name')):
        raise ValueError('Current recipient layout differs from the verified request')
    native=targeting_state(observer.adapter)
    if any(candidate.get('target_mode',{}).get(key)!=native[key] for key in ('rva','value')):
        raise ValueError('Trade target mode differs from the verified native accessor')
    return actor


_MALFORMED=object()


def _json(value):
    """Parse durable journal JSON without allowing malformed evidence to prove work."""
    try:
        parsed=json.loads(value)
    except (TypeError,ValueError):
        return _MALFORMED
    return parsed


def _number(value):
    return type(value) in (int,float) and math.isfinite(value)


def _same_participant(snapshot, receipt):
    return all(snapshot.get(field)==receipt.get(field) for field in
               ('identity','character','character_uid','server','silver'))


def _same_replay_participant(snapshot, receipt):
    """Fields that cannot change while native refill opens an owned booth."""
    return _same_participant(snapshot,receipt) and all(snapshot.get(field)==receipt.get(field) for field in
        ('capacity','map_id','own_booth_uid')) and type(receipt.get('own_booth_uid')) is int \
        and receipt['own_booth_uid']>0 and snapshot.get('booth_open') is True


def _stock(snapshot):
    """Canonical inventory/booth stock, or None for an incomplete snapshot."""
    try:
        return exact_items(snapshot['inventory']),exact_listings(snapshot.get('booth',[]))
    except (KeyError,TypeError,ValueError):
        return None


def _verified_listing(journal, row, before, result, expected_character):
    """Return canonical evidence only for a complete native listing receipt."""
    key=row['id']
    database_character=character_name(expected_character)
    if (row.get('character')!=database_character or row.get('kind')!='listing'
            or row.get('phase')!='verified' or not key.startswith(f'listing:{str(database_character)}:')
            or not _number(row.get('created')) or not _number(row.get('updated'))
            or row['created']>row['updated']):
        return None
    if not isinstance(before,dict) or not isinstance(result,dict):
        return None
    # A listing uses the same target price in its durable plan and terminal
    # result.  It is never a receipt for a trade, sale, or arbitrary move.
    if (type(before.get('uid')) is not int or before.get('uid')!=result.get('uid')
            or type(before.get('price')) is not int or before['price']<=0
            or result.get('price')!=before['price'] or not isinstance(before.get('item'),dict)
            or before['item'].get('uid')!=before['uid']):
        return None
    with journal.db() as db:
        steps=[dict(step) for step in db.execute(
            'SELECT stage,status,payload,timestamp FROM transaction_steps WHERE transaction_id=? ORDER BY id',(key,))]
        events=[dict(event) for event in db.execute(
            "SELECT event,payload,timestamp FROM events WHERE character=? AND event='listing_verified' ORDER BY id",
            (database_character,))]
    phases=[]
    for step in steps:
        payload=_json(step.get('payload'))
        if payload is _MALFORMED or not _number(step.get('timestamp')) or not row['created']<=step['timestamp']<=row['updated']:
            return None
        if step.get('stage')=='transaction':phases.append((step.get('status'),payload,step['timestamp']))
    if [phase[0] for phase in phases] != ['prepared','submitted','verified']:
        return None
    if (phases[0][1]!={} or phases[1][1] is not None
            or phases[-1][1]!=result or phases[-1][2]!=row['updated']):
        return None
    matching=[]
    for event in events:
        payload=_json(event.get('payload'))
        if isinstance(payload,dict) and payload.get('transaction_id')==key:
            matching.append((event,payload))
    if not (len(matching)==1 and matching[0][1].get('result')==result
            and _number(matching[0][0].get('timestamp'))
            and matching[0][0]['timestamp']==row['updated']):
        return None
    return {'id':key,'created':row['created'],'updated':row['updated'],
            'before':before,'result':result,
            'steps':[{'stage':step['stage'],'status':step['status'],
                      'payload':_json(step['payload']),'timestamp':step['timestamp']} for step in steps],
            'event':{'event':matching[0][0]['event'],'payload':matching[0][1],
                     'timestamp':matching[0][0]['timestamp']}}


def _listing_chain_reconciles(state, journal, farmer, merchant, *, now):
    """Accept only a complete post-delivery, verified owned-booth evidence chain.

    This is deliberately narrower than delivery reconciliation.  It exists for
    the harmless inventory-to-owned-booth mutation made by native refill after
    a completed delivery, and rejects all other later mutations.
    """
    intent=state.get('intent',{})
    staged_farmer,state_merchant=state.get('farmer_after'),state.get('merchant_after')
    verified_at=state.get('verified_at')
    if (not isinstance(staged_farmer,dict) or not isinstance(state_merchant,dict)
            or not _number(verified_at) or verified_at>now):
        return False
    try:
        staged_at=max(staged_farmer['timestamp'],state_merchant['timestamp'])
        # The terminal bilateral snapshots are the first link.  Current
        # ownership alone is never evidence of the historical delivery.
        if staged_at>verified_at or not reconcile(intent,staged_farmer,state_merchant,now=staged_at):
            return False
        validate_snapshot(farmer,intent['farmer']['character'],now)
        validate_snapshot(merchant,intent['merchant']['character'],now)
        delivered=exact_items(intent['items'])
        initial_inventory,initial_booth=_stock(state_merchant)
        current_farmer,current_booth=_stock(farmer)
        current_inventory,current_merchant_booth=_stock(merchant)
    except (KeyError,TypeError,ValueError):
        return False
    if (not _same_participant(farmer,staged_farmer) or not _same_participant(merchant,state_merchant)
            or current_farmer!=exact_items(staged_farmer['inventory'])
            or current_booth!=exact_listings(staged_farmer.get('booth',[]))
            or farmer.get('trade') is not None or merchant.get('trade') is not None
            or farmer.get('request') is not None or merchant.get('request') is not None
            or merchant.get('booth_open') is not True
            or type(state_merchant.get('own_booth_uid')) is not int
            or state_merchant['own_booth_uid']<=0
            or merchant.get('own_booth_uid')!=state_merchant['own_booth_uid']):
        return False
    # The staged receipt must put every offered UID in merchant inventory with
    # its immutable attributes; otherwise no later journal row can repair it.
    if any(initial_inventory.get(uid)!=attributes or uid in initial_booth
           for uid,attributes in delivered.items()):
        return False
    try:
        # Replay *every* merchant operation that could have happened between
        # the staged terminal receipt and this fresh observation.  Ignoring an
        # unrelated-looking row would turn the journal into an oracle rather
        # than a complete ownership chain.
        through=max(farmer['timestamp'],merchant['timestamp'])
        database_character=character_name(intent['merchant']['character'])
        with journal.db() as db:
            rows=[dict(row) for row in db.execute(
                'SELECT * FROM transactions WHERE character=? AND created>=? AND created<=? ORDER BY created,id',
                (database_character,verified_at,through))]
    except Exception:
        return False
    expected_inventory=dict(initial_inventory);expected_booth=dict(initial_booth)
    previous_updated=verified_at
    for row in rows:
        before,result=_json(row.get('before_json')),_json(row.get('result_json'))
        # Any unverified, malformed, or non-listing operation in the interval
        # leaves an unexplained ownership transition.
        if (not _number(row.get('created')) or not _number(row.get('updated'))
                or row['created']<verified_at or row['created']<previous_updated
                or row['updated']>merchant['timestamp']
                or not _verified_listing(journal,row,before,result,intent['merchant']['character'])):
            return False
        uid=before['uid']
        try:
            item_attributes=exact_items([before['item']]).get(uid)
        except (KeyError,TypeError,ValueError):
            return False
        if item_attributes is None:
            return False
        snapshot=before.get('snapshot')
        stock=_stock(snapshot) if isinstance(snapshot,dict) else None
        try:
            validate_snapshot(snapshot,intent['merchant']['character'],row['created'])
        except (KeyError,TypeError,ValueError):
            return False
        if (not stock or not _same_replay_participant(snapshot,state_merchant)
                or stock!=(expected_inventory,expected_booth)):
            return False
        # The source item's old representation must be exactly the previous
        # chain state.  The verified transaction then changes only its owned
        # booth price, retaining the immutable item fingerprint.
        source_inventory=uid in expected_inventory
        if not source_inventory or uid in expected_booth:
            return False
        source=next((item for item in snapshot['inventory'] if item.get('uid')==uid),None)
        if (not isinstance(source,dict) or source.get('price') is not None
                or source.get('bound') is not False or source.get('type_id')==1088001
                or expected_inventory[uid]!=item_attributes or result.get('old_price') is not None
                or snapshot.get('trade') is not None or snapshot.get('request') is not None
                or snapshot.get('booth_open') is not True
                or snapshot.get('own_booth_uid')!=state_merchant['own_booth_uid']):
            return False
        expected_inventory.pop(uid)
        expected_booth[uid]=(*item_attributes,before['price'])
        previous_updated=row['updated']
    # A current booth item is not enough: it must be exactly the terminal
    # result of the complete chain, with no sale/silver/stock side effect.
    return (any(uid in current_merchant_booth for uid in delivered)
            and (current_inventory,current_merchant_booth)==(expected_inventory,expected_booth))


def _chain_manifest(state, journal, farmer, merchant, *, now):
    """Canonical, stable reference for an already validated listing replay."""
    if not _listing_chain_reconciles(state,journal,farmer,merchant,now=now):
        return None
    intent=state['intent'];verified_at=state['verified_at']
    try:
        database_character=character_name(intent['merchant']['character'])
        through=merchant['timestamp']
        with journal.db() as db:
            rows=[dict(row) for row in db.execute(
                'SELECT * FROM transactions WHERE character=? AND created>=? AND created<=? ORDER BY created,id',
                (database_character,verified_at,through))]
        listings=[]
        for row in rows:
            evidence=_verified_listing(journal,row,_json(row['before_json']),_json(row['result_json']),
                                       intent['merchant']['character'])
            if evidence is None:
                return None
            listings.append(evidence)
        def observed(snapshot):
            inventory,booth=_stock(snapshot)
            return {field:snapshot.get(field) for field in
                    ('identity','character','character_uid','server','silver','capacity','map_id',
                     'booth_open','own_booth_uid')} | {'inventory':inventory,'booth':booth}
        body={'version':1,'receipt_digest':evidence_digest(state),
              'farmer_profile_id':state.get('farmer_profile_id'),
              'merchant_profile_id':state.get('target_profile_id'),
              'delivered':exact_items(intent['items']),'verified_at':verified_at,
              'merchant_transactions':listings,
              'fresh':{'farmer':observed(farmer),'merchant':observed(merchant)}}
        raw=json.dumps(body,sort_keys=True,separators=(',',':')).encode('utf-8')
        return {**body,'digest':hashlib.sha256(raw).hexdigest()}
    except (KeyError,TypeError,ValueError,OSError):
        return None


def _archive_chain_manifest(receipt, manifest):
    """Publish a never-overwritten, fsynced chain receipt beside delivery audit evidence."""
    if not manifest or not isinstance(manifest.get('digest'),str):
        return None
    body={key:value for key,value in manifest.items() if key!='digest'}
    raw=json.dumps(body,sort_keys=True,separators=(',',':')).encode('utf-8')
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=manifest['digest']:
        raise ValueError('Listing chain manifest digest changed')
    # Keep this beside, rather than below, the receipt audit directory: deep
    # profile roots can otherwise exceed native Windows component-path limits.
    path=Path(receipt).parent.parent/'delivery-promotion-chain-audit'/f'{digest}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        with path.open('r+b') as stream:
            if stream.read()!=raw:raise ValueError('Listing chain manifest differs from its audit receipt')
            stream.flush();os.fsync(stream.fileno())
        return path
    fd,name=tempfile.mkstemp(prefix=digest+'.',suffix='.tmp',dir=path.parent);temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:
            if stream.write(raw)!=len(raw):raise OSError('Incomplete listing chain manifest')
            stream.flush();os.fsync(stream.fileno())
        try:os.link(temporary,path)
        except FileExistsError:pass
        with path.open('r+b') as stream:
            if stream.read()!=raw:raise ValueError('Listing chain manifest differs from its audit receipt')
            stream.flush();os.fsync(stream.fileno())
    finally:
        temporary.unlink(missing_ok=True)
    return path


def promote_current(ui):
    """Resolve paths and participants in-process, without changing user intent."""
    with available(ui.coordinator.lock),ExitStack() as locks:
        state=delivery_probe.read_probe(read_only=True)
        if not state or state.get('phase')!='delivery_verified' or not state.get('verified_at'):
            raise ValueError('Promotion requires the current verified delivery receipt')
        digest=evidence_digest(state)
        character=_merchant_key(state)
        context=current();profiles=registry()
        farmer_id=context.profile.id if context else 'Farmer'
        if (profiles is not None and context is None
                or state.get('farmer_profile_id')!=farmer_id
                or context and (context.profile.role!='Farmer' or not context.profile.local_enabled)):
            raise ValueError('Delivery receipt belongs to a different farmer profile')
        observer=ui.app.observer;receiver=ui.runtime.observers.get(character)
        controller=ui.runtime.controllers.get(character)
        if observer is None or receiver is None or controller is None or controller.driver.observer is not receiver:
            raise ValueError('Both exact delivery observers must be attached before promotion')
        for account in (observer,receiver):locks.enter_context(available(account.lock))
        paths={
            'receipt':Path(state_path('reports/merchants/delivery-request-probe.json')),
            'candidate':Path(state_path('reports/merchants/trade-layout-candidate.json')),
            'farmer':qualification_path(observer,migrate=False),
            'merchant':merchant_directory(character)/'qualification.json'}
        if (delivery_probe.JOURNAL.resolve()!=paths['receipt'].resolve()
                or controller.driver.qualification.resolve()!=paths['merchant'].resolve()):
            raise ValueError('Delivery qualification paths differ from the selected profiles')
        candidate=read_json(paths['candidate'])
        from conquest.memory_build_layout import CLIENT_SHA256_1078
        build=candidate.get('client_sha256')
        if build not in (CLIENT_SHA256,CLIENT_SHA256_1078):
            raise ValueError('Trade layout build differs from the verified delivery')
        if build==CLIENT_SHA256_1078:
            from conquest.merchants.trade_driver_1078 import validate_receipt
            validate_receipt(state)
        candidate_digest=evidence_digest(candidate)
        def merchant_profile_bytes():
            try:return paths['merchant'].read_bytes()
            except FileNotFoundError:
                if build==CLIENT_SHA256_1078:return None
                raise
        merchant_before=merchant_profile_bytes()
        if merchant_before is not None:
            try:peer=json.loads(merchant_before)
            except (ValueError,UnicodeError) as error:
                raise ValueError('Existing merchant qualification is unreadable; preserve it for reconciliation') from error
            if (not isinstance(peer,dict) or peer.get('character')!=state['intent']['merchant']['character']
                    or peer.get('client_sha256') not in (CLIENT_SHA256,build)):
                raise ValueError('Existing merchant qualification belongs to another participant or build')
        control=ui.app.control.snapshot()
        intent=state['intent']
        chain_manifest=None
        promotion_mode=None

        def guard():
            nonlocal chain_manifest,promotion_mode
            delivery_probe.recovery_available(ui)
            ui.coordinator.check()
            if (ui.coordinator.owner is not None or not ui.safe_to_yield()
                    or ui.app.control.snapshot()!=control or control['enabled'] or control.get('paused')
                    or getattr(ui,'closed',False) or getattr(ui.app,'closing',False)
                    or getattr(ui,'calibrating',()) or getattr(ui.runtime,'refilling',{})
                    or getattr(ui.runtime,'delivery_window',None) or getattr(ui.runtime,'refill_window',None)):
                raise ValueError('Release all delivery input before qualification promotion')
            if (ui.app.observer is not observer or ui.runtime.observers.get(character) is not receiver
                    or ui.runtime.controllers.get(character) is not controller
                    or controller.driver.observer is not receiver
                    or current()!=context or _merchant_key(state)!=character):
                raise ValueError('Delivery participant attachment or profile changed')
            if any(ui.coordinator.manual_session_blocked(owner) for owner in (farmer_id,character)):
                raise ValueError('Manual visitor session holds a delivery participant')
            if ui.runtime.journal.pending(character):
                raise ValueError('Reconcile pending merchant transactions before promotion')
            if (evidence_digest(delivery_probe.read_probe(read_only=True))!=digest
                    or evidence_digest(read_json(paths['candidate']))!=candidate_digest
                    or merchant_profile_bytes()!=merchant_before):
                raise ValueError('Delivery receipt or qualification evidence changed before promotion')
            for role,account in (('farmer',observer),('merchant',receiver)):
                expected=intent[role]
                if (account.adapter.expected_sha256!=build
                        or account.adapter.identity!=expected['identity']
                        or account.character!=expected['character']):
                    raise ValueError('Delivery participant process or build changed')
                account.adapter.assert_identity()
            farmer,merchant=pair(ui,character)
            for role,snapshot in (('farmer',farmer),('merchant',merchant)):
                expected=intent[role]
                if any(snapshot.get(key)!=expected.get(key) for key in
                       ('identity','character','character_uid','server')):
                    raise ValueError('Delivery participant identity differs from the verified receipt')
            if context and any(intent['farmer'].get(key)!=getattr(context.profile,key) for key in
                               ('character_uid','server')):
                raise ValueError('Verified farmer UID differs from its selected profile')
            if profiles:
                profile=profiles.resolve(state['target_profile_id'],role='Merchant',server='America')
                if profile.character_uid!=intent['merchant']['character_uid']:
                    raise ValueError('Verified merchant UID differs from its selected profile')
            observed_at=time.time()
            if reconcile(intent,farmer,merchant,now=observed_at):
                if promotion_mode not in (None,'direct'):
                    raise ValueError('Fresh delivery shape changed after listing-chain qualification')
                promotion_mode='direct'
            else:
                if promotion_mode=='direct':
                    raise ValueError('Fresh delivery shape changed after direct qualification')
                manifest=_chain_manifest(state,ui.runtime.journal,farmer,merchant,now=observed_at)
                if manifest is None:
                    raise ValueError('Fresh bilateral ownership does not reconcile the verified delivery or its verified listing chain')
                if chain_manifest is not None and manifest['digest']!=chain_manifest['digest']:
                    raise ValueError('Verified listing chain changed during qualification validation')
                chain_manifest=manifest
                promotion_mode='listing_chain'
            validate_candidate(observer,candidate,farmer,merchant,state)
            # Memory reads may have overlapped a receipt replacement or Stop.
            ui.coordinator.check()
            if (evidence_digest(delivery_probe.read_probe(read_only=True))!=digest
                    or evidence_digest(read_json(paths['candidate']))!=candidate_digest
                    or merchant_profile_bytes()!=merchant_before
                    or ui.app.control.snapshot()!=control):
                raise ValueError('Delivery authority changed during qualification validation')

        guard()
        # Later probes replace the current journal. Qualification provenance
        # must point at an immutable, fsynced copy of this completed exchange.
        receipt=delivery_probe.archive_probe(state)
        chain_evidence=_archive_chain_manifest(receipt,chain_manifest) if chain_manifest else None
        delivery_qualification.promote(receipt,paths['candidate'],paths['farmer'],paths['merchant'],
                                       chain_evidence=chain_evidence,check=guard)
        return {'promoted':True,'receipt_digest':digest,'evidence':str(receipt),
            **({'listing_chain_evidence':str(chain_evidence)} if chain_evidence else {}),
            'farmer':{'profile_id':farmer_id,'character':intent['farmer']['character'],
                'character_uid':intent['farmer']['character_uid'],'identity':intent['farmer']['identity'],
                'capabilities':{'farmer_delivery':True}},
            'merchant':{'profile_id':state['target_profile_id'],'character':str(character),
                'character_uid':intent['merchant']['character_uid'],'identity':intent['merchant']['identity'],
                'capabilities':{'trade_request':True,'trade':True}},
            'permissions_changed':False,'input_sent':False}
