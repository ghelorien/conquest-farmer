"""Promote only the control capabilities proven by a reconciled live exchange."""
from pathlib import Path
from conquest.discord_notify import read_json,write_json
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.delivery import reconcile,exact_items


def promote(receipt_path,candidate_path,farmer_path,merchant_path,*,chain_evidence=None,check=None):
    state=read_json(receipt_path);candidate=read_json(candidate_path)
    intent=state.get('intent',{});farmer=state.get('farmer_after',{});merchant=state.get('merchant_after',{})
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    build=candidate.get('client_sha256')
    if (state.get('phase')!='delivery_verified' or build not in (CLIENT_SHA256,CLIENT_SHA256_1078)
            or not state.get('verified_at') or not intent.get('items')
            or not reconcile(intent,farmer,merchant,now=max(farmer.get('timestamp',0),merchant.get('timestamp',0)))):
        raise ValueError('Qualification requires an exact completed two-account receipt')
    if build==CLIENT_SHA256_1078 and any(
            snapshot.get('client_sha256')!=build or snapshot.get('reader_build')!='1078-canonical-trade'
            for snapshot in (farmer,merchant,intent.get('farmer',{}),intent.get('merchant',{}))):
        raise ValueError('1078 qualification requires exact-build canonical observations throughout the exchange')
    if farmer['identity']!=intent['farmer']['identity'] or merchant['identity']!=intent['merchant']['identity']:
        raise ValueError('Input qualification requires the same client processes throughout the exchange')
    if (state.get('recipient',{}).get('uid')!=intent['merchant']['character_uid']
            or state.get('recipient',{}).get('name')!=intent['merchant']['character']
            or not state.get('accept_point') or not state.get('confirm_point')
            or set(state.get('offered_uids',[]))!=set(exact_items(intent['items']))):
        raise ValueError('Live request, placement and confirmation evidence is incomplete')
    windows={w['name']:w for w in farmer['windows']}
    inventory=windows['Inventory/##ItemGrid_A800F95C'];hud=windows['##Control']
    controls={
        'start_trade':dict(window='##Control',size=list(hud['geometry'][2:]),mode='native_items_trade',label='Trade'),
        'open_inventory':dict(window='##Control',size=list(hud['geometry'][2:]),mode='native_items_trade',label='Items'),
        'inventory_item':dict(window=inventory['name'],size=list(inventory['geometry'][2:]),offset=[20,20],
            table='##ItemTable',columns=10,stride=[40,40],cell_offset=[20,20]),
        'trade_drop':dict(window='Trade##TradeWindow',mode='native_trade_drop'),
        'confirm_trade':dict(window='Trade##TradeWindow',mode='native_trade_confirm',label='Accept Trade')}
    profile=dict(character=farmer['character'],server='America',client_sha256=build,
        native_trade_layout_revision=1,controls=controls,recipient=candidate['recipient'],target_mode=candidate['target_mode'],
        gui_size=[int(hud['geometry'][0]*2+hud['geometry'][2]),int(hud['geometry'][1]+hud['geometry'][3])],
        evidence=str(Path(receipt_path)),capabilities={'farmer_delivery':True})
    profile['client_size']=profile['gui_size']
    if chain_evidence is not None:
        chain_evidence=str(Path(chain_evidence))
        if not Path(chain_evidence).is_file():
            raise ValueError('Listing-chain audit evidence is unavailable')
        profile['listing_chain_evidence']=chain_evidence
    peer=read_json(merchant_path)
    if build==CLIENT_SHA256_1078 and (not peer or
            peer.get('character')==merchant['character'] and peer.get('client_sha256')==CLIENT_SHA256):
        previous=peer
        peer={'character':merchant['character'],'character_uid':merchant['character_uid'],
              'server':'America','client_sha256':build,'controls':{},'capabilities':{},
              'evidence':str(Path(receipt_path)),'native_trade_layout_revision':1}
        if previous:peer['previous_build_qualification']=previous
    if peer.get('character')!=merchant['character'] or peer.get('client_sha256')!=build:
        raise ValueError('Merchant profile does not match the verified recipient')
    if build==CLIENT_SHA256_1078:
        from copy import deepcopy
        from conquest.merchants.trade_driver_1078 import validate_receipt,receipt_digest
        validate_receipt(state)
        for document in (profile,peer):
            document['trade_receipt_1078']=deepcopy(state)
            document['trade_receipt_1078_sha256']=receipt_digest(state)
            document['native_trade_layout_revision']=1
    peer.setdefault('controls',{}).update(
        accept_request=dict(window='###Confirm',mode='native_trade_request',label='Accept'),
        accept_trade=dict(window='Trade##TradeWindow',mode='native_trade_confirm',label='Accept Trade'))
    peer.setdefault('capabilities',{}).update(trade_request=True,trade=True)
    evidence=str(Path(receipt_path))
    if evidence not in peer.setdefault('trade_evidence',[]):peer['trade_evidence'].append(evidence)
    if chain_evidence is not None:
        peer['listing_chain_evidence']=chain_evidence
    from conquest.merchants.farmer_qualification import promotion_destination
    farmer_path,profile=promotion_destination(farmer_path,profile,farmer)
    if check is not None:check()
    write_json(farmer_path,profile);write_json(merchant_path,peer)
    return {'farmer':farmer['character'],'merchant':merchant['character'],'items':len(intent['items'])}
