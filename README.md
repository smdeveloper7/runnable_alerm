# 러너블(Runable) 취소표 모니터 & 자동 신청

마감된 러너블 대회 상품 페이지에서 **취소표(잔여 재고) 발생을 감지 → Slack 알림 → (선택) Playwright 자동 신청**까지 도와주는 스크립트입니다.

- 타깃: `https://runable.me/product/19627?comp=18962` (2026 GARMIN RUN KOREA 추가접수)
- 감지 원리: 페이지가 내부적으로 호출하는 공개 API
  `GET https://runable.me/next-api/index/v1/product/detail/19627?auth=false`
  의 `productEvents[].stock.count` 값을 폴링합니다. 프론트 코드 기준 종목 선택 가능 조건은
  `isUse && stock.count > 0 && quantity != 0` 이며, 판매기간(`saleStartDateTime`~`saleEndDateTime`, KST) 안에서만 유효합니다.
  HTML 렌더링 없이 JSON 한 번만 받으므로 가볍고 빠릅니다.

## 1. 폴더 구조

```
runable-monitor/
├── .env.example          # 환경 변수 템플릿 → 복사해서 .env 로 사용
├── requirements.txt
├── config.py             # .env 로딩, 공통 설정
├── runable_api.py        # 상품 API 조회 + 재고 파싱 (순수 로직)
├── slack_notify.py       # Slack Incoming Webhook + Block Kit 메시지
├── monitor.py            # [1단계] 폴링 루프 + 쿨다운 + 알림
├── auto_apply.py         # [2단계] Playwright 자동 클릭/옵션 선택
├── browser-profile/      # (자동 생성) 로그인 상태가 저장되는 브라우저 프로필
└── tests/
    ├── fixtures/product_detail_soldout.json   # 실제 API 응답 샘플(품절 상태)
    ├── test_runable_api.py
    └── test_monitor.py
```

## 2. 설치

```powershell
cd runable-monitor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium     # 2단계(auto_apply.py) 사용 시에만 필요
```

Python 3.10 이상이 필요합니다.

## 3. Slack Webhook URL 설정 (.env)

1. https://api.slack.com/apps → **Create New App** → *From scratch* → 앱 이름/워크스페이스 선택
2. 왼쪽 메뉴 **Incoming Webhooks** → *Activate Incoming Webhooks* 를 **On**
3. 하단 **Add New Webhook to Workspace** → 알림 받을 채널 선택 → *Allow*
4. 생성된 `https://hooks.slack.com/services/T.../B.../...` URL 복사
5. 프로젝트 폴더에서 `.env.example` 을 `.env` 로 복사하고 붙여넣기

```powershell
Copy-Item .env.example .env
notepad .env
```

```ini
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T0000/B0000/XXXXXXXX
WATCH_EVENTS=HALF,10K
POLL_INTERVAL=4
POLL_JITTER=2
ALERT_COOLDOWN=300
```

`.env` 는 비밀값이므로 git 에 올리지 마세요(`.gitignore` 포함됨).

전송 테스트:

```powershell
python -c "import slack_notify, config; print(slack_notify.send(config.settings.slack_webhook_url, slack_notify.build_text_blocks('테스트', '러너블 모니터 연결 OK', config.settings.product_url)))"
```

## 4. [1단계] 모니터링 실행

```powershell
python monitor.py
```

- `POLL_INTERVAL` + `0~POLL_JITTER` 초의 랜덤 지연으로 요청하며, 요청마다 User-Agent 를 바꿉니다.
- 종목별로 **품절 → 잔여 전환 시 즉시 알림**, 잔여 상태가 유지되면 `ALERT_COOLDOWN` 초마다 1회만 재알림합니다.
- 다시 품절되면 1회 알림(`NOTIFY_SOLD_OUT=false` 로 끌 수 있음).
- 이상이 없어도 `HEARTBEAT_INTERVAL` 초(기본 3600 = 1시간)마다 "🟢 감시 정상 동작 중" 보고를 보냅니다. 현재 상태·재고·해당 주기의 조회/오류 횟수가 포함되며, `0` 으로 끌 수 있습니다.
- 네트워크 오류는 지수 백오프(최대 60초)로 재시도하고 종료하지 않습니다. 10회 연속 실패 시 Slack 경고 1회.
- 알림 메시지(Block Kit): 헤더 + 종목/잔여/가격 + 감지 시각 + **[대회 신청 페이지 바로가기]** 버튼

## 5. [2단계] 자동 신청 (Playwright)

### 5-1. 최초 1회 로그인 (세션 저장)

```powershell
python auto_apply.py --login
```

Chromium 이 열리면 카카오/구글/이메일로 로그인한 뒤 터미널에서 Enter. 로그인 쿠키가
`BROWSER_PROFILE_DIR`(기본 `./browser-profile`) 에 저장되어 다음 실행부터 로그인 상태가 유지됩니다.

### 5-2. 옵션 설정 (.env)

```ini
APPLY_EVENT=HALF                     # HALF | 10K
APPLY_PARTICIPANT_NAME=홍길동
APPLY_TARGET_TIME=하프 1:50:00 이내    # 10K 는 예: 10K 55:00 이내
APPLY_SHIRT_SIZE=남성 XL             # HALF=싱글렛(남성 S~2XL / 여성 S~L), 10K=티셔츠(S~2XL)
APPLY_RESUME_PREVIOUS=true           # "대회신청을 이어할까요?" 팝업에서 이어하기
```

값은 실제 드롭다운 문구와 **부분 일치**하면 됩니다. 현재 확인된 옵션값:

| 종목 | 목표기록 | 사이즈 |
|------|----------|--------|
| HALF | 하프 1:30:00 이내 / 1:50:00 이내 / 2:10:00 이내 / 2:10:00 초과 | 싱글렛: 남성 S·M·L·XL·2XL, 여성 S·M·L |
| 10K  | 10K 45:00 이내 / 55:00 이내 / 55:00 초과 | 티셔츠: S·M·L·XL·2XL |

### 5-3. 실행

```powershell
python auto_apply.py            # API 감시 → 취소표 발생 즉시 브라우저에서 자동 신청
python auto_apply.py --dry-run  # 재고와 무관하게 지금 신청 흐름을 1회 시도(셀렉터 점검)
```

취소표가 감지되면: 페이지 새로고침 → **대회 신청 하기** 클릭 → 모달에서 이름/종목/목표기록/사이즈 선택 → **신청하기** →
`/register`(참가자 정보·본인인증·결제) 페이지 도달 시 멈추고 Slack 으로 알린 뒤 브라우저를 앞으로 띄웁니다.
**본인인증과 결제는 반드시 사용자가 직접 진행**합니다.

## 6. 테스트

```powershell
python -m pytest -q
```

실제 API 응답 샘플(`tests/fixtures`)로 파싱 로직과 쿨다운 로직을 검증합니다.

## 7. 유의사항

- 페이지는 판매기간 내에는 "대회 신청 하기" 버튼이 **항상 활성**이고, 종목 드롭다운 항목이 "(품절)" 로 비활성됩니다. 따라서 버튼 상태가 아니라 재고 수량으로 판단합니다.
- 러너블 UI 가 바뀌면 `auto_apply.py` 상단의 셀렉터(`APPLY_BTN`, `NAME_INPUT`, `pick_dropdown` 의 `strong + div` 구조)를 조정하세요. `--dry-run` 으로 빠르게 점검할 수 있습니다.
- 너무 짧은 폴링 간격은 IP 차단 위험이 있습니다. 3~5초 이상을 권장합니다.
- 헤드리스 Chromium 기본 User-Agent(`HeadlessChrome`)로 접속하면 사이트가 상품 정보를 렌더링하지 않습니다. `auto_apply.py` 는 일반 Chrome UA 를 고정해서 실행합니다.
- 검증 범위: 비로그인 상태에서 "대회 신청 하기" 버튼 클릭 → "로그인 필요" 팝업까지는 실제 페이지로 확인했습니다. 로그인 후 모달(이름/종목/목표기록/사이즈 드롭다운) 셀렉터는 프론트 번들 코드 분석 기반이므로, 로그인 뒤 `--dry-run` 으로 한 번 점검하세요.
