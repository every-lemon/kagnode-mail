"""Mail provider identity cannot create or unlock a mailbox by itself."""

from kagnode import db
from test_account_login import Issuer


def provider(client, app, tmp_path, linked=False):
    fake = Issuer(tmp_path)
    links = app.extensions["kagnode_account"]
    links.transport = fake.call
    links.clock = lambda: fake.now
    app.extensions["kagnode_signins"].clock = lambda: fake.now
    if linked:
        with db.connect(app.config["DATABASE"]) as conn:
            uid = conn.execute(
                "SELECT id FROM users WHERE email='member@example.com'"
            ).fetchone()[0]
        import json

        with links.db() as conn:
            conn.execute(
                "INSERT INTO links VALUES(?,?,?,?,?,0)",
                (
                    str(uid),
                    "subject-a",
                    json.dumps(
                        {
                            "issuer": links.issuer,
                            "subject": "subject-a",
                            "name": "Linked",
                            "photo": "",
                        }
                    ),
                    "O" * 43,
                    1000,
                ),
            )
    csrf = client.get("/api/session").json["csrf"]
    headers = {"X-CSRF-Token": csrf}
    started = client.post(
        "/api/kagnode-login", json={"action": "start"}, headers=headers
    )
    assert started.status_code == 200, started.json
    attempt = started.json["attempt_id"]
    from urllib.parse import urlencode

    fake = client.application.extensions["kagnode_account"].transport.__self__
    returned = client.get(
        "/auth/kagnode/callback?"
        + urlencode(
            {"state": fake.device["state"], "iss": fake.links.issuer, "code": "C" * 43}
        )
    )
    assert (
        returned.status_code == 303 and returned.headers["Cache-Control"] == "no-store"
    )
    assert (
        "code=" not in returned.headers["Location"]
        and "state=" not in returned.headers["Location"]
    )
    confirmed = client.post(
        "/api/kagnode-login",
        json={"action": "status", "attempt_id": attempt},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.json
    return headers, attempt, confirmed.json, fake


def test_confirmed_identity_still_needs_mailbox_password(client, app, tmp_path):
    headers, attempt, result, _ = provider(client, app, tmp_path, linked=True)
    assert (
        result["continuation"] == "mailbox" and result["email"] == "member@example.com"
    )
    assert client.get("/api/session").json["user"] is None
    bad = client.post(
        "/api/login",
        json={
            "email": "member@example.com",
            "password": "wrong",
            "provider_attempt": attempt,
        },
        headers=headers,
    )
    assert bad.status_code == 401
    good = client.post(
        "/api/login",
        json={
            "email": "member@example.com",
            "password": "CorrectPassword123!",
            "provider_attempt": attempt,
        },
        headers=headers,
    )
    assert good.status_code == 200 and not good.json["user"]["admin"]
    assert client.get("/api/session").json["user"]["email"] == "member@example.com"


def test_linked_identity_cannot_choose_another_mailbox(client, app, tmp_path):
    headers, attempt, _, _ = provider(client, app, tmp_path, linked=True)
    response = client.post(
        "/api/login",
        json={
            "email": "admin@example.com",
            "password": "CorrectPassword123!",
            "provider_attempt": attempt,
        },
        headers=headers,
    )
    assert response.status_code == 401
    assert client.get("/api/session").json["user"] is None


def test_new_subject_links_only_after_explicit_local_password(client, app, tmp_path):
    headers, attempt, result, _ = provider(client, app, tmp_path)
    assert result["email"] == "" and result["linked"] is False
    with db.connect(app.config["DATABASE"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    response = client.post(
        "/api/login",
        json={
            "email": "member@example.com",
            "password": "CorrectPassword123!",
            "provider_attempt": attempt,
        },
        headers=headers,
    )
    assert response.status_code == 200
    uid = response.json["user"]["id"]
    assert (
        app.extensions["kagnode_account"].state(uid, "Member", refresh=False)[
            "profile"
        ]["subject"]
        == "subject-a"
    )


def test_provider_requires_csrf_same_origin_browser_and_policy(client, app, tmp_path):
    csrf = client.get("/api/session").json["csrf"]
    assert (
        client.post("/api/kagnode-login", json={"action": "start"}).status_code == 403
    )
    assert (
        client.post(
            "/api/kagnode-login",
            json={"action": "start"},
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    app.extensions["kagnode_account"].handle(
        "owner", "Owner", True, "policy", {"enabled": False}
    )
    assert client.get("/api/session").json["provider_enabled"] is False
    assert (
        client.post(
            "/api/kagnode-login",
            json={"action": "start"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 403
    )
    local = client.post(
        "/api/login",
        json={"email": "member@example.com", "password": "CorrectPassword123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert local.status_code == 200
