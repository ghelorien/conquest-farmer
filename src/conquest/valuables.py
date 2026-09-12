"""Shared valuable policy, mapped to the pinned client definition table.

See profiles/valuable-items.json for read-only live qualification provenance.
Names only fail closed for storage protection; pickup always uses exact type IDs.
"""
import re

DRAGONBALL_NAMES = {1088000: 'DragonBall', 720028: 'DBScroll', 2000031: '1-StarDragonBall', 2000032: '2-StarDragonBall', 2000033: '3-StarDragonBall', 2000034: '4-StarDragonBall', 2000035: '5-StarDragonBall', 2000036: '6-StarDragonBall', 2000037: '7-StarDragonBall', 2000038: 'EpicDragonBall'}
DRAGONBALL_TYPES = frozenset(DRAGONBALL_NAMES)
STORAGE_ONLY_TYPES = frozenset((2000031, 2000032, 2000033, 2000034, 2000035, 2000036, 2000037, 2000038))
SPECIAL_LOOT_TYPES = DRAGONBALL_TYPES | {1088001,720027}


def storage_only(item):
    get=item.get if isinstance(item,dict) else lambda k,d=None:getattr(item,k,d)
    if get('type_id') in STORAGE_ONLY_TYPES:return True
    name=get('name','')
    return isinstance(name,str) and bool(re.fullmatch(r'(?:[1-9][0-9]*[- ]?Star|Epic)DragonBall',name,re.I))


def require_marketable(item):
    if storage_only(item):
        raise ValueError('Rare Dragonball is storage-only; automatic sale is forbidden')
