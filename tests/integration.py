"""Real SMTP, DKIM, IMAP, TLS and web app checks against a disposable local stack."""

import email
import http.cookiejar
import imaplib
import json
import smtplib
import socket
import ssl
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

import dkim

PASSWORD = "LabOnlyPassword123!"
ADMIN = "admin@example.test"
MEMBER = "member@example.test"
BASE = "https://127.0.0.1"
CTX = ssl.create_default_context(
    cafile="/etc/letsencrypt/live/mail.example.test/fullchain.pem"
)
# The certificate is valid for mail.example.test, but the test namespace uses loopback.
# Verify the trust chain here; separately check SAN/hostname explicitly below.
CTX.check_hostname = False
jar = http.cookiejar.CookieJar()
http = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=CTX)
)


def req(path, data=None, method=None, csrf=None):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    r = urllib.request.Request(
        BASE + "/api" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with http.open(r, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"{path}: HTTP {exc.code}: {exc.read().decode()}") from exc


def wait(port):
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), 0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"Port {port} did not become ready")


def smtp():
    s = smtplib.SMTP("127.0.0.1", 587, timeout=10)
    s.starttls(context=CTX)
    s.login(MEMBER, PASSWORD)
    return s


def expect_reject(fn, code):
    try:
        fn()
    except smtplib.SMTPResponseException as exc:
        assert exc.smtp_code == code, (exc.smtp_code, exc.smtp_error)
    except smtplib.SMTPRecipientsRefused as exc:
        assert all(v[0] == code for v in exc.recipients.values()), exc.recipients
    else:
        raise AssertionError("SMTP request unexpectedly succeeded")


def message(sender=MEMBER):
    m = EmailMessage()
    m["From"] = sender
    m["To"] = ADMIN
    m["Subject"] = "Morrow real-stack test"
    m.set_content("SMTP, DKIM and LMTP delivery work.")
    return m


for port in (25, 587, 993, 8000, 443, 1143, 1587, 8891, 8892):
    wait(port)
csrf = req("/session")["csrf"]
csrf = req("/login", {"email": ADMIN, "password": PASSWORD}, csrf=csrf)["csrf"]
req(
    "/admin/users",
    {"email": MEMBER, "name": "Member", "password": PASSWORD, "quota_mb": 64},
    csrf=csrf,
)
req("/admin/aliases", {"email": "hello@example.test", "destination": ADMIN}, csrf=csrf)
print("PASS web login, CSRF-protected account and alias creation")

with smtplib.SMTP("127.0.0.1", 25, timeout=10) as s:
    s.ehlo()
    assert not s.has_extn("auth")
    s.mail("outsider@outside.test")
    code, _ = s.rcpt("another@outside.test")
    assert code == 554, code
print("PASS port 25 relay denied; no authentication advertised")

with smtplib.SMTP("127.0.0.1", 587, timeout=10) as s:
    s.ehlo()
    assert not s.has_extn("auth")
    code, _ = s.mail(MEMBER)
    assert code == 530, code
print("PASS submission requires STARTTLS before login")

with smtp() as s:
    expect_reject(
        lambda: s.send_message(message(ADMIN), from_addr=ADMIN, to_addrs=[ADMIN]), 553
    )
with smtp() as s:
    expect_reject(
        lambda: s.send_message(message(ADMIN), from_addr=MEMBER, to_addrs=[ADMIN]), 550
    )
with smtp() as s:
    forged = message()
    forged["Sender"] = ADMIN
    expect_reject(
        lambda: s.send_message(forged, from_addr=MEMBER, to_addrs=[ADMIN]), 550
    )
print("PASS envelope spoof, visible From spoof and Sender-header spoof rejected")

users = req("/admin")["users"]
uid = next(u["id"] for u in users if u["email"] == MEMBER)
req(
    f"/admin/users/{uid}/identities",
    {"identities": ["hello@example.test"]},
    method="PUT",
    csrf=csrf,
)
with smtp() as s:
    assert not s.send_message(
        message("hello@example.test"),
        from_addr="hello@example.test",
        to_addrs=["hello@example.test"],
    )
print("PASS granted identity accepted and queued through alias")

raw = None
for _ in range(50):
    with imaplib.IMAP4_SSL("127.0.0.1", 993, ssl_context=CTX, timeout=10) as c:
        c.login(ADMIN, PASSWORD)
        c.select("INBOX")
        _, ids = c.uid("SEARCH", None, "ALL")
        if ids[0]:
            _, data = c.uid("FETCH", ids[0].split()[-1], "(BODY.PEEK[])")
            raw = next(r[1] for r in data if isinstance(r, tuple))
            break
    time.sleep(0.2)
assert raw, "Local SMTP message did not arrive"
parsed = email.message_from_bytes(raw)
assert parsed["From"] == "hello@example.test"
key = Path("/etc/morrow/dkim-public.txt").read_text().encode()
assert dkim.verify(raw, dnsfunc=lambda name, timeout=5: key), (
    "DKIM signature did not verify"
)
print(
    "PASS LMTP delivery, alias routing, IMAPS retrieval and cryptographic DKIM verification"
)

req(f"/admin/users/{uid}/identities", {"identities": []}, method="PUT", csrf=csrf)
with smtp() as s:
    expect_reject(
        lambda: s.send_message(
            message("hello@example.test"),
            from_addr="hello@example.test",
            to_addrs=[ADMIN],
        ),
        553,
    )
print("PASS revoked send-as permission immediately rejected by native SMTP")

# Webmail runs as the unprivileged morrow user and must authenticate over loopback.
result = req(
    "/send",
    {
        "to": MEMBER,
        "subject": "Webmail transport check",
        "body": "From the real application.",
    },
    csrf=csrf,
)
assert not result["warning"], result
sent = req("/messages?folder=sent")
assert sent["total"] >= 1
inbox = req("/messages?folder=inbox")
assert inbox["total"] >= 1
uid_msg = inbox["items"][0]["uid"]
read = req("/messages/" + uid_msg + "?folder=inbox")
assert "SMTP" in read["body"]
req(
    "/messages/" + uid_msg + "/action",
    {"folder": "inbox", "action": "archive"},
    csrf=csrf,
)
assert req("/messages?folder=archive")["total"] >= 1
print("PASS web send, Sent append, inbox list/read and archive over real IMAP")

boundary = "MorrowLabMultipartBoundary"
fields = {
    "from": ADMIN,
    "to": MEMBER,
    "bcc": ADMIN,
    "subject": "Saved draft",
    "body": "Keep this thought.",
}
parts = []
for name, value in fields.items():
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    )
parts.append(
    f'--{boundary}\r\nContent-Disposition: form-data; name="attachments"; filename="notes.txt"\r\nContent-Type: text/plain\r\n\r\nReal attachment bytes.\r\n--{boundary}--\r\n'.encode()
)
r = urllib.request.Request(
    BASE + "/api/drafts",
    data=b"".join(parts),
    headers={
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "X-CSRF-Token": csrf,
    },
)
with http.open(r, timeout=20) as response:
    assert response.status == 200
saved = req("/messages?folder=drafts")
assert saved["total"] == 1
saved_uid = saved["items"][0]["uid"]
saved_message = req("/messages/" + saved_uid + "?folder=drafts")
assert (
    saved_message["bcc"] == ADMIN
    and saved_message["attachments"][0]["name"] == "notes.txt"
)
part = saved_message["attachments"][0]["id"]
with http.open(
    BASE + f"/api/messages/{saved_uid}/attachments/{part}?folder=drafts", timeout=20
) as response:
    assert response.read() == b"Real attachment bytes."
    assert "attachment;" in response.headers["Content-Disposition"]
req(
    "/messages/" + saved_uid + "/action",
    {"folder": "drafts", "action": "trash"},
    csrf=csrf,
)
trash = req("/messages?folder=trash")
assert trash["total"] >= 1
req(
    "/messages/" + trash["items"][0]["uid"] + "/action",
    {"folder": "trash", "action": "delete"},
    csrf=csrf,
)
assert req("/messages?folder=trash")["total"] == 0
print(
    "PASS multipart draft save, Bcc preservation, attachment download, trash and UID-specific deletion"
)

req(f"/admin/users/{uid}", {"active": False}, method="PATCH", csrf=csrf)
try:
    with smtp():
        pass
except smtplib.SMTPAuthenticationError:
    pass
else:
    raise AssertionError("Disabled user could still authenticate")
print("PASS disabled account denied at Dovecot SASL authentication")

with socket.create_connection(("127.0.0.1", 993), timeout=10) as raw_sock:
    strict = ssl.create_default_context(
        cafile="/etc/letsencrypt/live/mail.example.test/fullchain.pem"
    )
    with strict.wrap_socket(raw_sock, server_hostname="mail.example.test") as tls:
        assert tls.version() in ("TLSv1.2", "TLSv1.3")
print("PASS certificate chain, hostname and modern TLS verified on IMAPS")

for port in (8000, 1143, 1587, 8891, 8892):
    import subprocess

    out = subprocess.check_output(["ss", "-H", "-ltn", f"sport = :{port}"], text=True)
    bindings = [line.split()[3] for line in out.splitlines()]
    assert bindings and all(value == "127.0.0.1:" + str(port) for value in bindings), (
        out
    )
print("PASS all private application/mail/filter listeners bound to loopback")
print("ALL REAL-STACK INTEGRATION CHECKS PASSED")
