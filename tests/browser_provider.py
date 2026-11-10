"""Disposable real app + mocked issuer browser acceptance; no native services."""

import argparse, importlib, json, os, sys, tempfile, threading, time
from pathlib import Path
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument(
    "--engine", default=os.environ.get("KAGNODE_BROWSER_ENGINE", "chromium")
)
parser.add_argument("--actual-issuer", action="store_true")
args = parser.parse_args()
args.product = "mail"
source = ROOT
sys.path.insert(0, str(source))
workspace = tempfile.TemporaryDirectory(prefix="shared-login-browser-")
base = Path(workspace.name)
PASSWORD = "Only a fictional local password!"


class Issuer:
    def __init__(self, links):
        self.links = links
        self.pending = True
        self.offline = False
        self.device = {}
        self.calls = []

    def call(self, method, endpoint, data=None, token=None):
        self.calls.append(endpoint)
        if self.offline:
            raise self.error("Fictional issuer unavailable", 502)
        if endpoint == "device":
            self.device = data
            return {
                "device_code": "D" * 43,
                "user_code": "ABCDE-12345",
                "verification_uri_complete": "https://nebulacoder.com/account/apps/device?code=ABCDE12345",
            }
        if endpoint == "token":
            if data.get("grant_type") == "refresh_token":
                return {"access_token": "A" * 43, "refresh_token": "Q" * 43}
            if self.pending:
                return {"error": "authorization_pending"}
            now = time.time()
            return {
                "scope": "profile.read email.read",
                "access_token": "A" * 43,
                "refresh_token": "R" * 43,
                "authorization": {
                    "issuer": self.links.issuer,
                    "subject": "fixture-subject",
                    "client_id": self.links.client,
                    "installation": self.device["installation"],
                    "purpose": "sign_in",
                    "nonce": self.device["nonce"],
                    "auth_time": now - 20,
                    "approved_at": now,
                    "issued_at": now,
                    "expires_at": now + 120,
                },
            }
        if endpoint.startswith("profile?"):
            return {
                "issuer": self.links.issuer,
                "subject": "fixture-subject",
                "name": "Fictional KagNode profile",
                "photo": "",
                "email": "provider@example.test",
                "email_verified": True,
            }
        return {}


if args.product == "mail":
    from cryptography.fernet import Fernet
    from kagnode import create_app, db
    from kagnode.account_sync import AccountLinkError
    import kagnode.kagnode_core as sdk

    path = base / "mail.sqlite3"
    db.init(path)
    with db.connect(path) as connection:
        connection.execute("INSERT INTO domains VALUES('example.test')")
        connection.execute(
            "INSERT INTO users(email,name,password,admin) VALUES(?,?,?,0)",
            ("local@example.test", "Local reader", db.password_hash(PASSWORD)),
        )
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(path),
            "SECRET_KEY": "fictional-only",
            "CREDENTIAL_KEY": Fernet.generate_key().decode(),
            "SESSION_COOKIE_SECURE": False,
            "MAIL_HOSTNAME": "mail.example.test",
        }
    )

    class Mail:
        def list(self, *args):
            return {
                "items": [],
                "folder": "inbox",
                "page": 1,
                "pages": 1,
                "total": 0,
                "uidvalidity": "1",
            }

    app.extensions["mail"] = Mail()
else:
    from authentactor.server import create_app
    from authentactor.account_sync import AccountLinkError
    import authentactor.kagnode_core as sdk

    app = create_app(base / "vault", preview=True)
    app.config["TESTING"] = True
    c = app.test_client()
    assert (
        c.post(
            "/api/setup",
            json={"email": "local@example.test", "password": PASSWORD},
            headers={"X-Requested-With": "Authentactor"},
        ).status_code
        == 200
    )
# Exact checked SDK assets are used in the isolated process only; no Core manager state changes.
issuer = Issuer(app.extensions["kagnode_account"])
issuer.error = AccountLinkError
app.extensions["kagnode_account"].transport = issuer.call
actual = None
if args.actual_issuer:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    actual = json.loads(Path(os.environ["KAGNODE_TEST_ISSUER_STATE"]).read_text())

    def actual_transport(method, endpoint, data=None, token=None):
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(
            actual["origin"] + "/api/v1/account-sync/" + endpoint,
            method=method,
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        try:
            response = urlopen(request, timeout=8)
        except HTTPError as error:
            response = error
        with response:
            if response.status == 204:
                return {}
            return json.loads(response.read(262144))

    app.extensions["kagnode_account"].transport = actual_transport
server = make_server("127.0.0.1", 0, app, threaded=True)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
origin = f"http://127.0.0.1:{server.server_port}"
output = Path(os.environ.get("KAGNODE_SCREENSHOT_DIR", ROOT / ".build/provider-login"))
output.mkdir(parents=True, exist_ok=True)
report = []
try:
    with sync_playwright() as pw:
        engine = getattr(pw, args.engine)
        kwargs = {"headless": True}
        if os.environ.get("KAGNODE_BROWSER_EXECUTABLE"):
            kwargs["executable_path"] = os.environ["KAGNODE_BROWSER_EXECUTABLE"]
        browser = engine.launch(**kwargs)
        for width in [390] if actual else [390, 820, 1440]:
            context = browser.new_context(
                viewport={"width": width, "height": 900}, color_scheme="dark"
            )
            external = []
            errors = []

            def guard(route):
                url = route.request.url
                if url.startswith(origin) or (
                    actual and url.startswith(actual["origin"])
                ):
                    route.continue_()
                elif actual and url.startswith(actual["issuer"] + "/"):
                    route.fulfill(
                        status=302,
                        headers={
                            "Location": actual["origin"] + url[len(actual["issuer"]) :]
                        },
                    )
                else:
                    external.append(url)
                    route.abort()

            context.route("**/*", guard)
            page = context.new_page()
            page.bring_to_front()
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.goto(origin + ("/" if args.product == "mail" else "/unlock"))
            provider = page.locator("[data-core-login-provider]")
            expect(provider).to_be_enabled()
            expect(page.locator("[data-core-login]")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            box = page.locator(".kn-core-login-card").bounding_box()
            assert box and box["width"] >= min(width - 40, 400), box
            page.screenshot(path=str(output / f"{args.engine}-{width}-login.png"))
            # Local login is an independently usable path.
            email = (
                page.locator("#login-form [name=email]")
                if args.product == "mail"
                else page.locator("#auth-email")
            )
            password = (
                page.locator("#login-form [name=password]")
                if args.product == "mail"
                else page.locator("#master-password")
            )
            email.fill("local@example.test")
            password.fill(PASSWORD)
            page.locator(
                "#login-form button[type=submit]"
                if args.product == "mail"
                else ".auth-submit"
            ).click()
            expect(
                page.locator("#workspace" if args.product == "mail" else "#dashboard")
            ).to_be_visible()
            context.close()
            context = browser.new_context(
                viewport={"width": width, "height": 900}, color_scheme="dark"
            )

            def guard(route):
                url = route.request.url
                if url.startswith(origin) or (
                    actual and url.startswith(actual["origin"])
                ):
                    route.continue_()
                elif actual and url.startswith(actual["issuer"] + "/"):
                    route.fulfill(
                        status=302,
                        headers={
                            "Location": actual["origin"] + url[len(actual["issuer"]) :]
                        },
                    )
                else:
                    external.append(url)
                    route.abort()

            context.route("**/*", guard)
            page = context.new_page()
            page.bring_to_front()
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.goto(origin + ("/" if args.product == "mail" else "/unlock"))
            provider = page.locator("[data-core-login-provider]")
            if actual:
                provider.click()
                with page.expect_popup() as opened:
                    page.get_by_role("link", name="Open KagNode to approve").click(
                        timeout=15000
                    )
                approval = opened.value
                form = approval.locator('form[action="/login"]')
                form.locator("[name=email]").fill(actual["user"]["email"])
                form.locator("[name=password]").fill(actual["user"]["password"])
                form.locator("button[type=submit]").click()
                approval.locator("button[name=decision][value=approve]").click(
                    timeout=15000
                )
            else:
                issuer.pending = True
                issuer.offline = False
                provider = page.locator("[data-core-login-provider]")
                provider.click()
                expect(page.get_by_text("ABCDE-12345", exact=True)).to_be_visible(
                    timeout=15000
                )
                page.get_by_role("button", name="Cancel connection").click()
                expect(provider).to_be_enabled()
                expect(page.locator("[data-core-login-status]")).to_contain_text(
                    "cancelled"
                )
                issuer.offline = True
                provider.click()
                expect(page.locator("[data-core-login-status]")).to_contain_text(
                    "Unable to connect", timeout=10000
                )
                issuer.offline = False
                issuer.pending = False
                provider.click()
            expect(
                page.locator(
                    "#provider-mailbox-note"
                    if args.product == "mail"
                    else "#provider-vault-note"
                )
            ).to_be_visible(timeout=40000)
            assert not page.locator(
                "#workspace" if args.product == "mail" else "#dashboard"
            ).is_visible()
            page.screenshot(
                path=str(output / f"{args.engine}-{width}-continuation.png")
            )
            email = (
                page.locator("#login-form [name=email]")
                if args.product == "mail"
                else page.locator("#auth-email")
            )
            password = (
                page.locator("#login-form [name=password]")
                if args.product == "mail"
                else page.locator("#master-password")
            )
            if args.product == "mail" and not email.evaluate("(e)=>e.readOnly"):
                email.fill("local@example.test")
            password.fill(PASSWORD)
            if (
                args.product == "passwords"
                and page.locator("#confirm-wrap").is_visible()
            ):
                page.locator("#confirm-password").fill(PASSWORD)
            page.locator(
                "#login-form button[type=submit]"
                if args.product == "mail"
                else ".auth-submit"
            ).click()
            expect(
                page.locator("#workspace" if args.product == "mail" else "#dashboard")
            ).to_be_visible(timeout=40000)
            assert not external, external
            assert not errors, errors
            context.close()
            report.append(
                {
                    "engine": args.engine,
                    "width": width,
                    "local_login": True,
                    "cancel": not bool(actual),
                    "issuer_failure": not bool(actual),
                    "actual_issuer": bool(actual),
                    "explicit_completion": True,
                    "external_requests": 0,
                    "page_errors": 0,
                }
            )
        browser.close()
    (output / f"{args.engine}-report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report))
finally:
    server.shutdown()
    workspace.cleanup()
