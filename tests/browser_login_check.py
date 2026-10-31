"""Check sign-in entry points against a disposable scripts/demo.py preview."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("KAGNODE_PREVIEW_URL", "http://127.0.0.1:8025").rstrip("/")
OUT = Path(os.environ.get("KAGNODE_SCREENSHOT_DIR", ".lab/login-access"))
OUT.mkdir(parents=True, exist_ok=True)
CANONICAL = "https://mail.example.com/"

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True, args=["--no-sandbox"],
        executable_path=os.environ.get("KAGNODE_BROWSER_EXECUTABLE") or None,
    )
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.emulate_media(reduced_motion="reduce")
    failures = []
    submissions = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.on("request", lambda request: submissions.append(request.url)
            if request.method == "POST" and request.url.endswith("/api/login") else None)
    blocked = {"user": None, "csrf": "preview-csrf", "demo": False, "identities": [],
               "hostname": "mail.example.com", "login_url": CANONICAL, "login_allowed": False}
    page.route("**/api/session", lambda route: route.fulfill(
        content_type="application/json", body=json.dumps(blocked)))
    page.goto(BASE)
    expect(page.locator("#login-access")).to_be_visible()
    expect(page.locator("#login-form")).to_be_hidden()
    expect(page.locator('#login-form [name=password]')).to_be_disabled()
    expect(page.locator("#secure-login-link")).to_have_attribute("href", CANONICAL)
    for width in (320, 390, 1440):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate("({width: innerWidth, overflow: [...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>[e.tagName,e.id,e.className,e.getBoundingClientRect().width])})")
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#login-form").evaluate(
        "form => form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))")
    page.wait_for_timeout(100)
    assert submissions == [], "Blocked entry point submitted credentials"
    assert page.url.rstrip("/") == BASE
    page.screenshot(path=str(OUT / "secure-entry-phone.png"), full_page=True)
    page.unroute("**/api/session")

    # Serve the same local assets under a fictional HTTPS origin. No request is
    # sent to that hostname; this checks the proxy-misconfiguration screen.
    def canonical_route(route):
        path = urlsplit(route.request.url).path
        if path == "/api/session":
            route.fulfill(content_type="application/json", body=json.dumps(blocked))
        elif path == "/" or path.startswith("/static/"):
            response = route.fetch(url=BASE + path)
            route.fulfill(response=response)
        else:
            route.abort()

    page.route(CANONICAL + "**", canonical_route)
    page.goto(CANONICAL)
    expect(page.locator("#login-access-message")).to_contain_text("proxy’s HTTPS settings")
    expect(page.locator("#secure-login-link")).to_be_hidden()
    expect(page.locator("#login-form")).to_be_hidden()
    assert page.url == CANONICAL
    page.screenshot(path=str(OUT / "proxy-configuration-phone.png"), full_page=True)
    assert submissions == []
    page.unroute(CANONICAL + "**")

    # Actual fictional preview authentication remains available over local HTTP.
    page.goto(BASE)
    expect(page.locator("#login-form")).to_be_visible()
    expect(page.locator("#login-access")).to_be_hidden()
    page.locator('#login-form [name=email]').fill("alex@example.com")
    page.locator('#login-form [name=password]').fill("KagNodePreview2026!")
    page.locator('#login-form button[type=submit]').click()
    expect(page.locator("#workspace")).to_be_visible(timeout=30000)
    page.locator(".message-row").first.wait_for()
    assert len(submissions) == 1
    assert not failures, failures
    browser.close()
    print("Login browser checks passed: blocked HTTP entry, no credential submission or redirect, proxy configuration feedback, responsive layout, and normal demo login.")
