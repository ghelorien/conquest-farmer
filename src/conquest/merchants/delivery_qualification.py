"""Promote only the control capabilities proven by a reconciled live exchange."""
from pathlib import Path
from conquest.discord_notify import read_json,write_json
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.delivery import reconcile,exact_items


def promote(receipt_path,candidate_path,farmer_path,merchant_path):
    state=read_json(receipt_path);candidate=read_json(candidate_path)
    intent=state.get('intent',{});farmer=state.get('farmer_after',{});merchant=state.get('merchant_after',{})
    if (state.get('phase')!='delivery_verified' or candidate.get('client_sha256')!=CLIENT_SHA256
            or not state.get('verified_at') or not intent.get('items')
            or not reconcile(intent,farmer,merchant,now=max(farmer.get('timestamp',0),merchant.get('timestamp',0)))):
        raise ValueError('Qualification requires an exact completed two-account receipt')
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
    profile=dict(character=farmer['character'],server='America',client_sha256=CLIENT_SHA256,
        native_trade_layout_revision=1,controls=controls,recipient=candidate['recipient'],target_mode=candidate['target_mode'],
        gui_size=[int(hud['geometry'][0]*2+hud['geometry'][2]),int(hud['geometry'][1]+hud['geometry'][3])],
        evidence=str(Path(receipt_path)),capabilities={'farmer_delivery':True})
    profile['client_size']=profile['gui_size']
    peer=read_json(merchant_path)
    if peer.get('character')!=merchant['character'] or peer.get('client_sha256')!=CLIENT_SHA256:
        raise ValueError('Merchant profile does not match the verified recipient')
    peer.setdefault('controls',{}).update(
        accept_request=dict(window='###Confirm',mode='native_trade_request',label='Accept'),
        accept_trade=dict(window='Trade##TradeWindow',mode='native_trade_confirm',label='Accept Trade'))
    peer.setdefault('capabilities',{}).update(trade_request=True,trade=True)
    peer.setdefault('trade_evidence',[]).append(str(Path(receipt_path)))
    write_json(farmer_path,profile);write_json(merchant_path,peer)
    return {'farmer':farmer['character'],'merchant':merchant['character'],'items':len(intent['items'])}
