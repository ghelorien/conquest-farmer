"""Owned-booth tile hit test: no qualified client build remains.

The native tile hit test was pinned to the retired 1074 client only. Callers
still import ``CONTROL`` and ``owned_booth_target``; the target fails closed
on every build until a separately live-qualified reader exists.
"""

CONTROL = {"mode": "native_booth_tiles", "revision": 1}


def owned_booth_target(observer, booth):
    """No client build is qualified for this hit test; always fails closed."""
    raise ValueError("Booth hit test belongs to an unqualified client")
