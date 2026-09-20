"""Managed observations layered over the portable archer shop catalog seed."""
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json,write_json


SEED=Path('profiles/archer-shop-catalog.json')
RUNTIME=Path(state_path('.runtime/archer-shop-catalog.json'))


def observed_catalog():
    observed=read_json(RUNTIME,{})
    return observed if isinstance(observed,dict) else {}


def catalog():
    seed=read_json(SEED,{})
    observed=observed_catalog()
    if not isinstance(seed,dict):seed={}
    result=dict(seed);cities={}
    for source in (seed,observed):
        for city,vendors in source.get('cities',{}).items() if isinstance(source.get('cities'),dict) else ():
            if isinstance(vendors,dict):cities[str(city)]={**cities.get(str(city),{}),**vendors}
    result['cities']=cities
    return result


def record(map_id,vendor,products,*,observed_at):
    saved=observed_catalog()
    cities=saved.get('cities')
    if not isinstance(cities,dict):cities={};saved['cities']=cities
    vendors=cities.get(str(map_id))
    if not isinstance(vendors,dict):vendors={};cities[str(map_id)]=vendors
    vendors[str(vendor)]={
        'observed_at':observed_at,'products':products}
    write_json(RUNTIME,saved)
