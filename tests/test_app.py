from conftest import login
from cryptography.fernet import Fernet

from kagnode import db


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
    old_cookie = client.get_cookie("kagnode_sid").value
    assert (
        client.post("/api/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    )
    assert client.post("/api/logout", headers=h).status_code == 200
    client.set_cookie("kagnode_sid", old_cookie)
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


def test_origin_and_fetch_metadata_reject_sibling_subdomains(client):
    h = login(client)
    for extra in (
        {"Origin": "https://attacker.example.com"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "cross-site"},
    ):
        response = client.post(
            "/api/admin/domains", json={"name": "second.com"}, headers=h | extra
        )
        assert response.status_code == 403
        assert "Cross-origin" in response.json["error"]
    assert (
        client.post(
            "/api/admin/domains",
            json={"name": "second.com"},
            headers=h | {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"},
        ).status_code
        == 201
    )


def test_production_cookies_are_host_scoped(app):
    from kagnode import create_app

    config = dict(app.config)
    config.update(TESTING=False, SESSION_COOKIE_SECURE=True)
    secure_app = create_app(config)
    client = secure_app.test_client()
    response = client.get("/api/session", base_url="https://mail.example.com")
    csrf = response.json["csrf"]
    response = client.post(
        "/api/login",
        base_url="https://mail.example.com",
        json={"email": "admin@example.com", "password": "CorrectPassword123!"},
        headers={"X-CSRF-Token": csrf, "Origin": "https://mail.example.com"},
    )
    assert response.status_code == 200
    for name in ("__Host-kagnode_sid",):
        cookie = client.get_cookie(name, domain="mail.example.com")
        assert cookie and cookie.secure and cookie.http_only
        assert (
            cookie.origin_only and cookie.path == "/" and cookie.same_site == "Strict"
        )
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"


def test_stale_admin_requires_password_and_rotates_token(app, client):
    import time

    h = login(client)
    old_cookie = client.get_cookie("kagnode_sid").value
    cipher = Fernet(app.config["CREDENTIAL_KEY"])
    old_ciphertext = cipher.encrypt_at_time(
        b"CorrectPassword123!", int(time.time()) - 901
    ).decode()
    with db.connect(app.config["DATABASE"]) as c:
        c.execute("UPDATE sessions SET password=?", (old_ciphertext,))
        expiry = c.execute("SELECT expires FROM sessions").fetchone()[0]
    response = client.post("/api/admin/domains", json={"name": "second.com"}, headers=h)
    assert response.status_code == 403 and response.json["code"] == "reauth_required"
    assert client.get("/api/admin").status_code == 200
    response = client.post("/api/account/reauth", json={"password": "wrong"}, headers=h)
    assert response.status_code == 403
    assert client.get_cookie("kagnode_sid").value == old_cookie
    response = client.post(
        "/api/account/reauth", json={"password": "CorrectPassword123!"}, headers=h
    )
    assert response.status_code == 200
    assert client.get_cookie("kagnode_sid").value != old_cookie
    assert (
        client.post(
            "/api/admin/domains", json={"name": "second.com"}, headers=h
        ).status_code
        == 201
    )
    with db.connect(app.config["DATABASE"]) as c:
        assert c.execute("SELECT expires FROM sessions").fetchone()[0] == expiry
    stolen = app.test_client()
    stolen.set_cookie("kagnode_sid", old_cookie)
    assert stolen.get("/api/admin").status_code == 401


def test_reauth_is_limited_and_expired_sessions_cannot_refresh(app, client):
    import time

    h = login(client)
    with db.connect(app.config["DATABASE"]) as c:
        c.execute(
            "INSERT INTO throttle VALUES('reauth:1',10,?)", (int(time.time()) + 900,)
        )
    assert (
        client.post(
            "/api/account/reauth", json={"password": "CorrectPassword123!"}, headers=h
        ).status_code
        == 429
    )
    with db.connect(app.config["DATABASE"]) as c:
        c.execute("UPDATE sessions SET expires=?", (int(time.time()) - 1,))
    csrf = client.get("/api/session").json["csrf"]
    assert (
        client.post(
            "/api/account/reauth",
            json={"password": "CorrectPassword123!"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 401
    )


def test_only_five_newest_sessions_are_kept(app):
    clients = [app.test_client() for _ in range(6)]
    for client in clients:
        login(client, "member")
    assert clients[0].get("/api/session").json["user"] is None
    assert all(client.get("/api/session").json["user"] for client in clients[1:])
    with db.connect(app.config["DATABASE"]) as c:
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 5


def test_small_admin_json_and_multipart_limits(client):
    import io

    h = login(client)
    assert (
        client.post(
            "/api/admin/domains", json={"name": "x" * 65536}, headers=h
        ).status_code
        == 413
    )
    files = [(io.BytesIO(b"x"), f"file-{i}.txt") for i in range(41)]
    assert (
        client.post(
            "/api/send",
            data={"to": "recipient@example.org", "attachments": files},
            headers=h,
        ).status_code
        == 413
    )


def test_json_arrays_are_rejected_before_handler(client):
    h = login(client)
    assert (
        client.post("/api/admin/domains", json=["example.com"], headers=h).status_code
        == 400
    )


def test_cli_secret_file_rejects_unsafe_types(tmp_path):
    import os

    import pytest

    from kagnode.cli import private_password

    secret = tmp_path / "secret"
    secret.write_text("CorrectPassword123!")
    secret.chmod(0o600)
    symlink = tmp_path / "symlink"
    symlink.symlink_to(secret)
    with pytest.raises(OSError):
        private_password(symlink)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError, match="single-link"):
        private_password(fifo)
    hardlink = tmp_path / "hardlink"
    os.link(secret, hardlink)
    with pytest.raises(ValueError, match="single-link"):
        private_password(hardlink)
    mode = tmp_path / "executable"
    mode.write_text("CorrectPassword123!")
    mode.chmod(0o700)
    with pytest.raises(ValueError, match="mode"):
        private_password(mode)


def test_root_cli_drops_database_privileges(monkeypatch):
    from types import SimpleNamespace

    from kagnode import cli

    calls = []
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        cli.pwd,
        "getpwnam",
        lambda name: SimpleNamespace(
            pw_name="kagnode",
            pw_gid=990,
            pw_uid=991,
        ),
    )
    monkeypatch.setattr(
        cli.os, "initgroups", lambda *args: calls.append(("groups", args))
    )
    monkeypatch.setattr(cli.os, "setgid", lambda gid: calls.append(("gid", gid)))
    monkeypatch.setattr(cli.os, "setuid", lambda uid: calls.append(("uid", uid)))
    cli.drop_database_privileges()
    assert calls == [("groups", ("kagnode", 990)), ("gid", 990), ("uid", 991)]
