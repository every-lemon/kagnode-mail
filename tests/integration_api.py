"""Imported by integration.py inside the disposable native mail stack."""

import imaplib
import json
import smtplib
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.fernet import Fernet


def run(env):
    req, csrf, member_id = env["req"], env["csrf"], env["uid"]
    member, admin = env["MEMBER"], env["ADMIN"]
    base, ctx = env["BASE"], env["CTX"]
    http = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    def issue(scopes, senders=(), expires_days=90):
        return req(
            "/admin/tokens",
            {
                "user_id": member_id,
                "label": "Native integration",
                "scopes": scopes,
                "senders": list(senders),
                "expires_days": expires_days,
            },
            csrf=csrf,
        )

    def api(
        secret,
        path,
        data=None,
        method=None,
        expected=200,
        raw=None,
        content_type="application/json",
    ):
        request = urllib.request.Request(
            base + "/api/v1" + path,
            data=raw
            if raw is not None
            else json.dumps(data).encode()
            if data is not None
            else None,
            headers={
                "Host": "mail.example.test",
                "Authorization": "Bearer " + secret,
                "Content-Type": content_type,
            },
            method=method,
        )
        try:
            response = http.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            assert response.status == expected, (
                path,
                response.status,
                response.read().decode(errors="replace"),
            )
            body = response.read()
            return (
                json.loads(body)
                if "application/json" in response.headers.get("Content-Type", "")
                else body
            )

    created = issue(["read", "send", "write", "delete"], [member], None)
    secret, token_id = created["secret"], created["token"]["id"]
    limited = issue(["read", "write"])["secret"]
    assert created["token"]["expires"] is None
    # The unprivileged web process encrypted this credential; inspect only inside this test lab.
    config = json.loads(Path("/etc/kagnode/config.json").read_text())
    with sqlite3.connect(config["DATABASE"]) as conn:
        encrypted = conn.execute(
            "SELECT backend_password FROM api_tokens WHERE id=?", (token_id,)
        ).fetchone()[0]
    backend = Fernet(config["CREDENTIAL_KEY"]).decrypt(encrypted.encode()).decode()
    login = "api-" + token_id + "@api.invalid"

    def public_rejects():
        password = backend
        try:
            with imaplib.IMAP4_SSL(
                "127.0.0.1", 993, ssl_context=ctx, timeout=30
            ) as client:
                client.login(login, password)
        except imaplib.IMAP4.error:
            pass
        else:
            raise AssertionError("API credential accepted on public IMAPS")
        try:
            with smtplib.SMTP("127.0.0.1", 587, timeout=30) as client:
                client.starttls(context=ctx)
                client.ehlo()
                # One explicit mechanism avoids smtplib retrying another mechanism;
                # allow Dovecot's deliberate increasing failure penalty to complete.
                client.auth(
                    "PLAIN", lambda challenge=None: "\0" + login + "\0" + password
                )
        except smtplib.SMTPAuthenticationError as exc:
            assert exc.smtp_code == 535, exc.smtp_code
        else:
            raise AssertionError("API credential accepted on public submission")
        print(
            "PASS internal token credential rejected by public IMAPS and submission, even on loopback"
        )

    assert api(secret, "/folders")["items"]
    assert (
        api(
            secret,
            "/send",
            {"to": member, "subject": "API transport check", "body": "Token API body."},
            expected=202,
        )["status"]
        == "accepted"
    )
    # Sending with a partially rejected local recipient still accepts the valid recipient exactly once.
    result = api(
        secret,
        "/send",
        {
            "to": admin + ", missing@example.test",
            "subject": "API partial recipients",
            "body": "One recipient.",
        },
        expected=202,
    )
    assert result["refused"] == ["missing@example.test"]
    assert (
        api(limited, "/send", {"to": admin}, expected=403)["error"]["code"]
        == "insufficient_scope"
    )
    for _ in range(50):
        listing = api(secret, "/messages?folder=inbox&q=API%20transport%20check")
        if listing["items"]:
            break
        time.sleep(0.2)
    assert listing["items"] and listing["uidvalidity"]
    item = listing["items"][0]
    reference = {"folder": "inbox", "uidvalidity": listing["uidvalidity"]}
    query = urllib.parse.urlencode(reference)
    item_path = "/messages/" + item["uid"]
    assert item["unread"]
    assert (
        api(secret, item_path + "?" + query)["body"].rstrip("\r\n") == "Token API body."
    )
    assert api(secret, "/messages?folder=inbox&q=API%20transport%20check")["items"][0][
        "unread"
    ]
    assert (
        api(secret, item_path + "?folder=inbox&uidvalidity=1", expected=409)["error"][
            "code"
        ]
        == "stale_mailbox"
    )
    api(secret, item_path + "/action", {**reference, "action": "star"})
    assert api(secret, "/messages?folder=inbox&q=API%20transport%20check")["items"][0][
        "starred"
    ]
    api(secret, item_path + "/action", {**reference, "action": "trash"})
    trash = api(secret, "/messages?folder=trash&q=API%20transport%20check")
    trash_ref = {
        "folder": "trash",
        "uidvalidity": trash["uidvalidity"],
        "action": "delete",
    }
    trash_path = "/messages/" + trash["items"][0]["uid"] + "/action"
    assert (
        api(limited, trash_path, trash_ref, expected=403)["error"]["code"]
        == "insufficient_scope"
    )
    api(secret, trash_path, trash_ref)
    assert not api(secret, "/messages?folder=trash&q=API%20transport%20check")["items"]
    print(
        "PASS bearer send/partial recipients, list/search/read, unchanged unread flag, stale refs, flags, trash and separate delete permission"
    )

    boundary = "KagNodeTokenLabMultipart"
    parts = []
    for name, value in {
        "from": member,
        "to": admin,
        "bcc": member,
        "subject": "API draft",
        "body": "API draft body.",
    }.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="attachments"; filename="api.txt"\r\nContent-Type: text/plain\r\n\r\nAPI attachment.\r\n--{boundary}--\r\n'.encode()
    )
    api(
        secret,
        "/drafts",
        raw=b"".join(parts),
        content_type="multipart/form-data; boundary=" + boundary,
        expected=201,
    )
    drafts = api(secret, "/messages?folder=drafts")
    draft = next(item for item in drafts["items"] if item["subject"] == "API draft")
    draft_query = urllib.parse.urlencode(
        {"folder": "drafts", "uidvalidity": drafts["uidvalidity"]}
    )
    draft_path = "/messages/" + draft["uid"]
    full = api(secret, draft_path + "?" + draft_query)
    assert full["bcc"] == member
    attachment = full["attachments"][0]
    assert (
        api(
            secret,
            draft_path + "/attachments/" + str(attachment["id"]) + "?" + draft_query,
        )
        == b"API attachment."
    )
    api(
        secret,
        "/drafts",
        {
            "draft_uid": draft["uid"],
            "uidvalidity": drafts["uidvalidity"],
            "to": admin,
            "subject": "API draft updated",
            "body": "Updated.",
        },
        expected=201,
    )
    assert (
        not api(secret, "/messages?folder=drafts&q=API%20draft")["items"][0]["subject"]
        == "API draft"
    )
    # The same multipart body also exercises real attachment submission and Sent append.
    accepted = api(
        secret,
        "/send",
        raw=b"".join(parts),
        content_type="multipart/form-data; boundary=" + boundary,
        expected=202,
    )
    assert accepted["warning"] is None
    assert api(secret, "/messages?folder=sent&q=API%20draft")["items"]
    print(
        "PASS API multipart draft/replace, Bcc, attachment download, attachment send and Sent copy"
    )

    req(
        f"/admin/users/{member_id}/identities",
        {"identities": ["hello@example.test"]},
        method="PUT",
        csrf=csrf,
    )
    assert (
        api(secret, "/send", {"from": "hello@example.test", "to": admin}, expected=403)[
            "error"
        ]["code"]
        == "forbidden"
    )
    with smtplib.SMTP("127.0.0.1", 1587, timeout=10) as client:
        client.login(login, backend)
        env["expect_reject"](
            lambda: client.send_message(
                env["message"]("hello@example.test"),
                from_addr="hello@example.test",
                to_addrs=[admin],
            ),
            553,
        )
    # Keep an authenticated SMTP connection open across token revocation and finish DATA.
    with smtplib.SMTP("127.0.0.1", 1587, timeout=10) as client:
        client.login(login, backend)
        # Public rejection tests run after successful operations and after this login.
        # Their auth-failure penalty must not interfere with a later successful login.
        public_rejects()
        assert client.mail(member)[0] == 250
        assert client.rcpt(admin)[0] == 250
        assert client.docmd("DATA")[0] == 354
        client.send(
            f"From: {member}\r\nTo: {admin}\r\nSubject: API revoked during DATA\r\n\r\nUploading.\r\n".encode()
        )
        time.sleep(0.2)
        req("/admin/tokens/" + token_id, method="DELETE", csrf=csrf)
        client.send(b".\r\n")
        assert client.getreply()[0] == 550
    assert api(secret, "/messages", expected=401)["error"]["code"] == "invalid_token"
    try:
        with imaplib.IMAP4("127.0.0.1", 1143, timeout=30) as client:
            client.login(login, backend)
    except imaplib.IMAP4.error:
        pass
    else:
        raise AssertionError("Revoked internal credential still authenticates")
    req(
        f"/admin/users/{member_id}/identities",
        {"identities": []},
        method="PUT",
        csrf=csrf,
    )
    metadata = req("/admin/tokens")
    assert all(
        "token_hash" not in t and "backend_password" not in t and "secret" not in t
        for t in metadata["tokens"]
    )
    assert next(t for t in metadata["tokens"] if t["id"] == token_id)["last_used"]
    print(
        "PASS per-token sender restrictions, token revocation during DATA, revoked API/private auth and secret-free metadata"
    )
