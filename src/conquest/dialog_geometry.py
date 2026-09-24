"""NPC choice geometry from the current memory snapshot, never screen scaling."""

import math


def option_geometry(data, option, viewport=None):
    options = [r for r in data["records"] if r["kind"] == 1]
    matches = [i for i, r in enumerate(options) if r["text"] == option]
    if len(matches) != 1 or any(r["kind"] == 2 for r in data["records"]):
        raise ValueError("Service choice is absent or ambiguous")
    window = data["window"]
    get = window.get if isinstance(window, dict) else lambda k: getattr(window, k)
    x, y = get("position")
    width, wh = get("size")
    sx, sy = get("scroll")
    left, top, right, height = data["table"]
    if (
        not all(
            math.isfinite(v)
            for v in (left, top, right, height, x, y, width, wh, sx, sy)
        )
        or sx != 0
        or sy < 0
        or right - left < 80
        or left != x + 20
        or right > x + width - 8
        or height != math.ceil(len(options) / 2) * 22
    ):
        raise ValueError("Service dialog layout is not qualified")
    i = matches[0]
    cell = (right - left) / 2
    rect = (
        left + (i % 2) * cell,
        top + (i // 2) * 22,
        left + (i % 2 + 1) * cell,
        top + (i // 2 + 1) * 22,
    )
    clip_top, clip_bottom = y + 20, y + wh
    if viewport is not None:
        vw, vh = viewport
        if (
            not all(math.isfinite(v) and v > 0 for v in viewport)
            or rect[0] < 0
            or rect[2] > vw
        ):
            raise ValueError("Service choice is horizontally clipped by the client")
        clip_top = max(0, clip_top)
        clip_bottom = min(vh, clip_bottom)
    if clip_bottom - clip_top < 22:
        raise ValueError("Service dialog is too short to show a choice")
    return rect, (clip_top, clip_bottom)


def scroll_direction(data, option, viewport=None):
    rect, (top, bottom) = option_geometry(data, option, viewport)
    if rect[1] < top:
        return 1
    if rect[3] > bottom:
        return -1
    return 0


def option_point(data, option, viewport=None):
    rect, (top, bottom) = option_geometry(data, option, viewport)
    if not top <= rect[1] < rect[3] <= bottom:
        raise ValueError(
            "Service dialog layout clips the selected choice; scroll before input"
        )
    return round((rect[0] + rect[2]) / 2), round((rect[1] + rect[3]) / 2)
