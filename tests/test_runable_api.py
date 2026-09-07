import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from runable_api import KST, EventStock, parse_product, sale_state

FIXTURE = Path(__file__).parent / "fixtures" / "product_detail_soldout.json"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_soldout_fixture_lists_only_isuse_events(payload):
    status = parse_product(payload)
    names = sorted(e.name for e in status.events)
    assert names == ["10K", "HALF"]  # isUse=false 종목(포러너 번들)은 제외
    assert all(e.stock == 0 for e in status.events)
    assert status.available_events() == []
    assert status.product_name == "2026 GARMIN RUN KOREA"


def test_parse_detects_available_event_when_stock_positive(payload):
    p = copy.deepcopy(payload)
    for ev in p["data"]["product"]["productEvents"]:
        if ev["compEvent"]["eventName"] == "HALF":
            ev["stock"]["count"] = 2
    status = parse_product(p)
    avail = status.available_events()
    assert [e.name for e in avail] == ["HALF"]
    assert avail[0].stock == 2
    assert avail[0].price == 80000


def test_event_with_zero_quantity_is_not_available():
    ev = EventStock(id=1, name="HALF", stock=3, quantity=0, price=1, is_use=True)
    assert not ev.available
    ev2 = EventStock(id=1, name="HALF", stock=3, quantity=5, price=1, is_use=True)
    assert ev2.available


def test_watch_filter_limits_events(payload):
    status = parse_product(payload, watch_events=["10k"])  # 대소문자 무시
    assert [e.name for e in status.events] == ["10K"]


@pytest.mark.parametrize(
    "now, expected",
    [
        (datetime(2026, 9, 1, tzinfo=KST), "upcoming"),
        (datetime(2026, 9, 10, tzinfo=KST), "open"),
        (datetime(2026, 10, 1, tzinfo=KST), "closed"),
    ],
)
def test_sale_state_uses_kst_window(payload, now, expected):
    product = payload["data"]["product"]
    assert sale_state(product, now=now) == expected


def test_status_summary_lines(payload):
    status = parse_product(payload)
    text = status.summary()
    assert "HALF" in text and "10K" in text and "품절" in text
