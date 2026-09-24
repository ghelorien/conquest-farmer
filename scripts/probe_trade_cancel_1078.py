"""Read-only evidence for the 1078 incoming-trade Cancel control.

This does not qualify or send input.  Run only for an exact, supervised empty
incoming request; the operator closes the request if the probe cannot.
"""

import argparse
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

if __name__ == "__main__":
    from _bootstrap import activate

    activate(__file__)

from conquest.memory import MemorySession
from conquest.merchants.memory import GuiReader, string
from conquest.merchants.reader_1078 import (
    CLIENT_SHA256_1078,
    TradeObservationReader1078,
)


def observe(pid, created, character):
    with MemorySession(pid, CLIENT_SHA256_1078) as session:
        if session.identity["creation_time_100ns"] != created:
            raise ValueError("Game process identity changed")
        adapter = SimpleNamespace(
            expected_sha256=session.expected_sha256,
            modules=session.modules,
            identity=session.identity,
            read=session.read,
            read_block=session.read,
            assert_identity=session.assert_identity,
        )
        reader = TradeObservationReader1078(adapter, character)
        before = reader.read_manual_ownership()
        request = before["request"]
        if request is None or before["trade"] is not None:
            return {"qualified": False, "reason": "No isolated incoming request"}
        gui = GuiReader.for_session(adapter)
        model = gui.model(15, reader.confirm_vtable_rva)
        labels = [
            string(adapter, model + offset, 256) for offset in (0x48, 0x68, 0x88, 0xA8)
        ]
        expected = ["Trade###Confirm", request["message"], "Accept", "Cancel"]
        if labels != expected:
            raise ValueError(
                "Request model labels differ from the exact incoming request"
            )
        windows = [
            window for window in gui.windows() if window["name"] == "Trade###Confirm"
        ]
        if len(windows) != 1:
            raise ValueError("Incoming request GUI window is absent or ambiguous")
        window = windows[0]
        raw = adapter.read_block(window["address"], 0x250)
        geometry = window["geometry"]
        end_x, button_y = struct.unpack_from("<2f", raw, 0xE8)
        line = struct.unpack_from("<f", raw, 0x114)[0]
        context = struct.unpack(
            "<Q", adapter.read_block(gui.base + gui.context_rva, 8)
        )[0]
        hovered_window = struct.unpack("<Q", adapter.read_block(context + 0x3EC0, 8))[0]
        hovered_id = struct.unpack("<I", adapter.read_block(context + 0x3EF0, 4))[0]
        seed = struct.unpack_from("<I", raw, 8)[0]
        cancel_id = zlib.crc32(b"Cancel", seed)
        after = reader.read_manual_ownership()
        if any(
            after[key] != before[key]
            for key in (
                "identity",
                "character_uid",
                "request",
                "trade",
                "inventory",
                "booth",
                "silver",
            )
        ):
            raise ValueError("Request or ownership changed during Cancel observation")
        session.assert_identity()
        return {
            "qualified": False,
            "reason": "Read-only Cancel candidate; handler and click are not qualified",
            "process_identity": session.identity,
            "request": request,
            "labels_match": True,
            "window_geometry": geometry,
            "window_address": hex(window["address"]),
            "model_address": hex(model),
            "end_x": end_x,
            "button_y": button_y,
            "line_height": line,
            "hovered_cancel": hovered_window == window["address"]
            and hovered_id == cancel_id,
            "hovered_window_matches": hovered_window == window["address"],
            "hovered_id": hex(hovered_id),
            "cancel_id": hex(cancel_id),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--created", type=int, required=True)
    parser.add_argument("--character", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing diagnostic")
    result = observe(args.pid, args.created, args.character)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "request_present": "request" in result,
                "hovered_cancel": result.get("hovered_cancel"),
            }
        )
    )


if __name__ == "__main__":
    main()
