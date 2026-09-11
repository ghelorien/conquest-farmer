"""Normal archer ammunition selection from live equipment and shop records."""
NORMAL_ARROWS={1050000:'LuckyArrow',1050001:'IronArrow',1050002:'SpeedArrow'}
MAX_ARROW_PACKS=10


def arrow_pack_count(snapshot):
    """Count physical arrow packs, including partial packs and equipped ammo."""
    if not isinstance(snapshot,dict):
        from dataclasses import asdict
        snapshot=asdict(snapshot)
    items=[i for i in snapshot['items'] if i['type_id'] in NORMAL_ARROWS and i['amount']>0]
    ammo=snapshot.get('equipped_ammo')
    equipped=bool(ammo and ammo['type_id'] in NORMAL_ARROWS and ammo['amount']>0
                  and (ammo.get('uid') is None or not any(i.get('uid')==ammo['uid'] for i in items)))
    return len(items)+int(equipped)


def require_arrow_purchase_room(snapshot):
    if arrow_pack_count(snapshot)>=MAX_ARROW_PACKS:
        raise ValueError('Arrow purchase blocked: already carrying ten or more packs')


def eligible_arrow(product,state):
    get=product.get if isinstance(product,dict) else lambda key,default=None:getattr(product,key,default)
    return (get('type_id') in NORMAL_ARROWS and 1<=get('level',0)<=state['level']
            and get('profession',0) in (0,40,41))


def current_arrow(state,default=1050000,reserves=()):
    item=state['equipment'].get('arrows')
    if item and eligible_arrow(item,state):return item['type_id']
    levels={1050000:1,1050001:32,1050002:73}
    usable=[kind for kind in reserves if kind in levels and levels[kind]<=state['level']]
    return max(usable,key=levels.get) if usable else default


def choose_arrow_upgrade(products,state,silver,reserve=3000):
    old=state['equipment'].get('arrows',{})
    candidates=[p for p in products if eligible_arrow(p,state) and 0<p['price']<=silver-reserve
        and p['attack_min']>=old.get('attack_min',0) and p['attack_max']>=old.get('attack_max',0)
        and p['attack_min']+p['attack_max']>old.get('attack_min',0)+old.get('attack_max',0)]
    return max(candidates,key=lambda p:(p['attack_min']+p['attack_max'],-p['price']),default=None)


def review_arrows(loop,products,state,silver):
    product=choose_arrow_upgrade(products,state,silver)
    if product:
        bag=loop.town('supplies')
        carried=[i for i in bag['items'] if i['type_id']==product['type_id'] and i['amount']>0]
        if carried:uid=max(carried,key=lambda i:i['amount'])['uid']
        else:
            if arrow_pack_count(bag)>=MAX_ARROW_PACKS:
                loop.record('arrow_upgrade_deferred',activity='Using existing ammunition; ten-pack purchase cap reached')
                if hasattr(loop,'adopt_ammunition'):loop.adopt_ammunition(state)
                return state
            loop.record('arrow_upgrade_buying',activity=f"Buying {product['name']} ammunition")
            before={i['uid'] for i in bag['items']}
            loop.town('buy',vendor_type=5,type_id=product['type_id'])
            fresh=loop.town('supplies')
            matches=[i for i in fresh['items'] if i['type_id']==product['type_id'] and i['uid'] not in before]
            if len(matches)!=1:raise ValueError('Purchased arrow stack identity is uncertain')
            uid=matches[0]['uid']
        loop.town('close',window='Shop')
        try:receipt=loop.town('equip-arrows',uid=uid)
        finally:loop.town('close',window='Inventory')
        loop.record('arrows_upgraded',receipt=receipt,activity=f"Using {product['name']} ammunition")
        loop.town('open',vendor_type=5)
        state=loop.town('gear')
    if hasattr(loop,'adopt_ammunition'):loop.adopt_ammunition(state)
    return state
