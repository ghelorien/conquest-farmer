"""Client geometry for memory coordinates; never reads screen pixels."""

DEFAULT_SIZE = (1036, 793)


def validate_size(size):
    if (
        len(size) != 2
        or any(type(v) is not int for v in size)
        or not 640 <= size[0] <= 7680
        or not 480 <= size[1] <= 4320
    ):
        raise ValueError("Client viewport is outside supported geometry bounds")
    return tuple(size)


def size_for(subject):
    subject = getattr(subject, "adapter", subject)
    provider = getattr(subject, "viewport_size", None)
    return validate_size(provider() if callable(provider) else provider or DEFAULT_SIZE)


def logical_client_size(hwnd):
    import win32gui
    from conquest.native_farm import logical_coordinates

    with logical_coordinates():
        return validate_size(win32gui.GetClientRect(hwnd)[2:])


def scene_bounds(size):
    width, height = validate_size(size)
    return 80, 140, width - 80, height - 126


def clear_scene(point, size=DEFAULT_SIZE):
    x, y = point
    left, top, right, bottom = scene_bounds(size)
    # The memory-qualified XP/Fly/Descend row sits above the control bar,
    # starting 180 px from the bottom. Its width grows when Descend appears.
    # Keep this small HUD region out of world targeting even before it opens.
    popup_row = y >= size[1] - 180
    over_xp_popup = popup_row and abs(x - size[0] / 2) <= 100
    over_skill_menu = popup_row and x >= size[0] - 160
    return (
        left < x < right
        and top < y < bottom
        and not (x < 615 and (y > size[1] - 243 or y < 170))
        and not over_xp_popup
        and not over_skill_menu
    )


def require_world_point(point, size):
    from conquest.capture import CaptureUnavailable

    if not clear_scene(point, size):
        raise CaptureUnavailable("World click overlaps the HUD; replanning")


def revive_point(session, size):
    size = validate_size(size)
    from conquest.memory_build_layout import CLIENT_SHA256_1078

    if getattr(session, "expected_sha256", None) == CLIENT_SHA256_1078:
        from conquest.native_revive import point

        return point(session, size)
    if size == DEFAULT_SIZE:
        return (518, 640)
    # The previously qualified point is centered 51 px above the control bar.
    # Translate its anchor from the live GUI instead of stretching old pixels.
    from conquest.memory_shop import MemoryGui

    control = MemoryGui(session).read("##Control")
    if control.size != (930.0, 102.0) or control.scroll != (0.0, 0.0):
        raise ValueError("Revive control-bar layout changed")
    point = (
        round(control.position[0] + control.size[0] / 2),
        round(control.position[1] - 51),
    )
    if abs(point[0] - size[0] / 2) > 1 or abs(control.position[1] + 102 - size[1]) > 1:
        raise ValueError("Revive control-bar anchoring changed")
    return point
