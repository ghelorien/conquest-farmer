from types import SimpleNamespace as NS

import pytest

from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.booth_target import owned_booth_target


def test_owned_booth_target_fails_closed_without_reading_memory():
    def read_block(*args):
        pytest.fail("An unqualified booth hit test must not read client memory")

    observer = NS(
        character="Spiritual",
        adapter=NS(
            expected_sha256=CLIENT_SHA256_1078,
            modules=[{"name": "imconquer.exe", "base": 0x140000000, "size": 1}],
            read_block=read_block,
            assert_identity=lambda: None,
        ),
    )
    with pytest.raises(ValueError, match="unqualified client"):
        owned_booth_target(observer, {"address": 0x20000000, "name": "Spiritual"})
