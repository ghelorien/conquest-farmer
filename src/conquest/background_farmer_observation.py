"""Bounded read-only evidence from the farmer's existing in-app session.

No observer, controller, bridge, or input object is created. The result never
qualifies input, selects an action, or changes Farming On/Off intent.

The evidence reader was qualified only on the retired 1074 client (its life,
motion and GUI fields). No build has a qualified layout for it, so every
attached farmer reports an unverified observation.
"""

from conquest.character_context import farmer_name


def _unavailable(reason):
    return {
        "schema_version": 1,
        "available": False,
        "character": farmer_name(),
        "source": "read_only_memory",
        "input_qualified": False,
        "reason": reason,
    }


def snapshot(ui):
    app = ui.app
    observer = getattr(app, "observer", None)
    if observer is None:
        return _unavailable("Farmer has no attached memory observer")
    if getattr(observer, "character", None) != farmer_name():
        return _unavailable("Attached farmer observer is not Parasite")
    if not observer.lock.acquire(timeout=0.1):
        return _unavailable("Farmer memory observer is busy")
    try:
        return _snapshot(app, observer)
    except ValueError as error:
        result = _unavailable("Farmer observation could not be verified")
        result["error_type"] = type(error).__name__
        return result
    finally:
        observer.lock.release()


def _snapshot(app, observer):
    # Fail closed on every build: the only qualified evidence layout was 1074.
    raise ValueError("Unsupported farmer fingerprint")
