"""Merchant and farmer identity comes from local profiles, never from names in code.

Failure modes this module must catch (written before the implementation):

1. With no profile registry the engines invent the original PC's merchants, so
   a fresh PC treats strangers who happen to share those names as owned shops
   (owned-price floor, alerts, refill notices, journal keys).
2. A merchant with any other name is not treated as owned: pricing lets it
   compete with its sibling shop, and its refill-blocker notice / #shops alert
   is silently dropped.
3. The owned-peer notice becomes too loose: an outside seller, a missing peer
   or a non-string peer produces an owned-merchant notice.
4. The exact-1078 listing/return profile gate refuses a correctly configured,
   UID-pinned, local America merchant because of its name.
5. The same gate becomes too loose once the name check is gone: a farmer role,
   a merchant disabled on this PC, or a merchant without a pinned UID passes.
6. The one-item booth calibration probe only accepts the historical merchant,
   or accepts an unpinned one.
7. The owned-floor pricing reason still names the historical merchants.
8. The legacy refill-cursor repair refuses a merchant that really holds the
   legacy cursor because of its name, or it touches the game/fence for a
   merchant with no legacy cursor instead of returning an inert blocker.
9. Farmer checks (town-corner recovery, background observation) reject the
   active farmer when it has any other name.
10. Operator scripts hard-code merchant names: the booth-verification option,
    the controller window title check and the UI smoke check.
11. Hard-coded historical names creep back into application code.

The end-to-end test builds a fresh PC's profiles with arbitrary names, runs
every gate above and writes ``portable-merchant-gates.json``; the scenario is
run twice in separate roots and must produce byte-identical artifacts.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import threading
from types import SimpleNamespace

import pytest

from conquest.character_profiles import ProfileRegistry

REPO = Path(__file__).resolve().parents[1]
LEGACY_CURSOR = [295705626, 294891157]


def fresh_pc(root):
    registry = ProfileRegistry(root)
    profiles = {
        "Varric": registry.add("Varric"),
        "Kalhiam": registry.add("Kalhiam", role="Merchant", character_uid=7001),
        "Brix": registry.add("Brix", role="Merchant", character_uid=7002),
        "Nova": registry.add("Nova", role="Merchant"),
        "Sleeper": registry.add(
            "Sleeper", role="Merchant", character_uid=7004, local_enabled=False
        ),
        "Kalhiam@Europe": registry.add(
            "Kalhiam", "Europe", "Merchant", character_uid=7005
        ),
        "Outsider": registry.add("Outsider"),
    }
    return registry, profiles


def activate(monkeypatch, registry, profile):
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(registry.root))
    monkeypatch.setenv("CONQUEST_PROFILE_ID", profile.id)


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        "portable_script_" + name, REPO / "scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refill_waiting(peer):
    return {
        "enabled": True,
        "connected": True,
        "refill": {"enabled": True},
        "foreground_refill_1078": {
            "state": "waiting",
            "blocker": "owned_peer_observation_unavailable",
            "unavailable_peer": peer,
        },
    }


def gate(check, name):
    try:
        return check(name).name
    except ValueError:
        return "rejected"


@pytest.mark.no_merchant_roster
def test_no_registry_means_no_merchants(monkeypatch):
    monkeypatch.delenv("CONQUEST_DATA_ROOT", raising=False)
    monkeypatch.delenv("CONQUEST_PROFILE_ID", raising=False)
    from conquest.character_context import (
        MerchantNames,
        OwnedMerchants,
        resolve_merchant,
    )
    from conquest.merchants.pricing import ItemKey, Listing, price_item

    assert tuple(MerchantNames()) == () and len(MerchantNames()) == 0
    assert set(OwnedMerchants()) == set() and "dutch" not in OwnedMerchants()
    with pytest.raises(ValueError):
        resolve_merchant("Dutch")
    key = ItemKey("Coat", "Normal", 1, ("No socket", "No socket"))
    decision = price_item(key, [Listing("Dutch", key, 100)])
    # A seller that merely shares the original PC's merchant name competes.
    assert decision.price == 99 and decision.sellers == 1


def test_owned_roster_and_pricing_follow_profiles(tmp_path, monkeypatch):
    registry, p = fresh_pc(tmp_path)
    activate(monkeypatch, registry, p["Varric"])
    from conquest.character_context import MerchantNames, OwnedMerchants
    from conquest.merchants.pricing import ItemKey, Listing, price_item

    assert list(MerchantNames()) == ["Kalhiam", "Brix", "Nova"]
    assert set(OwnedMerchants()) == {"kalhiam", "brix", "nova", "sleeper"}
    key = ItemKey("Coat", "Normal", 1, ("No socket", "No socket"))
    decision = price_item(
        key, [Listing("Kalhiam", key, 100), Listing("Outside", key, 150)]
    )
    assert decision.price == 100 and decision.sellers == 1
    assert decision.reason == (
        "Match lowest owned merchant price; already lowest valid offer"
    )


@pytest.mark.parametrize(
    "peer, expected",
    [
        ("Brix", "Brix"),
        ("kalhiam", "kalhiam"),
        ("Outsider", None),
        (None, None),
        (7, None),
    ],
)
def test_owned_peer_notices_follow_profiles(tmp_path, monkeypatch, peer, expected):
    registry, p = fresh_pc(tmp_path)
    activate(monkeypatch, registry, p["Varric"])
    from conquest.merchants.alerts import unavailable_owned_peer
    from conquest.merchants.simple_controls import current_refill_blocker

    state = refill_waiting(peer)
    assert unavailable_owned_peer(state) == expected
    notice = current_refill_blocker(state)
    if expected is None:
        assert notice is None
    else:
        assert notice.startswith(f"Auto-refill waiting: {peer} owned booth memory")


def test_listing_and_return_gates_accept_any_pinned_local_merchant(
    tmp_path, monkeypatch
):
    registry, p = fresh_pc(tmp_path)
    activate(monkeypatch, registry, p["Varric"])
    from conquest.merchants import booth_listing_once_1078 as listing
    from conquest.merchants import return_1078

    for check in (listing._profile, return_1078._profile):
        assert check("Kalhiam").id == p["Kalhiam"].id
        assert check(SimpleNamespace(profile_id=p["Brix"].id)).id == p["Brix"].id
        for refused in ("Nova", "Sleeper", "Varric", "Unknown"):
            with pytest.raises(ValueError):
                check(refused)


def test_booth_probe_gate_accepts_any_pinned_local_merchant(tmp_path, monkeypatch):
    registry, p = fresh_pc(tmp_path)
    activate(monkeypatch, registry, p["Varric"])
    from conquest.merchants import booth_probe_1078 as probe

    assert probe._profile("Brix").id == p["Brix"].id
    assert probe._profile("Kalhiam").id == p["Kalhiam"].id
    for refused in ("Nova", "Sleeper"):
        with pytest.raises(ValueError, match="UID-pinned local merchant"):
            probe._profile(refused)
    with pytest.raises(ValueError, match="profile ID"):
        probe._profile("Varric")


class _Runtime:
    def __init__(self, journal, fenced):
        self.journal = journal
        self.fenced = fenced
        self.fence_reads = []

    def read_only_1078(self, character, *, force=False):
        self.fence_reads.append(str(character))
        return self.fenced


def test_legacy_cursor_repair_keys_on_evidence_not_name(tmp_path, monkeypatch):
    registry, p = fresh_pc(tmp_path / "pc")
    activate(monkeypatch, registry, p["Varric"])
    from conquest.merchants.journal import Journal
    from conquest.merchants.refill_cursor_reconcile_1078 import reconcile

    journal = Journal(tmp_path / "journal.sqlite3")
    # No legacy cursor: an inert blocker before any fence/game read.
    runtime = _Runtime(journal, fenced=True)
    result = reconcile(runtime, "Brix")
    assert result["blocker"] == "no_legacy_refill_cursor"
    assert result["reconciled"] is False and result["game_input"] is False
    assert runtime.fence_reads == []
    journal.set(
        "Brix",
        "refill",
        {"pending": True, "status": "checking", "cursor": [1, 2], "listed": 0},
    )
    assert reconcile(runtime, "Brix")["blocker"] == "no_legacy_refill_cursor"
    assert runtime.fence_reads == []
    # A merchant with any name that really holds the legacy cursor proceeds
    # to the unchanged evidence checks (here: the input fence is not active).
    journal.set(
        "Kalhiam",
        "refill",
        {
            "pending": True,
            "status": "checking",
            "cursor": list(LEGACY_CURSOR),
            "listed": 0,
        },
    )
    runtime = _Runtime(journal, fenced=False)
    assert reconcile(runtime, "Kalhiam")["blocker"] == "1078_input_fence_not_active"
    assert runtime.fence_reads == ["Kalhiam"]


def _corner_loop(character):
    from conquest import town_corner as tc

    terrain = SimpleNamespace(
        map_id=1011,
        source_sha256=tc.TERRAIN_SHA256,
        walkable=lambda point: True,
        travel_path=lambda *a: [tc.DESTINATION],
    )
    life = {
        "character": character,
        "map_id": 1011,
        "position": list(tc.SOURCE),
        "dead_candidate": False,
    }
    panels = []
    return SimpleNamespace(
        terrain=terrain,
        living=lambda: {
            "embedded_controls": {
                "life": dict(life),
                "manual_mouse": False,
                "control": {"enabled": False},
            }
        },
        town=lambda *a: panels.append(a) or {"closed_panel": None},
    ), panels


def test_farmer_checks_use_the_active_farmer_profile(tmp_path, monkeypatch):
    registry, p = fresh_pc(tmp_path)
    activate(monkeypatch, registry, p["Varric"])
    from conquest import background_farmer_observation as bfo
    from conquest import town_corner as tc

    loop, panels = _corner_loop("Varric")
    with pytest.raises(ValueError, match="not qualified"):
        tc.recover_corner(loop, (227, 243))
    assert panels == [("clear-travel-panels",)]
    loop, panels = _corner_loop("Parasite")
    assert tc.recover_corner(loop, (227, 243)) is False and panels == []

    def ui(character):
        observer = SimpleNamespace(character=character, lock=threading.Lock())
        return SimpleNamespace(app=SimpleNamespace(observer=observer))

    assert bfo.snapshot(ui("Parasite"))["reason"] == (
        "Attached farmer observer is not Varric"
    )
    active = bfo.snapshot(ui("Varric"))
    assert active["character"] == "Varric" and "is not" not in active["reason"]


def test_scripts_take_merchants_from_profiles(tmp_path, monkeypatch):
    registry, p = fresh_pc(tmp_path)
    activate(monkeypatch, registry, p["Varric"])
    from conquest.portable_ui import controller_title

    title = controller_title(registry)
    assert title == "Conquest — Varric · Kalhiam · Brix · Nova · Kalhiam · Outsider"
    close = load_script("close_controller")
    assert close.expected_title(registry.root) == title
    scan = load_script("merchant_scan")
    assert scan.merchant_argument("kalhiam", registry) == "Kalhiam"
    for refused in ("Varric", "Sleeper", "Dutch"):
        with pytest.raises(argparse.ArgumentTypeError):
            scan.merchant_argument(refused, registry)
    smoke = load_script("validate_portable_ui")
    options = smoke.arguments([])
    assert options.merchant and not {"Dutch", "Spiritual"} & set(options.merchant)
    assert smoke.arguments(["--merchant", "Brix"]).merchant == ["Brix"]


def _python_files(root):
    return sorted(Path(root).rglob("*.py"))


def test_application_code_has_no_hard_coded_character_names():
    src = REPO / "src" / "conquest"
    merchant_names = re.compile(r"\b(dutch|spiritual)\b", re.IGNORECASE)
    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _python_files(src)
        if path.name != "profile_migration.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if merchant_names.search(line)
    ]
    assert offenders == []
    farmer_literals = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _python_files(src)
        if path.name not in ("profile_migration.py", "character_context.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if '"Parasite"' in line
    ]
    assert farmer_literals == []
    scripts = [
        REPO / "scripts" / name
        for name in (
            "merchant_scan.py",
            "probe_booth_modal_code_1078.py",
            "close_controller.py",
            "validate_portable_ui.py",
        )
    ]
    assert [
        path.name
        for path in scripts
        if merchant_names.search(path.read_text(encoding="utf-8"))
    ] == []


def _gate_scenario(root, monkeypatch):
    registry, p = fresh_pc(root)
    activate(monkeypatch, registry, p["Varric"])
    from conquest.character_context import MerchantNames, OwnedMerchants
    from conquest.merchants import booth_listing_once_1078 as listing
    from conquest.merchants import booth_probe_1078 as probe
    from conquest.merchants import return_1078
    from conquest.merchants.alerts import unavailable_owned_peer
    from conquest.merchants.pricing import ItemKey, Listing, price_item
    from conquest.merchants.simple_controls import current_refill_blocker
    from conquest.portable_ui import controller_title

    names = ("Kalhiam", "Brix", "Nova", "Sleeper", "Varric", "Outsider")
    key = ItemKey("Coat", "Normal", 1, ("No socket", "No socket"))
    decision = price_item(
        key,
        [
            Listing("Brix", key, 400),
            Listing("Kalhiam", key, 500),
            Listing("Outside", key, 450),
        ],
    )
    artifact = {
        "roster": [str(name) for name in MerchantNames()],
        "owned": sorted(OwnedMerchants()),
        "listing_gate": {n: gate(listing._profile, n) for n in names},
        "return_gate": {n: gate(return_1078._profile, n) for n in names},
        "probe_gate": {n: gate(probe._profile, n) for n in names},
        "pricing": {
            "price": decision.price,
            "reason": decision.reason,
            "independent_sellers": decision.sellers,
        },
        "alerts": {n: unavailable_owned_peer(refill_waiting(n)) for n in names},
        "refill_notice": {
            n: current_refill_blocker(refill_waiting(n)) is not None for n in names
        },
        "controller_title": controller_title(registry),
    }
    path = Path(root) / "portable-merchant-gates.json"
    path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_fresh_pc_merchant_gates_e2e(tmp_path, monkeypatch):
    first = _gate_scenario(tmp_path / "pc-a", monkeypatch)
    second = _gate_scenario(tmp_path / "pc-b", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    artifact = json.loads(first.read_text(encoding="utf-8"))
    pinned = {"Kalhiam": "Kalhiam", "Brix": "Brix"}
    refused = {n: "rejected" for n in ("Nova", "Sleeper", "Varric", "Outsider")}
    assert artifact == {
        "roster": ["Kalhiam", "Brix", "Nova"],
        "owned": ["brix", "kalhiam", "nova", "sleeper"],
        "listing_gate": {**pinned, **refused},
        "return_gate": {**pinned, **refused},
        "probe_gate": {**pinned, **refused},
        "pricing": {
            "price": 400,
            "reason": "Match lowest owned merchant price; already lowest valid offer",
            "independent_sellers": 1,
        },
        "alerts": {
            "Kalhiam": "Kalhiam",
            "Brix": "Brix",
            "Nova": "Nova",
            "Sleeper": "Sleeper",
            "Varric": None,
            "Outsider": None,
        },
        "refill_notice": {
            "Kalhiam": True,
            "Brix": True,
            "Nova": True,
            "Sleeper": True,
            "Varric": False,
            "Outsider": False,
        },
        "controller_title": (
            "Conquest — Varric · Kalhiam · Brix · Nova · Kalhiam · Outsider"
        ),
    }
