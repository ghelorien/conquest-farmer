"""Explain delivery prerequisites without acquiring input or granting permission."""
from conquest.discord_notify import read_json
from conquest.merchants.journal import CHARACTERS
from conquest.merchants.recovery import credential_path

RECEIVER_CAPABILITIES=('trade_request','trade','booth_input','inventory_panel',
                       'login','market_return','booth_setup','booth_panel')


def describe(ui,farmer_driver):
    try:
        farmer_driver(ui).require_qualified()
        farmer_qualified=True
    except (ValueError,OSError,AttributeError):
        farmer_qualified=False
    policy=read_json('profiles/merchant-deliveries.json')
    runtime=getattr(ui,'runtime',None)
    controllers=getattr(runtime,'controllers',{})
    merchants={}
    for character in CHARACTERS:
        controller=controllers.get(character)
        missing=[]
        for capability in RECEIVER_CAPABILITIES:
            try:
                if controller is None:raise ValueError('Merchant is not attached')
                controller.driver.require_qualified(capability)
            except (ValueError,OSError,AttributeError):missing.append(capability)
        credentials_present=credential_path(character).is_file()
        merchants[character]={'attached':controller is not None,
            'credentials_present':credentials_present,
            'missing_qualifications':missing,
            'trading_enabled':bool(runtime and runtime.enabled(character))}
    blockers=[]
    from conquest.merchants.farmer_preferences import enabled,rollout_enabled,rollout_source
    from conquest.merchants.farmer_identity import ui_character
    if not enabled(ui_character(ui)):blockers.append('farmer_transfers_off')
    rollout=rollout_enabled(ui_character(ui),policy=policy)
    if not rollout:blockers.append('delivery_rollout_disabled')
    if not farmer_qualified:blockers.append('farmer_trade_controls_unqualified')
    for character,state in merchants.items():
        if not state['credentials_present']:blockers.append(f'{character}:credentials_missing')
        blockers.extend(f'{character}:{capability}' for capability in state['missing_qualifications'])
        if not state['trading_enabled']:blockers.append(f'{character}:trading_paused')
    # Preserve the existing farmer-only field used by route preflights. The
    # stricter diagnostic is not an authorization token: submission still
    # rechecks both live inventories, participants, capacity and input owner.
    return {'qualified':farmer_qualified,'qualification_scope':'farmer_trade_controls',
            'configured_prerequisites_met':not blockers,'blockers':blockers,
            'merchants':merchants,
            'delivery_enabled':rollout,'parity_verified':policy.get('parity_verified') is True,
            'delivery_setting_source':rollout_source(ui_character(ui)),
            'hunting_handoffs_enabled':bool(policy.get('hunting_handoffs_enabled')),
            'live_submission_checks_required':True}
