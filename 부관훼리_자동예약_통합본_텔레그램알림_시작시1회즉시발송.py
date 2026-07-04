# -*- coding: utf-8 -*-
"""
부관훼리 자동예약 + 잔여석 변경 Telegram 알림 통합본

이 파일 하나로 실행됩니다. 별도 '부관훼리_자동예약.py'를 import 하지 않습니다.

주요 기능
1. 자동예약 진행
   - 로그인
   - 예약 조건 입력
   - Step 2 진입
   - 상품 선택
   - 잔여석 조회
   - Step 3 승객정보 페이지 이동

2. Telegram 잔여석 변경 알림
   - Step 2 진입 후 초기 잔여석 스냅샷 저장
   - 일정 간격으로 재검색
   - 부산 출발편 / 시모노세키 출발편 잔여석, 상품키, 비활성 상태 변경 감지
   - 변경 시 Telegram으로 알림 발송

3. Telegram 단독 테스트
   - 사이트 접속 없이 Telegram 메시지 발송 여부만 확인

실행 예시

[1] Telegram 알림만 테스트
    python 부관훼리_자동예약_통합본_텔레그램_바로알림.py --test-telegram

[2] 기본 실행 = 잔여석 Telegram 감시 + 초기 잔여석 1회 발송
    python 부관훼리_자동예약_통합본_텔레그램_바로알림.py

[3] 60초마다 잔여석 변경 감시
    python 부관훼리_자동예약_통합본_텔레그램_바로알림.py --interval-seconds 60

[4] 자동예약만 실행하고 싶을 때
    python 부관훼리_자동예약_통합본_텔레그램_바로알림.py --auto

[5] 자동예약 후 브라우저 종료
    python 부관훼리_자동예약_통합본_텔레그램_바로알림.py --auto --no-wait

필요 패키지
    pip install playwright telepot
    playwright install chrome

주의
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID는 아래 설정값을 사용합니다.
- 외부 공유 시 Telegram Bot Token 노출에 주의하세요.
"""

import argparse
import ctypes
import platform
import time
from typing import Any, Dict, Optional

import telepot
from playwright.sync_api import sync_playwright


# ============================================================
# 1. 기본 설정값
# ============================================================

LOGIN_URL = "https://www.pukwan.co.kr/MEMBER/002/member/login"
HOME_URL = "https://www.pukwan.co.kr/"

USER_ID = "mardep00"
PASSWORD = "mardep00"

TRIP_TYPE = "shuttle"  # "shuttle" 왕복, "oneway" 편도
START_DATE = "20260923"
END_DATE = "20260926"

ADULT_COUNT = "2"
CHILD_COUNT = "1"

PRODUCT_SELECTOR = "#rdbProduct_1"

DEFAULT_INTERVAL_SECONDS = 300

TELEGRAM_BOT_TOKEN = "581414697:AAHQiAgI-U3C0dNojeQ6ne0V149SvILlqx4"
TELEGRAM_CHAT_ID = 752516623


# ============================================================
# 2. 설정 검증 / 공통 함수
# ============================================================

def validate_config() -> None:
    if TRIP_TYPE not in {"shuttle", "oneway"}:
        raise ValueError('TRIP_TYPE must be either "shuttle" or "oneway"')
    if TRIP_TYPE == "shuttle" and not END_DATE:
        raise ValueError("END_DATE is required when TRIP_TYPE is shuttle")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is empty")
    if not TELEGRAM_CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID is empty")


def click_search_if_found(page) -> bool:
    """Step 2 페이지에 fnSearchAndRole 함수가 있으면 재검색 실행."""
    if page.evaluate("typeof fnSearchAndRole === 'function'"):
        page.evaluate("fnSearchAndRole()")
        return True
    return False


def confirm_step2_conditions(page) -> None:
    """Step 2 조건값을 다시 확인/입력."""
    if page.locator("#ipbSearchStDt").count():
        page.locator("#ipbSearchStDt").fill(START_DATE)

    if TRIP_TYPE == "shuttle" and page.locator("#ipbSearchEdDt").count():
        page.locator("#ipbSearchEdDt").fill(END_DATE)

    if page.locator("#selPeople_D").count():
        page.locator("#selPeople_D").select_option(ADULT_COUNT)

    if page.locator("#selPeople_S").count():
        page.locator("#selPeople_S").select_option(CHILD_COUNT)


def select_product(page) -> bool:
    """PRODUCT_SELECTOR에 해당하는 상품 라디오 버튼 선택."""
    if not page.locator(PRODUCT_SELECTOR).count():
        return False

    page.locator(PRODUCT_SELECTOR).scroll_into_view_if_needed()
    page.evaluate(
        """selector => {
            const radio = document.querySelector(selector);
            if (!radio) throw new Error(`${selector} not found`);
            radio.disabled = false;
            radio.checked = true;
            radio.dispatchEvent(new Event('change', { bubbles: true }));
            radio.dispatchEvent(new Event('click', { bubbles: true }));
        }""",
        PRODUCT_SELECTOR,
    )
    return page.locator(PRODUCT_SELECTOR).is_checked()


def get_search_dates(page):
    """Step 2 내부 input 값에서 출발일/귀국일 추출."""
    dates = page.evaluate(
        """() => {
            const departure = Array.from(document.querySelectorAll('input[id^="p_search_stdt_"]'))
                .map(input => input.value)
                .find(Boolean) || "";
            const ret = Array.from(document.querySelectorAll('input[id^="p_search_eddt_"]'))
                .map(input => input.value)
                .find(Boolean) || "";
            return { departure, returnDate: ret };
        }"""
    )
    return dates["departure"], dates["returnDate"]


def get_product_options(page):
    """현재 Step 2 상품 목록에서 선택 가능 상품 정보 추출."""
    products = page.evaluate(
        """() => {
            const list = document.querySelector("#pProductList") || document;
            return Array.from(list.querySelectorAll('input[id^="p_key_"]'))
                .map(input => {
                    const match = input.id.match(/^p_key_(\\d+)$/);
                    if (!match || !input.value) return null;

                    const productNo = Number(match[1]);
                    const nameInput = document.querySelector(`#p_name_${productNo}`);
                    const title = document.querySelector(`#p_product_${productNo} b`);
                    const radio = document.querySelector(`#rdbProduct_${productNo}`);

                    return {
                        product_no: productNo,
                        label: (nameInput?.value || title?.textContent || `상품 ${productNo}`).trim(),
                        product_key: input.value,
                        disabled: Boolean(radio?.disabled),
                    };
                })
                .filter(product => product && !product.disabled)
                .filter(Boolean);
        }"""
    )

    if not products:
        raise RuntimeError("No products were found in #pProductList after search")
    return products


# ============================================================
# 3. Telegram 발송 함수
# ============================================================

def send_telegram_message(message: str) -> None:
    """Telegram Bot으로 메시지 발송. 4096자 초과 시 분할 발송."""
    bot = telepot.Bot(token=TELEGRAM_BOT_TOKEN)
    max_length = 4096

    for start in range(0, len(message), max_length):
        bot.sendMessage(
            chat_id=TELEGRAM_CHAT_ID,
            text=message[start : start + max_length],
        )


def test_telegram_message() -> None:
    """사이트 접속 없이 Telegram 알림만 단독 테스트."""
    message = (
        "[부관훼리 자동예약 테스트 알림]\n\n"
        "Telegram 알림 테스트입니다.\n"
        "이 메시지가 도착하면 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 설정이 정상입니다."
    )
    send_telegram_message(message)
    print("Telegram test message sent.", flush=True)


# ============================================================
# 4. Step 2 진입 / 검색
# ============================================================

def open_step2_and_search(page) -> None:
    """로그인 후 예약 검색을 실행하고 Step 2에서 상품 선택까지 수행."""
    print("1. Go to login page", flush=True)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    print("2. Fill login fields", flush=True)
    page.locator("#ipbUserId").fill(USER_ID)
    page.locator("#ipbUserPass").fill(PASSWORD)

    print("3. Click login", flush=True)
    page.evaluate("fnUserLogin()")
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    print(f"   URL after login: {page.url}", flush=True)

    print("4. Open reservation page", flush=True)
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.locator(f"#sel-{TRIP_TYPE}").click()
    page.locator("#ipbSearchStartDt").fill(START_DATE)

    if TRIP_TYPE == "shuttle":
        page.locator("#ipbSearchEndDt").fill(END_DATE)

    page.locator("#select-adult").select_option(ADULT_COUNT)
    page.locator("#select-young-child").select_option(CHILD_COUNT)
    page.locator("#select-child").select_option("0")
    page.locator("#select-baby").select_option("0")

    print("5. Submit reservation search", flush=True)
    page.locator("#btn-reserv-submit").click()
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    print(f"   Step 2 URL: {page.url}", flush=True)

    print("6. Confirm step-2 conditions", flush=True)
    confirm_step2_conditions(page)

    print("7. Run search and select product", flush=True)
    if click_search_if_found(page):
        page.wait_for_timeout(2000)
    else:
        print("   Search function was not found. Continuing with visible products.", flush=True)

    print(f"   {PRODUCT_SELECTOR} checked: {select_product(page)}", flush=True)


# ============================================================
# 5. 잔여석 스냅샷 / 변경 감지
# ============================================================

def collect_snapshot(page) -> Dict[int, Dict[str, Any]]:
    """현재 Step 2 잔여석 상태를 dict 스냅샷으로 수집."""
    departure_date, return_date = get_search_dates(page)
    if not departure_date:
        raise RuntimeError("Step-2 departure date input was not found")

    snapshot: Dict[int, Dict[str, Any]] = {}

    for product in get_product_options(page):
        busan_count = page.evaluate(
            """({ departureDate, productKey }) => fnProductCntChk(departureDate, productKey, 'PS', null, null)""",
            {"departureDate": departure_date, "productKey": product["product_key"]},
        )

        shimono_count: Optional[int] = None
        if TRIP_TYPE == "shuttle" and return_date:
            shimono_count = page.evaluate(
                """({ returnDate, productKey }) => fnProductCntChk(returnDate, productKey, 'SP', null, null)""",
                {"returnDate": return_date, "productKey": product["product_key"]},
            )

        product_no = product["product_no"]
        snapshot[product_no] = {
            "label": product["label"],
            "product_key": product["product_key"],
            "disabled": product["disabled"],
            "busan": int(busan_count),
            "shimono": None if shimono_count is None else int(shimono_count),
        }

    return snapshot


def format_snapshot(snapshot: Dict[int, Dict[str, Any]]) -> str:
    """스냅샷을 사람이 읽기 좋은 문자열로 변환."""
    lines = []
    for product_no in sorted(snapshot):
        item = snapshot[product_no]
        lines.append(f"{item['label']} 상품({product_no})")
        lines.append(f"  부산 출발편 잔여석: {item['busan']}개")

        if item["shimono"] is not None:
            lines.append(f"  시모노세키 출발편 잔여석: {item['shimono']}개")

        lines.append(f"  상품키: {item['product_key']}")
        lines.append("")

    return "\n".join(lines).rstrip()


def snapshot_changed(prev_snapshot, current_snapshot) -> bool:
    """이전 스냅샷과 현재 스냅샷 비교."""
    if prev_snapshot is None:
        return False

    if set(prev_snapshot) != set(current_snapshot):
        return True

    for product_no in current_snapshot:
        prev_item = prev_snapshot[product_no]
        curr_item = current_snapshot[product_no]

        if prev_item["product_key"] != curr_item["product_key"]:
            return True
        if prev_item["disabled"] != curr_item["disabled"]:
            return True
        if prev_item["busan"] != curr_item["busan"]:
            return True
        if prev_item["shimono"] != curr_item["shimono"]:
            return True

    return False


def build_change_message(prev_snapshot, current_snapshot) -> str:
    prev_text = format_snapshot(prev_snapshot)
    curr_text = format_snapshot(current_snapshot)
    return (
        "잔여석 변동 감지\n\n"
        "[이전]\n"
        f"{prev_text}\n\n"
        "[현재]\n"
        f"{curr_text}"
    )


def notify_change(prev_snapshot, current_snapshot) -> None:
    """변경 감지 시 Telegram으로 발송."""
    message = build_change_message(prev_snapshot, current_snapshot)
    send_telegram_message(message)


def show_remaining_seats_popup_or_console(page) -> None:
    """자동예약 모드에서 현재 잔여석을 Windows 팝업 또는 콘솔로 표시."""
    snapshot = collect_snapshot(page)
    message = format_snapshot(snapshot)

    if platform.system().lower() == "windows":
        ctypes.windll.user32.MessageBoxW(0, message, "잔여석 조회", 0)
    else:
        print("\n[잔여석 조회]")
        print(message)
        print()


# ============================================================
# 6. 실행 모드
# ============================================================

def create_browser_page(p):
    browser = p.chromium.launch(channel="chrome", headless=False)
    page = browser.new_page(viewport={"width": 1280, "height": 900})

    def handle_dialog(dialog):
        print(f"   Browser {dialog.type}: {dialog.message}", flush=True)
        dialog.accept()

    page.on("dialog", handle_dialog)
    return browser, page


def run_auto_reservation(args) -> None:
    """자동예약 모드."""
    with sync_playwright() as p:
        print("0. Launch Chrome", flush=True)
        browser, page = create_browser_page(p)

        open_step2_and_search(page)
        show_remaining_seats_popup_or_console(page)

        if not args.no_wait and not args.skip_step2_prompt:
            input("Step 2 is ready. Review the browser, then press Enter here to go to step 3...")

        print("8. Agree to step-2 rules and click next", flush=True)
        page.locator("#chkSuccessY").scroll_into_view_if_needed()
        page.evaluate(
            """() => {
                const checkbox = document.querySelector("#chkSuccessY");
                if (!checkbox) throw new Error("#chkSuccessY not found");
                checkbox.checked = true;
                checkbox.dispatchEvent(new Event("change", { bubbles: true }));
            }"""
        )

        page.locator("#btnNext").scroll_into_view_if_needed()
        page.locator("#btnNext").click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)

        print(f"   URL after step-2 next: {page.url}", flush=True)
        if "mc001_step03" not in page.url:
            raise RuntimeError(f"Expected step 3 URL, but current URL is {page.url}")

        print("9. Step 3 passenger-info page reached", flush=True)

        if not args.no_wait and not args.skip_step3_prompt:
            input("Step 3 is ready. Fill/review passenger info in the browser, then press Enter here...")

        if args.no_wait:
            print("Done. Closing browser.", flush=True)
            browser.close()
        else:
            print("Done. Browser remains open for manual confirmation.", flush=True)
            page.wait_for_timeout(10_000_000)


def run_monitor(args) -> None:
    """잔여석 변경 Telegram 모니터링 모드."""
    if args.interval_seconds <= 0:
        raise ValueError("--interval-seconds must be greater than 0")

    with sync_playwright() as p:
        print("0. Launch Chrome", flush=True)
        browser, page = create_browser_page(p)

        open_step2_and_search(page)

        previous_snapshot = collect_snapshot(page)
        initial_text = format_snapshot(previous_snapshot)
        print("Initial snapshot:")
        print(initial_text)

        if args.send_initial:
            send_telegram_message("[부관훼리 초기 잔여석]\n\n" + initial_text)
            print("Initial snapshot sent to Telegram.", flush=True)

        while True:
            print(f"Waiting {args.interval_seconds} seconds before next check...", flush=True)

            try:
                time.sleep(args.interval_seconds)
            except KeyboardInterrupt:
                print("Ctrl+C detected. Stopping monitor.", flush=True)
                break

            try:
                click_search_if_found(page)
                page.wait_for_timeout(1000)
                select_product(page)
                current_snapshot = collect_snapshot(page)
            except KeyboardInterrupt:
                print("Ctrl+C detected. Stopping monitor.", flush=True)
                break
            except Exception as exc:
                print(f"Check failed: {exc}", flush=True)
                continue

            if snapshot_changed(previous_snapshot, current_snapshot):
                notify_change(previous_snapshot, current_snapshot)
                previous_snapshot = current_snapshot
                print("Change detected and Telegram message sent.", flush=True)
            else:
                print("No change detected.", flush=True)

        browser.close()


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Send one Telegram test message and exit without opening the reservation site.",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Run remaining-seat change monitor and send Telegram alerts when changed.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run auto-reservation mode instead of default Telegram monitor mode.",
    )
    parser.add_argument(
        "--send-initial",
        action="store_true",
        help="In monitor mode, send the initial remaining-seat snapshot to Telegram once.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="How often to re-check remaining seats in monitor mode.",
    )

    parser.add_argument("--no-wait", action="store_true", help="Close the browser after the scripted steps.")
    parser.add_argument(
        "--skip-step2-prompt",
        action="store_true",
        help="Do not wait for Enter before moving from step 2 to step 3.",
    )
    parser.add_argument(
        "--skip-step3-prompt",
        action="store_true",
        help="Do not wait on the passenger-info page after reaching step 3.",
    )

    return parser.parse_args()


def main() -> None:
    validate_config()
    args = parse_args()

    if args.test_telegram:
        test_telegram_message()
    elif args.auto:
        run_auto_reservation(args)
    else:
        # 기본 실행은 Telegram 감시 모드로 동작합니다.
        # 사용자가 --send-initial을 빼먹어도 시작 시 현재 잔여석을 1회 발송합니다.
        args.send_initial = True
        run_monitor(args)


if __name__ == "__main__":
    main()
