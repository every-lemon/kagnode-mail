"""Production sign-in advertises only the configured HTTPS entry point."""

from kagnode import create_app


def test_signin_access_matches_transport_and_canonical_host(app):
    production = create_app(dict(
        app.config, TESTING=False, SESSION_COOKIE_SECURE=True,
        TRUSTED_PROXIES=["192.168.0.6"],
    ))
    client = production.test_client()
    cases = [
        ("http://192.168.0.10:8000", "192.168.0.20", {}, False),
        ("https://wrong.example", "192.168.0.20", {}, False),
        ("https://mail.example.com", "192.168.0.20", {}, True),
        ("http://mail.example.com", "192.168.0.6", {"X-Forwarded-Proto": "https"}, True),
        ("http://mail.example.com", "192.168.0.20", {"X-Forwarded-Proto": "https"}, False),
        ("http://attacker.invalid", "192.168.0.6", {
            "X-Forwarded-Proto": "https", "X-Forwarded-Host": "mail.example.com",
        }, False),
    ]
    for origin, peer, headers, allowed in cases:
        result = client.get("/api/session", base_url=origin, headers=headers,
                            environ_overrides={"REMOTE_ADDR": peer})
        assert result.status_code == 200
        assert result.json["login_allowed"] is allowed
        assert result.json["login_url"] == "https://mail.example.com/"
        assert "Location" not in result.headers
    entry = client.get("/", base_url="http://192.168.0.10:8000")
    assert entry.status_code == 200
    assert "Location" not in entry.headers


def test_insecure_origin_remains_rejected_for_production_login(app):
    production = create_app(dict(app.config, TESTING=False, SESSION_COOKIE_SECURE=True))
    client = production.test_client()
    result = client.post("/api/login", base_url="http://192.168.0.10:8000", json={},
                         headers={"Origin": "http://192.168.0.10:8000"})
    assert result.status_code == 403
    assert "Cross-origin" in result.json["error"]
    canonical = client.get("/api/session", base_url="https://mail.example.com")
    assert canonical.json["login_allowed"] is True
    cookie = client.get_cookie("__Host-kagnode_csrf", domain="mail.example.com")
    assert cookie and cookie.secure and cookie.http_only


def test_demo_and_test_login_remain_available_over_http(app):
    assert app.test_client().get("/api/session").json["login_allowed"] is True
    preview = create_app(dict(app.config, TESTING=False, DEMO=True))
    result = preview.test_client().get("/api/session")
    assert result.json["login_allowed"] is True
    assert result.json["demo"] is True
