"""Fail-closed submission milter: bind the visible From to an authorized identity."""

import os
import sqlite3
import time
from contextlib import closing
from email import policy
from email.parser import Parser


def allowed_sender(path, login, envelope, headers):
    # One From mailbox only; reject malformed or duplicate headers and Sender spoofing.
    if len(headers) != 1 or len(headers[0].encode("utf-8")) > 8192:
        return False
    msg = Parser(policy=policy.default).parsestr("From: " + headers[0] + "\n\n")
    field = msg["From"]
    if len(msg.keys()) != 1 or field.defects or len(field.addresses) != 1:
        return False
    visible = field.addresses[0].addr_spec.lower()
    if visible != envelope.lower() or not login:
        return False
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM sender_owners WHERE email=? AND owner=?",
                (visible, login.lower()),
            ).fetchone()
            is not None
        )


def run():
    import Milter

    path = os.environ.get("KAGNODE_DATABASE", "/var/lib/kagnode/directory.sqlite3")

    class SenderGuard(Milter.Base):
        def envfrom(self, sender, *args):
            self.envelope = sender.strip("<>").lower()
            self.login = (self.getsymval("{auth_authen}") or "").lower()
            self.headers = []
            self.bad_sender = False
            if not self.login:
                self.setreply("550", "5.7.1", "Authentication required")
                return Milter.REJECT
            return Milter.CONTINUE

        def header(self, name, value):
            if name.lower() == "from":
                if self.headers or len(value.encode("utf-8")) > 8192:
                    self.setreply(
                        "550", "5.7.1", "Exactly one bounded From header is required"
                    )
                    return Milter.REJECT
                self.headers.append(value)
            if name.lower() in ("sender", "resent-from", "resent-sender"):
                self.bad_sender = True
            return Milter.CONTINUE

        def eoh(self):
            try:
                if self.bad_sender or not allowed_sender(
                    path, self.login, self.envelope, self.headers
                ):
                    self.setreply(
                        "550",
                        "5.7.1",
                        "From must match an authorized envelope sender; Sender and Resent headers are not supported",
                    )
                    return Milter.REJECT
            except (sqlite3.Error, ValueError, AttributeError):
                self.setreply("451", "4.7.1", "Cannot verify sender permissions")
                return Milter.TEMPFAIL
            return Milter.CONTINUE

        def eom(self):
            try:
                # A slow DATA upload must not retain a grant revoked after EOH.
                if not allowed_sender(path, self.login, self.envelope, self.headers):
                    self.setreply(
                        "550", "5.7.1", "Sender permission is no longer active"
                    )
                    return Milter.REJECT
                # Native SMTP clients share a per-account hourly cap; no IP-only bypass.
                with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
                    conn.execute(
                        "DELETE FROM throttle WHERE until < ?", (int(time.time()),)
                    )
                    conn.execute(
                        "INSERT INTO throttle VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1",
                        ("smtp:" + self.login, int(time.time()) + 3600),
                    )
                    count = conn.execute(
                        "SELECT count FROM throttle WHERE key=?",
                        ("smtp:" + self.login,),
                    ).fetchone()[0]
                if count > 60:
                    self.setreply(
                        "451", "4.7.1", "Hourly account sending limit reached"
                    )
                    return Milter.TEMPFAIL
            except (sqlite3.Error, ValueError, AttributeError):
                self.setreply("451", "4.7.1", "Rate limit service unavailable")
                return Milter.TEMPFAIL
            return Milter.CONTINUE

    Milter.factory = SenderGuard
    Milter.runmilter("kagnode-sender-guard", "inet:8892@127.0.0.1", 30)


if __name__ == "__main__":
    run()
