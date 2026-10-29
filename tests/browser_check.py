"""Run against the local read-only preview: python tests/browser_check.py."""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/screenshots"
OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page(
        viewport={"width": 1440, "height": 1000}, device_scale_factor=1
    )
    page.emulate_media(reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto("http://127.0.0.1:8025")
    page.locator("#login-view").wait_for(state="visible")
    page.screenshot(path=str(OUT / "login.png"), full_page=True)
    page.locator("[name=email]").fill("alex@example.com")
    page.locator("#login-form [name=password]").fill("MorrowPreview2026!")
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
    page.locator('a[href="#users"]').click()
    page.get_by_role("heading", name="Your people", exact=True).wait_for()
    page.screenshot(path=str(OUT / "people.png"), full_page=True)
    page.locator("button").filter(has_text="Send as").nth(1).click()
    page.locator("#form-dialog").wait_for(state="visible")
    page.screenshot(path=str(OUT / "permissions.png"), full_page=True)
    page.locator('#form-dialog button[aria-label="Close dialog"]').click()
    page.locator('a[href="#domains"]').click()
    page.locator("button").filter(has_text="DNS setup").first.click()
    page.locator(".dns-record").first.wait_for()
    page.screenshot(path=str(OUT / "dns.png"), full_page=True)
    page.locator('#form-dialog button[aria-label="Close dialog"]').click()
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
    page.locator("#login-form [name=password]").fill("MorrowPreview2026!")
    page.locator("#login-form button").click()
    page.locator("#workspace").wait_for(state="visible")
    assert page.locator("#admin-nav-wrap").is_hidden()
    page.goto("http://127.0.0.1:8025/#users")
    page.wait_for_url("**/#inbox")
    assert not errors, errors
    browser.close()
    print(
        "Browser checks passed: login, reading, send-as selector, demo write protection, people, permissions, DNS, theme, mobile layout and navigation."
    )
