import argparse
import ctypes
import importlib.util
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


SCRIPT_PATH = Path(__file__).with_name("부관훼리_자동예약.py")
DEFAULT_INTERVAL_SECONDS = 300


def load_base_module():
    spec = importlib.util.spec_from_file_location("pukwan_auto_base", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load base script from {SCRIPT_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_base_module()


def collect_snapshot(page):
    departure_date, return_date = BASE.get_search_dates(page)
    if not departure_date:
        raise RuntimeError("Step-2 departure date input was not found")

    snapshot = {}
    for product in BASE.get_product_options(page):
        busan_count = page.evaluate(
            """({ departureDate, productKey }) => fnProductCntChk(departureDate, productKey, 'PS', null, null)""",
            {"departureDate": departure_date, "productKey": product["product_key"]},
        )

        shimono_count = None
        if BASE.TRIP_TYPE == "shuttle" and return_date:
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


def format_snapshot(snapshot) -> str:
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


def popup_change(prev_snapshot, current_snapshot):
    prev_text = format_snapshot(prev_snapshot)
    curr_text = format_snapshot(current_snapshot)
    message = (
        "잔여석 변동 감지\n\n"
        "[이전]\n"
        f"{prev_text}\n\n"
        "[현재]\n"
        f"{curr_text}"
    )
    ctypes.windll.user32.MessageBoxW(0, message, "잔여석 변경 알림", 0)


def open_step2_and_search(page):
    print("1. Go to login page", flush=True)
    page.goto(BASE.LOGIN_URL, wait_until="domcontentloaded")

    print("2. Fill login fields", flush=True)
    page.locator("#ipbUserId").fill(BASE.USER_ID)
    page.locator("#ipbUserPass").fill(BASE.PASSWORD)

    print("3. Click login", flush=True)
    page.evaluate("fnUserLogin()")
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    print("4. Open reservation page", flush=True)
    page.goto(BASE.HOME_URL, wait_until="domcontentloaded")
    page.locator(f"#sel-{BASE.TRIP_TYPE}").click()
    page.locator("#ipbSearchStartDt").fill(BASE.START_DATE)
    if BASE.TRIP_TYPE == "shuttle":
        page.locator("#ipbSearchEndDt").fill(BASE.END_DATE)
    page.locator("#select-adult").select_option(BASE.ADULT_COUNT)
    page.locator("#select-young-child").select_option(BASE.CHILD_COUNT)
    page.locator("#select-child").select_option("0")
    page.locator("#select-baby").select_option("0")

    print("5. Submit reservation search", flush=True)
    page.locator("#btn-reserv-submit").click()
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    print("6. Confirm step-2 conditions", flush=True)
    BASE.confirm_step2_conditions(page)

    print("7. Run search and select product", flush=True)
    if BASE.click_search_if_found(page):
        page.wait_for_timeout(2000)
    else:
        print("   Search function was not found. Continuing with visible products.", flush=True)
    print(f"   {BASE.PRODUCT_SELECTOR} checked: {BASE.select_product(page)}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="How often to re-check the remaining seats.",
    )
    args = parser.parse_args()

    if args.interval_seconds <= 0:
        raise ValueError("--interval-seconds must be greater than 0")

    with sync_playwright() as p:
        print("0. Launch Chrome", flush=True)
        browser = p.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        def handle_dialog(dialog):
            print(f"   Browser {dialog.type}: {dialog.message}", flush=True)
            dialog.accept()

        page.on("dialog", handle_dialog)

        open_step2_and_search(page)

        previous_snapshot = collect_snapshot(page)
        print("Initial snapshot:")
        print(format_snapshot(previous_snapshot))

        while True:
            print(f"Waiting {args.interval_seconds} seconds before next check...", flush=True)
            try:
                time.sleep(args.interval_seconds)
            except KeyboardInterrupt:
                print("Ctrl+C detected. Stopping monitor.", flush=True)
                break

            try:
                BASE.click_search_if_found(page)
                page.wait_for_timeout(1000)
                BASE.select_product(page)
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


if __name__ == "__main__":
    main()
