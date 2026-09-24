"""Independent, portable combat speed settings for each farmer profile."""

from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field


class CombatSpeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_interval: float = Field(default=0.15, ge=0.15, le=10)
    scatter_recast_seconds: float = Field(default=0.8, ge=0.2, le=5)
    scatter_receipt_seconds: float = Field(default=0.8, ge=0.15, le=5)
    scatter_receipt_arrows: int = Field(default=3, ge=2, le=3)
    jump_arrival_seconds: float = Field(default=0.4, ge=0.15, le=2)
    jump_attack_guard_seconds: float = Field(default=0.44, ge=0.15, le=2)
    scatter_during_jump: bool = False
    coherent_projection: bool = False
    selected_target_refresh: bool = False
    packed_monster_records: bool = False
    cluster_lookahead: bool = False
    regional_search_expansion: bool = False
    counter_gap_recovery: bool = False
    cross_region_scatter: bool = False
    fast_scatter_planning: bool = False
    scene_reuse_seconds: float = Field(default=0, ge=0, le=0.15)
    torn_life_attempts: int = Field(default=1, ge=1, le=3)
    moving_observation_retry_seconds: float = Field(default=0.1, ge=0.01, le=0.5)
    force_jump_scatter: bool = True


class FarmerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character: str
    combat_speed: CombatSpeed = Field(default_factory=CombatSpeed)


def load_combat_speed(character, root="profiles/farmers"):
    if (
        not character
        or any(c in character for c in '/\\:*?"<>|')
        or character in (".", "..")
    ):
        raise ValueError("Invalid farmer profile name")
    path = Path(root) / (character + ".yaml")
    if not path.exists():
        return CombatSpeed()
    profile = FarmerProfile.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
    if profile.character != character:
        raise ValueError("Farmer speed profile belongs to another character")
    return profile.combat_speed
