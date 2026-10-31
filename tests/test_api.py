import hashlib
import io
import json
import smtplib
import time
from unittest.mock import Mock

import pytest
from conftest import login
from cryptography.fernet import Fernet

from kagnode import db
from kagnode.mail import MessageNotFound, SendUncertain, StaleMailbox


def issue(client, headers, **overrides):
    payload = dict(
        user_id=2,
        label="Integration",
        scopes=["read", "send", "write"],
        senders=["member@example.com"],
    )
    payload.update(overrides)
    response = client.post("/api/admin/tokens", headers=headers, json=payload)
    assert response.status_code == 201, response.json
    return response.json


def bearer(item):
    return {"Authorization": "Bearer " + item["secret"]}


@pytest.fixture
def token(app, client):
    return issue(client, login(client))


@pytest.fixture
def mail(app):
    adapter = Mock()
    adapter.list.return_value = dict(
        items=[], folder="inbox", uidvalidity="42", page=1, pages=1, total=0
    )
    adapter.read.return_value = dict(
        uid="12", folder="inbox", uidvalidity="42", body="Hello"
    )
    adapter.attachment.return_value = (b"attachment", "file.txt")
    adapter.send.return_value = dict(
        status="accepted", message_id="test", warning=None, refused=[]
    )
    adapter.draft.return_value = dict(folder="drafts", uidvalidity="43", uid="14")
    app.extensions["mail"] = adapter
    return adapter


def test_token_secret_storage_and_metadata(app, client, token):
    metadata = token["token"]
    assert 89 * 86400 < metadata["expires"] - time.time() <= 90 * 86400
    with db.connect(app.config["DATABASE"]) as conn:
        row = conn.execute("SELECT * FROM api_tokens").fetchone()
        assert row["token_hash"] == hashlib.sha256(token["secret"].encode()).hexdigest()
        assert token["secret"] not in json.dumps(dict(row))
        password = (
            Fernet(app.config["CREDENTIAL_KEY"])
            .decrypt(row["backend_password"].encode())
            .decode()
        )
        assert db.verify(password, row["backend_hash"])
        assert password not in json.dumps(dict(row))
    listed = client.get("/api/admin/tokens").json
    assert listed["tokens"] == [metadata]
    assert "secret" not in json.dumps(listed)
    assert "backend" not in json.dumps(listed)
    assert listed["users"][1]["identities"]


def test_admin_only_issuance_and_csrf(app):
    client = app.test_client()
    headers = login(client, "member")
    assert client.get("/api/admin/tokens").status_code == 403
    assert client.post("/api/admin/tokens", json={}, headers=headers).status_code == 403
    assert (
        client.delete("/api/admin/tokens/missing", headers=headers).status_code == 403
    )
    client = app.test_client()
    login(client)
    assert client.post("/api/admin/tokens", json={}).status_code == 403


def test_token_create_requires_recent_admin_auth(app, client, token):
    with db.connect(app.config["DATABASE"]) as conn:
        conn.execute(
            "UPDATE sessions SET password=?",
            (
                Fernet(app.config["CREDENTIAL_KEY"])
                .encrypt_at_time(b"CorrectPassword123!", int(time.time()) - 901)
                .decode(),
            ),
        )
    csrf = client.get("/api/session").json["csrf"]
    result = client.post("/api/admin/tokens", json={}, headers={"X-CSRF-Token": csrf})
    assert result.status_code == 403
    assert result.json["code"] == "reauth_required"
    assert (
        client.delete(
            "/api/admin/tokens/" + token["token"]["id"], headers={"X-CSRF-Token": csrf}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "change",
    [
        {"scopes": []},
        {"scopes": ["admin"]},
        {"scopes": [1]},
        {"senders": []},
        {"senders": ["admin@example.com"]},
        {"expires_days": 0},
        {"expires_days": 366},
        {"expires_days": True},
        {"user_id": "2"},
        {"label": ""},
        {"label": "a\n"},
    ],
)
def test_token_validation(client, change):
    h = login(client)
    payload = dict(
        user_id=2, label="Test", scopes=["send"], senders=["member@example.com"]
    )
    payload.update(change)
    assert client.post("/api/admin/tokens", headers=h, json=payload).status_code == 400


def test_never_expiry_and_expired_rejected(app, client, mail):
    h = login(client)
    item = issue(client, h, expires_days=None)
    assert item["token"]["expires"] is None
    assert client.get("/api/v1/messages", headers=bearer(item)).status_code == 200
    with db.connect(app.config["DATABASE"]) as conn:
        conn.execute("UPDATE api_tokens SET expires=?", (int(time.time()) - 1,))
    response = client.get("/api/v1/messages", headers=bearer(item))
    assert response.status_code == 401
    assert response.json["error"]["code"] == "invalid_token"


def test_auth_mechanisms_are_separate(app, client, token):
    assert client.get("/api/v1/folders").status_code == 401  # Even with admin cookies.
    assert client.get("/api/v1/folders?token=" + token["secret"]).status_code == 401
    independent = app.test_client()
    assert (
        independent.get("/api/admin/tokens", headers=bearer(token)).status_code == 401
    )
    assert (
        independent.post(
            "/api/admin/tokens", headers=bearer(token), json={}
        ).status_code
        == 403
    )
    assert independent.get("/api/v1/folders", headers=bearer(token)).status_code == 200
    assert (
        independent.get(
            "/api/v1/folders", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert (
        independent.get(
            "/api/v1/folders",
            headers={**bearer(token), "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        independent.get(
            "/api/v1/folders",
            headers={**bearer(token), "Sec-Fetch-Site": "same-origin"},
        ).status_code
        == 403
    )
    assert (
        "Access-Control-Allow-Origin"
        not in independent.get("/api/v1/folders", headers=bearer(token)).headers
    )
    app.config["TESTING"] = False
    assert independent.get("/api/v1/folders", headers=bearer(token)).status_code == 403
    assert (
        independent.get(
            "/api/v1/folders",
            headers=bearer(token),
            base_url="https://mail.example.com",
        ).status_code
        == 200
    )


def test_reads_use_private_identity_and_do_not_mark_seen(client, token, mail):
    h = bearer(token)
    assert (
        client.get("/api/v1/messages?folder=inbox&q=test&page=2", headers=h).status_code
        == 200
    )
    user, password, folder, page, query = mail.list.call_args.args
    assert user["email"] == "member@example.com"
    assert user["auth_username"] == "api-" + token["token"]["id"] + "@api.invalid"
    assert password and password != token["secret"]
    assert (folder, page, query) == ("inbox", 2, "test")
    assert (
        client.get(
            "/api/v1/messages/12?folder=inbox&uidvalidity=42", headers=h
        ).status_code
        == 200
    )
    assert mail.read.call_args.kwargs == {"uidvalidity": "42", "mark_seen": False}
    assert client.get("/api/v1/messages/12", headers=h).status_code == 400
    assert (
        client.get("/api/v1/messages/12/attachments/1?uidvalidity=42", headers=h).data
        == b"attachment"
    )
    assert mail.attachment.call_args.kwargs == {"uidvalidity": "42"}


def test_scope_separation_and_delete_permission(client, mail):
    h = login(client)
    read = issue(client, h, scopes=["read"], senders=[])
    write = issue(client, h, scopes=["write"], senders=[])
    delete = issue(client, h, scopes=["delete"], senders=[])
    assert client.post("/api/v1/send", headers=bearer(read), json={}).status_code == 403
    action = {"folder": "inbox", "uidvalidity": "42", "action": "trash"}
    assert (
        client.post(
            "/api/v1/messages/12/action", headers=bearer(read), json=action
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/messages/12/action", headers=bearer(write), json=action
        ).status_code
        == 200
    )
    action.update(folder="trash", action="delete")
    assert (
        client.post(
            "/api/v1/messages/12/action", headers=bearer(write), json=action
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/messages/12/action", headers=bearer(delete), json=action
        ).status_code
        == 200
    )
    assert client.get("/api/v1/messages", headers=bearer(write)).status_code == 403
    action["folder"] = "inbox"
    assert (
        client.post(
            "/api/v1/messages/12/action", headers=bearer(delete), json=action
        ).status_code
        == 400
    )


def test_sender_subset_and_live_grant_revocation(app, client, mail):
    h = login(client)
    with db.connect(app.config["DATABASE"]) as conn:
        conn.execute("INSERT INTO grants VALUES(2,'hello@example.com')")
    item = issue(client, h, scopes=["send"], senders=["hello@example.com"])
    data = dict(to="other@example.com", subject="API", body="Hi")
    assert (
        client.post("/api/v1/send", headers=bearer(item), json=data).status_code == 403
    )
    data["from"] = "hello@example.com"
    result = client.post("/api/v1/send", headers=bearer(item), json=data)
    assert result.status_code == 202 and result.json["status"] == "accepted"
    with db.connect(app.config["DATABASE"]) as conn:
        conn.execute("DELETE FROM grants WHERE user_id=2")
    assert (
        client.post("/api/v1/send", headers=bearer(item), json=data).status_code == 403
    )
    assert mail.send.call_count == 1


def test_multipart_send_and_drafts(client, token, mail):
    response = client.post(
        "/api/v1/send",
        headers=bearer(token),
        data={
            "to": "other@example.com",
            "subject": "Attachment",
            "body": "Hello",
            "attachments": (io.BytesIO(b"file"), "file.txt"),
        },
    )
    assert response.status_code == 202
    msg = mail.send.call_args.args[2]
    assert list(msg.iter_attachments())[0].get_payload(decode=True) == b"file"
    assert (
        client.post(
            "/api/v1/drafts", headers=bearer(token), json={"subject": "Draft"}
        ).status_code
        == 201
    )
    result = client.post(
        "/api/v1/drafts",
        headers=bearer(token),
        json={"draft_uid": "14", "uidvalidity": "43"},
    )
    assert result.status_code == 201
    assert mail.draft.call_args.kwargs == {"replace_uid": "14", "uidvalidity": "43"}
    assert (
        client.post(
            "/api/v1/drafts", headers=bearer(token), json={"draft_uid": "14"}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "exc,status,code",
    [
        (StaleMailbox("stale"), 409, "stale_mailbox"),
        (MessageNotFound("missing"), 404, "message_not_found"),
    ],
)
def test_reference_errors(client, token, mail, exc, status, code):
    mail.read.side_effect = exc
    response = client.get("/api/v1/messages/12?uidvalidity=42", headers=bearer(token))
    assert response.status_code == status and response.json["error"]["code"] == code


def test_send_outcomes(client, token, mail):
    data = {"to": "other@example.com", "body": "Hello"}
    mail.send.return_value = {
        "refused": ["bad@example.com"],
        "warning": "Sent copy failed",
    }
    result = client.post("/api/v1/send", headers=bearer(token), json=data)
    assert result.status_code == 202 and result.json["refused"] == ["bad@example.com"]
    mail.send.side_effect = SendUncertain("timeout")
    result = client.post("/api/v1/send", headers=bearer(token), json=data)
    assert result.status_code == 502 and result.json["error"]["do_not_retry"] is True
    mail.send.side_effect = smtplib.SMTPRecipientsRefused(
        {"bad@example.com": (550, b"no")}
    )
    result = client.post("/api/v1/send", headers=bearer(token), json=data)
    assert (
        result.status_code == 422
        and result.json["error"]["code"] == "recipients_refused"
    )


def test_revocation_wipes_credentials(app, client, token):
    csrf = client.get("/api/session").json["csrf"]
    assert (
        client.delete(
            "/api/admin/tokens/" + token["token"]["id"], headers={"X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/folders", headers=bearer(token)).status_code == 401
    with db.connect(app.config["DATABASE"]) as conn:
        row = conn.execute("SELECT * FROM api_tokens").fetchone()
        assert (
            row["revoked"] and not row["backend_password"] and not row["backend_hash"]
        )


@pytest.mark.parametrize("update", ["password='newhash'", "active=0"])
def test_database_trigger_revokes_for_all_password_and_disable_paths(
    app, client, token, update
):
    with db.connect(app.config["DATABASE"]) as conn:
        conn.execute("UPDATE users SET " + update + " WHERE id=2")
        row = conn.execute("SELECT * FROM api_tokens").fetchone()
        assert (
            row["revoked"] and not row["backend_password"] and not row["backend_hash"]
        )
        conn.execute("UPDATE users SET active=1 WHERE id=2")
    assert client.get("/api/v1/folders", headers=bearer(token)).status_code == 401


def test_oversized_and_nonobject_bodies(client, token):
    for value in ([], "text", 1):
        assert (
            client.post("/api/v1/send", headers=bearer(token), json=value).status_code
            == 400
        )
    result = client.post(
        "/api/v1/send", headers=bearer(token), json={"body": "a" * (2 * 1024 * 1024)}
    )
    assert result.status_code == 413
    assert "code" in result.json["error"]


def test_request_and_account_send_limits(app, client, token, mail):
    now = int(time.time())
    with db.connect(app.config["DATABASE"]) as conn:
        conn.execute(
            "INSERT INTO throttle VALUES(?,?,?)",
            ("api:" + token["token"]["id"], 120, now + 60),
        )
    result = client.get("/api/v1/folders", headers=bearer(token))
    assert result.status_code == 429 and result.headers["Retry-After"] == "60"
    with db.connect(app.config["DATABASE"]) as conn:
        conn.execute("DELETE FROM throttle")
        conn.execute("INSERT INTO throttle VALUES('send:2',60,?)", (now + 3600,))
    result = client.post(
        "/api/v1/send", headers=bearer(token), json={"to": "other@example.com"}
    )
    assert (
        result.status_code == 429
        and result.json["error"]["code"] == "send_rate_limited"
    )
    mail.send.assert_not_called()


def test_internal_auth_domain_is_reserved():
    with pytest.raises(ValueError, match="reserved"):
        db.domain("API.INVALID")
    with pytest.raises(ValueError, match="reserved"):
        db.address("api-123@api.invalid")


def test_completed_mail_mutations_keep_success_when_audit_fails(
    client, token, mail, monkeypatch
):
    import sqlite3

    def fail(*args):
        raise sqlite3.OperationalError("database busy")

    monkeypatch.setattr(db, "audit", fail)
    response = client.post(
        "/api/v1/send",
        headers=bearer(token),
        json={"to": "other@example.com", "body": "Hello"},
    )
    assert response.status_code == 202 and response.json["status"] == "accepted"
    assert "audit" in response.json["warning"]
    response = client.post(
        "/api/v1/drafts", headers=bearer(token), json={"subject": "Draft"}
    )
    assert response.status_code == 201 and "audit" in response.json["warning"]
    response = client.post(
        "/api/v1/messages/12/action",
        headers=bearer(token),
        json={"folder": "inbox", "uidvalidity": "42", "action": "read"},
    )
    assert response.status_code == 200 and "audit" in response.json["warning"]


@pytest.mark.parametrize("invalid", [[], {}, False, None, 0])
def test_invalid_reference_shapes_are_bounded_errors(client, token, invalid):
    response = client.post(
        "/api/v1/messages/12/action",
        headers=bearer(token),
        json={"folder": invalid, "uidvalidity": "42", "action": "trash"},
    )
    assert response.status_code == 400
    response = client.post(
        "/api/v1/messages/12/action",
        headers=bearer(token),
        json={"folder": "inbox", "uidvalidity": invalid, "action": "trash"},
    )
    assert response.status_code == 400


def test_get_accepts_json_content_type_without_body(client, token, mail):
    headers = {**bearer(token), "Content-Type": "application/json"}
    for path in (
        "/api/v1/folders",
        "/api/v1/messages",
        "/api/v1/messages/12?uidvalidity=42",
    ):
        assert client.get(path, headers=headers).status_code == 200


def test_token_html_submission_and_draft_keep_plain_alternative(client, token, mail):
    payload = {"to": "friend@example.org", "subject": "Account", "body": "Account update", "html": "<h1>Account update</h1>"}
    result = client.post("/api/v1/send", headers=bearer(token), json=payload)
    assert result.status_code == 202
    message = mail.send.call_args.args[2]
    assert message.get_body(preferencelist=("html",)).get_content().strip() == payload["html"]
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == payload["body"]
    result = client.post("/api/v1/drafts", headers=bearer(token), json=payload)
    assert result.status_code == 201
    message = mail.draft.call_args.args[2]
    assert message.get_body(preferencelist=("html",)).get_content().strip() == payload["html"]
    for extra in [{"body": ""}, {"html": []}, {"html": "a" * 1048577}]:
        assert client.post("/api/v1/send", headers=bearer(token), json={**payload, **extra}).status_code == 400
    assert mail.send.call_count == 1
