"""Per-character delivery preference, independent of trading and rollout gates."""
from pathlib import Path
from conquest.discord_notify import read_json,write_json

PATH=Path('.runtime/farmer-transfer-preferences.json')


def enabled(character='Parasite'):
    return read_json(PATH).get('farmers',{}).get(character,True) is True


def set_enabled(character,value):
    if not isinstance(character,str) or not character.strip() or type(value) is not bool:
        raise ValueError('A farmer name and boolean transfer setting are required')
    settings=read_json(PATH)
    settings.setdefault('farmers',{})[character]=value
    write_json(PATH,settings)


def permits_new_delivery(character='Parasite'):
    if not enabled(character):raise ValueError('Merchant transfers are Off for '+character)
