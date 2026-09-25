"""Compatibility boundary between local profiles and existing behavior engines.

No active profile environment means legacy CLI/test behavior. Portable launchers
set the environment before importing engines, and subprocesses inherit it.
"""

from collections.abc import Sequence, Set
from functools import lru_cache
from pathlib import Path
import os
from conquest.character_profiles import ProfileRegistry, context_for


def registry():
    root = os.environ.get("CONQUEST_DATA_ROOT")
    return ProfileRegistry(root) if root else None


@lru_cache(maxsize=32)
def _context(root, profile_id, revision):
    return context_for(profile_id, root)


def current():
    r = registry()
    profile_id = os.environ.get("CONQUEST_PROFILE_ID")
    if not r or not profile_id:
        return None
    return _context(str(r.root), profile_id, r.path.stat().st_mtime_ns)


def farmer_name():
    ctx = current()
    return ctx.profile.name if ctx and ctx.profile.role == "Farmer" else "Parasite"


class ProfileName(str):
    def __new__(cls, name, profile_id):
        value = super().__new__(cls, name)
        value.profile_id = profile_id
        return value

    def __getnewargs__(self):
        # copy/deepcopy/pickle rebuild str subclasses through __new__; without
        # this they call ProfileName(name) and raise (live 2026-09-25 r41/r42).
        return (str(self), self.profile_id)


def is_farmer_owner(value):
    return value == "Farmer" and not isinstance(value, ProfileName)


class ProfileMap(dict):
    """Separate character keys from UI/role labels, even for a character named Farmer."""

    @staticmethod
    def key(value):
        return (
            ("character", value.profile_id) if isinstance(value, ProfileName) else value
        )

    def __getitem__(self, key):
        return super().__getitem__(self.key(key))

    def __setitem__(self, key, value):
        return super().__setitem__(self.key(key), value)

    def get(self, key, default=None):
        return super().get(self.key(key), default)


class MerchantNames(Sequence):
    def values(self):
        r = registry()
        if r is None:
            return ("Spiritual", "Dutch")
        return tuple(
            ProfileName(p.name, p.id)
            for p in r.profiles()
            if p.role == "Merchant" and p.local_enabled and p.server == "America"
        )

    def __len__(self):
        return len(self.values())

    def __getitem__(self, index):
        return self.values()[index]

    def __iter__(self):
        return iter(self.values())


class OwnedMerchants(Set):
    def values(self):
        r = registry()
        return (
            frozenset(
                p.name.casefold()
                for p in r.profiles()
                if p.role == "Merchant" and p.server == "America"
            )
            if r
            else frozenset(("spiritual", "dutch"))
        )

    def __iter__(self):
        return iter(self.values())

    def __len__(self):
        return len(self.values())

    def __contains__(self, value):
        return value in self.values()


def resolve_merchant(value):
    r = registry()
    if r:
        p = r.resolve(value, role="Merchant", server="America")
        if not p.local_enabled:
            raise ValueError("This merchant is not enabled on this PC")
        return ProfileName(p.name, p.id)
    matches = [n for n in MerchantNames() if n.casefold() == str(value).casefold()]
    if len(matches) != 1:
        raise ValueError("Unknown merchant")
    return matches[0]


def database_character(value):
    r = registry()
    if r and isinstance(value, str):
        try:
            p = r.resolve(value, role="Merchant")
            return ProfileName(p.name, p.id)
        except ValueError:
            pass  # Retain historical rows for archived/removed characters.
    return value


def trusted_delivery(merchant, participant, uid=None, *, require_uid=True):
    r = registry()
    if r is None:
        return participant == "Parasite" and (not require_uid or bool(uid))
    try:
        p = r.resolve(merchant, role="Merchant", server="America")
    except ValueError:
        return False
    return any(
        t["name"] == participant
        and t["server"] == p.server
        and (not require_uid or t["character_uid"] == uid)
        for t in p.trusted_sources
    )


def credential_for(merchant):
    r = registry()
    if r:
        return context_for(
            r.resolve(merchant, role="Merchant", server="America").id, r.root
        ).credentials
    return (
        Path(".runtime/merchants")
        / str(resolve_merchant(merchant)).lower()
        / "account.dpapi"
    )


def state_path(value):
    """Resolve old runtime/report names into a machine and character namespace."""
    r = registry()
    if r is None:
        return value
    value = str(value).replace("\\", "/")
    if value == ".runtime/merchant-input.lock":
        return str(r.root / "locks" / "input.lock")
    ctx = current()
    if value == ".runtime/account.dpapi" and ctx:
        return str(ctx.credentials)
    if value in (".runtime", "reports") or value.startswith((".runtime/", "reports/")):
        prefix, _, suffix = value.partition("/")
        shared = suffix.startswith(("merchants", "merchant-", "shop-", "shops-"))
        base = r.root / "machine-state" if shared or ctx is None else ctx.state_dir
        return str(base / prefix / suffix)
    return value


def game_root(default=r"C:\Program Files\Classic Conquer 2.0"):
    ctx = current()
    return str(ctx.installation) if ctx and ctx.installation else default


def installation_path(value):
    original = str(value).replace("\\", "/")
    prefix = "C:/Program Files/Classic Conquer 2.0"
    if not original.casefold().startswith(prefix.casefold()):
        return value
    root = game_root()
    if root == r"C:\Program Files\Classic Conquer 2.0":
        return value
    return str(Path(root) / original[len(prefix) :].lstrip("/"))


def context_arguments():
    r = registry()
    if not r:
        return []
    result = ["--data-root", str(r.root)]
    if os.environ.get("CONQUEST_PROFILE_ID"):
        result += ["--profile-id", os.environ["CONQUEST_PROFILE_ID"]]
    return result


def apply_overrides(config, route=None):
    ctx = current()
    if not ctx:
        return config
    supported = set(type(config).model_fields)
    unavailable = set(ctx.settings) - supported - {"recover_after_death"}
    if unavailable:
        raise ValueError(
            "Engine does not expose these overrides: " + ", ".join(sorted(unavailable))
        )
    values = {k: v for k, v in ctx.settings.items() if k in supported}
    if "attack_range_tiles" in values:
        values["attack_range_tiles"] = min(
            values["attack_range_tiles"], config.attack_range_tiles
        )
    # Normal model validation, never unchecked model_copy for user preferences.
    return type(config).model_validate(
        {**config.model_dump(), **values, "character": ctx.profile.name}
    )


def merchant_context(character):
    r = registry()
    return (
        context_for(r.resolve(character, role="Merchant", server="America").id, r.root)
        if r
        else None
    )


def merchant_installation(character):
    ctx = merchant_context(character)
    if ctx:
        if not ctx.installation:
            raise ValueError(
                "Configure the installation on this PC before launching this character"
            )
        return ctx.installation
    return Path(game_root())


def merchant_directory(character):
    ctx = merchant_context(character)
    return (
        ctx.state_dir / ".runtime"
        if ctx
        else Path(".runtime/merchants") / str(character).lower()
    )


def profile_status():
    r = registry()
    if not r:
        return []
    ctx = current()
    return [
        {
            "profile_id": p.id,
            "character": p.name,
            "server": p.server,
            "label": p.label or p.name,
            "role": p.role,
            "active_farmer": bool(
                ctx and ctx.profile.id == p.id and p.role == "Farmer"
            ),
            "local_enabled": p.local_enabled,
        }
        for p in r.profiles()
    ]
