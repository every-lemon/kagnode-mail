from conftest import login

from morrow import db


def test_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"


def test_csrf_on_login_and_writes(client):
    assert client.post("/api/login", json={}).status_code == 403
    h = login(client)
    assert (
        client.post("/api/admin/domains", json={"name": "second.com"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/domains", json={"name": "second.com"}, headers=h
        ).status_code
        == 201
    )


def test_member_cannot_admin(client):
    h = login(client, "member")
    for path in ["/api/admin", "/api/admin/dns/example.com"]:
        assert client.get(path).status_code == 403
    assert client.post("/api/admin/users", json={}, headers=h).status_code == 403
    assert (
        client.put(
            "/api/admin/users/2/identities",
            json={"identities": ["admin@example.com"]},
            headers=h,
        ).status_code
        == 403
    )


def test_grant_revoke_and_encryption(app, client):
    h = login(client)
    assert (
        client.put(
            "/api/admin/users/2/identities",
            json={"identities": ["hello@example.com"]},
            headers=h,
        ).status_code
        == 200
    )
    with db.connect(app.config["DATABASE"]) as c:
        assert db.identities(c, 2) == ["hello@example.com", "member@example.com"]
        sess = c.execute("SELECT * FROM sessions").fetchone()
        assert "CorrectPassword" not in sess["password"]
        assert len(sess["token"]) == 64
    assert (
        client.put(
            "/api/admin/users/2/identities", json={"identities": []}, headers=h
        ).status_code
        == 200
    )
    with db.connect(app.config["DATABASE"]) as c:
        assert db.identities(c, 2) == ["member@example.com"]


def test_invalid_identity_and_external_domain(client):
    h = login(client)
    for identity in ["ceo@gmail.com", "madeup@example.com", "x'@example.com"]:
        assert (
            client.put(
                "/api/admin/users/2/identities",
                json={"identities": [identity]},
                headers=h,
            ).status_code
            == 400
        )


def test_last_admin_preserved(client):
    h = login(client)
    for change in [{"admin": False}, {"active": False}]:
        assert (
            client.patch("/api/admin/users/1", json=change, headers=h).status_code
            == 400
        )
    assert client.get("/api/admin").status_code == 200


def test_disable_invalidates_session(app, client):
    member = app.test_client()
    login(member, "member")
    h = login(client)
    assert (
        client.patch(
            "/api/admin/users/2", json={"active": False}, headers=h
        ).status_code
        == 200
    )
    assert member.get("/api/session").json["user"] is None
    with db.connect(app.config["DATABASE"]) as c:
        assert not db.identities(c, 2)


def test_password_change_revokes_sessions(app, client):
    h = login(client, "member")
    second = app.test_client()
    login(second, "member")
    r = client.post(
        "/api/account/password",
        json={"current": "CorrectPassword123!", "password": "BrandNewPassword123!"},
        headers=h,
    )
    assert r.status_code == 200
    assert second.get("/api/session").json["user"] is None
    assert client.get("/api/session").json["user"] is None


def test_alias_collision_and_grant_cleanup(client, app):
    h = login(client)
    assert (
        client.post(
            "/api/admin/aliases",
            json={"email": "member@example.com", "destination": "admin@example.com"},
            headers=h,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/admin/users",
            json={
                "email": "hello@example.com",
                "name": "Collision",
                "password": "CorrectPassword123!",
            },
            headers=h,
        ).status_code
        == 400
    )
    client.put(
        "/api/admin/users/2/identities",
        json={"identities": ["hello@example.com"]},
        headers=h,
    )
    assert (
        client.delete("/api/admin/aliases/hello@example.com", headers=h).status_code
        == 200
    )
    with db.connect(app.config["DATABASE"]) as c:
        assert db.identities(c, 2) == ["member@example.com"]


def test_domain_validation(client):
    h = login(client)
    for value in [
        "example.com\nmydestination = bad",
        "--help",
        "localhost",
        "a/../../b",
        "a..com",
        "a;echo.com",
    ]:
        assert (
            client.post(
                "/api/admin/domains", json={"name": value}, headers=h
            ).status_code
            == 400
        )


def test_dns_and_role(client):
    login(client)
    r = client.get("/api/admin/dns/example.com")
    assert r.status_code == 200
    assert {v["type"] for v in r.json["records"]} == {"A", "MX", "TXT", "PTR"}
    assert len([v for v in r.json["records"] if v["type"] == "TXT"]) == 3


def test_rate_limit(client):
    token = client.get("/api/session").json["csrf"]
    for _ in range(10):
        r = client.post(
            "/api/login",
            json={"email": "member@example.com", "password": "wrong"},
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 401
    assert (
        client.post(
            "/api/login",
            json={"email": "member@example.com", "password": "wrong"},
            headers={"X-CSRF-Token": token},
        ).status_code
        == 429
    )


def test_unauthenticated_mail_denied(client):
    assert client.get("/api/messages").status_code == 401
    assert client.get("/api/messages/1").status_code == 401
    assert client.get("/api/messages/1/attachments/original").status_code == 401


def test_forged_csrf_and_logout(app, client):
    h = login(client)
    old_cookie = client.get_cookie("morrow_sid").value
    assert (
        client.post("/api/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    )
    assert client.post("/api/logout", headers=h).status_code == 200
    client.set_cookie("morrow_sid", old_cookie)
    assert client.get("/api/admin").status_code == 401


def test_server_send_uses_identity_and_password(app, client):
    class FakeMail:
        def send(self, user, password, msg, sender, targets):
            assert user["email"] == "member@example.com"
            assert password == "CorrectPassword123!"
            assert sender == "member@example.com"
            assert targets == ["recipient@example.org"]
            assert msg["Bcc"] is None
            return {"message": "Accepted for delivery", "warning": None, "refused": []}

    app.extensions["mail"] = FakeMail()
    h = login(client, "member")
    payload = {
        "from": "admin@example.com",
        "to": "recipient@example.org",
        "subject": "Hello",
        "body": "Hello there",
    }
    assert client.post("/api/send", json=payload, headers=h).status_code == 403
    payload["from"] = "member@example.com"
    assert client.post("/api/send", json=payload, headers=h).status_code == 200


def test_quota_and_password_validation(client):
    h = login(client)
    for value in [0, -1, 1048577]:
        assert (
            client.patch(
                "/api/admin/users/2", json={"quota_mb": value}, headers=h
            ).status_code
            == 400
        )
    assert (
        client.patch(
            "/api/admin/users/2", json={"password": "weak"}, headers=h
        ).status_code
        == 400
    )


def test_readonly_demo(client, app):
    app.config["DEMO"] = True
    h = login(client)
    assert (
        client.post(
            "/api/admin/domains", json={"name": "second.com"}, headers=h
        ).status_code
        == 403
    )


def test_public_ip_change_requires_admin_and_changes_dns(app, client):
    member = app.test_client()
    mh = login(member, "member")
    assert (
        member.put(
            "/api/admin/network", json={"ipv4": "192.0.2.20"}, headers=mh
        ).status_code
        == 403
    )
    h = login(client)
    assert (
        client.put(
            "/api/admin/network", json={"ipv4": "bad; data"}, headers=h
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/admin/network", json={"ipv4": "192.0.2.20"}, headers=h
        ).status_code
        == 200
    )
    records = client.get("/api/admin/dns/example.com").json["records"]
    assert next(r for r in records if r["type"] == "A")["value"] == "192.0.2.20"
