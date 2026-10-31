"""Proxy trust must not be inferred from headers supplied by a LAN client."""

import hashlib
import json

import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Request, Response

from kagnode import create_app, db
from kagnode.proxy import TrustedProxyFix


@Request.application
def inspect_request(request):
    return Response(json.dumps({
        "peer": request.remote_addr,
        "scheme": request.scheme,
        "host": request.host,
        "forwarded": request.headers.get("Forwarded"),
        "xff": request.headers.get("X-Forwarded-For"),
    }), content_type="application/json")


@pytest.mark.parametrize("peer", ["192.168.0.20", "2001:db8::20", "invalid", ""])
def test_direct_clients_cannot_spoof_proxy_headers(peer):
    client = Client(TrustedProxyFix(inspect_request, ["127.0.0.1", "::1"]), Response)
    result = client.get("/", environ_overrides={"REMOTE_ADDR": peer}, headers={
        "X-Forwarded-For": "198.51.100.10",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "attacker.invalid",
        "Forwarded": 'for=198.51.100.10;proto=https;host="attacker.invalid"',
    })
    assert result.json == {
        "peer": peer, "scheme": "http", "host": "localhost", "forwarded": None, "xff": None,
    }


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "192.168.0.6"])
def test_explicit_proxy_preserves_forwarded_client_and_https(peer):
    client = Client(TrustedProxyFix(inspect_request, [peer]), Response)
    result = client.get("/", environ_overrides={"REMOTE_ADDR": peer}, headers={
        "Host": "mail.example.com", "X-Forwarded-For": "198.51.100.10",
        "X-Forwarded-Proto": "https", "X-Forwarded-Host": "attacker.invalid",
    })
    assert result.json["peer"] == "198.51.100.10"
    assert result.json["scheme"] == "https"
    assert result.json["host"] == "mail.example.com"


def test_empty_trust_list_disables_even_loopback_forwarding():
    client = Client(TrustedProxyFix(inspect_request, []), Response)
    result = client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}, headers={
        "X-Forwarded-For": "198.51.100.10", "X-Forwarded-Proto": "https",
    })
    assert result.json["peer"] == "127.0.0.1"
    assert result.json["scheme"] == "http"


@pytest.mark.parametrize("value", [
    None, "192.168.0.6", [True], [123], ["proxy.local"], ["192.168.0.0/24"],
    ["*"], ["0.0.0.0"], ["::"], ["224.0.0.1"], ["fe80::1%eth0"],
])
def test_invalid_proxy_configuration_fails_closed(value):
    with pytest.raises(ValueError, match="TRUSTED_PROXIES"):
        TrustedProxyFix(inspect_request, value)


def test_production_api_requires_real_https_or_an_explicit_proxy(app):
    production = create_app(dict(app.config, TESTING=False, TRUSTED_PROXIES=["192.168.0.6"]))
    client = production.test_client()
    headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.10"}
    direct = client.get("/api/v1/messages", headers=headers,
                        environ_overrides={"REMOTE_ADDR": "192.168.0.20"})
    assert direct.status_code == 403
    assert direct.json["error"]["code"] == "https_required"
    proxied = client.get("/api/v1/messages", headers=headers,
                         environ_overrides={"REMOTE_ADDR": "192.168.0.6"})
    assert proxied.status_code == 401
    assert proxied.json["error"]["code"] == "invalid_token"
    direct_tls = client.get("/api/v1/messages", base_url="https://mail.example.com",
                            environ_overrides={"REMOTE_ADDR": "192.168.0.20"})
    assert direct_tls.status_code == 401


def test_changing_forwarded_ip_cannot_escape_api_authentication_throttle(app):
    peer = "192.168.0.20"
    key = "api-auth:" + hashlib.sha256(peer.encode()).hexdigest()
    client = app.test_client()
    # Use two requests around the real limit, avoiding slow repeated failures.
    first = client.get("/api/v1/messages", headers={"X-Forwarded-For": "198.51.100.10"},
                       environ_overrides={"REMOTE_ADDR": peer})
    assert first.status_code == 401
    with db.connect(app.config["DATABASE"]) as conn:
        assert conn.execute("SELECT count FROM throttle WHERE key=?", (key,)).fetchone()[0] == 1
        conn.execute("UPDATE throttle SET count=60 WHERE key=?", (key,))
    second = client.get("/api/v1/messages", headers={"X-Forwarded-For": "198.51.100.11"},
                        environ_overrides={"REMOTE_ADDR": peer})
    assert second.status_code == 429
    assert second.json["error"]["code"] == "rate_limited"
