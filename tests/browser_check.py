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
    browser = p.chromium.launch(
        headless=True, args=["--no-sandbox"],
        executable_path=os.environ.get("KAGNODE_BROWSER_EXECUTABLE") or None,
    )
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
    # A mailbox outage has an explicit recovery action without retrying sends.
    page.route("**/api/messages?*", lambda route: route.fulfill(
        status=502, content_type="application/json",
        body=json.dumps({"error": "Could not connect to your mailbox."}),
    ), times=1)
    page.locator("#refresh-mail").click()
    expect(page.locator("#message-list")).to_contain_text("Could not connect to your mailbox.")
    expect(page.locator("#next-page")).to_be_disabled()
    page.get_by_role("button", name="Try again", exact=True).click()
    page.locator(".message-row").first.wait_for()
    expect(page.locator('#mail-nav a[href="#inbox"]')).to_have_attribute("aria-current", "page")
    expect(page.locator('.sidebar .website-link')).to_have_attribute("href", "https://nebulacoder.com")
    expect(page.locator('.sidebar .website-link')).to_have_text("KagNode Website")
    # Quick navigation follows the same account destinations and marks the current one.
    page.keyboard.press("Control+k")
    expect(page.locator('#navigation-results a[href="#inbox"]')).to_have_attribute("aria-current", "page")
    page.locator("#navigation-query").fill("Account")
    page.keyboard.press("Enter")
    expect(page.locator("#profile-button")).to_have_attribute("aria-current", "page")
    expect(page.locator("#location-page")).to_have_text("Account")
    page.locator('#mail-nav a[href="#inbox"]').click()
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
    # Tokens use mock mutations so the preview remains read-only.
    token_users = page.request.get(URL + "/api/admin").json()["users"]
    token_owner = next(u for u in token_users if u["email"] == "alex@example.com")
    token_records = [{
        "id": "preview-token", "user_id": token_owner["id"],
        "email": token_owner["email"], "label": "Notifications",
        "scopes": ["read", "send"], "senders": ["hello@example.com"],
        "expires": None, "created": 1793318400, "last_used": 1793404800,
        "revoked": None,
    }]
    token_calls = {"creates": [], "deletes": [], "confirmed": False, "attempts": 0}
    fixture_secret = "kgn_preview_browser_only_not_a_real_token"

    def token_api(route):
        method = route.request.method
        if method == "GET":
            result = {"tokens": token_records, "users": token_users}
        elif method == "POST":
            token_calls["attempts"] += 1
            if not token_calls["confirmed"]:
                route.fulfill(status=403, content_type="application/json",
                    body=json.dumps({"error": "Confirm your password.", "code": "reauth_required"}))
                return
            data = route.request.post_data_json
            token_calls["creates"].append(data)
            owner = next(u for u in token_users if u["id"] == data["user_id"])
            record = {
                "id": "created-" + str(len(token_calls["creates"])),
                "user_id": owner["id"], "email": owner["email"],
                "label": data["label"], "scopes": data["scopes"],
                "senders": data["senders"], "expires": None,
                "created": 1793404800, "last_used": None, "revoked": None,
            }
            token_records.append(record)
            result = {"token": record, "secret": fixture_secret}
        else:
            token_id = route.request.url.rsplit("/", 1)[-1]
            token_calls["deletes"].append(token_id)
            next(t for t in token_records if t["id"] == token_id)["revoked"] = 1793404800
            result = {"ok": True}
        route.fulfill(content_type="application/json", body=json.dumps(result))

    def token_reauth(route):
        token_calls["confirmed"] = True
        route.fulfill(content_type="application/json", body=json.dumps({"ok": True}))

    page.route("**/api/account/reauth", token_reauth)
    page.route("**/api/admin/tokens", token_api)
    page.route("**/api/admin/tokens/*", token_api)
    page.locator('a[href="#tokens"]').click()
    page.get_by_role("heading", name="Notifications", exact=True).wait_for()
    page.screenshot(path=str(OUT / "tokens.png"), full_page=True)
    page.get_by_role("button", name="Create token", exact=True).click()
    page.locator("#token-form").wait_for()
    assert page.locator('#token-form [name=scopes]:checked').count() == 0
    expect(page.locator('#token-form [name=expiry]')).to_have_value("90")
    page.locator('#token-form [name=label]').fill("App server")
    page.locator('#token-form button[type=submit]').click()
    page.locator('#token-form .form-error').filter(has_text="Select at least one permission").wait_for()
    assert token_calls["creates"] == []
    page.locator('#token-form [name=scopes][value=send]').check()
    page.locator('#token-form button[type=submit]').click()
    page.locator('#token-form .form-error').filter(has_text="Select at least one sender").wait_for()
    page.locator('#token-form [name=senders][value="hello@example.com"]').check()
    page.locator('#token-form [name=scopes][value=read]').check()
    page.locator('#token-form [name=expiry]').select_option("custom")
    expect(page.locator('#token-form [name=expires_days]')).to_be_visible()
    page.locator('#token-form [name=expires_days]').fill("30")
    page.locator('#token-form button[type=submit]').click()
    page.locator('#reauth-dialog').wait_for(state="visible")
    assert token_calls["creates"] == []
    page.get_by_role("button", name="Cancel password confirmation").click()
    page.locator('#token-form .form-error').filter(has_text="Change canceled").wait_for()
    assert token_calls["attempts"] == 1 and token_calls["creates"] == []
    page.locator('#token-form button[type=submit]').click()
    page.locator('#reauth-form [name=password]').fill("KagNodePreview2026!")
    page.locator('#reauth-form button[type=submit]').click()
    page.get_by_role("heading", name="Token created", exact=True).wait_for()
    assert token_calls["attempts"] == 3
    page.unroute("**/api/account/reauth", token_reauth)
    assert token_calls["creates"][0]["expires_days"] == 30
    assert token_calls["creates"][0]["senders"] == ["hello@example.com"]
    assert set(token_calls["creates"][0]["scopes"]) == {"read", "send"}
    expect(page.locator('#token-secret')).to_have_value(fixture_secret)
    assert fixture_secret not in page.evaluate("JSON.stringify({...localStorage,...sessionStorage})")
    page.evaluate("() => { navigator.clipboard.writeText = async () => { throw new Error('Permission denied'); }; }")
    page.get_by_role("button", name="Copy token", exact=True).click()
    page.locator('#toast').filter(has_text="Copy the selected token.").wait_for()
    assert page.locator('#token-secret').evaluate("e => e.selectionStart === 0 && e.selectionEnd === e.value.length")
    page.get_by_role("button", name="Done", exact=True).click()
    page.locator('#form-dialog').wait_for(state="hidden")
    expect(page.locator('#token-secret')).to_have_count(0)
    page.locator('[data-token-id="preview-token"]').get_by_role("button", name="Replace", exact=True).click()
    expect(page.locator('#token-form [name=scopes][value=send]')).to_be_checked()
    expect(page.locator('#token-form [name=senders][value="hello@example.com"]')).to_be_checked()
    page.locator('#token-form [name=expiry]').select_option("never")
    page.locator('#token-form button[type=submit]').click()
    page.get_by_role("heading", name="Token created", exact=True).wait_for()
    assert token_calls["creates"][1]["expires_days"] is None
    assert token_calls["deletes"] == [], "Replacement must not revoke before confirmation"
    page.get_by_role("button", name="Revoke old token", exact=True).click()
    page.get_by_text("Old token revoked.", exact=True).wait_for()
    assert token_calls["deletes"] == ["preview-token"]
    expect(page.locator('#token-secret')).to_have_value(fixture_secret)
    page.get_by_role("button", name="Done", exact=True).click()
    page.locator('[data-token-id="preview-token"]').get_by_text("Revoked", exact=True).wait_for()
    page.locator('[data-token-id="created-1"]').get_by_role("button", name="Revoke", exact=True).click()
    page.get_by_role("button", name="Revoke token", exact=True).click()
    page.locator('[data-token-id="created-1"]').get_by_text("Revoked", exact=True).wait_for()
    page.locator('#toast').wait_for(state='hidden')
    page.locator('#theme-toggle').click()
    page.screenshot(path=str(OUT / "tokens-dark.png"), full_page=True)
    assert page.locator('.token-card').first.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(0, 0, 0)"
    page.locator('#theme-toggle').click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(OUT / "tokens-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Token mobile overflow"
    page.get_by_role("button", name="Create token", exact=True).click()
    page.locator('#token-form [name=scopes][value=send]').check()
    assert page.locator('#form-dialog').evaluate("e => e.scrollWidth <= e.clientWidth"), "Token form overflow"
    page.get_by_role("button", name="Close dialog", exact=True).click()
    page.set_viewport_size({"width": 1440, "height": 1000})
    hostile_label = '<img src=x onerror="window.__token_xss=1">'
    hostile_sender = '<svg onload="window.__token_xss=1">'
    token_records.append({**token_records[0], "id": "hostile", "label": hostile_label,
                          "senders": [hostile_sender]})
    page.reload()
    hostile_card = page.locator('[data-token-id="hostile"]')
    hostile_card.get_by_role("heading", name=hostile_label, exact=True).wait_for()
    assert hostile_card.locator("img,svg").count() == 0
    assert hostile_sender in hostile_card.inner_text()
    assert page.evaluate("window.__token_xss === undefined")
    hostile_card.get_by_role("button", name="Replace", exact=True).click()
    expect(page.locator('#token-form [name=label]')).to_have_value(hostile_label)
    page.get_by_role("button", name="Close dialog", exact=True).click()
    token_records.pop()
    page.get_by_role("button", name="Create token", exact=True).click()
    page.locator('#token-form [name=label]').fill("Navigation cleanup")
    page.locator('#token-form [name=scopes][value=read]').check()
    page.locator('#token-form button[type=submit]').click()
    page.get_by_role("heading", name="Token created", exact=True).wait_for()
    page.evaluate("location.hash = 'inbox'")
    page.locator('#form-dialog').wait_for(state="hidden")
    expect(page.locator('#token-secret')).to_have_count(0)
    page.unroute("**/api/admin/tokens", token_api)
    page.unroute("**/api/admin/tokens/*", token_api)
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
    # Navigation remains reachable on short phones and tablets; account/site actions
    # stay visible while the folder list scrolls independently.
    # Mobile composition is reachable directly; the drawer stays closed on return.
    page.evaluate("location.hash='inbox'")
    page.locator(".message-row").first.wait_for()
    page.locator("#compose-mobile").click()
    expect(page.locator("#compose-dialog")).to_be_visible()
    expect(page.locator("#mobile-menu")).to_have_attribute("aria-expanded", "false")
    page.locator("[data-close=compose-dialog]").click()
    page.locator("#mobile-menu").click()
    page.locator("#compose-button").click()
    expect(page.locator("#compose-dialog")).to_be_visible()
    expect(page.locator("#mobile-menu")).to_have_attribute("aria-expanded", "false")
    page.locator("[data-close=compose-dialog]").click()
    for width, height in [(320, 568), (390, 844), (768, 1024), (900, 700)]:
        page.set_viewport_size({"width": width, "height": height})
        page.locator("#mobile-menu").click()
        expect(page.locator("#mobile-menu")).to_have_attribute("aria-expanded", "true")
        page.locator('#admin-nav a[href="#tokens"]').scroll_into_view_if_needed()
        assert page.locator('.sidebar .website-link').evaluate(
            "el => {const r=el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight;}"
        ), f"Sidebar bottom outside viewport at {width}px"
        page.locator("#profile-button").click()
        expect(page.locator("#location-page")).to_have_text("Account")
        expect(page.locator("#location-page")).to_be_visible()
        expect(page.locator("#mobile-menu")).to_have_attribute("aria-expanded", "false")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow at {width}px"
        page.locator("#mobile-menu").click()
        page.keyboard.press("Escape")
        expect(page.locator("#mobile-menu")).to_be_focused()
    page.screenshot(path=str(OUT / "tablet-navigation.png"), full_page=True)
    page.locator("#logout-button").click()
    page.locator("#login-view").wait_for(state="visible")
    page.locator("[name=email]").fill("sam@example.com")
    page.locator("#login-form [name=password]").fill("KagNodePreview2026!")
    page.locator("#login-form button").click()
    page.locator("#workspace").wait_for(state="visible")
    assert page.locator("#admin-nav-wrap").is_hidden()
    page.goto(URL + "/#users")
    page.wait_for_url("**/#inbox")
    page.goto(URL + "/#tokens")
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
        "Browser checks passed: branding, login, reading, send-as selector, demo write protection, people, permissions, admin password reconfirmation (cancel/failure/success), DNS, API token permissions and replacement/revocation, secret cleanup, theme, mobile layout and navigation."
    )
