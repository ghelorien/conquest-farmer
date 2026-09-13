"""Independent, portable combat speed settings for each farmer profile."""
from pathlib import Path
import yaml
from pydantic import BaseModel,ConfigDict,Field

class CombatSpeed(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action_interval:float=Field(default=.15,ge=.15,le=10)
    scatter_recast_seconds:float=Field(default=.8,ge=.2,le=5)
    scatter_receipt_seconds:float=Field(default=.8,ge=.15,le=5)
    scatter_receipt_arrows:int=Field(default=3,ge=2,le=3)
    jump_arrival_seconds:float=Field(default=.4,ge=.15,le=2)
    jump_attack_guard_seconds:float=Field(default=.44,ge=.15,le=2)
    torn_life_attempts:int=Field(default=1,ge=1,le=3)
    moving_observation_retry_seconds:float=Field(default=.1,ge=.01,le=.5)

class FarmerProfile(BaseModel):
    model_config=ConfigDict(extra='forbid')
    character:str
    combat_speed:CombatSpeed=Field(default_factory=CombatSpeed)

def load_combat_speed(character,root='profiles/farmers'):
    if not character or any(c in character for c in '/\\:*?"<>|') or character in ('.','..'):
        raise ValueError('Invalid farmer profile name')
    path=Path(root)/(character+'.yaml')
    if not path.exists():return CombatSpeed()
    profile=FarmerProfile.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))
    if profile.character!=character:raise ValueError('Farmer speed profile belongs to another character')
    return profile.combat_speed
