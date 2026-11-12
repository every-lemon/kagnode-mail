"""Browser redirect proof is bound to cookie, issuer, state and one-use S256 exchange."""

import base64
import hashlib
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from kagnode.account_sync import ProfileLinks, AccountLinkError
from kagnode.account_login import AccountSignIns


class BrowserIssuer:
    def __init__(self, path):
        self.now = 1000
        self.calls = []
        self.request = None
        self.hook = None
        self.fail = None
        self.links = ProfileLinks(
            path / "profiles",
            "kagnode-mail",
            transport=self.call,
            clock=lambda: self.now,
        )
        self.signins = AccountSignIns(self.links)
        self.browser = secrets.token_urlsafe(32)

    def call(self, method, endpoint, data=None, token=None):
        self.calls.append((endpoint, data))
        if endpoint == "browser/start":
            self.request = data
            return {
                "request_id": "Q" * 43,
                "authorization_uri": self.links.issuer
                + "/oauth/authorize?request="
                + "Q" * 43,
                "expires_in": 600,
            }
        if endpoint == "token":
            assert data["grant_type"] == "authorization_code"
            assert data["redirect_uri"] == self.request["redirect_uri"]
            assert (
                base64.urlsafe_b64encode(
                    hashlib.sha256(data["code_verifier"].encode()).digest()
                )
                .decode()
                .rstrip("=")
                == self.request["code_challenge"]
            )
            if self.hook:
                self.hook()
            if self.fail:
                raise self.fail
            return {
                "scope": "profile.read email.read",
                "access_token": "A" * 43,
                "refresh_token": "R" * 43,
                "authorization": {
                    "issuer": self.links.issuer,
                    "subject": "fixture-subject",
                    "client_id": self.links.client,
                    "installation": self.request["installation"],
                    "purpose": "sign_in",
                    "nonce": self.request["nonce"],
                    "auth_time": 900,
                    "approved_at": 1000,
                    "issued_at": 1000,
                    "expires_at": 1120,
                },
            }
        if endpoint.startswith("profile?"):
            return {
                "issuer": self.links.issuer,
                "subject": "fixture-subject",
                "name": "Fictional user",
                "photo": "",
                "email": "fixture@example.test",
                "email_verified": True,
            }
        return {}

    def start(self):
        return self.signins.start_browser(self.browser, "https://consumer.example.test")

    def callback(self, **kw):
        args = {
            "browser": self.browser,
            "state": self.request["state"],
            "issuer": self.links.issuer,
            "code": "C" * 43,
        }
        args.update(kw)
        return self.signins.callback(**args)


@pytest.fixture
def issuer(tmp_path):
    return BrowserIssuer(tmp_path)


def test_browser_flow_keeps_verifier_private_and_status_never_polls(issuer):
    start = issuer.start()
    assert start["authorization_uri"].startswith(
        issuer.links.issuer + "/oauth/authorize?"
    )
    assert "user_code" not in start and "verification_uri" not in start
    with issuer.links.db() as db:
        row = db.execute("SELECT * FROM sign_in_attempts").fetchone()
        verifier = row["verifier"]
    assert verifier not in json.dumps(start)
    assert issuer.request["state"] not in json.dumps(start)
    for _ in range(3):
        assert issuer.signins.status(issuer.browser, start["attempt_id"])["pending"]
    assert not any(p == "token" for p, _ in issuer.calls)
    assert issuer.callback() == start["attempt_id"]
    assert issuer.signins.status(issuer.browser, start["attempt_id"])["authorized"]
    with issuer.links.db() as db:
        assert db.execute(
            "SELECT verifier,state_hash FROM sign_in_attempts"
        ).fetchone()[:] == ("", "")
    issuer.signins.finish(issuer.browser, start["attempt_id"], "local-approved")
    with pytest.raises(AccountLinkError):
        issuer.signins.finish(issuer.browser, start["attempt_id"], "other")


@pytest.mark.parametrize(
    "field,value",
    [
        ("browser", "Z" * 43),
        ("state", "Z" * 43),
        ("state", "bad"),
        ("issuer", "https://evil.test"),
        ("issuer", ""),
        ("code", "bad"),
        ("code", ""),
    ],
)
def test_wrong_callback_cannot_exchange_or_consume_valid_attempt(issuer, field, value):
    start = issuer.start()
    with pytest.raises(AccountLinkError):
        issuer.callback(**{field: value})
    assert not any(p == "token" for p, _ in issuer.calls)
    assert issuer.callback() == start["attempt_id"]


def test_error_callback_never_exchanges_or_reflects_error_text(issuer):
    start = issuer.start()
    assert (
        issuer.callback(code="", error="<script>untrusted</script>")
        == start["attempt_id"]
    )
    with pytest.raises(AccountLinkError) as exc:
        issuer.signins.status(issuer.browser, start["attempt_id"])
    assert "script" not in str(exc.value)
    assert not any(p == "token" for p, _ in issuer.calls)
    assert any(p == "browser/cancel" for p, _ in issuer.calls)
    with pytest.raises(AccountLinkError):
        issuer.callback()


def test_parallel_callback_claims_once(issuer):
    issuer.start()
    entered, release = threading.Event(), threading.Event()
    issuer.hook = lambda: (entered.set(), release.wait(3))
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(issuer.callback)
        assert entered.wait(3)
        with pytest.raises(AccountLinkError):
            issuer.callback()
        release.set()
        future.result()
    assert sum(p == "token" for p, _ in issuer.calls) == 1


def test_cancel_during_exchange_revokes_unused_token(issuer):
    start = issuer.start()
    issuer.hook = lambda: issuer.signins.cancel(issuer.browser, start["attempt_id"])
    issuer.callback()
    with pytest.raises(AccountLinkError):
        issuer.signins.status(issuer.browser, start["attempt_id"])
    assert any(p == "revoke" for p, _ in issuer.calls)


def test_timeout_never_retries_exchange(issuer):
    start = issuer.start()
    issuer.fail = TimeoutError("fictional transport timeout")
    issuer.callback()
    with pytest.raises(AccountLinkError):
        issuer.callback()
    with pytest.raises(AccountLinkError):
        issuer.signins.status(issuer.browser, start["attempt_id"])
    assert sum(p == "token" for p, _ in issuer.calls) == 1


def test_admin_disabled_and_expired_attempt_cannot_exchange(issuer):
    start = issuer.start()
    issuer.now = 1601
    with pytest.raises(AccountLinkError):
        issuer.callback()
    assert not any(p == "token" for p, _ in issuer.calls)
    issuer.now = 1000
    issuer.start()
    with issuer.links.db() as db:
        db.execute("UPDATE policy SET enabled=0,generation=generation+1 WHERE id=1")
    with pytest.raises(AccountLinkError):
        issuer.callback()
    assert not any(p == "token" for p, _ in issuer.calls)


def test_additive_reopen_preserves_ready_attempt(issuer):
    start = issuer.start()
    issuer.callback()
    reopened = AccountSignIns(issuer.links)
    assert (
        reopened.verified(issuer.browser, start["attempt_id"])["profile"]["subject"]
        == "fixture-subject"
    )


def test_slow_start_response_never_extends_original_local_deadline(issuer):
    original = issuer.links.transport

    def slow(method, endpoint, data=None, token=None):
        if endpoint == "browser/start":
            issuer.now += 500
        return original(method, endpoint, data, token)

    issuer.links.transport = slow
    start = issuer.start()
    assert start["expires_in"] == 100
