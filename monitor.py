"""[1단계] 러너블 취소표 모니터링 + Slack 알림.

실행:  python monitor.py
종료:  Ctrl+C
"""
from __future__ import annotations

import logging
import random
import sys
import time
from dataclasses import dataclass, field

import requests

import slack_notify
from config import settings
from runable_api import EventStock, ProductStatus, fetch_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("monitor")


@dataclass
class AlertTracker:
    """종목별 알림 중복(도배) 방지.

    - 품절 → 잔여 전환(edge)은 즉시 알림
    - 잔여 상태가 유지되는 동안은 cooldown 초마다 한 번만 재알림
    - 잔여 → 품절 전환은 (옵션) 한 번 알림
    """

    cooldown: float
    last_alert: dict[int, float] = field(default_factory=dict)
    was_available: dict[int, bool] = field(default_factory=dict)

    def decide(self, events: list[EventStock], now: float) -> tuple[list[EventStock], list[EventStock]]:
        """(알림 보낼 잔여 종목, 방금 품절된 종목) 반환."""
        to_alert: list[EventStock] = []
        just_sold_out: list[EventStock] = []
        for e in events:
            prev = self.was_available.get(e.id, False)
            if e.available:
                last = self.last_alert.get(e.id)
                if not prev or last is None or now - last >= self.cooldown:
                    to_alert.append(e)
                    self.last_alert[e.id] = now
            elif prev:
                just_sold_out.append(e)
                self.last_alert.pop(e.id, None)
            self.was_available[e.id] = e.available
        return to_alert, just_sold_out


@dataclass
class Heartbeat:
    """이상 없어도 interval 초마다 한 번 '정상 감시 중' 보고. interval<=0 이면 비활성."""

    interval: float
    started_at: float
    last_sent: float | None = None
    checks: int = 0
    errors: int = 0

    def record(self, ok: bool) -> None:
        if ok:
            self.checks += 1
        else:
            self.errors += 1

    def due(self, now: float) -> bool:
        if self.interval <= 0:
            return False
        anchor = self.last_sent if self.last_sent is not None else self.started_at
        return now - anchor >= self.interval

    def mark_sent(self, now: float) -> tuple[int, int]:
        """전송 시점 기록 후 (조회 수, 오류 수) 를 반환하고 카운터 초기화."""
        self.last_sent = now
        stats = (self.checks, self.errors)
        self.checks = self.errors = 0
        return stats


def next_delay(base: float, jitter: float, backoff_step: int = 0) -> float:
    """기본 간격 + 랜덤 지터. 연속 오류 시 지수 백오프(최대 60초)."""
    delay = base + random.uniform(0, jitter)
    if backoff_step:
        delay = min(60.0, delay * (2 ** backoff_step))
    return delay


def run() -> None:
    log.info("감시 시작: %s", settings.product_url)
    log.info("API: %s", settings.api_url)
    log.info("간격 %.1fs(+0~%.1fs), 쿨다운 %.0fs, 대상 종목 %s",
             settings.poll_interval, settings.poll_jitter, settings.alert_cooldown,
             settings.watch_events or "전체")
    if not settings.slack_webhook_url:
        log.warning("SLACK_WEBHOOK_URL 미설정 — 콘솔 로그만 출력합니다.")

    tracker = AlertTracker(cooldown=settings.alert_cooldown)
    heartbeat = Heartbeat(interval=settings.heartbeat_interval, started_at=time.time())
    if heartbeat.interval > 0:
        log.info("하트비트: %.0f초마다 '이상 없음' 보고", heartbeat.interval)
    session = requests.Session()
    errors = 0
    last_summary = None
    sale_state_notified = None

    while True:
        try:
            status: ProductStatus = fetch_status(
                settings.api_url, settings.product_url, settings.watch_events, session=session
            )
            errors = 0
            heartbeat.record(ok=True)
            summary = f"[{status.sale_state}] {status.summary()}"
            if summary != last_summary:
                log.info("상태 변경: %s", summary)
                last_summary = summary
            else:
                log.debug("변화 없음: %s", summary)

            if status.sale_state != "open" and sale_state_notified != status.sale_state:
                log.warning("판매기간 밖(%s) — 접수 버튼이 비활성 상태입니다.", status.sale_state)
                sale_state_notified = status.sale_state

            to_alert, just_sold_out = tracker.decide(status.events, time.time())
            if to_alert:
                log.info("🎉 취소표 감지! %s", ", ".join(f"{e.name}={e.stock}" for e in to_alert))
                slack_notify.send(
                    settings.slack_webhook_url,
                    slack_notify.build_available_blocks(status, to_alert, settings.product_url),
                )
            if just_sold_out and settings.notify_sold_out:
                log.info("⛔ 다시 품절: %s", ", ".join(e.name for e in just_sold_out))
                slack_notify.send(
                    settings.slack_webhook_url,
                    slack_notify.build_sold_out_blocks(status, just_sold_out, settings.product_url),
                )

            now = time.time()
            if heartbeat.due(now):
                checks, errs = heartbeat.mark_sent(now)
                log.info("🟢 하트비트 전송 (조회 %d회 / 오류 %d회): %s", checks, errs, status.summary())
                slack_notify.send(
                    settings.slack_webhook_url,
                    slack_notify.build_heartbeat_blocks(status, settings.product_url, checks, errs),
                )
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # 네트워크 오류 등: 종료하지 않고 재시도
            errors += 1
            heartbeat.record(ok=False)
            log.error("조회 실패(%d회 연속): %s", errors, exc)
            if errors == 10:
                slack_notify.send(
                    settings.slack_webhook_url,
                    slack_notify.build_text_blocks("⚠️ 모니터 경고", f"API 조회가 10회 연속 실패했습니다: `{exc}`", settings.product_url),
                )

        time.sleep(next_delay(settings.poll_interval, settings.poll_jitter, backoff_step=min(errors, 4)))


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log.info("종료합니다.")
        sys.exit(0)
