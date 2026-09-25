"""Shop-flag collision target: no qualified client build remains.

The native flag collision projection was pinned to the retired 1074 client
only. Callers still import ``CONTROL`` and ``flag_target``; the target fails
closed on every build until a separately live-qualified reader exists.
"""

CONTROL = {"mode": "native_flag_bounds", "revision": 1}


def flag_target(observer, flag):
    """No client build is qualified for this projection; always fails closed."""
    raise ValueError("Flag target client is unqualified")
