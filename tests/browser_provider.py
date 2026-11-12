"""Disposable real app + mocked issuer browser acceptance; no native services."""

import argparse, importlib, json, os, sys, tempfile, threading, time
from pathlib import Path
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

# Real local password hashing and durable SQLite operations run under shared CI load.
expect.set_options(timeout=20000)


def navigation_icons(page, selector):
    """Real destinations retain one visible icon after Core normalization."""
    page.wait_for_function(
        "selector => { const links=[...document.querySelectorAll(selector)]; return links.length>0 && links.every(a=>a.querySelectorAll('svg').length===1); }",
        arg=selector,
        timeout=20000,
    )
    assert page.locator(selector).count() > 0
    assert page.locator(selector + " svg").count() == page.locator(selector).count()


def shared_login_style(page):
    """Assert the real app uses the canonical frame and resolved action geometry."""
    return page.evaluate(
        """expected => {
      const root=document.querySelector('[data-core-login]');
      const card=root.querySelector('.kn-core-login-card');
      const input=root.querySelector('input:not([type=hidden])');
      const submit=root.querySelector('button[data-core-login-submit]');
      const meta=JSON.parse(document.querySelector('meta[name=kagnode-core]').content);
      if(meta.version!==expected) throw Error('Expected reviewed Core5');
      if(!root.querySelector('.kn-core-login-mark')) throw Error('Missing canonical login mark');
      if(document.querySelector('[data-theme-toggle],.theme-toggle,#theme-toggle,[data-kn-theme]')) throw Error('Standalone theme control remains');
      const css=el=>{const c=getComputedStyle(el);return Object.fromEntries(['fontFamily','fontSize','fontWeight','lineHeight','borderRadius','backgroundColor','color','paddingTop','paddingRight','marginTop','marginBottom','minHeight'].map(k=>[k,c[k]]));};
      const style={card:css(card),input:css(input),submit:css(submit),product:root.dataset.coreLoginProduct};
      for (const part of ['card','input','submit']) {
        if(style[part].marginTop!=='0px'||style[part].marginBottom!=='0px') throw Error('Host margin leaked into Core '+part);
      }
      if(style.input.fontWeight!=='400'||style.input.borderRadius!=='8px'||style.input.minHeight!=='44px') throw Error('Host input styling overrides Core');
      if(style.submit.fontWeight!=='600'||style.submit.borderRadius!=='8px'||style.submit.minHeight!=='44px') throw Error('Host primary action styling overrides Core');
      return style;
    }""",
        json.loads((ROOT / "core.lock.json").read_text())["baseline"],
    )


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
        if endpoint == "browser/start":
            self.device = data
            return {
                "request_id": "Q" * 43,
                "authorization_uri": self.links.issuer
                + "/oauth/authorize?request="
                + "Q" * 43,
                "expires_in": 600,
            }
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
        kwargs = {"headless": True}
        if os.environ.get("KAGNODE_BROWSER_EXECUTABLE"):
            kwargs["executable_path"] = os.environ["KAGNODE_BROWSER_EXECUTABLE"]
        browser = getattr(pw, args.engine).launch(**kwargs)
        for width in [390] if actual else [390, 820, 1440]:
            context = browser.new_context(
                viewport={"width": width, "height": 900}, color_scheme="dark"
            )
            errors, external = [], []

            def guard(route):
                url = route.request.url
                if url.startswith(origin + "/"):
                    route.continue_()
                    return
                if actual and os.environ.get("KAGNODE_TEST_DIRECT_ISSUER"):
                    if url.startswith(actual["origin"] + "/"):
                        route.continue_()
                        return
                    if url.startswith(actual["issuer"] + "/"):
                        import html

                        route.fulfill(
                            content_type="text/html",
                            body='<meta http-equiv="refresh" content="0;url='
                            + html.escape(
                                actual["origin"] + url[len(actual["issuer"]) :],
                                quote=True,
                            )
                            + '">',
                        )
                        return
                if actual and url.startswith(actual["issuer"] + "/"):
                    # Preserve browser fetch metadata and cookies through the
                    # isolated loopback transport (APIRequestContext can rewrite them).
                    import http.client
                    from types import SimpleNamespace
                    from urllib.parse import urlsplit

                    endpoint = urlsplit(actual["origin"])
                    connection = http.client.HTTPConnection(
                        endpoint.hostname, endpoint.port, timeout=20
                    )
                    headers = route.request.all_headers()
                    headers.pop("accept-encoding", None)
                    if route.request.is_navigation_request():
                        headers["sec-fetch-dest"] = "document"
                    connection.request(
                        route.request.method,
                        url[len(actual["issuer"]) :],
                        body=route.request.post_data_buffer,
                        headers=headers,
                    )
                    raw_response = connection.getresponse()
                    returned = dict(raw_response.getheaders())
                    returned = {k.lower(): v for k, v in returned.items()}
                    cookies = raw_response.headers.get_all("Set-Cookie", [])
                    if cookies:
                        returned["set-cookie"] = "\n".join(cookies)
                    if (
                        os.environ.get("KAGNODE_TRACE_ISSUER")
                        and route.request.is_navigation_request()
                    ):
                        print(
                            "COOKIE_TRACE",
                            json.dumps(
                                {
                                    "request_header_names": list(headers),
                                    "set_cookie": [
                                        {
                                            "name": item.split("=", 1)[0],
                                            "attributes": item.split(";")[1:],
                                        }
                                        for item in cookies
                                    ],
                                }
                            ),
                            flush=True,
                        )
                    raw = raw_response.read(4194305)
                    assert len(raw) <= 4194304
                    response = SimpleNamespace(
                        status=raw_response.status, headers=returned, body=raw
                    )
                    connection.close()
                    if (
                        os.environ.get("KAGNODE_TRACE_ISSUER")
                        and route.request.is_navigation_request()
                    ):
                        from urllib.parse import urlsplit, parse_qs

                        safe = lambda u: {
                            "origin": urlsplit(u).netloc,
                            "path": urlsplit(u).path,
                            "query_keys": sorted(parse_qs(urlsplit(u).query)),
                            "next_path": urlsplit(
                                parse_qs(urlsplit(u).query).get("next", [""])[0]
                            ).path,
                        }
                        posted = parse_qs(route.request.post_data or "")
                        print(
                            "ISSUER_TRACE",
                            json.dumps(
                                {
                                    "request": safe(url),
                                    "method": route.request.method,
                                    "status": response.status,
                                    "location": safe(
                                        response.headers.get("location", "")
                                    ),
                                    "posted_keys": sorted(posted),
                                    "posted_next": urlsplit(
                                        posted.get("next", [""])[0]
                                    ).path,
                                    "cookie_present": "cookie"
                                    in route.request.all_headers(),
                                }
                            ),
                            flush=True,
                        )
                    if response.status in (301, 302, 303):
                        # Playwright routes only the first HTTP redirect hop. A test-only
                        # explicit navigation keeps all subsequent issuer requests local.
                        import html
                        from urllib.parse import urljoin

                        target = urljoin(url, response.headers["location"])
                        if not target.startswith(actual["issuer"] + "/"):
                            raise AssertionError("Unexpected issuer redirect origin")
                        headers = {
                            k: v
                            for k, v in response.headers.items()
                            if k
                            not in {"location", "content-length", "content-encoding"}
                        }
                        headers["content-type"] = "text/html; charset=utf-8"
                        route.fulfill(
                            status=200,
                            headers=headers,
                            body='<meta http-equiv="refresh" content="0;url='
                            + html.escape(target, quote=True)
                            + '">',
                        )
                    else:
                        route.fulfill(
                            status=response.status,
                            headers=response.headers,
                            body=response.body,
                        )
                    return
                if not actual and url.startswith(
                    issuer.links.issuer + "/oauth/authorize?"
                ):
                    import html

                    callback = html.escape(
                        origin + "/auth/kagnode/callback", quote=True
                    )
                    state = html.escape(issuer.device["state"], quote=True)
                    route.fulfill(
                        content_type="text/html",
                        body=f'<h1>Fictional KagNode consent</h1><form method="get" action="{callback}"><input type="hidden" name="state" value="{state}"><input type="hidden" name="iss" value="https://nebulacoder.com"><button name="code" value="'
                        + "C" * 43
                        + '">Approve</button><button name="error" value="access_denied">Cancel</button></form>',
                    )
                    return
                external.append(url)
                route.abort()

            context.route("**/*", guard)
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            home = origin + ("/" if args.product == "mail" else "/unlock")
            provider = page.locator("[data-core-login-provider]")
            form = page.locator(
                "#login-form" if args.product == "mail" else "#auth-form"
            )
            email = page.locator(
                "#login-email" if args.product == "mail" else "#auth-email"
            )
            secret = page.locator(
                "#login-password" if args.product == "mail" else "#master-password"
            )
            workspace_view = page.locator(
                "#workspace" if args.product == "mail" else "#dashboard"
            )
            note = page.locator(
                "#provider-mailbox-note"
                if args.product == "mail"
                else "#provider-vault-note"
            )

            def advance():
                if not secret.is_visible():
                    form.get_by_role("button", name="Continue", exact=True).click()

            def local_submit():
                advance()
                secret.fill(PASSWORD)
                if (
                    args.product == "passwords"
                    and page.locator("#confirm-wrap").is_visible()
                ):
                    page.locator("#confirm-password").fill(PASSWORD)
                form.locator("button[type=submit]").click()
                expect(workspace_view).to_be_visible(timeout=20000)

            def signout():
                page.locator("[data-core-account-trigger]").click()
                page.locator("[data-core-account-menu] button").last.click()
                expect(provider).to_be_enabled()

            def approve():
                if actual:
                    page.wait_for_url(
                        (
                            actual["origin"]
                            if os.environ.get("KAGNODE_TEST_DIRECT_ISSUER")
                            else actual["issuer"]
                        )
                        + "/**",
                        timeout=20000,
                    )
                    page.locator(
                        'form[action="/login"], button[name=decision][value=approve]'
                    ).first.wait_for(state="visible", timeout=20000)
                    login = page.locator('form[action="/login"]')
                    if login.is_visible():
                        if os.environ.get("KAGNODE_TRACE_ISSUER"):
                            print(
                                "ISSUER_FORM",
                                json.dumps(
                                    login.evaluate(
                                        "f=>({action:f.getAttribute('action'),hidden:[...f.querySelectorAll('input[type=hidden]')].map(e=>({name:e.name,value:e.name==='next'?e.value.split('?')[0]:'[redacted]'}))})"
                                    )
                                ),
                                flush=True,
                            )
                        login.locator("[name=email]").fill(actual["user"]["email"])
                        # Issuer may use the same progressive frame.
                        password = login.locator("[name=password]")
                        if not password.is_visible():
                            login.get_by_role(
                                "button", name="Continue", exact=True
                            ).click()
                        password.fill(actual["user"]["password"])
                        login.locator("button[type=submit]").click()
                    try:
                        page.locator("button[name=decision][value=approve]").click(
                            timeout=20000
                        )
                    except Exception:
                        page.screenshot(
                            path=str(
                                output / f"{args.engine}-{width}-issuer-failure.png"
                            )
                        )
                        if os.environ.get("KAGNODE_TRACE_ISSUER"):
                            from urllib.parse import urlsplit

                            print(
                                "ISSUER_FAILURE",
                                json.dumps(
                                    {
                                        "path": urlsplit(page.url).path,
                                        "text": page.locator("body").inner_text()[
                                            :1500
                                        ],
                                    }
                                ),
                                flush=True,
                            )
                        raise
                else:
                    page.get_by_role("button", name="Approve", exact=True).click()
                expect(note).to_be_visible(timeout=30000)
                assert (
                    page.url.startswith(origin + "/")
                    and "kagnode_attempt" not in page.url
                    and "code=" not in page.url
                    and "state=" not in page.url
                )
                assert not workspace_view.is_visible()

            page.goto(home)
            expect(provider).to_be_enabled()
            style = shared_login_style(page)
            expect(secret).to_be_hidden()
            email.fill("local@example.test")
            advance()
            expect(secret).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
            page.screenshot(
                path=str(output / f"{args.engine}-{width}-local-secret.png")
            )
            local_submit()
            navigation_icons(
                page, "#mail-nav a" if args.product == "mail" else "#sidebar nav a"
            )
            signout()
            if not actual and width == 390:
                issuer.offline = True
                provider.click()
                expect(page.locator("[data-core-login-status]")).to_contain_text(
                    "Unable to connect", timeout=15000
                )
                issuer.offline = False
                provider.click()
                page.get_by_role("button", name="Cancel", exact=True).click()
                expect(page.locator("[data-core-login-status]")).to_contain_text(
                    "Unable to check the connection", timeout=15000
                )
                expect(provider).to_be_enabled()
            provider.click()
            approve()
            if args.product == "mail" and not email.evaluate("e=>e.readOnly"):
                email.fill("local@example.test")
            advance()
            page.screenshot(
                path=str(output / f"{args.engine}-{width}-provider-secret.png")
            )
            links = app.extensions["kagnode_account"]
            with links.db() as connection:
                row = connection.execute(
                    "SELECT id,profile FROM sign_in_attempts WHERE status='ready'"
                ).fetchone()
                assert row is not None
                attempt = row["id"]
                subject = json.loads(row["profile"])["subject"]
            local_submit()
            with links.db() as connection:
                assert not connection.execute(
                    "SELECT 1 FROM sign_in_attempts WHERE id=?", (attempt,)
                ).fetchone()
                assert connection.execute(
                    "SELECT 1 FROM links WHERE subject=?", (subject,)
                ).fetchone()
            signout()
            provider.click()
            approve()
            expect(secret).to_be_visible()
            page.get_by_role("link", name="Use local sign-in").click()
            expect(provider).to_be_enabled()
            assert not email.evaluate("e=>e.readOnly")
            assert not external, external
            assert not errors, errors
            report.append(
                {
                    "engine": args.engine,
                    "width": width,
                    "actual_issuer": bool(actual),
                    "issuer_browser_transport": "direct-loopback"
                    if actual and os.environ.get("KAGNODE_TEST_DIRECT_ISSUER")
                    else "canonical-https-proxy"
                    if actual
                    else "mock",
                    "local_progressive": True,
                    "same_tab_redirect": True,
                    "callback_scrubbed": True,
                    "explicit_completion": True,
                    "authorization_consumed": True,
                    "repeat_provider_after_signout": True,
                    "local_fallback_cancels": True,
                    "cancel": not bool(actual) and width == 390,
                    "issuer_failure": not bool(actual) and width == 390,
                    "login_style": style,
                    "external_requests": 0,
                    "page_errors": 0,
                }
            )
            context.close()
        browser.close()
    (output / f"{args.engine}-report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report))
finally:
    server.shutdown()
    workspace.cleanup()
