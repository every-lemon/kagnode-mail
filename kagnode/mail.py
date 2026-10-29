"""IMAP/SMTP adapter. Only connects to private loopback listeners."""

import email
import imaplib
import re
import smtplib
import time
from contextlib import contextmanager
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from html.parser import HTMLParser

from .db import address

FOLDERS = {
    "inbox": "INBOX",
    "sent": "Sent",
    "drafts": "Drafts",
    "archive": "Archive",
    "trash": "Trash",
    "junk": "Junk",
}
MAX_MESSAGE = 25 * 1024 * 1024
MAX_ATTACHMENTS = 20
MAX_MIME_PARTS = 200


def safe_filename(value):
    """Treat names from received mail as untrusted download metadata."""
    name = str(value or "attachment").replace("\\", "/").split("/")[-1]
    # Remove controls, bidi overrides and other invisible formatting characters.
    import unicodedata

    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    return name[:200].strip(" .") or "attachment"


def parse_message(raw):
    """Bound object creation for hostile MIME trees before parsing the body."""
    if len(raw) > MAX_MESSAGE:
        raise ValueError("Message too large for webmail. Use an IMAP client.")
    header_end = re.search(rb"\r?\n\r?\n", raw[:65540])
    if (header_end.start() if header_end else len(raw)) > 65536:
        raise ValueError("Message headers are too large for webmail.")
    count = 0

    def factory(*args, **kwargs):
        nonlocal count
        count += 1
        if count > MAX_MIME_PARTS:
            raise ValueError(
                "Message has too many MIME parts for webmail. Use an IMAP client."
            )
        return EmailMessage(*args, **kwargs)

    return email.message_from_bytes(
        raw, policy=policy.default.clone(message_factory=factory)
    )


def recipients(value):
    if not value.strip():
        return []
    if "\r" in value or "\n" in value:
        raise ValueError("Recipients must be on one line.")
    result = [address(v) for _, v in getaddresses([value])]
    if not result and value.strip():
        raise ValueError("Enter a valid recipient.")
    if len(result) > 50:
        raise ValueError("Send to at most 50 recipients at a time.")
    return list(dict.fromkeys(result))


def build_message(data, user, allowed, attachments=(), draft=False):
    if any(
        not isinstance(data.get(k, ""), str)
        for k in ("from", "to", "cc", "bcc", "subject", "body")
    ):
        raise ValueError("Message fields must be text.")
    sender = address(data.get("from", user["email"]))
    if sender not in allowed:
        raise PermissionError("You do not have permission to send from this address.")
    to, cc, bcc = (recipients(data.get(k, "")) for k in ("to", "cc", "bcc"))
    targets = list(dict.fromkeys(to + cc + bcc))
    if (not targets and not draft) or len(targets) > 50:
        raise ValueError("Choose between 1 and 50 recipients.")
    subject = data.get("subject", "").strip()
    if any(c in subject for c in "\r\n") or len(subject) > 300:
        raise ValueError("Subject must be a single line of at most 300 characters.")
    body = data.get("body", "")
    if len(body.encode()) > 1024 * 1024:
        raise ValueError("Message text is too large.")
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = sender
    msg["To"] = ", ".join(to) if to else "undisclosed-recipients:;"
    if cc:
        msg["Cc"] = ", ".join(cc)
    if draft and bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[1])
    msg.set_content(body)
    msg._kagnode_bcc = bcc
    total = 0
    for index, item in enumerate(attachments):
        if index >= MAX_ATTACHMENTS:
            raise ValueError("Attach at most 20 files per message.")
        content = item.read(16 * 1024 * 1024 + 1)
        total += len(content)
        if total > 16 * 1024 * 1024:
            raise ValueError("Attachments must total 16 MiB or less.")
        filename = safe_filename(item.filename)
        msg.add_attachment(
            content, maintype="application", subtype="octet-stream", filename=filename
        )
    if len(msg.as_bytes()) > MAX_MESSAGE:
        raise ValueError("Message exceeds the 25 MiB limit.")
    return msg, sender, targets


class PlainHTML(HTMLParser):
    """Readable HTML-only mail without active content, remote fetches, or tracking."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ("p", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def decode_text(part):
    raw = part.get_payload(decode=True) or b""
    try:
        return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def message_text(msg):
    part = msg.get_body(preferencelist=("plain",))
    if part:
        return decode_text(part)
    part = msg.get_body(preferencelist=("html",))
    if part:
        parser = PlainHTML()
        parser.feed(decode_text(part))
        return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.parts)).strip()
    return "(No readable message text.)"


class Mail:
    def __init__(self, config):
        self.config = config

    @contextmanager
    def imap(self, user, password):
        client = imaplib.IMAP4(
            self.config["IMAP_HOST"], self.config["IMAP_PORT"], timeout=15
        )
        try:
            client.login(user["email"], password)
            status, capabilities = client.capability()
            if status != "OK":
                raise imaplib.IMAP4.error(
                    "Could not read authenticated IMAP capabilities."
                )
            client.capabilities = tuple(capabilities[0].decode("ascii").upper().split())
            yield client
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def select(self, client, folder, readonly=False):
        if folder not in FOLDERS:
            raise ValueError("Unknown folder.")
        name = FOLDERS[folder]
        status, _ = client.select(name, readonly)
        if status != "OK":
            client.create(name)
            status, _ = client.select(name, readonly)
        if status != "OK":
            raise ValueError("Could not open this folder.")

    def list(self, user, password, folder, page=1, search=""):
        with self.imap(user, password) as c:
            self.select(c, folder, True)
            # Search is a quoted IMAP string; never interpolate raw protocol input.
            criteria = (
                ("TEXT", '"' + search.replace("\\", "\\\\").replace('"', '\\"') + '"')
                if search
                else ("ALL",)
            )
            if any(ord(ch) < 32 or ord(ch) > 126 for ch in search):
                raise ValueError("Mailbox search currently supports ASCII text.")
            status, data = c.uid("SEARCH", None, *criteria)
            if status != "OK":
                raise ValueError("Mailbox search failed.")
            uids = data[0].split()[::-1]
            items = []
            selected = uids[(page - 1) * 30 : page * 30]
            if selected:
                status, rows = c.uid(
                    "FETCH",
                    b",".join(selected),
                    "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])",
                )
                if status != "OK":
                    raise ValueError("Could not load messages.")
                for row in rows:
                    if not isinstance(row, tuple):
                        continue
                    meta, raw = row
                    msg = parse_message(raw)
                    uid = re.search(rb"UID (\d+)", meta)[1].decode()
                    items.append(
                        dict(
                            uid=uid,
                            sender=str(msg.get("From", "Unknown sender")),
                            to=str(msg.get("To", "")),
                            subject=str(msg.get("Subject", "(No subject)")),
                            date=str(msg.get("Date", "")),
                            unread=b"\\Seen" not in meta,
                            starred=b"\\Flagged" in meta,
                        )
                    )
            items.sort(key=lambda x: int(x["uid"]), reverse=True)
            return dict(
                items=items,
                total=len(uids),
                page=page,
                pages=max(1, (len(uids) + 29) // 30),
            )

    def raw(self, c, uid):
        if not re.fullmatch(r"[1-9][0-9]{0,9}", str(uid)):
            raise ValueError("Invalid message identifier.")
        status, rows = c.uid("FETCH", str(uid), "(RFC822.SIZE)")
        if status != "OK" or not rows or not rows[0]:
            raise ValueError("Message no longer exists.")
        meta = rows[0][0] if isinstance(rows[0], tuple) else rows[0]
        size = re.search(rb"RFC822.SIZE (\d+)", meta)
        if not size or int(size[1]) > MAX_MESSAGE:
            raise ValueError("Message too large for webmail. Use an IMAP client.")
        status, rows = c.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if status != "OK":
            raise ValueError("Could not read message.")
        for row in rows:
            if isinstance(row, tuple):
                return parse_message(row[1])
        raise ValueError("Message no longer exists.")

    def read(self, user, password, folder, uid):
        with self.imap(user, password) as c:
            self.select(c, folder)
            msg = self.raw(c, uid)
            c.uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Seen)")
            attachments = []
            for i, part in enumerate(msg.walk()):
                if (
                    part.get_filename()
                    or part.get_content_disposition() == "attachment"
                ):
                    attachments.append(
                        dict(id=i, name=safe_filename(part.get_filename()))
                    )
            return dict(
                uid=str(uid),
                sender=str(msg.get("From", "")),
                to=str(msg.get("To", "")),
                cc=str(msg.get("Cc", "")),
                bcc=str(msg.get("Bcc", "")),
                subject=str(msg.get("Subject", "(No subject)")),
                date=str(msg.get("Date", "")),
                body=message_text(msg),
                attachments=attachments,
            )

    def attachment(self, user, password, folder, uid, index):
        with self.imap(user, password) as c:
            self.select(c, folder, True)
            msg = self.raw(c, uid)
            if index == "original":
                return msg.as_bytes(), "message.eml"
            parts = list(msg.walk())
            i = int(index)
            if (
                i < 0
                or i >= len(parts)
                or not (
                    parts[i].get_filename()
                    or parts[i].get_content_disposition() == "attachment"
                )
            ):
                raise ValueError("Attachment not found.")
            part = parts[i]
            raw = part.get_payload(decode=True)
            if raw is None and part.get_content_type() == "message/rfc822":
                raw = b"\r\n".join(child.as_bytes() for child in part.get_payload())
            elif raw is None and part.is_multipart():
                raw = part.as_bytes()
            return raw or b"", safe_filename(part.get_filename())

    def action(self, user, password, folder, uid, action):
        if not re.fullmatch(r"[1-9][0-9]{0,9}", str(uid)):
            raise ValueError("Invalid message identifier.")
        with self.imap(user, password) as c:
            self.select(c, folder)
            if action in ("read", "unread", "star", "unstar"):
                flag = "\\Seen" if action in ("read", "unread") else "\\Flagged"
                result = c.uid(
                    "STORE",
                    uid,
                    "+FLAGS.SILENT" if action in ("read", "star") else "-FLAGS.SILENT",
                    f"({flag})",
                )
            elif action == "delete" and folder == "trash":
                if "UIDPLUS" not in c.capabilities:
                    raise ValueError("The mail server must support UIDPLUS.")
                result = c.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
                if result[0] == "OK":
                    result = c.uid("EXPUNGE", uid)
            elif action in ("archive", "trash", "inbox", "junk"):
                c.create(FOLDERS[action])
                if "MOVE" not in c.capabilities:
                    raise ValueError("The mail server must support IMAP MOVE.")
                result = c.uid("MOVE", uid, FOLDERS[action])
            else:
                raise ValueError("Unknown action.")
            if result[0] != "OK":
                raise ValueError("Message action failed.")

    def draft(self, user, password, msg, replace_uid=None):
        if replace_uid and not re.fullmatch(r"[1-9][0-9]{0,9}", str(replace_uid)):
            raise ValueError("Invalid draft identifier.")
        with self.imap(user, password) as c:
            self.select(c, "drafts")
            status, _ = c.append(
                "Drafts",
                "\\Draft",
                imaplib.Time2Internaldate(time.time()),
                msg.as_bytes(),
            )
            if status != "OK":
                raise ValueError("Could not save draft.")
            if replace_uid and "UIDPLUS" in c.capabilities:
                c.uid("STORE", str(replace_uid), "+FLAGS.SILENT", "(\\Deleted)")
                c.uid("EXPUNGE", str(replace_uid))

    def send(self, user, password, msg, sender, recipients):
        with smtplib.SMTP(
            self.config["SMTP_HOST"], self.config["SMTP_PORT"], timeout=25
        ) as s:
            s.login(user["email"], password)
            refused = s.send_message(msg, from_addr=sender, to_addrs=recipients)
        warning = None
        if getattr(msg, "_kagnode_bcc", None):
            msg["Bcc"] = ", ".join(msg._kagnode_bcc)
        try:
            with self.imap(user, password) as c:
                c.create("Sent")
                status, _ = c.append(
                    "Sent",
                    "\\Seen",
                    imaplib.Time2Internaldate(time.time()),
                    msg.as_bytes(),
                )
                if status != "OK":
                    raise ValueError("APPEND failed")
        except (OSError, imaplib.IMAP4.error, ValueError):
            warning = "Mail was accepted for delivery, but a Sent copy could not be saved. Do not resend."
        return dict(
            message="Accepted for delivery", warning=warning, refused=list(refused)
        )
