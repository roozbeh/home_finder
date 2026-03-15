#!/usr/bin/env python3
"""
get_cookies.py
--------------
Logs in to ConnectMLS via Selenium and stores the session cookies
in MongoDB (auth_tokens collection).

Usage:
    export MLS_USERNAME='...'
    export MLS_PASSWORD='...'
    export MONGO_URI='mongodb://mlsuser:mlspassword@localhost:27017/mls?authSource=admin'
    python get_cookies.py
"""

import os
import sys
import time
from datetime import datetime, timezone

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from pymongo import MongoClient

# ── Config ────────────────────────────────────────────────────────────────────

LOGIN_URL    = "https://maxebrdi.clareityiam.net/idp/login"
USERNAME     = os.environ.get("MLS_USERNAME", "")
PASSWORD     = os.environ.get("MLS_PASSWORD", "")
HEADLESS     = os.environ.get("HEADLESS", "true").lower() != "false"
MONGO_URI    = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB     = os.environ.get("MONGO_DB", "mls")
WAIT_TIMEOUT = 60

# Override these to point at a specific Chrome/Chromium binary or chromedriver.
#
# Docker (set in Dockerfile.app):
#   CHROME_BIN=/usr/bin/chromium
#   CHROMEDRIVER_BIN=/usr/bin/chromedriver
#
# Mac (add to .env or export in shell — leave blank to auto-detect via PATH):
#   CHROME_BIN=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
#   CHROMEDRIVER_BIN=   # leave empty; webdriver-manager will download one
#
CHROME_BIN       = os.environ.get("CHROME_BIN", "")
CHROMEDRIVER_BIN = os.environ.get("CHROMEDRIVER_BIN", "")


# ── Browser setup ─────────────────────────────────────────────────────────────

def make_driver() -> webdriver.Chrome:
    from selenium.webdriver.chrome.service import Service

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

    # Set browser binary if explicitly provided or if the Debian Chromium path exists
    chrome_bin = CHROME_BIN or ("/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "")
    if chrome_bin:
        opts.binary_location = chrome_bin

    # Resolve chromedriver: explicit env var → Debian default → webdriver-manager
    chromedriver = CHROMEDRIVER_BIN or ("/usr/bin/chromedriver" if os.path.exists("/usr/bin/chromedriver") else "")
    if chromedriver:
        driver = webdriver.Chrome(service=Service(chromedriver), options=opts)
    else:
        try:
            driver = webdriver.Chrome(options=opts)
        except Exception:
            from webdriver_manager.chrome import ChromeDriverManager
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), options=opts
            )

    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


# ── Login flow ────────────────────────────────────────────────────────────────

def login_and_get_cookies(username: str, password: str) -> list[dict]:
    driver = make_driver()
    wait   = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        # Step 1: Load login page
        print("[1/4] Loading login page...")
        driver.get(LOGIN_URL)
        wait.until(EC.presence_of_element_located((By.ID, "username")))
        print(f"      Page title: {driver.title}")

        # Step 2: Wait for PingOne bot-detection token (MUST be present)
        print("[2/4] Waiting for PingOne bot-detection token...")
        ping_populated = False
        for i in range(30):
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
            print("      ⚠ Token never populated — proceeding anyway")

        # Step 3: Fill credentials and submit
        print("[3/4] Filling credentials and submitting...")
        user_field = driver.find_element(By.ID, "username")
        pass_field = driver.find_element(By.ID, "password")

        # Fire input/change events so the page's validation enables the button
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

        # Wait up to 5s for button to become enabled
        for _ in range(10):
            if not driver.execute_script(
                "return document.getElementById('loginbtn').disabled;"
            ):
                break
            time.sleep(0.5)

        # JS click bypasses overlapping DOM elements
        driver.execute_script("document.getElementById('loginbtn').click();")

        # Step 4: Check for login error
        time.sleep(2)
        try:
            err = driver.find_element(By.ID, "error").text.strip()
            if err:
                raise RuntimeError(f"Login error: '{err}'")
        except NoSuchElementException:
            pass

        # Step 5: Wait for redirect to portal
        print("      Waiting for redirect to portal...")
        deadline = time.time() + WAIT_TIMEOUT
        while time.time() < deadline:
            cur = driver.current_url
            if "bridge.connectmls.com" in cur:
                print("  ✓ Landed directly on ConnectMLS!")
                break
            if "clareity.net" in cur and "clareityiam.net" not in cur:
                print("  ✓ Reached Clareity portal")
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Timed out waiting for portal. Still at: {driver.current_url}")

        # Step 6: Click ConnectMLS tile if not already there
        if "bridge.connectmls.com" not in driver.current_url:
            print("      Looking for ConnectMLS tile...")
            mls_link = None
            for _ in range(20):
                mls_link = _find_connectmls_link(driver)
                if mls_link:
                    break
                time.sleep(1)

            if not mls_link:
                raise RuntimeError("Could not find ConnectMLS tile")

            handles_before = set(driver.window_handles)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", mls_link)
            time.sleep(0.4)
            try:
                mls_link.click()
            except Exception:
                driver.execute_script("arguments[0].click();", mls_link)

            deadline = time.time() + WAIT_TIMEOUT
            reached  = False
            while time.time() < deadline:
                if "bridge.connectmls.com" in driver.current_url:
                    reached = True
                    break
                new_handles = set(driver.window_handles) - handles_before
                if new_handles:
                    driver.switch_to.window(next(iter(new_handles)))
                    tab_dl = time.time() + 15
                    while time.time() < tab_dl:
                        if "bridge.connectmls.com" in driver.current_url:
                            reached = True
                            break
                        time.sleep(0.5)
                    if reached:
                        break
                    handles_before = set(driver.window_handles)
                time.sleep(1)

            if not reached:
                raise RuntimeError(f"Did not reach ConnectMLS: {driver.current_url}")

        print(f"  ✓ On ConnectMLS: {driver.current_url}")
        time.sleep(3)

        cookies = driver.get_cookies()
        print(f"  Collected {len(cookies)} cookies")
        return cookies

    finally:
        driver.quit()


def _find_connectmls_link(driver):
    selectors = [
        (By.XPATH, "//div[contains(@class,'app-border') and @data-title='ConnectMLS']"
                   "//div[contains(@class,'app-icon')]"),
        (By.XPATH, "//div[contains(@class,'app-border') and @data-title='ConnectMLS']"),
        (By.CSS_SELECTOR, "div.app-border[data-title='ConnectMLS']"),
        (By.XPATH, "//img[@alt='Connect MLS']"),
        (By.XPATH, "//a[contains(@href,'bridge.connectmls.com')]"),
    ]
    for by, sel in selectors:
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                return el
        except NoSuchElementException:
            pass
    return None


# ── MongoDB persistence ───────────────────────────────────────────────────────

def save_cookies_to_mongo(cookies: list[dict], error: str = None):
    client    = MongoClient(MONGO_URI)
    db        = client[MONGO_DB]
    now       = datetime.now(timezone.utc)
    if not error:
        result = db.auth_tokens.insert_one({"timestamp": now, "cookies": cookies})
        print(f"  ✓ Saved to MongoDB auth_tokens (id={result.inserted_id})")
    db.mls_runs.insert_one({
        "type":          "login",
        "timestamp":     now,
        "cookie_count":  len(cookies),
        "error":         error,
    })
    client.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not USERNAME or not PASSWORD:
        print("ERROR: Set MLS_USERNAME and MLS_PASSWORD env vars")
        sys.exit(1)

    print("=== get_cookies.py: MLS Login ===")

    try:
        cookies = login_and_get_cookies(USERNAME, PASSWORD)
    except RuntimeError as e:
        print(f"\n✗ {e}")
        save_cookies_to_mongo([], error=str(e))
        sys.exit(1)

    jsessionid = next((c["value"] for c in cookies if c["name"] == "JSESSIONID"), None)
    print(f"\n✓ JSESSIONID = {jsessionid}" if jsessionid else "\n⚠  No JSESSIONID")

    print("[4/4] Saving cookies to MongoDB...")
    save_cookies_to_mongo(cookies)
    print("Done.")


if __name__ == "__main__":
    main()
