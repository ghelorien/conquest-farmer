import copy
import time
from types import SimpleNamespace

import pytest

from conquest.merchants.public_market import (
    normalize_pages,
    collect_public,
    socket_label,
)


def pages():
    metadata = dict(
        pageSize=100,
        totalCount=101,
        totalPages=2,
        servers=[dict(server=0, isAvailable=True, updatedAtUtc="2026-09-12T01:36:44Z")],
    )

    def item(uid):
        return dict(
            itemId=uid,
            server=0,
            attributeName="Hat",
            itemMinorClass="Helmet",
            sellerName="Trader",
            qualityName="Normal",
            additionLevel=1,
            gem1="None",
            gem2="Empty",
            price=20000,
        )

    return [
        dict(metadata, page=1, items=[item(i) for i in range(1, 101)]),
        dict(metadata, page=2, items=[item(101)]),
    ]


def normalize(data):
    return normalize_pages(data, [dict(id=111005, name="Hat")], observed_at=time.time())


def test_complete_public_market_preserves_price_attributes_and_timestamp():
    data = normalize(pages())
    assert data["total"] == 101 and data["complete"]
    assert data["listings"][0]["price"] == 20000
    assert data["listings"][0]["sockets"] == ["No socket", "Empty"]
    assert data["listings"][0]["quantity"] == 1
    assert data["collection_method"] == "public-market-api"


@pytest.mark.parametrize(
    "failure",
    ["missing", "duplicate", "server", "version", "count", "rows", "price", "socket"],
)
def test_inconsistent_public_data_is_rejected(failure):
    data = copy.deepcopy(pages())
    if failure == "missing":
        data.pop()
    if failure == "duplicate":
        data[1]["items"][0]["itemId"] = 1
    if failure == "server":
        data[1]["items"][0]["server"] = 1
    if failure == "version":
        data[1]["servers"] = [dict(server=0, isAvailable=True, updatedAtUtc="new")]
    if failure == "count":
        data[1]["totalCount"] = 100
    if failure == "rows":
        data[1]["items"] = []
    if failure == "price":
        data[1]["items"][0]["price"] = "20000"
    if failure == "socket":
        data[1]["items"][0]["gem1"] = "Unknown"
    with pytest.raises(ValueError):
        normalize(data)


def test_public_requests_use_supported_page_size_and_recheck_first_page():
    data = pages()
    calls = []

    def get(url, params, timeout):
        calls.append(params)
        return SimpleNamespace(
            ok=True,
            headers={"content-type": "application/json"},
            json=lambda: data[params["page"] - 1],
        )

    result = collect_public(SimpleNamespace(get=get), [dict(id=111005, name="Hat")])
    assert result["total"] == 101
    assert [c["page"] for c in calls] == [1, 2, 1]
    assert all(c["pageSize"] == 100 and c["server"] == 0 for c in calls)


def test_final_market_change_cannot_publish():
    data = pages()
    calls = []

    def get(url, params, timeout):
        calls.append(params)
        value = copy.deepcopy(data[params["page"] - 1])
        if len(calls) == 3:
            value["items"][0]["price"] = 1
        return SimpleNamespace(
            ok=True, headers={"content-type": "application/json"}, json=lambda: value
        )

    with pytest.raises(ValueError, match="verification"):
        collect_public(SimpleNamespace(get=get), [dict(id=111005, name="Hat")])


def test_socket_enum_matches_memory_label():
    assert socket_label("SuperDragonGem") == "Super DragonGem"
