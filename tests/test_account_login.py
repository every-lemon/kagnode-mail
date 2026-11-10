"""Fresh provider identity is browser-bound, single-use and policy constrained."""

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from kagnode.account_sync import ProfileLinks, AccountLinkError
from kagnode.account_login import AccountSignIns


class Issuer:
    def __init__(self, tmp_path):
        self.now = 1000
        self.calls = []
        self.subject = "subject-a"
        self.reply = None
        self.device = None
        self.hook = None
        self.links = ProfileLinks(
            tmp_path / "profiles",
            "kagnode-mail",
            transport=self.call,
            clock=lambda: self.now,
        )
        self.login = AccountSignIns(self.links)

    def call(self, method, endpoint, data=None, token=None):
        self.calls.append((endpoint, data))
        if endpoint == "device":
            self.device = data
            return {
                "device_code": "D" * 43,
                "user_code": "ABCDE-12345",
                "verification_uri_complete": "https://nebulacoder.com/account/apps/device?code=ABCDE12345",
            }
        if endpoint == "token":
            if self.hook:
                self.hook()
            return copy.deepcopy(
                self.reply if self.reply is not None else self.authorized()
            )
        if endpoint.startswith("profile?"):
            return {
                "issuer": self.links.issuer,
                "subject": self.subject,
                "name": "Fictional person",
                "photo": "",
                "email": "fictional@example.test",
                "email_verified": True,
            }
        return {}

    def authorized(self):
        return {
            "scope": "profile.read email.read",
            "access_token": "A" * 43,
            "refresh_token": "R" * 43,
            "authorization": {
                "issuer": self.links.issuer,
                "subject": self.subject,
                "client_id": self.links.client,
                "installation": self.device["installation"],
                "purpose": "sign_in",
                "nonce": self.device["nonce"],
                "auth_time": 900,
                "approved_at": 1000,
                "issued_at": self.now,
                "expires_at": self.now + 120,
            },
        }

    def start(self, browser="B" * 43):
        return self.login.start(browser, "https://mail.example.test")["attempt_id"]


@pytest.fixture
def issuer(tmp_path):
    return Issuer(tmp_path)


def test_signin_secrets_stay_private_and_authorization_is_one_use(issuer):
    attempt = issuer.start()
    response = issuer.login.status("B" * 43, attempt)
    assert response["authorized"]
    for private in (
        "D" * 43,
        "A" * 43,
        "R" * 43,
        issuer.device["nonce"],
        "fictional@example.test",
    ):
        assert private not in json.dumps(response)
    proof = issuer.login.verified("B" * 43, attempt)
    assert proof["local_id"] == ""
    assert proof["profile"]["email"] == "fictional@example.test"
    issuer.login.finish("B" * 43, attempt, "approved-local-user")
    with pytest.raises(AccountLinkError):
        issuer.login.finish("B" * 43, attempt, "another-user")
    assert issuer.links.state("approved-local-user", "Local", refresh=False)[
        "connected"
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "https://evil.test"),
        ("subject", "wrong"),
        ("client_id", "kagnode-vpn"),
        ("installation", "https://other.test"),
        ("purpose", "profile"),
        ("nonce", "wrong"),
        ("auth_time", 0),
        ("approved_at", 1),
        ("issued_at", 1040),
        ("expires_at", 1000),
        ("expires_at", 1121),
        ("expires_at", float("nan")),
    ],
)
def test_invalid_fresh_proof_never_links(issuer, field, value):
    attempt = issuer.start()
    issuer.reply = issuer.authorized()
    issuer.reply["authorization"][field] = value
    with pytest.raises(AccountLinkError):
        issuer.login.status("B" * 43, attempt)
    assert not issuer.links.state("any", "Any", refresh=False)["connected"]
    assert any(endpoint == "revoke" for endpoint, _ in issuer.calls)


def test_profile_or_refresh_token_response_cannot_sign_in(issuer):
    attempt = issuer.start()
    issuer.reply = {"access_token": "A" * 43, "refresh_token": "R" * 43}
    with pytest.raises(AccountLinkError, match="fresh"):
        issuer.login.status("B" * 43, attempt)


def test_browser_binding_and_cancel_are_attempt_scoped(issuer):
    attempt = issuer.start()
    with pytest.raises(AccountLinkError):
        issuer.login.status("C" * 43, attempt)
    issuer.login.cancel("C" * 43, attempt)
    assert issuer.login.row("B" * 43, attempt)
    newer = issuer.start()
    issuer.login.cancel("B" * 43, attempt)
    assert issuer.login.row("B" * 43, newer)
    issuer.login.cancel("B" * 43, newer)
    with pytest.raises(AccountLinkError):
        issuer.login.status("B" * 43, newer)


def test_policy_disable_invalidates_pending_and_ready_authorizations(issuer):
    attempt = issuer.start()
    issuer.login.status("B" * 43, attempt)
    issuer.links.handle("owner", "Owner", True, "policy", {"enabled": False})
    with pytest.raises(AccountLinkError):
        issuer.login.finish("B" * 43, attempt, "local")
    with issuer.links.db() as db:
        assert db.execute("SELECT COUNT(*) FROM sign_in_attempts").fetchone()[0] == 0
    assert any(endpoint == "revoke" for endpoint, _ in issuer.calls)


def test_cancel_during_exchange_discards_late_authorization(issuer):
    attempt = issuer.start()
    issuer.hook = lambda: issuer.login.cancel("B" * 43, attempt)
    with pytest.raises(AccountLinkError):
        issuer.login.status("B" * 43, attempt)
    assert any(endpoint == "revoke" for endpoint, _ in issuer.calls)


def test_concurrent_poll_never_exchanges_same_device_twice(issuer):
    attempt = issuer.start()
    entered, release = threading.Event(), threading.Event()

    def wait():
        entered.set()
        assert release.wait(5)

    issuer.hook = wait
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(issuer.login.status, "B" * 43, attempt)
        assert entered.wait(5)
        issuer.now += (
            10  # Beyond the poll interval while the first request is in flight.
        )
        assert issuer.login.status("B" * 43, attempt)["pending"]
        release.set()
        assert first.result()["authorized"]
    assert sum(endpoint == "token" for endpoint, _ in issuer.calls) == 1


def test_lost_exchange_response_requires_new_attempt(issuer):
    attempt = issuer.start()

    def fail():
        raise AccountLinkError("Transport timeout", 502)

    issuer.hook = fail
    with pytest.raises(AccountLinkError):
        issuer.login.status("B" * 43, attempt)
    with pytest.raises(AccountLinkError):
        issuer.login.status("B" * 43, attempt)
    assert sum(endpoint == "token" for endpoint, _ in issuer.calls) == 1


def test_existing_subject_cannot_move_to_another_local_account(issuer):
    attempt = issuer.start()
    issuer.login.status("B" * 43, attempt)
    issuer.login.finish("B" * 43, attempt, "local-a")
    attempt = issuer.start()
    issuer.login.status("B" * 43, attempt)
    assert issuer.login.verified("B" * 43, attempt)["local_id"] == "local-a"
    with pytest.raises(AccountLinkError):
        issuer.login.finish("B" * 43, attempt, "local-b")
    issuer.links.handle("local-a", "A", False, "disconnect")
    with pytest.raises(AccountLinkError):
        issuer.login.finish("B" * 43, attempt, "local-a")


def test_throttling_slowdown_and_expiry(issuer):
    attempt = issuer.start()
    issuer.reply = {"error": "slow_down"}
    assert issuer.login.status("B" * 43, attempt)["interval"] == 10
    assert issuer.login.status("B" * 43, attempt)["pending"]
    assert sum(endpoint == "token" for endpoint, _ in issuer.calls) == 1
    issuer.now += 601
    with pytest.raises(AccountLinkError):
        issuer.login.status("B" * 43, attempt)
    issuer.login.cleanup()
    with issuer.links.db() as db:
        assert db.execute("SELECT COUNT(*) FROM sign_in_attempts").fetchone()[0] == 0


def test_attempt_count_is_bounded(issuer):
    for index in range(128):
        issuer.start(f"{index:043d}")
    with pytest.raises(AccountLinkError) as error:
        issuer.start("X" * 43)
    assert error.value.status == 429


def test_unverified_email_is_not_used_as_verified_profile(issuer):
    profile = issuer.links.profile(
        {
            "issuer": issuer.links.issuer,
            "subject": "a",
            "name": "A",
            "photo": "",
            "email": "fake@example.test",
            "email_verified": False,
        }
    )
    assert profile["email"] == "" and profile["email_verified"] is False


def test_csrf_binding_survives_another_worker_without_sharing_browser_secrets(issuer):
    token = issuer.login.csrf("B" * 43)
    reopened = AccountSignIns(issuer.links)
    assert reopened.csrf("B" * 43) == token
    assert reopened.csrf("C" * 43) != token


def test_identity_scope_cannot_silently_read_email(issuer):
    attempt = issuer.start()
    issuer.reply = issuer.authorized()
    issuer.reply["scope"] = "profile.read"
    issuer.login.status("B" * 43, attempt)
    assert issuer.login.verified("B" * 43, attempt)["profile"]["email"] == ""


def test_source_rate_is_bounded_and_recovers_after_window(issuer):
    for _ in range(12):
        issuer.login.rate("fictional peer")
    with pytest.raises(AccountLinkError) as failure:
        issuer.login.rate("fictional peer")
    assert failure.value.status == 429
    issuer.now += 901
    issuer.login.rate("fictional peer")
