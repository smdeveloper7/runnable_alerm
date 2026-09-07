"""[2단계] Playwright 기반 자동 클릭 + 옵션 선택 (결제 직전까지).

동작 흐름
  1. 영구 브라우저 프로필(BROWSER_PROFILE_DIR)로 Chromium 실행 → 로그인 상태 유지
  2. 최초 실행(--login) 시 /sign-in 을 열어 사용자가 직접 로그인(카카오/구글/이메일)
  3. API 폴링으로 취소표 감지 → 즉시 상품 페이지 새로고침
  4. '대회 신청 하기' 클릭 → 모달에서 참가자 이름 / 종목 / 목표기록 / 사이즈 선택 → '신청하기'
  5. /register(참가자 정보·본인인증·결제) 페이지에 도달하면 멈추고 Slack 알림 → 사용자가 직접 마무리

실행
  python auto_apply.py --login      # 최초 1회: 브라우저에서 로그인 후 Enter
  python auto_apply.py              # 감시 + 자동 신청
  python auto_apply.py --dry-run    # 재고와 무관하게 지금 바로 신청 흐름 시도(셀렉터 점검용)
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time

import requests
from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    sync_playwright,
)

import slack_notify
from config import settings
from monitor import AlertTracker, next_delay
from runable_api import EventStock, ProductStatus, fetch_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("auto_apply")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
APPLY_BTN = "button:has-text('대회 신청 하기')"
NAME_INPUT = "input[placeholder*='참가자 이름']"
SUBMIT_BTN = "button:has-text('신청하기')"
LOGIN_POPUP = "text=로그인 필요"
RESUME_POPUP = "text=대회신청을 이어할까요?"


class ApplyError(RuntimeError):
    pass


# ── 브라우저 ────────────────────────────────────────────────────────────
def launch(pw: Playwright, headless: bool) -> BrowserContext:
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(settings.browser_profile_dir),
        headless=headless,
        viewport={"width": 480, "height": 900},  # 모바일 레이아웃 기준 UI
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        # 기본 헤드리스 UA("HeadlessChrome")로 접속하면 사이트가 상품 정보를 렌더링하지 않음 → 일반 Chrome UA 고정
        user_agent=BROWSER_UA,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx.set_default_timeout(8000)
    return ctx


def is_logged_in(page: Page) -> bool:
    """프로필 API 가 200 이면 로그인 상태."""
    try:
        resp = page.request.get("https://runable.me/next-api/index/v1/member/my/profile")
        return resp.ok
    except Exception:
        return False


def interactive_login(ctx: BrowserContext) -> None:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://runable.me/sign-in")
    print("\n브라우저에서 로그인(카카오/구글/이메일)을 완료한 뒤 이 창에서 Enter 를 누르세요.")
    input()
    page.goto(settings.product_url)
    if is_logged_in(page):
        log.info("로그인 확인 완료. 프로필이 %s 에 저장되었습니다.", settings.browser_profile_dir)
    else:
        log.warning("로그인 상태를 확인하지 못했습니다. 다시 시도하세요.")


# ── 커스텀 드롭다운 조작 ────────────────────────────────────────────────
def pick_dropdown(page: Page, label_keyword: str, option_text: str) -> None:
    """<strong>라벨</strong> 바로 뒤의 드롭다운을 열고 option_text 항목을 클릭.

    러너블 셀렉트는 native <select> 가 아니라 div/ul/li 커스텀 컴포넌트다.
    """
    trigger = page.locator(f"strong:has-text('{label_keyword}') + div").first
    trigger.wait_for(state="visible")
    trigger.click()
    option = page.locator(f"strong:has-text('{label_keyword}') ~ div li:has-text('{option_text}')").first
    option.wait_for(state="visible")
    if option.get_attribute("disabled") is not None or "품절" in (option.inner_text() or ""):
        raise ApplyError(f"'{label_keyword}' 옵션 '{option_text}' 품절/비활성")
    option.click()
    # 선택 반영 확인
    page.locator(f"strong:has-text('{label_keyword}') + div").first.wait_for(state="visible")


def fill_apply_modal(page: Page, event_name: str) -> None:
    page.locator(NAME_INPUT).wait_for(state="visible", timeout=10000)
    if settings.apply_participant_name:
        page.fill(NAME_INPUT, settings.apply_participant_name)

    pick_dropdown(page, "종목", event_name)
    time.sleep(0.4)  # 종목 선택 후 옵션(사은품) 영역 렌더 대기

    if settings.apply_target_time:
        pick_dropdown(page, "목표기록", settings.apply_target_time)
    if settings.apply_shirt_size:
        # HALF 는 '싱글렛', 10K 는 '티셔츠' 라벨. 둘 중 존재하는 것을 사용
        label = "싱글렛" if page.locator("strong:has-text('싱글렛')").count() else "티셔츠"
        pick_dropdown(page, label, settings.apply_shirt_size)


def handle_popups(page: Page) -> None:
    if page.locator(LOGIN_POPUP).count():
        raise ApplyError("로그인 필요 팝업 — `python auto_apply.py --login` 으로 먼저 로그인하세요.")
    if page.locator(RESUME_POPUP).count():
        btn = "이어하기" if settings.apply_resume_previous else "아니오"
        log.info("이전 신청 정보 팝업 → '%s' 선택", btn)
        page.locator(f"button:has-text('{btn}')").first.click()


def try_apply(page: Page, event_name: str) -> bool:
    """상품 페이지에서 신청 모달을 채우고 /register 진입까지 시도. 성공 시 True."""
    page.goto(settings.product_url, wait_until="domcontentloaded")
    btn = page.locator(APPLY_BTN).first
    btn.wait_for(state="visible", timeout=15000)
    if btn.is_disabled():
        raise ApplyError("접수 버튼 비활성(접수 마감/시작 전)")
    btn.click()
    time.sleep(0.5)
    handle_popups(page)

    fill_apply_modal(page, event_name)
    page.locator(SUBMIT_BTN).last.click()

    # 실패 문구 확인
    time.sleep(0.6)
    for msg in ("품절된 상품이 있습니다", "품절된 종목/상품이 있습니다", "옵션값을 확인해주세요"):
        if page.locator(f"text={msg}").count():
            raise ApplyError(f"신청 실패 팝업: {msg}")

    try:
        page.wait_for_url("**/register**", timeout=15000)
    except PWTimeout:
        raise ApplyError(f"/register 로 이동하지 못했습니다 (현재 URL: {page.url})")
    return True


# ── 메인 루프 ───────────────────────────────────────────────────────────
def watch_and_apply(ctx: BrowserContext, dry_run: bool = False) -> None:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(settings.product_url)
    if not is_logged_in(page):
        log.warning("로그인되어 있지 않습니다. 먼저 `python auto_apply.py --login` 을 실행하세요.")
    else:
        log.info("로그인 상태 OK")

    tracker = AlertTracker(cooldown=settings.alert_cooldown)
    session = requests.Session()
    target = settings.apply_event.upper()
    errors = 0
    log.info("감시 시작: 종목=%s, 이름=%s, 목표=%s, 사이즈=%s",
             target, settings.apply_participant_name, settings.apply_target_time, settings.apply_shirt_size)

    while True:
        try:
            status: ProductStatus = fetch_status(settings.api_url, settings.product_url, [target], session=session)
            errors = 0
            avail = status.available_events()
            to_alert, _ = tracker.decide(status.events, time.time())

            if avail or dry_run:
                ev = avail[0] if avail else EventStock(0, target, 0, 0, 0, True)
                log.info("🎉 취소표 감지 (%s 잔여 %d) → 자동 신청 시도", ev.name, ev.stock)
                if to_alert:
                    slack_notify.send(settings.slack_webhook_url,
                                      slack_notify.build_available_blocks(status, to_alert, settings.product_url))
                try:
                    try_apply(page, ev.name)
                    log.info("✅ 결제/본인인증 단계(/register) 도달. 브라우저에서 직접 마무리하세요.")
                    slack_notify.send(settings.slack_webhook_url, slack_notify.build_text_blocks(
                        "✅ 자동 신청 진행 완료", f"*{ev.name}* 옵션 선택 후 결제 단계까지 진입했습니다. 브라우저에서 결제를 완료하세요!",
                        settings.product_url))
                    page.bring_to_front()
                    print("\n결제/본인인증을 브라우저에서 완료하세요. 스크립트를 끝내려면 Enter.")
                    input()
                    return
                except ApplyError as exc:
                    log.warning("자동 신청 실패: %s — 계속 감시합니다.", exc)
                    if dry_run:
                        return
            else:
                log.debug("[%s] %s", status.sale_state, status.summary())
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            errors += 1
            log.error("오류(%d회 연속): %s", errors, exc)
        time.sleep(next_delay(settings.poll_interval, settings.poll_jitter, backoff_step=min(errors, 4)))


def main() -> None:
    parser = argparse.ArgumentParser(description="러너블 취소표 자동 신청")
    parser.add_argument("--login", action="store_true", help="브라우저를 열어 로그인만 수행하고 프로필에 저장")
    parser.add_argument("--dry-run", action="store_true", help="재고와 무관하게 즉시 신청 흐름을 1회 시도")
    parser.add_argument("--headless", action="store_true", help="헤드리스 실행(.env HEADLESS 보다 우선)")
    args = parser.parse_args()

    headless = False if args.login else (args.headless or settings.headless)
    with sync_playwright() as pw:
        ctx = launch(pw, headless=headless)
        try:
            if args.login:
                interactive_login(ctx)
            else:
                watch_and_apply(ctx, dry_run=args.dry_run)
        finally:
            ctx.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("종료합니다.")
        sys.exit(0)
