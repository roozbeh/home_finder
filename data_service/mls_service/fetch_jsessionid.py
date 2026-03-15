#!/usr/bin/env python3
"""
fetch_jsessionid.py
--------------------
Automates the full Clareity SSO → ConnectMLS login flow using Selenium.

Key facts from HTML inspection:
  - Form id="form_login", action="" (posts to same URL), method="POST"
  - Username: id="username", name="username"
  - Password: id="password", name="password"
  - Submit button: id="loginbtn", type="button" — triggers inputCheck() via JS
    which calls document.forms["form_login"].submit() after validation
  - Hidden field "pingOneSignalsResult" is populated ASYNC by PingOne Signals SDK
    Must wait for it to be non-empty before clicking or server rejects login
  - Error div: id="error" — shows "No User Found" on bad creds or missing token

Install:
    pip install selenium webdriver-manager

Usage:
    export MLS_USERNAME='your_username'
    export MLS_PASSWORD='your_password'
    export HEADLESS=false          # optional: watch the browser
    python fetch_jsessionid.py
"""

import os
import sys
import json
import time
import requests

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ── Config ────────────────────────────────────────────────────────────────────

LOGIN_URL  = "https://maxebrdi.clareityiam.net/idp/login"
SEARCH_URL = "https://bridge.connectmls.com/api/search/listing/list"

USERNAME = os.environ.get("MLS_USERNAME", "your_username_here")
PASSWORD = os.environ.get("MLS_PASSWORD", "your_password_here")
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"

WAIT_TIMEOUT = 60


# ── Browser setup ─────────────────────────────────────────────────────────────

def make_driver() -> webdriver.Chrome:
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    try:
        driver = webdriver.Chrome(options=opts)
    except Exception:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=opts
            )
        except ImportError:
            print("ERROR: Run: pip install webdriver-manager")
            sys.exit(1)

    # Mask webdriver flag to avoid bot detection
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


# ── Login flow ────────────────────────────────────────────────────────────────

def login_and_get_cookies(username: str, password: str) -> list[dict]:
    driver = make_driver()
    wait   = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        # ── Step 1: Load login page ───────────────────────────────────────────
        print("[1/4] Loading login page...")
        driver.get(LOGIN_URL)
        wait.until(EC.presence_of_element_located((By.ID, "username")))
        print(f"      Page title: {driver.title}")

        # ── Step 2: Wait for PingOne Signals SDK to populate bot-detection field
        # This field MUST be non-empty or the server returns "No User Found"
        # even with correct credentials. It's populated asynchronously.
        print("[2/4] Waiting for PingOne bot-detection token to populate...")
        ping_populated = False
        for i in range(30):  # wait up to 30 seconds
            val = driver.execute_script(
                "var el = document.getElementById('pingOneSignalsResult');"
                "return el ? el.value : '';"
            )
            if val and len(val) > 50:
                print(f"      ✓ Token populated after {i+1}s ({len(val)} chars)")
                ping_populated = True
                break
            print(f"      {i+1}s — token length: {len(val) if val else 0}")
            time.sleep(1)

        if not ping_populated:
            print("      ⚠ Token never populated — attempting login anyway")
            print("        (This may cause 'No User Found' even with correct credentials)")

        # ── Step 3: Fill credentials and submit ──────────────────────────────
        print("[3/4] Filling credentials and submitting...")

        user_field = driver.find_element(By.ID, "username")
        pass_field = driver.find_element(By.ID, "password")

        # Use JS to set values AND fire input/change events so the page's
        # validation listener enables the login button.
        driver.execute_script("""
            function fillAndNotify(el, val) {
                var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                nativeInputValueSetter.call(el, val);
                el.dispatchEvent(new Event('input',  {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keyup',    {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keypress', {bubbles: true}));
            }
            fillAndNotify(arguments[0], arguments[1]);
            fillAndNotify(arguments[2], arguments[3]);
        """, user_field, username, pass_field, password)
        time.sleep(1)

        # Wait for the button to become enabled (up to 5s)
        for _ in range(10):
            is_disabled = driver.execute_script(
                "return document.getElementById('loginbtn').disabled;"
            )
            if not is_disabled:
                break
            time.sleep(0.5)

        # Use JS click to bypass any overlapping DOM elements
        driver.execute_script("document.getElementById('loginbtn').click();")

        # ── Step 4: Wait briefly then check for error ─────────────────────────
        time.sleep(2)
        try:
            error_div = driver.find_element(By.ID, "error")
            error_text = error_div.text.strip()
            if error_text:
                print(f"\n  ✗ Server returned login error: '{error_text}'")
                if "No User Found" in error_text:
                    print("    Possible causes:")
                    print("    1. Username or password is wrong")
                    print("    2. PingOne bot-detection token was missing/invalid")
                    print(f"    Token was populated: {ping_populated}")
                _dump_page(driver, "/tmp/debug_after_login.html")
                raise RuntimeError(f"Login failed with server error: '{error_text}'")
        except NoSuchElementException:
            pass  # no error div = good sign

        # ── Step 5: Wait for redirect to portal ──────────────────────────────
        print("      Waiting for redirect to Clareity portal...")
        deadline = time.time() + WAIT_TIMEOUT
        while time.time() < deadline:
            cur = driver.current_url
            remaining = int(deadline - time.time())
            print(f"  {remaining:2d}s — {cur[:90]}")

            if "bridge.connectmls.com" in cur:
                print("  ✓ Landed directly on ConnectMLS!")
                break
            if "clareity.net" in cur and "clareityiam.net" not in cur:
                print("  ✓ Reached Clareity portal")
                break

            # Mid-redirect error check
            try:
                err = driver.find_element(By.ID, "error").text.strip()
                if err:
                    print(f"  ⚠  Error: '{err}'")
            except NoSuchElementException:
                pass

            time.sleep(1)
        else:
            _dump_page(driver, "/tmp/debug_after_login.html")
            body = driver.find_element(By.TAG_NAME, "body").text[:400]
            raise RuntimeError(
                f"Timed out waiting for portal. Still at: {driver.current_url}\n"
                f"Page text: {body}"
            )

        # ── Step 6: Click ConnectMLS tile (if not already there) ──────────────
        if "bridge.connectmls.com" not in driver.current_url:
            print(f"\n      On portal: {driver.current_url}")
            print("      Looking for ConnectMLS tile...")

            # Angular portals render tiles asynchronously — poll for up to 20 s
            mls_link = None
            for attempt in range(20):
                mls_link = _find_connectmls_link(driver)
                if mls_link:
                    break
                if attempt == 0:
                    print("      (waiting for Angular tiles to render...)")
                time.sleep(1)

            if not mls_link:
                _dump_page(driver, "/tmp/debug_portal.html")
                # Log every h4 tile title visible on the page to help debug
                print("\n  All app tiles found on portal page (h4.apptitle):")
                for h in driver.find_elements(By.CSS_SELECTOR, "h4.apptitle")[:30]:
                    print(f"    '{h.text.strip()}'")
                print("\n  All data-title divs found:")
                for d in driver.find_elements(By.CSS_SELECTOR, "div[data-title]")[:30]:
                    print(f"    data-title='{d.get_attribute('data-title')}'")
                raise RuntimeError(
                    "Could not find ConnectMLS tile. "
                    "Check /tmp/debug_portal.html"
                )

            print(f"  ✓ Found ConnectMLS: tag={mls_link.tag_name} "
                  f"href='{mls_link.get_attribute('href')}' "
                  f"id='{mls_link.get_attribute('id')}'")

            # Remember how many tabs we have before clicking
            handles_before = set(driver.window_handles)

            # Scroll element into view, then try a real click;
            # fall back to JS click if intercepted (covered by another element)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", mls_link)
            time.sleep(0.4)
            try:
                mls_link.click()
                print("  ✓ Clicked ConnectMLS tile (native click)")
            except Exception as click_err:
                print(f"  ⚠ Native click failed ({click_err}), trying JS click...")
                driver.execute_script("arguments[0].click();", mls_link)
                print("  ✓ Clicked ConnectMLS tile (JS click)")

            # Wait for navigation — handles same-tab redirect OR new tab opening
            print("  Waiting for ConnectMLS navigation...")
            deadline = time.time() + WAIT_TIMEOUT
            reached = False
            while time.time() < deadline:
                remaining = int(deadline - time.time())

                # Check current tab first
                cur = driver.current_url
                if "bridge.connectmls.com" in cur:
                    reached = True
                    break

                # Check if a new tab opened and switch to it
                current_handles = set(driver.window_handles)
                new_handles = current_handles - handles_before
                if new_handles:
                    new_handle = next(iter(new_handles))
                    print(f"  ✓ New tab opened — switching to it")
                    driver.switch_to.window(new_handle)
                    # Give the new tab up to 15 s to load connectmls.com
                    tab_deadline = time.time() + 15
                    while time.time() < tab_deadline:
                        if "bridge.connectmls.com" in driver.current_url:
                            reached = True
                            break
                        time.sleep(0.5)
                    if reached:
                        break
                    # If still not there, keep waiting in outer loop
                    handles_before = current_handles  # don't re-detect same tab

                print(f"  {remaining:2d}s — {cur[:80]}")
                time.sleep(1)

            if not reached:
                _dump_page(driver, "/tmp/debug_after_mls_click.html")
                raise RuntimeError(
                    f"Did not reach bridge.connectmls.com. "
                    f"Still at: {driver.current_url}"
                )

        print(f"\n  ✓ On ConnectMLS: {driver.current_url}")
        time.sleep(3)

        # ── Collect all cookies ───────────────────────────────────────────────
        cookies = driver.get_cookies()
        print(f"\n  Cookies collected:")
        for c in cookies:
            print(f"    {c['name']} = {c['value'][:60]}  (domain={c.get('domain')})")

        return cookies

    finally:
        driver.quit()


def _find_connectmls_link(driver):
    """
    The ConnectMLS tile is an Angular component. The structure in the DOM is:

        <div class="app-border ... appsection" data-title="ConnectMLS" ...>
            <div class="app-icon ..." isformpost="false" id="1096">   ← click THIS
                <img alt="Connect MLS" ...>
            </div>
        </div>

    The h4.apptitle text node is inside a sibling div and has no click handler.
    Angular's (click) binding lives on the outer app-border div or app-icon div.
    We target the most specific reliable element first (the app-icon div by id),
    then fall back to broader selectors.
    """
    selectors = [
        # 1. The app-icon div — Angular's click handler fires here
        #    data-title="ConnectMLS" on the parent div.app-border, img alt="Connect MLS"
        (By.XPATH, "//div[contains(@class,'app-border') and @data-title='ConnectMLS']//div[contains(@class,'app-icon')]"),
        # 2. The outer app-border div itself (parent of the icon and title)
        (By.XPATH, "//div[contains(@class,'app-border') and @data-title='ConnectMLS']"),
        # 3. app-border via data-title attribute (CSS)
        (By.CSS_SELECTOR, "div.app-border[data-title='ConnectMLS']"),
        # 4. app-standard-app Angular component wrapping the ConnectMLS icon
        (By.XPATH, "//app-standard-app[.//h4[contains(normalize-space(text()),'ConnectMLS')]]"),
        # 5. The img with alt="Connect MLS" — clicking the image also works
        (By.XPATH, "//img[@alt='Connect MLS']"),
        (By.CSS_SELECTOR, "img[alt='Connect MLS']"),
        # 6. The app-icon div by its appDetailConnect title sibling  
        (By.XPATH, "//div[@id='appDetailConnect MLS']/preceding-sibling::div[contains(@class,'app-icon')]"),
        # 7. Real anchor fallbacks (for future-proofing)
        (By.XPATH, "//a[contains(@href,'bridge.connectmls.com')]"),
        (By.XPATH, "//a[contains(@href,'connectmls')]"),
        (By.CSS_SELECTOR, "a[href*='connectmls']"),
    ]
    for by, sel in selectors:
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                return el
        except NoSuchElementException:
            pass
    return None


def _dump_page(driver, path: str):
    try:
        with open(path, "w") as f:
            f.write(driver.page_source)
        print(f"  Debug HTML → {path}")
    except Exception:
        pass


# ── Transfer cookies to requests session ──────────────────────────────────────

def build_requests_session(selenium_cookies: list[dict]) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin":          "https://bridge.connectmls.com",
        "Referer":         "https://bridge.connectmls.com/",
    })
    for c in selenium_cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    return session


# ── MLS search ────────────────────────────────────────────────────────────────

SEARCH_PAYLOAD = {
    "searchclass": "RE",
    "searchtype": "LISTING",
    "boundaries": None,
    "layers": [],
    "report": "agent-rd-table",
    "fields": [
        {"ordinal": None, "id": "MLS_STATUS",
         "value": "ACTV,BOMK,AC,NEW,PCH,CS",
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "CITY", "value": "BURLINGAME",
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "COUNTY_OR_PARISH", "value": "",
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "SUBDIVISION_NAME", "value": "",
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "SRCHPRICE", "value": None, "option": None,
         "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "BUILDING_TYPE", "value": "",
         "option": "", "min": None, "max": None, "none": "", "all": ""},
        {"ordinal": None, "id": "BEDROOMS_TOTAL", "value": None, "option": None,
         "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "BATHROOMS_FULL", "value": None, "option": None,
         "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "SQFT", "value": None, "option": None,
         "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "DEFAULT_ADDRESS_SEARCH", "value": "",
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "LISTING_CONTRACT_DATE", "value": None,
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "OFF_MARKET_DATE", "value": None,
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "CLOSE_DATE", "value": None,
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "BOARDID", "value": "",
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "LISTING_AGREEMENT", "value": "",
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "SPECIAL_INFO", "value": "",
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "LISTING_ID", "value": None,
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "FEATURES_SEARCH", "value": None,
         "option": "", "min": None, "max": None, "none": "", "all": ""},
        {"ordinal": None, "id": "SOURCE_MLS",
         "value": "BR,CC,BE,ML,SF,BA,ME,CR,CL,CD",
         "option": "", "min": None, "max": None, "none": None, "all": None},
    ],
    "record": True,
    "context_data": {},
}


def run_search(session: requests.Session, output_file: str = "listings.json"):
    print(f"\n--- POSTing to search API ---")
    resp = session.post(
        SEARCH_URL,
        json=SEARCH_PAYLOAD,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=60,
    )
    print(f"Status: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        with open(output_file, "w") as f:
            json.dump(data, f, indent=2)
        print(f"✓ Results saved to {output_file}")
        if isinstance(data, dict):
            total = data.get("total") or data.get("count") or "?"
            print(f"  Total listings: {total}")
    else:
        print(f"✗ Error:\n{resp.text[:600]}")
    return resp


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if USERNAME == "your_username_here" or PASSWORD == "your_password_here":
        print("ERROR: Set credentials:")
        print("  export MLS_USERNAME='your_user'")
        print("  export MLS_PASSWORD='your_pass'")
        sys.exit(1)

    try:
        cookies = login_and_get_cookies(USERNAME, PASSWORD)
    except RuntimeError as e:
        print(f"\n✗ {e}")
        sys.exit(1)

    jsessionid = next((c["value"] for c in cookies if c["name"] == "JSESSIONID"), None)
    print(f"\n✓ JSESSIONID = {jsessionid}" if jsessionid
          else "\n⚠  No JSESSIONID — proceeding with other cookies")

    session = build_requests_session(cookies)
    run_search(session)


if __name__ == "__main__":
    main()
