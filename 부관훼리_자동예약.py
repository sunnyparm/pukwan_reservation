import argparse
import ctypes

from playwright.sync_api import sync_playwright


LOGIN_URL = "https://www.pukwan.co.kr/MEMBER/002/member/login"
HOME_URL = "https://www.pukwan.co.kr/"

USER_ID = "mardep00"
PASSWORD = "mardep00"

TRIP_TYPE = "shuttle"  # "shuttle" for round trip, "oneway" for one way
START_DATE = "20260923"
END_DATE = "20260926"
ADULT_COUNT = "2"
CHILD_COUNT = "1"
PRODUCT_SELECTOR = "#rdbProduct_1"


def click_search_if_found(page) -> bool:
    if page.evaluate("typeof fnSearchAndRole === 'function'"):
        page.evaluate("fnSearchAndRole()")
        return True
    return False


def confirm_step2_conditions(page) -> None:
    if page.locator("#ipbSearchStDt").count():
        page.locator("#ipbSearchStDt").fill(START_DATE)
    if TRIP_TYPE == "shuttle" and page.locator("#ipbSearchEdDt").count():
        page.locator("#ipbSearchEdDt").fill(END_DATE)
    if page.locator("#selPeople_D").count():
        page.locator("#selPeople_D").select_option(ADULT_COUNT)
    if page.locator("#selPeople_S").count():
        page.locator("#selPeople_S").select_option(CHILD_COUNT)


def select_product(page) -> bool:
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


def show_remaining_seats_popup(page) -> None:
    departure_date, return_date = get_search_dates(page)

    if not departure_date:
        raise RuntimeError("Required search data was not populated before reading remaining seats")

    product_rows = []
    for product in get_product_options(page):
        busan_count = page.evaluate(
            """({ departureDate, productKey }) => fnProductCntChk(departureDate, productKey, 'PS', null, null)""",
            {"departureDate": departure_date, "productKey": product["product_key"]},
        )

        shimono_count = 0
        if TRIP_TYPE == "shuttle" and return_date:
            shimono_count = page.evaluate(
                """({ returnDate, productKey }) => fnProductCntChk(returnDate, productKey, 'SP', null, null)""",
                {"returnDate": return_date, "productKey": product["product_key"]},
            )

        product_rows.append(
            f"{product['label']} 상품({product['product_no']})\n"
            f"  부산 출발편 잔여석: {busan_count}개\n"
            f"  시모노세키 출발편 잔여석: {shimono_count}개"
        )

    message = "\n\n".join(product_rows)
    ctypes.windll.user32.MessageBoxW(0, message, "잔여석 조회", 0)
def main() -> None:
    if TRIP_TYPE not in {"shuttle", "oneway"}:
        raise ValueError('TRIP_TYPE must be either "shuttle" or "oneway"')
    if TRIP_TYPE == "shuttle" and not END_DATE:
        raise ValueError("END_DATE is required when TRIP_TYPE is shuttle")

    parser = argparse.ArgumentParser()
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
    parser.add_argument(
        "--skip-step2-refresh-loop",
        action="store_true",
        help="Do not repeat step-2 refresh/search/product selection before the step-2 prompt.",
    )
    args = parser.parse_args()

    with sync_playwright() as p:
        print("0. Launch Chrome", flush=True)
        browser = p.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        def handle_dialog(dialog):
            print(f"   Browser {dialog.type}: {dialog.message}", flush=True)
            dialog.accept()

        page.on("dialog", handle_dialog)

        print(f"1. Go to login page: {LOGIN_URL}", flush=True)
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print("2. Fill normal-member login fields", flush=True)
        page.locator("#ipbUserId").fill(USER_ID)
        page.locator("#ipbUserPass").fill(PASSWORD)

        print("3. Click normal login", flush=True)
        page.evaluate("fnUserLogin()")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        print(f"   URL after login: {page.url}", flush=True)

        print("4. Fill reservation search on home page", flush=True)
        page.goto(HOME_URL, wait_until="domcontentloaded")
        page.locator(f"#sel-{TRIP_TYPE}").click()
        page.locator("#ipbSearchStartDt").fill(START_DATE)
        if TRIP_TYPE == "shuttle":
            page.locator("#ipbSearchEndDt").fill(END_DATE)
        page.locator("#select-adult").select_option(ADULT_COUNT)
        page.locator("#select-young-child").select_option(CHILD_COUNT)
        page.locator("#select-child").select_option("0")
        page.locator("#select-baby").select_option("0")

        print("5. Click reservation button", flush=True)
        page.locator("#btn-reserv-submit").click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        print(f"   Step 2 URL: {page.url}", flush=True)

        print("6. Confirm step-2 conditions", flush=True)
        confirm_step2_conditions(page)

        print("7. Click search and select product", flush=True)
        if click_search_if_found(page):
            page.wait_for_timeout(2000)
        else:
            print("   Search function was not found. Continuing with visible products.", flush=True)

        print(f"   {PRODUCT_SELECTOR} checked: {select_product(page)}", flush=True)
        show_remaining_seats_popup(page)

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


if __name__ == "__main__":
    main()
