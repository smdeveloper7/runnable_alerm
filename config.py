"""환경 변수(.env) 로딩 및 공통 설정."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _list(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Settings:
    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")
    product_id: int = int(os.getenv("PRODUCT_ID", "19627"))
    comp_id: int = int(os.getenv("COMP_ID", "18962"))
    watch_events: list[str] = field(default_factory=lambda: _list(os.getenv("WATCH_EVENTS")))

    poll_interval: float = float(os.getenv("POLL_INTERVAL", "4"))
    poll_jitter: float = float(os.getenv("POLL_JITTER", "2"))
    alert_cooldown: float = float(os.getenv("ALERT_COOLDOWN", "300"))
    notify_sold_out: bool = _bool(os.getenv("NOTIFY_SOLD_OUT"), True)
    # 이상 없어도 주기적으로 "정상 감시 중" 알림을 보내는 간격(초). 0이면 끔
    heartbeat_interval: float = float(os.getenv("HEARTBEAT_INTERVAL", "3600"))

    browser_profile_dir: Path = BASE_DIR / os.getenv("BROWSER_PROFILE_DIR", "./browser-profile")
    headless: bool = _bool(os.getenv("HEADLESS"), False)
    apply_event: str = os.getenv("APPLY_EVENT", "HALF")
    apply_participant_name: str = os.getenv("APPLY_PARTICIPANT_NAME", "")
    apply_target_time: str = os.getenv("APPLY_TARGET_TIME", "")
    apply_shirt_size: str = os.getenv("APPLY_SHIRT_SIZE", "")
    apply_resume_previous: bool = _bool(os.getenv("APPLY_RESUME_PREVIOUS"), True)

    @property
    def product_url(self) -> str:
        return f"https://runable.me/product/{self.product_id}?comp={self.comp_id}"

    @property
    def api_url(self) -> str:
        return f"https://runable.me/next-api/index/v1/product/detail/{self.product_id}?&auth=false"


settings = Settings()
