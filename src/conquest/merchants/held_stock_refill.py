"""Refill verified current stock without erasing an older recovery incident."""
import time
from conquest.merchants.controller import identities


def market_ready(runtime, character, snapshot):
    evidence=runtime.journal.get(character,'held_stock_refill') or {}
    observer=runtime.observers.get(character)
    return bool(snapshot and evidence.get('qualified') and observer
        and snapshot.get('identity')==evidence.get('identity')==observer.adapter.identity
        and snapshot.get('own_booth_uid')==evidence.get('booth_uid')
        and snapshot.get('map_id')==1036 and snapshot.get('booth_open')
        and not snapshot.get('trade') and not snapshot.get('request')
        and 0 <= time.time()-snapshot.get('timestamp',0) <= 2
        and not runtime.journal.pending(character)
        and not runtime.journal.get(character,'connect_hold',False))


def allowed(runtime, character, snapshot):
    state=runtime.returns[character].state() or {}
    if state.get('phase')!='needs_attention' or not runtime.refill_enabled(character): return False
    if (snapshot.get('map_id')!=1036 or not snapshot.get('booth_open')
            or not snapshot.get('own_booth_uid') or snapshot.get('trade') or snapshot.get('request')
            or not 0 <= time.time()-snapshot.get('timestamp',0) <= 2
            or runtime.journal.pending(character)
            or runtime.journal.get(character,'connect_hold',False)):
        return False
    observer=runtime.observers.get(character)
    if observer is None or observer.adapter.identity != snapshot.get('identity'): return False
    current=identities(snapshot['inventory']+snapshot['booth'])
    # Two observations pin the account and its own booth. Every submitted item
    # is checked again by the normal refill driver before and after its click.
    evidence=runtime.journal.get(character,'held_stock_refill') or {}
    identity=snapshot['identity']; booth=snapshot['own_booth_uid']
    if evidence.get('identity')!=identity or evidence.get('booth_uid')!=booth:
        runtime.journal.set(character,'held_stock_refill',{
            'identity':identity,'booth_uid':booth,'first_seen':snapshot['timestamp'],'qualified':False})
        return False
    if snapshot['timestamp'] <= evidence['first_seen']: return False
    if not evidence.get('qualified'):
        before=state.get('before') or {}
        previous=identities(before.get('inventory',[])+before.get('booth',[]))
        evidence.update(qualified=True,verified_at=snapshot['timestamp'],current_snapshot=snapshot,
            missing_uids=sorted(previous.keys()-current.keys()),new_uids=sorted(current.keys()-previous.keys()),
            incident_unresolved=True,original_return_state=state)
        runtime.journal.set(character,'held_stock_refill',evidence)
        runtime.journal.event(character,'held_stock_refill_qualified',
            missing_uids=evidence['missing_uids'],new_uids=evidence['new_uids'],incident_unresolved=True)
    return True
