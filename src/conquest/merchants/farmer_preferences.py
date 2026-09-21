"""Local farmer delivery intent; operator rollout is not validation evidence."""
from pathlib import Path
from conquest.discord_notify import read_json,write_json
from conquest.character_context import farmer_name, state_path

PATH=Path('.runtime/farmer-transfer-preferences.json')


def enabled(character=None):
    # New installations require one explicit operator enable.  Existing
    # saved `true` preferences retain their intent unchanged.
    return read_json(state_path(PATH)).get('farmers',{}).get(character or farmer_name(),False) is True


def set_enabled(character,value):
    if not isinstance(character,str) or not character.strip() or type(value) is not bool:
        raise ValueError('A farmer name and boolean transfer setting are required')
    settings=read_json(state_path(PATH))
    settings.setdefault('farmers',{})[character]=value
    write_json(state_path(PATH),settings)


def rollout_enabled(character=None,*,policy=None):
    character=character or farmer_name()
    settings=read_json(state_path(PATH))
    if settings.get('farmers',{}).get(character,False) is not True:return False
    override=settings.get('rollout',{}).get(character)
    if type(override) is bool:return override
    policy=read_json('profiles/merchant-deliveries.json') if policy is None else policy
    return policy.get('enabled') is True and policy.get('parity_verified') is True


def rollout_source(character=None):
    override=read_json(state_path(PATH)).get('rollout',{}).get(character or farmer_name())
    return 'operator' if type(override) is bool else 'packaged_policy'


def set_delivery_enabled(character,value):
    """One explicit toggle, one atomic file; never modify packaged policy."""
    if not isinstance(character,str) or not character.strip() or type(value) is not bool:
        raise ValueError('A farmer name and boolean delivery setting are required')
    settings=read_json(state_path(PATH))
    settings.setdefault('farmers',{})[character]=value
    settings.setdefault('rollout',{})[character]=value
    write_json(state_path(PATH),settings)


def permits_new_delivery(character=None):
    character=character or farmer_name()
    if not enabled(character):raise ValueError('Merchant transfers are Off for '+character)
