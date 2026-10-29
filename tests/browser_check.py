"""Run against the local read-only preview: python tests/browser_check.py."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("KAGNODE_SCREENSHOT_DIR", ROOT / "docs/screenshots"))
URL = os.environ.get("KAGNODE_PREVIEW_URL", "http://127.0.0.1:8025").rstrip("/")
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page(
        viewport={"width": 1440, "height": 1000}, device_scale_factor=1
    )
    page.emulate_media(reduced_motion="reduce", color_scheme="light")
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(URL)
    page.locator("#login-view").wait_for(state="visible")
    assert "KagNode Mail" in page.title()
    assert page.locator("#login-view .brand-mark").evaluate(
        "img => img.complete && img.naturalWidth > 0"
    )
    page.screenshot(path=str(OUT / "login.png"), full_page=True)
    page.locator("[name=email]").fill("alex@example.com")
    page.locator("#login-form [name=password]").fill("KagNodePreview2026!")
    page.route(
        "**/api/login",
        lambda route: route.fulfill(
            status=429, content_type="text/html", body="<h1>Too many requests</h1>"
        ),
        times=1,
    )
    page.locator("#login-form button").click()
    page.locator("#login-error").filter(
        has_text="Too many requests. Please wait"
    ).wait_for()
    page.locator("#login-form button").click()
    page.locator(".message-row").first.wait_for()
    page.locator(".message-row").first.click()
    page.locator(".message-body").wait_for()
    page.screenshot(path=str(OUT / "inbox.png"), full_page=True)
    page.locator("#compose-button").click()
    page.locator("#compose-from").select_option("hello@example.com")
    page.locator("#compose-form [name=to]").fill("friend@example.org")
    page.locator("#compose-form [name=subject]").fill("A little hello")
    page.locator("#compose-form [name=body]").fill(
        "Hello from my own corner of the internet."
    )
    page.screenshot(path=str(OUT / "compose.png"), full_page=True)
    page.locator("#compose-form button[type=submit]").click()
    page.locator("#compose-error").filter(has_text="read-only preview").wait_for()
    page.locator("[data-close=compose-dialog]").click()
    page.locator('a[href="#overview"]').click()
    page.get_by_role("heading", name="Activity", exact=True).wait_for()
    page.screenshot(path=str(OUT / "overview.png"), full_page=True)
    page.locator('a[href="#users"]').click()
    page.get_by_role("heading", name="Accounts", exact=True).wait_for()
    page.screenshot(path=str(OUT / "people.png"), full_page=True)
    page.locator("button").filter(has_text="Send as").nth(1).click()
    page.locator("#form-dialog").wait_for(state="visible")
    page.screenshot(path=str(OUT / "permissions.png"), full_page=True)
    # A stale administrative session must never replay a change before confirmation.
    attempts = {"writes": 0, "passwords": 0, "confirmed": False}

    def write_challenge(route):
        attempts["writes"] += 1
        route.fulfill(
            status=200 if attempts["confirmed"] else 403,
            content_type="application/json",
            body=json.dumps(
                {"ok": True}
                if attempts["confirmed"]
                else {"error": "Confirm your password.", "code": "reauth_required"}
            ),
        )

    def password_challenge(route):
        attempts["passwords"] += 1
        attempts["confirmed"] = attempts["passwords"] > 1
        route.fulfill(
            status=200 if attempts["confirmed"] else 403,
            content_type="application/json",
            body=json.dumps(
                {"ok": True}
                if attempts["confirmed"]
                else {"error": "Password is incorrect."}
            ),
        )

    page.route("**/api/admin/users/*/identities", write_challenge)
    page.route("**/api/account/reauth", password_challenge)
    page.locator('#form-dialog button[type="submit"]').click()
    page.locator("#reauth-dialog").wait_for(state="visible")
    assert page.locator("#reauth-form [name=password]").evaluate(
        "el => el === document.activeElement"
    )
    page.get_by_role("button", name="Cancel password confirmation").click()
    page.locator("#form-dialog .form-error").filter(
        has_text="Change canceled"
    ).wait_for()
    assert attempts["writes"] == 1
    page.locator('#form-dialog button[type="submit"]').click()
    page.locator("#reauth-form [name=password]").fill("IncorrectPassword")
    page.locator('#reauth-form button[type="submit"]').click()
    page.locator("#reauth-error").filter(has_text="Password is incorrect").wait_for()
    assert attempts["writes"] == 2
    page.locator("#reauth-form [name=password]").fill("KagNodePreview2026!")
    page.locator('#reauth-form button[type="submit"]').click()
    page.locator("#form-dialog").wait_for(state="hidden")
    assert attempts["writes"] == 3
    assert page.locator("#reauth-form [name=password]").input_value() == ""
    page.unroute("**/api/admin/users/*/identities", write_challenge)
    page.unroute("**/api/account/reauth", password_challenge)
    page.locator("#toast").wait_for(state="hidden")
    page.locator('a[href="#domains"]').click()
    page.get_by_role("heading", name="Provider setup", exact=True).wait_for()
    page.screenshot(path=str(OUT / "domains.png"), full_page=True)
    page.locator("button").filter(has_text="DNS setup").first.click()
    page.locator(".dns-record").first.wait_for()
    page.screenshot(path=str(OUT / "dns.png"), full_page=True)
    page.locator('#form-dialog button[aria-label="Close dialog"]').click()
    page.locator("#profile-button").click()
    page.get_by_role("heading", name="Mail clients", exact=True).wait_for()
    page.screenshot(path=str(OUT / "account.png"), full_page=True)
    page.locator('#mail-nav a[href="#inbox"]').click()
    page.locator(".message-row").first.click()
    page.locator(".message-body").wait_for()
    page.locator("#theme-toggle").click()
    page.screenshot(path=str(OUT / "dark.png"), full_page=True)
    page.locator("#theme-toggle").click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(250)
    page.screenshot(path=str(OUT / "mobile.png"), full_page=False)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Mobile overflow"
    )
    page.locator(".reader-back").click()
    page.locator(".message-row").first.wait_for(state="visible")
    page.locator("#mobile-menu").click()
    page.locator("#sidebar.open").wait_for(state="visible")
    page.locator('a[href="#domains"]').click()
    page.locator("#sidebar:not(.open)").wait_for(state="attached")
    page.locator("#logout-button").click()
    page.locator("#login-view").wait_for(state="visible")
    page.locator("[name=email]").fill("sam@example.com")
    page.locator("#login-form [name=password]").fill("KagNodePreview2026!")
    page.locator("#login-form button").click()
    page.locator("#workspace").wait_for(state="visible")
    assert page.locator("#admin-nav-wrap").is_hidden()
    page.goto(URL + "/#users")
    page.wait_for_url("**/#inbox")
    # Without an explicit preference the interface follows the operating system.
    page.evaluate("localStorage.removeItem('kagnode-theme')")
    page.emulate_media(color_scheme="dark")
    page.reload()
    page.locator(".message-row").first.wait_for()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    page.emulate_media(color_scheme="light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    assert not errors, errors
    browser.close()
    print(
        "Browser checks passed: branding, login, reading, send-as selector, demo write protection, people, permissions, admin password reconfirmation (cancel/failure/success), DNS, theme, mobile layout and navigation."
    )
