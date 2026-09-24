"""Read-only, fingerprint-pinned evidence for background input experiments.

The captured c2b53437 renderer places its input vector at context+0x37d8
and Win32 backend pointer at context+0x90. Mouse event constructors at RVAs
0xf160/0xf210/0xf2c0 append 24-byte records; the handler at 0x398e0 maintains
the backend tracking fields. The event processor at 0x1eeb0 writes MousePos
at context+0xd94 and Ctrl/Shift/Alt/Super at +0xdac through +0xdaf. The active-ID
setter at 0x142f0 writes ID at +0x3f04 and its window at +0x3f20.
MouseButton events write five booleans at +0xd9c (0x1f075/0x1f095), while
the scene wheel handler tests WantCaptureMouse at +0xd0 (0xd43ed/0xd43f4).
BeginDragDropSource at 0x25810 maintains +0x425c/+0x4264/+0x4268.
The CQITEM writer at 0xac82d..0xac8bf copies item+8 into the four-byte local
payload at +0x42f0. This build has a 33-byte type field: AcceptDragDropPayload
sets Preview at +0x42a9 and Delivery at +0x42aa (0x25c6c/0x25d65).
These layouts are diagnostics, not qualification
that a queued event produced its intended game action. Text/key payloads are
deliberately excluded so the evidence cannot record typed credentials.
"""

from copy import deepcopy
import math
import struct

from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.memory import GuiObservationChanged, GuiReader


MAX_EVENTS = 256
MAX_CAPACITY = 4096
EVENT_SIZE = 24


class BackgroundObservationReader:
    def __init__(self, session):
        self.gui = GuiReader(session)
        self.session = session

    def _read(self, address, size):
        data = self.session.read_block(checked_address(address), size)
        if len(data) != size:
            raise ValueError("Incomplete background input observation")
        return data

    def snapshot(self, *, attempts=3):
        if type(attempts) is not int or not 1 <= attempts <= 5:
            raise ValueError("Background observation attempts must be 1 through 5")
        for attempt in range(attempts):
            self.session.assert_identity()
            identity = deepcopy(self.session.identity)
            try:
                result = self._snapshot()
            except GuiObservationChanged:
                self.session.assert_identity()
                if self.session.identity != identity:
                    raise ValueError("Background observation process identity changed")
                if attempt + 1 == attempts:
                    raise
                continue
            self.session.assert_identity()
            if self.session.identity != identity:
                raise ValueError("Background observation process identity changed")
            return {
                "schema_version": 1,
                "client_sha256": CLIENT_SHA256,
                "identity": identity,
                **result,
            }

    def _snapshot(self):
        pointer_address = self.gui.base + 0x6966F0
        pointer = self._read(pointer_address, 8)
        context = checked_address(struct.unpack("<Q", pointer)[0])
        frame_raw = self._read(context + 0x3E38, 4)
        backend_pointer = self._read(context + 0x90, 8)
        backend_address = checked_address(struct.unpack("<Q", backend_pointer)[0])
        backend_raw = self._read(backend_address, 24)
        hwnd, mouse_hwnd = struct.unpack_from("<QQ", backend_raw)
        tracked = backend_raw[0x10]
        buttons = struct.unpack_from("<I", backend_raw, 0x14)[0]
        accepting_raw = self._read(context + 0x37C1, 1)
        if (
            not hwnd
            or tracked not in (0, 1)
            or buttons & ~0x1F
            or accepting_raw[0] not in (0, 1)
        ):
            raise ValueError("Invalid background input backend state")
        hover_window_raw = self._read(context + 0x3EC0, 8)
        hover_id_raw = self._read(context + 0x3EF0, 4)
        hover_window = struct.unpack("<Q", hover_window_raw)[0]
        if hover_window:
            checked_address(hover_window)
        config_raw = self._read(context + 8, 4)
        mouse_raw = self._read(context + 0xD94, 8)
        mouse_down_raw = self._read(context + 0xD9C, 5)
        capture_raw = self._read(context + 0xD0, 1)
        modifiers_raw = self._read(context + 0xDAC, 4)
        key_mods_raw = self._read(context + 0xE00, 4)
        active_id_raw = self._read(context + 0x3F04, 4)
        active_window_raw = self._read(context + 0x3F20, 8)
        mouse_position = list(struct.unpack("<ff", mouse_raw))
        key_mods = struct.unpack("<I", key_mods_raw)[0]
        active_window = struct.unpack("<Q", active_window_raw)[0]
        if not all(math.isfinite(value) for value in mouse_position):
            raise ValueError("Non-finite background mouse position")
        if any(value not in (0, 1) for value in mouse_down_raw + capture_raw):
            raise ValueError("Invalid processed background mouse state")
        if any(value not in (0, 1) for value in modifiers_raw) or key_mods & ~0xF:
            raise ValueError("Invalid background modifier state")
        if active_window:
            checked_address(active_window)
        drag_raw = self._read(context + 0x425C, 0x50)
        drag = _drag(drag_raw, context)
        drag_data = None
        if drag["payload_type"] == "CQITEM" and drag["data_frame"] >= 0:
            # Only this statically proven local UID payload is dereferenced.
            # Unknown payloads could contain private text or arbitrary pointers.
            if drag["data_size"] != 4 or drag["data_address"] != context + 0x42F0:
                raise ValueError("Unrecognized CQITEM drag payload layout")
            drag_data = self._read(context + 0x42F0, 4)
            drag["source_item_uid"] = struct.unpack("<I", drag_data)[0]
        queue_address = context + 0x37D8
        queue_raw = self._read(queue_address, 16)
        count, capacity, data_address = struct.unpack("<iiQ", queue_raw)
        if (
            not 0 <= count <= min(capacity, MAX_EVENTS)
            or not 0 <= capacity <= MAX_CAPACITY
        ):
            raise ValueError("Background input queue exceeds diagnostic bounds")
        if capacity:
            checked_address(data_address)
        elif data_address:
            raise ValueError("Invalid empty background input queue")
        data = self._read(data_address, count * EVENT_SIZE) if count else b""
        events = [
            _event(data[offset : offset + EVENT_SIZE])
            for offset in range(0, len(data), EVENT_SIZE)
        ]
        # Check both topology and mutable content. A frame crossing or an input
        # arriving mid-read invalidates this sample, rather than mixing states.
        checks = [
            (pointer_address, pointer),
            (context + 0x90, backend_pointer),
            (queue_address, queue_raw),
            (backend_address, backend_raw),
            (context + 0x37C1, accepting_raw),
            (context + 0x3EC0, hover_window_raw),
            (context + 0x3EF0, hover_id_raw),
            (context + 8, config_raw),
            (context + 0xD94, mouse_raw),
            (context + 0xD9C, mouse_down_raw),
            (context + 0xD0, capture_raw),
            (context + 0xDAC, modifiers_raw),
            (context + 0xE00, key_mods_raw),
            (context + 0x3F04, active_id_raw),
            (context + 0x3F20, active_window_raw),
            (context + 0x425C, drag_raw),
        ]
        if drag_data is not None:
            checks.append((context + 0x42F0, drag_data))
        if count:
            checks.append((data_address, data))
        checks.append((context + 0x3E38, frame_raw))
        if any(
            self._read(address, len(before)) != before for address, before in checks
        ):
            raise GuiObservationChanged(
                "Background input state changed during observation"
            )
        return {
            "context": context,
            "frame": struct.unpack("<I", frame_raw)[0],
            "accepting_events": bool(accepting_raw[0]),
            "backend": {
                "address": backend_address,
                "hwnd": hwnd,
                "mouse_hwnd": mouse_hwnd,
                "mouse_tracked": bool(tracked),
                "buttons_down": buttons,
            },
            "hover": {
                "window": hover_window,
                "id": struct.unpack("<I", hover_id_raw)[0],
            },
            "mouse_position": mouse_position,
            "mouse_down": list(map(bool, mouse_down_raw)),
            "want_capture_mouse": bool(capture_raw[0]),
            "mouse_position_valid": all(value >= -256000 for value in mouse_position),
            "modifiers": dict(
                zip(("ctrl", "shift", "alt", "super"), map(bool, modifiers_raw))
            ),
            "key_mods": key_mods,
            "config_flags": struct.unpack("<I", config_raw)[0],
            "active": {
                "id": struct.unpack("<I", active_id_raw)[0],
                "window": active_window,
            },
            "drag": drag,
            "queue": {"size": count, "capacity": capacity, "events": events},
        }


def _drag(raw, context):
    active, within_source, within_target = raw[:3]
    preview, delivery = raw[0x4D:0x4F]
    flags, source_frame, button = struct.unpack_from("<Iii", raw, 4)
    data_address, data_size, source_id, parent_id, data_frame = struct.unpack_from(
        "<QiIIi", raw, 0x14
    )
    if any(
        value not in (0, 1)
        for value in (active, within_source, within_target, preview, delivery)
    ):
        raise ValueError("Invalid drag state boolean")
    if (
        not -1 <= button <= 4
        or source_frame < -1
        or data_frame < -1
        or not 0 <= data_size <= 4096
    ):
        raise ValueError("Invalid bounded drag payload state")
    if data_address:
        checked_address(data_address)
    elif data_size:
        raise ValueError("Missing drag payload address")
    type_raw = raw[0x2C:0x4D]
    if b"\0" not in type_raw:
        raise ValueError("Unterminated drag payload type")
    type_raw = type_raw.split(b"\0", 1)[0]
    if any(value < 32 or value > 126 for value in type_raw):
        raise ValueError("Invalid drag payload type")
    return {
        "active": bool(active),
        "within_source": bool(within_source),
        "within_target": bool(within_target),
        "source_flags": flags,
        "source_frame": source_frame,
        "mouse_button": button,
        "data_address": data_address,
        "data_size": data_size,
        "source_id": source_id,
        "source_parent_id": parent_id,
        "data_frame": data_frame,
        "payload_type": type_raw.decode("ascii"),
        "preview": bool(preview),
        "delivery": bool(delivery),
        "source_item_uid": None,
    }


def _event(raw):
    kind, source = struct.unpack_from("<II", raw)
    if not 1 <= kind <= 6 or not 0 <= source <= 5:
        raise ValueError("Unrecognized background input event")
    result = {
        "type": {
            1: "mouse_position",
            2: "mouse_wheel",
            3: "mouse_button",
            4: "key",
            5: "text",
            6: "focus",
        }[kind],
        "source": source,
    }
    if kind in (1, 2):
        x, y = struct.unpack_from("<ff", raw, 8)
        if not all(math.isfinite(value) for value in (x, y)):
            raise ValueError("Non-finite background mouse event")
        result.update(x=x, y=y)
        if kind == 1:
            result["position_valid"] = x >= -256000 and y >= -256000
    elif kind == 3:
        button = struct.unpack_from("<I", raw, 8)[0]
        down = raw[12]
        if button > 4 or down not in (0, 1):
            raise ValueError("Invalid background mouse button event")
        result.update(button=button, down=bool(down))
    elif kind == 6:
        if raw[8] not in (0, 1):
            raise ValueError("Invalid background focus event")
        result["focused"] = bool(raw[8])
    # Key and text event payloads are intentionally not returned.
    return result
