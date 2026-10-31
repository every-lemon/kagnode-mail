import io

import pytest
from werkzeug.datastructures import FileStorage

from kagnode import db
from kagnode.guard import allowed_sender
from kagnode.mail import Mail, build_message, recipients

USER = {"id": 2, "email": "member@example.com"}


@pytest.mark.parametrize("value, expected", [
    ("friend@example.org,", ["friend@example.org"]),
    ('"Last, First" <Friend@EXAMPLE.org>, other@example.org', ["Friend@example.org", "other@example.org"]),
    ("o'connor@example.org, _support@example.org, foo-@example.org", ["o'connor@example.org", "_support@example.org", "foo-@example.org"]),
    ('"a b"@example.org, friend+tag@example.org', ['"a b"@example.org', "friend+tag@example.org"]),
    ("friend@example.org, friend@example.org", ["friend@example.org"]),
])
def test_external_recipient_addresses(value, expected):
    msg, _, targets = build_message({"to": value}, USER, [USER["email"]])
    assert targets == expected
    assert [item.addr_spec for item in msg["To"].addresses] == expected


@pytest.mark.parametrize("value", [
    "friend@example.org, broken", "friend@example.org junk", "a@@example.org",
    "friend@example.org\r\nBcc: evil@example.org", "friend@example.org\x00",
    "fr\u200biend@example.org", "müller@example.org", "a..b@example.org",
    "friend@example.org; other@example.org", "a" * 65 + "@example.org",
    ",".join(f"person{i}@example.org" for i in range(51)),
])
def test_invalid_recipient_list_is_never_partially_accepted(value):
    with pytest.raises(ValueError):
        recipients(value)


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
def test_recipient_error_identifies_field(field):
    with pytest.raises(ValueError, match=f"^{field.capitalize()}: "):
        build_message({"to": "friend@example.org", field: "broken"}, USER, [USER["email"]])


def test_missing_sender_identifies_from_field():
    with pytest.raises(ValueError, match="^From:"):
        build_message({"from": "", "to": "friend@example.org"}, USER, [USER["email"]])


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


def test_stale_mailbox_rejected_before_read_or_write(monkeypatch):
    from contextlib import contextmanager
    from kagnode.mail import StaleMailbox

    class IMAP:
        def select(self, *args):
            return "OK", []

        def response(self, code):
            assert code == "UIDVALIDITY"
            return code, [b"555"]

        def uid(self, *args):
            pytest.fail("Stale reference reached a message command")

    mail = Mail({})

    @contextmanager
    def imap(*args):
        yield IMAP()

    monkeypatch.setattr(mail, "imap", imap)
    with pytest.raises(StaleMailbox):
        mail.read(USER, "pw", "inbox", "12", uidvalidity="554", mark_seen=False)
    with pytest.raises(StaleMailbox):
        mail.action(USER, "pw", "inbox", "12", "trash", uidvalidity="554")
    with pytest.raises(StaleMailbox):
        mail.attachment(USER, "pw", "inbox", "12", "original", uidvalidity="554")
    message, _, _ = build_message({}, USER, [USER["email"]], draft=True)
    with pytest.raises(StaleMailbox):
        mail.draft(USER, "pw", message, "12", uidvalidity="554")


def test_api_read_never_marks_message_seen(monkeypatch):
    from contextlib import contextmanager
    from email.message import EmailMessage

    class IMAP:
        def select(self, folder, readonly):
            assert readonly
            return "OK", []

        def response(self, code):
            return code, [b"555"]

        def uid(self, *args):
            pytest.fail("Read-only call attempted to update flags")

    mail = Mail({})

    @contextmanager
    def imap(*args):
        yield IMAP()

    msg = EmailMessage()
    msg.set_content("Unseen message")
    monkeypatch.setattr(mail, "imap", imap)
    monkeypatch.setattr(mail, "raw", lambda *args: msg)
    result = mail.read(USER, "pw", "inbox", "12", uidvalidity="555", mark_seen=False)
    assert result["uidvalidity"] == "555"
    assert result["folder"] == "inbox"


def test_missing_message_action_is_not_silent_success(monkeypatch):
    from contextlib import contextmanager
    from kagnode.mail import MessageNotFound

    class IMAP:
        def select(self, *args):
            return "OK", []

        def response(self, code):
            return code, [b"555"]

        def uid(self, *args):
            assert args == ("FETCH", "12", "(UID)")
            return "OK", [None]

    mail = Mail({})

    @contextmanager
    def imap(*args):
        yield IMAP()

    monkeypatch.setattr(mail, "imap", imap)
    with pytest.raises(MessageNotFound):
        mail.action(USER, "pw", "inbox", "12", "trash", uidvalidity="555")


@pytest.mark.parametrize("failure", ["submission", "quit", "quit_rejected", "login"])
def test_send_disconnect_outcome_is_explicit(monkeypatch, failure):
    import smtplib
    from kagnode.mail import SendUncertain

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if failure == "quit":
                raise smtplib.SMTPServerDisconnected("quit lost")
            if failure == "quit_rejected":
                raise smtplib.SMTPResponseException(500, b"quit rejected")

        def login(self, username, password):
            assert username == "api-test@api.invalid"
            if failure == "login":
                raise smtplib.SMTPServerDisconnected("login lost")

        def send_message(self, *args, **kwargs):
            if failure == "submission":
                raise smtplib.SMTPServerDisconnected("submission lost")
            return {"refused@example.org": (550, b"rejected")}

    monkeypatch.setattr("kagnode.mail.smtplib.SMTP", SMTP)
    mail = Mail({"SMTP_HOST": "127.0.0.1", "SMTP_PORT": 1587})

    def fail(*args):
        raise OSError("IMAP unavailable")

    monkeypatch.setattr(mail, "imap", fail)
    msg, sender, targets = build_message(
        {"to": "one@example.org"}, USER, [USER["email"]]
    )
    user = dict(USER, auth_username="api-test@api.invalid")
    if failure == "submission":
        with pytest.raises(SendUncertain):
            mail.send(user, "pw", msg, sender, targets)
    elif failure == "login":
        with pytest.raises(smtplib.SMTPServerDisconnected):
            mail.send(user, "pw", msg, sender, targets)
    else:
        result = mail.send(user, "pw", msg, sender, targets)
        assert result["status"] == "accepted"
        assert result["message_id"] == msg["Message-ID"]
        assert result["refused"] == ["refused@example.org"]
        assert "Do not resend" in result["warning"]


def test_saved_draft_reports_success_if_old_copy_removal_disconnects(monkeypatch):
    from contextlib import contextmanager

    class IMAP:
        capabilities = ("UIDPLUS",)

        def select(self, *args):
            return "OK", []

        def response(self, code):
            return code, [b"555"]

        def append(self, *args):
            return "OK", [b"[APPENDUID 555 13] Appended"]

        def uid(self, command, *args):
            if command == "FETCH":
                return "OK", [b"1 (UID 12)"]
            raise OSError("Connection lost after saving the new draft")

    mail = Mail({})

    @contextmanager
    def imap(*args):
        yield IMAP()

    monkeypatch.setattr(mail, "imap", imap)
    msg, _, _ = build_message({}, USER, [USER["email"]], draft=True)
    result = mail.draft(USER, "pw", msg, replace_uid="12", uidvalidity="555")
    assert result["uid"] == "13"
    assert result["uidvalidity"] == "555"
    assert result["warning"].startswith("Draft saved,")
