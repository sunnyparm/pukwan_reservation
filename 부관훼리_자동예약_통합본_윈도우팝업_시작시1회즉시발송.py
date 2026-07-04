# -*- coding: utf-8 -*-
"""
부관훼리 자동예약 + 잔여석 변경 Windows 팝업 알림 통합본

이 파일 하나로 실행됩니다. 별도 '부관훼리_자동예약.py'를 import 하지 않습니다.

기준
- 사용자가 제공한 Windows 팝업 알림 코드의 핵심 로직을 반영했습니다.
- 변경 감지 시 ctypes.windll.user32.MessageBoxW 로 Windows 팝업을 띄웁니다.
- Telegram 발송 코드는 제거했습니다.

주요 기능
1. 자동예약 진행
   - 로그인
   - 예약 조건 입력
   - Step 2 진입
   - 상품 선택
   - 현재 잔여석 팝업 표시
   - Step 3 승객정보 페이지 이동

2. Windows 팝업 잔여석 변경 감시
   - Step 2 진입 후 초기 잔여석 스냅샷 저장
   - 일정 간격으로 재검색
   - 부산 출발편 / 시모노세키 출발편 잔여석, 상품키, 비활성 상태 변경 감지
   - 변경 시 Windows 팝업 알림 표시

실행 예시

[1] 기본 실행 = 잔여석 변경 감시 + Windows 팝업 알림
    python 부관훼리_자동예약_통합본_윈도우팝업.py

[2] 60초마다 잔여석 변경 감시
    python 부관훼리_자동예약_통합본_윈도우팝업.py --interval-seconds 60

[3] 시작 시 현재 잔여석도 팝업으로 1회 표시
    python 부관훼리_자동예약_통합본_윈도우팝업.py --show-initial

[4] 자동예약만 실행
    python 부관훼리_자동예약_통합본_윈도우팝업.py --auto

[5] 자동예약 후 브라우저 종료
    python 부관훼리_자동예약_통합본_윈도우팝업.py --auto --no-wait

필요 패키지
    pip install playwright
    playwright install chrome

주의
- Windows MessageBoxW 사용 파일입니다.
- macOS/Linux에서는 ctypes.windll.user32가 없으므로 팝업 대신 콘솔 출력으로 처리합니다.
"""

import argparse
import ctypes
import platform
import time
from typing import Any, Dict, Optional

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


# ============================================================
# 2. 설정 검증 / 공통 함수
# ============================================================

def validate_config() -> None:
    if TRIP_TYPE not in {"shuttle", "oneway"}:
        raise ValueError('TRIP_TYPE must be either "shuttle" or "oneway"')
    if TRIP_TYPE == "shuttle" and not END_DATE:
        raise ValueError("END_DATE is required when TRIP_TYPE is shuttle")


def show_popup_or_console(message: str, title: str = "알림") -> None:
    """Windows에서는 MessageBoxW 팝업, 그 외 환경에서는 콘솔 출력."""
    if platform.system().lower() == "windows":
        ctypes.windll.user32.MessageBoxW(0, message, title, 0)
    else:
        print(f"\n[{title}]")
        print(message)
        print()


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
# 3. Step 2 진입 / 검색
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
# 4. 잔여석 스냅샷 / 변경 감지 / 팝업 알림
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


def popup_change(prev_snapshot, current_snapshot) -> None:
    """변경 감지 시 Windows 팝업 표시. 사용자가 제공한 정상 작동 코드의 알림 방식."""
    message = build_change_message(prev_snapshot, current_snapshot)
    show_popup_or_console(message, "잔여석 변경 알림")


def popup_snapshot(snapshot, title: str = "잔여석 조회") -> None:
    message = format_snapshot(snapshot)
    show_popup_or_console(message, title)


# ============================================================
# 5. 실행 모드
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
        popup_snapshot(collect_snapshot(page), "잔여석 조회")

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


def run_popup_monitor(args) -> None:
    """잔여석 변경 Windows 팝업 모니터링 모드."""
    if args.interval_seconds <= 0:
        raise ValueError("--interval-seconds must be greater than 0")

    with sync_playwright() as p:
        print("0. Launch Chrome", flush=True)
        browser, page = create_browser_page(p)

        open_step2_and_search(page)

        previous_snapshot = collect_snapshot(page)
        print("Initial snapshot:")
        print(format_snapshot(previous_snapshot))

        if args.show_initial:
            popup_snapshot(previous_snapshot, "초기 잔여석 조회")
            print("Initial snapshot popup shown.", flush=True)

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
                popup_change(previous_snapshot, current_snapshot)
                previous_snapshot = current_snapshot
                print("Change detected and popup shown.", flush=True)
            else:
                print("No change detected.", flush=True)

        browser.close()


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run auto-reservation mode instead of default popup monitor mode.",
    )
    parser.add_argument(
        "--show-initial",
        action="store_true",
        help="In popup monitor mode, show the initial remaining-seat snapshot once.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="How often to re-check remaining seats in popup monitor mode.",
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

    if args.auto:
        run_auto_reservation(args)
    else:
        # 기본 실행은 Windows 팝업 변경감시 모드입니다.
        run_popup_monitor(args)


if __name__ == "__main__":
    main()
