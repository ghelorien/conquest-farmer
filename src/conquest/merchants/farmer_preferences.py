"""Per-character delivery preference, independent of trading and rollout gates."""
from pathlib import Path
from conquest.discord_notify import read_json,write_json
from conquest.character_context import farmer_name, state_path

PATH=Path('.runtime/farmer-transfer-preferences.json')


def enabled(character=None):
    return read_json(state_path(PATH)).get('farmers',{}).get(character or farmer_name(),True) is True


def set_enabled(character,value):
    if not isinstance(character,str) or not character.strip() or type(value) is not bool:
        raise ValueError('A farmer name and boolean transfer setting are required')
    settings=read_json(state_path(PATH))
    settings.setdefault('farmers',{})[character]=value
    write_json(state_path(PATH),settings)


def permits_new_delivery(character=None):
    character=character or farmer_name()
    if not enabled(character):raise ValueError('Merchant transfers are Off for '+character)
