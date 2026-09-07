"""러너블 상품 상세 API 조회 및 재고 파싱.

runable.me 상품 페이지는 클라이언트에서
`GET /next-api/index/v1/product/detail/{id}?auth=false` 를 호출해 종목/재고를 그리며,
프론트 코드 기준 종목 선택 가능 조건은 ``isUse && stock.count > 0 && quantity != 0`` 이다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

try:  # Windows 에는 tz DB 가 없을 수 있음 → KST 는 DST 가 없으므로 고정 오프셋으로 폴백
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:  # ZoneInfoNotFoundError 등
    KST = timezone(timedelta(hours=9), name="KST")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]


@dataclass
class EventStock:
    id: int
    name: str
    stock: int
    quantity: int
    price: int
    is_use: bool

    @property
    def available(self) -> bool:
        return self.is_use and self.stock > 0 and self.quantity != 0


@dataclass
class ProductStatus:
    product_id: int
    product_name: str
    sale_state: str  # open | upcoming | closed | unknown
    events: list[EventStock] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(KST))

    def available_events(self) -> list[EventStock]:
        if self.sale_state != "open":
            return []
        return [e for e in self.events if e.available]

    def summary(self) -> str:
        parts = []
        for e in self.events:
            parts.append(f"{e.name}: {'잔여 ' + str(e.stock) if e.available else '품절'}")
        return " | ".join(parts) if parts else "감시 대상 종목 없음"


def _parse_kst(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=KST)


def sale_state(product: dict[str, Any], now: datetime | None = None) -> str:
    """프론트의 eS() 와 동일한 판정: 판매기간 기준 open/upcoming/closed."""
    now = now or datetime.now(KST)
    start = _parse_kst(product.get("saleStartDateTime"))
    end = _parse_kst(product.get("saleEndDateTime"))
    if start is None or end is None:
        return "unknown"
    if now > end:
        return "closed"
    if start < now < end:
        return "open"
    if now < start:
        return "upcoming"
    return "unknown"


def parse_product(payload: dict[str, Any], watch_events: list[str] | None = None,
                  now: datetime | None = None) -> ProductStatus:
    product = payload["data"]["product"]
    watch = {w.upper() for w in (watch_events or [])}
    events: list[EventStock] = []
    for ev in product.get("productEvents") or []:
        if ev.get("isDeleted") or not ev.get("isUse"):
            continue
        name = (ev.get("compEvent") or {}).get("eventName") or f"event-{ev.get('id')}"
        if watch and name.upper() not in watch:
            continue
        events.append(
            EventStock(
                id=int(ev["id"]),
                name=name,
                stock=int(((ev.get("stock") or {}).get("count")) or 0),
                quantity=int(ev.get("quantity") or 0),
                price=int(ev.get("price") or 0),
                is_use=bool(ev.get("isUse")),
            )
        )
    events.sort(key=lambda e: e.name)
    return ProductStatus(
        product_id=int(product["id"]),
        product_name=product.get("name", ""),
        sale_state=sale_state(product, now=now),
        events=events,
    )


def build_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": referer,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def fetch_product(api_url: str, referer: str, session: requests.Session | None = None,
                  timeout: float = 10.0) -> dict[str, Any]:
    sess = session or requests.Session()
    resp = sess.get(api_url, headers=build_headers(referer), timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK" or "data" not in data:
        raise ValueError(f"unexpected API response: {str(data)[:200]}")
    return data


def fetch_status(api_url: str, referer: str, watch_events: list[str] | None = None,
                 session: requests.Session | None = None) -> ProductStatus:
    return parse_product(fetch_product(api_url, referer, session=session), watch_events=watch_events)
