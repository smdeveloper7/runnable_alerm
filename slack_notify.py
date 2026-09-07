"""Slack Incoming Webhook + Block Kit 알림."""
from __future__ import annotations

import logging
from datetime import datetime

import requests

from runable_api import KST, EventStock, ProductStatus

log = logging.getLogger(__name__)


def _ts(dt: datetime | None = None) -> str:
    return (dt or datetime.now(KST)).strftime("%Y-%m-%d %H:%M:%S KST")


def build_available_blocks(status: ProductStatus, events: list[EventStock], product_url: str) -> dict:
    lines = "\n".join(
        f"• *{e.name}*  잔여 *{e.stock}*장  ({e.price:,}원)" for e in events
    )
    return {
        "text": f"🎉 [{status.product_name}] 취소표 발생! {', '.join(e.name for e in events)}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🎉 러너블 취소표 발생!", "emoji": True},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{status.product_name}*\n{lines}"},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"🕒 감지 시각: {_ts(status.checked_at)}"}],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "대회 신청 페이지 바로가기", "emoji": True},
                        "style": "primary",
                        "url": product_url,
                    }
                ],
            },
        ],
    }


def build_sold_out_blocks(status: ProductStatus, events: list[EventStock], product_url: str) -> dict:
    names = ", ".join(e.name for e in events)
    return {
        "text": f"⛔ [{status.product_name}] {names} 다시 품절",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"⛔ *{status.product_name}* — *{names}* 다시 품절되었습니다."},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"🕒 {_ts(status.checked_at)}  ·  <{product_url}|대회 페이지>"}],
            },
        ],
    }


def build_heartbeat_blocks(status: ProductStatus, product_url: str, checks: int, errors: int) -> dict:
    """주기적 '이상 없음' 상태 보고."""
    state_label = {"open": "접수중(판매기간 내)", "upcoming": "접수 시작 전", "closed": "접수 마감"}.get(
        status.sale_state, status.sale_state
    )
    return {
        "text": f"🟢 [{status.product_name}] 감시 정상 — {status.summary()}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"🟢 *감시 정상 동작 중* — {status.product_name}\n"
                        f"• 상태: {state_label}\n"
                        f"• 재고: {status.summary()}\n"
                        f"• 최근 주기 조회 {checks}회 / 오류 {errors}회"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"🕒 {_ts(status.checked_at)}  ·  <{product_url}|대회 페이지>"}],
            },
        ],
    }


def build_text_blocks(title: str, body: str, product_url: str | None = None) -> dict:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{body}"}}]
    ctx = f"🕒 {_ts()}"
    if product_url:
        ctx += f"  ·  <{product_url}|대회 페이지>"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ctx}]})
    return {"text": f"{title} {body}", "blocks": blocks}


def send(webhook_url: str, payload: dict, timeout: float = 10.0) -> bool:
    if not webhook_url:
        log.warning("SLACK_WEBHOOK_URL 이 비어 있어 알림을 건너뜁니다: %s", payload.get("text"))
        return False
    try:
        resp = requests.post(webhook_url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            log.error("Slack 전송 실패 %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        log.error("Slack 전송 오류: %s", exc)
        return False
