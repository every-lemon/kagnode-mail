import io

import pytest
from werkzeug.datastructures import FileStorage

from kagnode import db
from kagnode.guard import allowed_sender
from kagnode.mail import Mail, build_message

USER = {"id": 2, "email": "member@example.com"}


def test_bcc_not_in_headers():
    msg, sender, rcpt = build_message(
        {"to": "one@example.org", "bcc": "hidden@example.org", "body": "hello"},
        USER,
        [USER["email"]],
    )
    assert "hidden@example.org" not in msg.as_string()
    assert rcpt == ["one@example.org", "hidden@example.org"]


@pytest.mark.parametrize(
    "payload",
    [
        {"to": "one@example.org\r\nBcc:evil@example.org"},
        {"to": "one@example.org", "subject": "x\nFrom:evil@example.org"},
        {"to": "invalid"},
        {"to": ""},
    ],
)
def test_header_injection_and_bad_recipients(payload):
    with pytest.raises(ValueError):
        build_message(payload, USER, [USER["email"]])


def test_sender_authorization():
    with pytest.raises(PermissionError):
        build_message(
            {"from": "admin@example.com", "to": "one@example.org"},
            USER,
            [USER["email"]],
        )


def test_attachments_and_mime():
    file = FileStorage(io.BytesIO(b"%PDF-example"), filename="../../report.pdf")
    msg, _, _ = build_message(
        {"to": "one@example.org", "body": "Attached"}, USER, [USER["email"]], [file]
    )
    attachment = list(msg.iter_attachments())[0]
    assert attachment.get_filename() == "report.pdf"
    assert attachment.get_payload(decode=True) == b"%PDF-example"
    assert attachment.get_content_type() == "application/octet-stream"


def test_attachment_size_limit():
    file = FileStorage(io.BytesIO(b"x" * (16 * 1024 * 1024 + 1)), filename="big.bin")
    with pytest.raises(ValueError):
        build_message({"to": "one@example.org"}, USER, [USER["email"]], [file])


def test_milter_guard_authorization_and_spoof(app):
    path = app.config["DATABASE"]
    assert allowed_sender(
        path,
        "member@example.com",
        "member@example.com",
        ["Member <member@example.com>"],
    )
    for headers in [
        [],
        ["admin@example.com"],
        ["member@example.com", "member@example.com"],
        ["member@example.com, admin@example.com"],
        ["garbage"],
        ["member@example.com\nFrom: admin@example.com"],
    ]:
        assert not allowed_sender(
            path, "member@example.com", "member@example.com", headers
        )
    assert not allowed_sender(path, "", "member@example.com", ["member@example.com"])
    assert not allowed_sender(
        path, "member@example.com", "admin@example.com", ["admin@example.com"]
    )
    with db.connect(path) as c:
        c.execute("INSERT INTO grants VALUES(2,'hello@example.com')")
    assert allowed_sender(
        path, "member@example.com", "hello@example.com", ["Hello <hello@example.com>"]
    )
    with db.connect(path) as c:
        c.execute("UPDATE users SET active=0 WHERE id=2")
    assert not allowed_sender(
        path, "member@example.com", "hello@example.com", ["hello@example.com"]
    )


def test_message_uid_injection_rejected():
    mail = Mail({})
    with pytest.raises(ValueError):
        mail.raw(None, "1:*")
    with pytest.raises(ValueError):
        mail.action(USER, "pw", "inbox", "1 STORE +FLAGS \\Deleted", "trash")


def test_sent_copy_failure_does_not_report_send_failure(monkeypatch):
    class SMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, *a):
            pass

        def send_message(self, *a, **kw):
            return {}

    monkeypatch.setattr("kagnode.mail.smtplib.SMTP", SMTP)
    mail = Mail({"SMTP_HOST": "127.0.0.1", "SMTP_PORT": 1587})

    def fail(*a):
        raise OSError("IMAP unavailable")

    monkeypatch.setattr(mail, "imap", fail)
    msg, sender, rcpt = build_message({"to": "one@example.org"}, USER, [USER["email"]])
    result = mail.send(USER, "pw", msg, sender, rcpt)
    assert result["warning"] and "Do not resend" in result["warning"]


def test_html_only_mail_has_readable_text_without_active_content():
    from email.message import EmailMessage

    from kagnode.mail import message_text

    msg = EmailMessage()
    msg.set_content(
        '<html><head><style>body{display:none}</style></head><body><h1>Hello</h1><p>A real message.</p><script>alert(1)</script><img src="https://tracker.invalid/1"></body></html>',
        subtype="html",
    )
    text = message_text(msg)
    assert "Hello" in text and "A real message." in text
    assert (
        "alert" not in text
        and "tracker.invalid" not in text
        and "display:none" not in text
    )


def test_empty_draft_and_bcc_preserved():
    msg, _, _ = build_message(
        {"body": "An unfinished idea", "bcc": "hidden@example.org"},
        USER,
        [USER["email"]],
        draft=True,
    )
    assert msg["Bcc"] == "hidden@example.org"
    msg, _, _ = build_message(
        {"body": "No recipient yet"}, USER, [USER["email"]], draft=True
    )
    assert "No recipient yet" in msg.get_content()


def test_move_uses_post_login_capabilities(monkeypatch):
    calls = []

    class IMAP:
        capabilities = ("IMAP4REV1",)

        def __init__(self, *a, **kw):
            pass

        def login(self, *a):
            calls.append("login")

        def capability(self):
            calls.append("capability")
            return "OK", [b"IMAP4rev1 MOVE UIDPLUS"]

        def select(self, *a):
            return "OK", []

        def create(self, *a):
            return "OK", []

        def uid(self, *a):
            calls.append(a)
            return "OK", []

        def logout(self):
            pass

    monkeypatch.setattr("kagnode.mail.imaplib.IMAP4", IMAP)
    mail = Mail({"IMAP_HOST": "127.0.0.1", "IMAP_PORT": 1143})
    mail.action(USER, "pw", "inbox", "12", "archive")
    assert calls == ["login", "capability", ("MOVE", "12", "Archive")]


def test_unknown_charset_is_readable():
    from email import message_from_bytes, policy

    from kagnode.mail import message_text

    msg = message_from_bytes(
        b"Content-Type: text/plain; charset=unknown-broken-charset\r\n\r\nHello from an old client.",
        policy=policy.default,
    )
    assert message_text(msg) == "Hello from an old client."


def test_attached_email_download_is_not_empty(monkeypatch):
    from contextlib import contextmanager
    from email import message_from_bytes, policy
    from email.message import EmailMessage

    inner = EmailMessage()
    inner["From"] = "person@example.org"
    inner["Subject"] = "An attached conversation"
    inner.set_content("Keep the original conversation.")
    outer = EmailMessage()
    outer.set_content("See attached.")
    outer.add_attachment(inner, filename="conversation.eml")
    adapter = Mail({})

    @contextmanager
    def imap(*args):
        yield object()

    monkeypatch.setattr(adapter, "imap", imap)
    monkeypatch.setattr(adapter, "select", lambda *args: None)
    monkeypatch.setattr(adapter, "raw", lambda *args: outer)
    index = next(i for i, part in enumerate(outer.walk()) if part.get_filename())
    content, filename = adapter.attachment(USER, "pw", "inbox", "1", index)
    decoded = message_from_bytes(content, policy=policy.default)
    assert filename == "conversation.eml"
    assert decoded["Subject"] == "An attached conversation"
    assert "Keep the original conversation." in decoded.get_content()


def test_filename_sanitization_for_downloads_and_uploads():
    from kagnode.mail import safe_filename

    assert safe_filename("../../danger\r\nname\u202e.pdf") == "dangername.pdf"
    assert safe_filename("C:\\downloads\\report.pdf") == "report.pdf"
    assert safe_filename("..") == "attachment"
    assert safe_filename("\x00\x7f") == "attachment"


def test_attachment_count_is_bounded():
    files = [FileStorage(io.BytesIO(b"x"), filename=f"file-{i}.txt") for i in range(21)]
    with pytest.raises(ValueError, match="at most 20"):
        build_message({"to": "recipient@example.org"}, USER, [USER["email"]], files)


def test_mime_part_and_header_limits():
    from kagnode.mail import parse_message

    raw = b"Content-Type: multipart/mixed; boundary=parts\r\n\r\n"
    raw += b"--parts\r\nContent-Type: text/plain\r\n\r\nhello\r\n" * 201
    raw += b"--parts--\r\n"
    with pytest.raises(ValueError, match="too many MIME"):
        parse_message(raw)
    with pytest.raises(ValueError, match="headers are too large"):
        parse_message(b"Subject: " + b"x" * 65536 + b"\r\n\r\nbody")
    parsed = parse_message(b"Subject: Hello\r\n\r\nReadable mail.")
    assert parsed["Subject"] == "Hello"


def test_oversized_sender_header_is_rejected_before_parsing(app):
    assert not allowed_sender(
        app.config["DATABASE"],
        "member@example.com",
        "member@example.com",
        ["A" * 8193 + " <member@example.com>"],
    )


def test_milter_rechecks_grant_after_message_body(app, monkeypatch):
    import sys
    from types import SimpleNamespace

    from kagnode import guard

    class Base:
        def getsymval(self, name):
            return "member@example.com"

        def setreply(self, *args):
            self.reply = args

    fake = SimpleNamespace(
        Base=Base, CONTINUE=0, REJECT=1, TEMPFAIL=2, runmilter=lambda *args: None
    )
    monkeypatch.setitem(sys.modules, "Milter", fake)
    monkeypatch.setenv("KAGNODE_DATABASE", app.config["DATABASE"])
    with db.connect(app.config["DATABASE"]) as c:
        c.execute("INSERT INTO grants VALUES(2,'hello@example.com')")
    guard.run()
    instance = fake.factory()
    assert instance.envfrom("<hello@example.com>") == fake.CONTINUE
    assert instance.header("From", "hello@example.com") == fake.CONTINUE
    assert instance.eoh() == fake.CONTINUE
    with db.connect(app.config["DATABASE"]) as c:
        c.execute("DELETE FROM grants WHERE user_id=2")
    assert instance.eom() == fake.REJECT
    assert instance.reply[0] == "550"
