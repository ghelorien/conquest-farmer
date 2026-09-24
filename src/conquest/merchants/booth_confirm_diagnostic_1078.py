"""Bounded 1078 booth-modal code evidence for offline, read-only research.

The caller supplies only a configured merchant.  All memory ranges come from
the exact client layout and an observed live Add Item to Booth modal. The
native OK/Cancel handlers are pinned, but listing input remains unauthorized.
"""

import struct
import time

from conquest.character_context import registry
from conquest.memory import MemorySession
from conquest.memory_build_layout import CLIENT_SHA256_1078, read_build_layout
from conquest.merchants.listing_preflight_1078 import (
    _modal_candidate,
    _modal_code_verified,
)
from conquest.merchants.memory import GuiReader, HoverNotReady
from conquest.merchants.observe_1078 import observe
from conquest.merchants.reader_1078 import open_read_only_1078


_SLOTS = tuple(range(0, 0x80, 8))  # First 16 exact model virtual slots.
_METHOD_PREFIX = 96
_STABLE = (
    "identity",
    "character",
    "character_uid",
    "server",
    "map_id",
    "hp",
    "silver",
    "capacity",
    "inventory",
    "booth",
    "own_booth_uid",
    "booth_open",
    "trade",
    "request",
)


def _model_methods(session, module, vtable_rva):
    """Read fixed vtable slots and bounded prefixes from this module only."""
    base, size = module["base"], module["size"]
    if not 0x1000 <= vtable_rva <= size - len(_SLOTS) * 8:
        raise ValueError("1078 booth model vtable is outside the exact client")
    address = base + vtable_rva
    raw = session.read(address, len(_SLOTS) * 8)
    pointers = struct.unpack("<" + "Q" * len(_SLOTS), raw)
    methods = []
    for slot, pointer in zip(_SLOTS, pointers):
        rva = pointer - base
        if not 0x1000 <= rva <= size - _METHOD_PREFIX:
            methods.append(
                {"slot": slot, "method_rva": None, "reason": "outside_exact_module"}
            )
            continue
        prefix = session.read(pointer, _METHOD_PREFIX)
        if session.read(pointer, _METHOD_PREFIX) != prefix:
            raise ValueError("1078 booth model method changed during observation")
        methods.append(
            {"slot": slot, "method_rva": rva, "method_prefix_hex": prefix.hex()}
        )
    if session.read(address, len(raw)) != raw:
        raise ValueError("1078 booth model vtable changed during observation")
    return methods


def collect(runtime, character):
    """Authenticated bridge calls this with a character; no address input."""
    started = time.monotonic()
    baseline = observe(runtime, character)
    profiles = registry()
    if profiles is None:
        raise ValueError("1078 booth diagnostic requires configured local profiles")
    profile = profiles.resolve(
        baseline["profile_id"], role="Merchant", server="America"
    )
    if (
        not profile.local_enabled
        or profile.name != baseline["character"]
        or profile.character_uid != baseline["character_uid"]
    ):
        raise ValueError("Merchant profile changed before booth diagnostic")
    if (
        baseline["map_id"] != 1036
        or baseline["hp"] <= 0
        or not baseline["own_booth_uid"]
        or not baseline["booth_open"]
        or not baseline["closed_modal"]
    ):
        raise ValueError(
            "Diagnostic requires a living idle merchant with an open owned Market booth"
        )
    identity = baseline["identity"]
    with MemorySession(identity["pid"], CLIENT_SHA256_1078) as session:
        if session.identity != identity:
            raise ValueError("Merchant process changed before booth diagnostic")
        layout = read_build_layout(session)
        _modal_code_verified(session, layout)
        snapshot = open_read_only_1078(session, profile.name).read_manual_ownership()
        for key in _STABLE:
            if key in baseline and baseline[key] != snapshot[key]:
                raise ValueError("Merchant ownership changed before booth diagnostic")
        if snapshot["trade"] is not None or snapshot["request"] is not None:
            raise ValueError("Trade or request opened before booth diagnostic")
        gui = GuiReader.for_session(session)
        model = gui.model(25, layout.merchant_booth_vtable_rva)
        windows = [w for w in gui.windows() if w["name"] == "Add Item to Booth"]
        if len(windows) != 1:
            raise ValueError("Exactly one live Add Item to Booth modal is required")
        modal = windows[0]
        candidate = _modal_candidate(gui.session, model, snapshot)
        owner = struct.unpack("<I", session.read(model + 0x4C, 4))[0]
        active = session.read(model + 12, 1)[0]
        if not active or owner != snapshot["own_booth_uid"]:
            raise ValueError("1078 booth model differs from the owned active booth")
        raw = session.read(modal["address"], 0x250)
        geometry = struct.unpack_from("<4f", raw, 0x18)
        end_x, button_y = struct.unpack_from("<2f", raw, 0xE8)
        start_x, start_y = struct.unpack_from("<2f", raw, 0xF0)
        button_height = struct.unpack_from("<f", raw, 0x114)[0]
        if (
            tuple(modal["geometry"]) != geometry
            or geometry[2:] != (264.0, 92.0)
            or not raw[0x97]
            or button_height != 18
            or end_x != geometry[0] + 256
            or button_y != geometry[1] + 66
            or (start_x, start_y) != (geometry[0] + 8, geometry[1] + 26)
        ):
            raise ValueError(
                "1078 modal geometry differs from the observed dialog layout"
            )
        module = [m for m in session.modules if m["name"].casefold() == "imconquer.exe"]
        if len(module) != 1:
            raise ValueError("Ambiguous exact 1078 client module")
        methods = _model_methods(session, module[0], layout.merchant_booth_vtable_rva)
        hovered = []
        for label in ("##Amount", "OK", "Cancel"):
            try:
                gui.assert_hovered(modal, label)
            except HoverNotReady:
                continue
            hovered.append(label)
        fresh = open_read_only_1078(session, profile.name).read_manual_ownership()
        current_modal = session.read(modal["address"], 0x250)
        fixed_offsets = ((0x18, 0x28), (0x97, 0x98), (0xE8, 0xF8), (0x114, 0x118))
        if any(fresh[key] != snapshot[key] for key in _STABLE):
            raise ValueError("Merchant ownership changed during booth diagnostic")
        if (
            gui.model(25, layout.merchant_booth_vtable_rva) != model
            or [w for w in gui.windows() if w["name"] == "Add Item to Booth"] != windows
            or any(
                current_modal[start:end] != raw[start:end]
                for start, end in fixed_offsets
            )
            or _modal_candidate(gui.session, model, fresh) != candidate
            or struct.unpack("<I", session.read(model + 0x4C, 4))[0] != owner
            or session.read(model + 12, 1)[0] != active
        ):
            raise ValueError("1078 modal or booth model changed during diagnostic")
        if profiles.resolve(profile.id, role="Merchant", server="America") != profile:
            raise ValueError("Merchant profile changed during booth diagnostic")
        session.assert_identity()
    if time.monotonic() - started > 6:
        raise ValueError(
            "1078 booth diagnostic expired; retry the read-only observation"
        )
    return {
        "read_only": True,
        "input_qualified": False,
        "native_ok_cancel_handlers_verified": True,
        "server_acceptance_verified": False,
        "client_sha256": CLIENT_SHA256_1078,
        "profile_id": profile.id,
        "character": profile.name,
        "character_uid": profile.character_uid,
        "process_identity": identity,
        "own_booth_uid": owner,
        "selected_item_uid": candidate["candidate_selected_item_uid"],
        "candidate_price_text": candidate["candidate_price_buffer_text"],
        "modal_geometry": list(geometry),
        "modal_visible": True,
        "hovered_control_labels": hovered,
        "candidate_control_layout": {
            "button_right_x": end_x,
            "button_y": button_y,
            "button_height": button_height,
            "amount_start": [start_x, start_y],
        },
        "booth_model_vtable_rva": layout.merchant_booth_vtable_rva,
        "booth_model_methods": methods,
    }
