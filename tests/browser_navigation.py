"""Actual Mail route/layout journeys using disposable accounts and fake mail only."""

import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from demo import demo_app

ENGINE = os.environ.get("KAGNODE_BROWSER_ENGINE", "chromium")
OUT = Path(os.environ.get("KAGNODE_SCREENSHOT_DIR", ROOT / ".build/navigation"))
OUT.mkdir(parents=True, exist_ok=True)
logging.getLogger("werkzeug").setLevel(logging.ERROR)
report = []


def mailbox(page, folder):
    expect(page.locator("html")).to_have_attribute("data-mail-layout", "mail")
    expect(page.locator("html")).to_have_attribute("data-core-shell", "inactive")
    expect(page.locator("#sidebar")).to_be_hidden()
    expect(page.locator("#mobile-menu")).to_be_hidden()
    expect(page.locator(".kn-core-drawer-scrim")).to_be_hidden()
    expect(page.locator('#mail-nav [aria-current="page"]')).to_have_attribute(
        "href", "#" + folder
    )
    expect(page.locator("#compose-button")).to_be_visible()
    layout = page.evaluate("""() => {
      const nav = document.querySelector('#mail-nav');
      const items = [...nav.querySelectorAll('a')].map(n => n.getBoundingClientRect());
      const host = document.querySelector('#mail-folder-host').getBoundingClientRect();
      const active = nav.querySelector('[aria-current]').getBoundingClientRect();
      return {
        inSidebar: !!nav.closest('#sidebar'), count: items.length,
        oneRow: items.every(r => Math.abs(r.y-items[0].y) < 1),
        activeVisible: active.left >= host.left-1 && active.right <= host.right+1,
        mainX: document.querySelector('#main').getBoundingClientRect().x,
        barHeight: document.querySelector('#mail-navigation').getBoundingClientRect().height,
        overflow: document.documentElement.scrollWidth > innerWidth
      };
    }""")
    assert layout["oneRow"], layout  # Previously inherited nav flex-direction: column.
    assert layout["count"] == 6 and not layout["inSidebar"], layout
    assert layout["activeVisible"] and layout["mainX"] == 0, layout
    assert layout["barHeight"] < 160 and not layout["overflow"], layout
    return layout


with tempfile.TemporaryDirectory(prefix="kagnode-mail-navigation-") as temporary:
    server = make_server(
        "127.0.0.1", 0, demo_app(Path(temporary) / "demo.sqlite3"), threaded=True
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as p:
            options = {"headless": True}
            if ENGINE == "chromium":
                options["args"] = ["--no-sandbox"]
            if os.environ.get("KAGNODE_BROWSER_EXECUTABLE"):
                options["executable_path"] = os.environ["KAGNODE_BROWSER_EXECUTABLE"]
            browser = getattr(p, ENGINE).launch(**options)
            for width, height in [(1440, 1000), (768, 1024), (390, 844), (320, 700)]:
                for theme in ["light", "dark"]:
                    for admin in [True, False]:
                        role = "admin" if admin else "member"
                        case = f"{ENGINE}-{width}-{theme}-{role}"
                        context = browser.new_context(
                            viewport={"width": width, "height": height},
                            color_scheme=theme,
                            reduced_motion="reduce",
                        )
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(url + "/#inbox")
                        page.locator('#login-form [name="email"]').fill(
                            "alex@example.com" if admin else "sam@example.com"
                        )
                        page.locator('#login-form [name="password"]').fill(
                            "KagNodePreview2026!"
                        )
                        page.locator('#login-form [type="submit"]').click()
                        page.locator(".message-row").first.wait_for()
                        initial = mailbox(page, "inbox")
                        # Every folder stays outside the workspace sidebar, including deep links.
                        for folder in [
                            "sent",
                            "drafts",
                            "archive",
                            "junk",
                            "trash",
                            "inbox",
                        ]:
                            page.locator(f'#mail-nav a[href="#{folder}"]').click()
                            mailbox(page, folder)
                        page.screenshot(path=str(OUT / (case + "-inbox.png")))
                        page.locator(".message-row").first.click()
                        expect(page.locator(".message-body")).to_be_visible()
                        if width < 900:
                            page.locator(".reader-back").click()
                        page.locator("#compose-button").click()
                        expect(page.locator("#compose-dialog")).to_be_visible()
                        expect(page.locator("#sidebar")).to_be_hidden()
                        page.locator('[data-close="compose-dialog"]').click()
                        page.locator("[data-core-account-trigger]").click()
                        page.locator(
                            '[data-core-account-menu] a[href="#account"]'
                        ).click()
                        expect(page.locator("html")).to_have_attribute(
                            "data-mail-layout", "settings"
                        )
                        expect(page.locator("#mail-navigation")).to_be_hidden()
                        expect(page.locator("#account-view")).to_be_visible()
                        if width < 1024:
                            page.locator("#mobile-menu").click()
                            expect(page.locator("html")).to_have_attribute(
                                "data-core-drawer", "true"
                            )
                        expect(page.locator("#sidebar")).to_be_visible()
                        assert page.locator("#sidebar #mail-nav").count() == 0
                        if admin:
                            expect(page.locator("#admin-nav-wrap")).to_be_visible()
                        else:
                            expect(page.locator("#admin-nav-wrap")).to_be_hidden()
                        page.wait_for_function("""() => {
                          const link = document.querySelector('#account-nav a[href="#inbox"]');
                          const r = link.getBoundingClientRect();
                          return r.x >= 0 && r.bottom <= innerHeight &&
                            link.contains(document.elementFromPoint(r.x+r.width/2, r.y+r.height/2));
                        }""")
                        # Wait for the drawer's composited frame, not merely its layout box.
                        page.evaluate(
                            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                        )
                        page.screenshot(path=str(OUT / (case + "-settings.png")))
                        if admin:
                            page.locator('#admin-nav a[href="#overview"]').click()
                            expect(page.locator("#admin-view")).to_be_visible()
                            if width < 1024:
                                expect(page.locator("html")).to_have_attribute(
                                    "data-core-drawer", "false"
                                )
                                page.locator("#mobile-menu").click()
                        page.locator('#account-nav a[href="#inbox"]').click()
                        page.locator(".message-row").first.wait_for()
                        mailbox(page, "inbox")
                        # Quick navigation/keyboard still reach offscreen folders; reload preserves location.
                        page.keyboard.press("Control+k")
                        page.locator("#navigation-query").fill("Trash")
                        page.keyboard.press("Enter")
                        mailbox(page, "trash")
                        page.reload()
                        mailbox(page, "trash")
                        if not admin:
                            page.goto(url + "/#users")
                            page.wait_for_url("**/#inbox")
                            mailbox(page, "inbox")
                        assert not errors, errors
                        report.append({"case": case, "layout": initial, "passed": True})
                        context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
(OUT / f"{ENGINE}-report.json").write_text(json.dumps(report, indent=2) + "\n")
print(
    f"Mail navigation: {len(report)} {ENGINE} desktop/tablet/mobile, theme and permission journeys passed."
)
