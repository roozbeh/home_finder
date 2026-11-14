#!/usr/bin/env python3
"""Capture an MLS session after a user completes the normal browser login.

This helper deliberately does not accept MLS credentials, fill fields, submit
forms, suppress browser automation flags, or work around MFA/bot checks.  The
operator completes the login in the visible browser and confirms in the
terminal only after reaching ConnectMLS.

Use only with an MLS/broker-approved session-storage workflow.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from pymongo import MongoClient
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

LOGIN_URL = "https://maxebrdi.clareityiam.net/idp/login"
MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB", "mls")
CHROME_BIN = os.environ.get("CHROME_BIN", "")
CHROMEDRIVER_BIN = os.environ.get("CHROMEDRIVER_BIN", "")


def make_visible_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--window-size=1440,1000")
    if CHROME_BIN:
        options.binary_location = CHROME_BIN
    if CHROMEDRIVER_BIN:
        return webdriver.Chrome(service=Service(CHROMEDRIVER_BIN), options=options)
    return webdriver.Chrome(options=options)


def save_cookies(cookies: list[dict]) -> None:
    client = MongoClient(MONGO_URI)
    try:
        result = client[MONGO_DB].auth_tokens.insert_one({
            "timestamp": datetime.now(timezone.utc),
            "cookies": cookies,
            "source": "manual_browser_login",
        })
        print(f"Saved {len(cookies)} authorized session cookies to {MONGO_DB}.auth_tokens (id={result.inserted_id}).")
    finally:
        client.close()


def main() -> None:
    if not MONGO_URI:
        sys.exit("MONGO_URI must be set; it is not read from source control.")
    driver = make_visible_driver()
    try:
        driver.get(LOGIN_URL)
        print("A browser window is open. Complete the normal MLS login and any MFA yourself.")
        input("After you have reached ConnectMLS, return here and press Enter to save this session: ")
        if "bridge.connectmls.com" not in driver.current_url:
            sys.exit("Browser is not at ConnectMLS; no session was saved.")
        cookies = driver.get_cookies()
        if not cookies:
            sys.exit("No browser cookies were found; no session was saved.")
        save_cookies(cookies)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
