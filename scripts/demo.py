#!/usr/bin/env python3
"""Disposable, read-only preview. No network mail transport or production data."""

import argparse
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet

from kagnode import create_app, db


class DemoMail:
    def __init__(self):
        now = datetime.now(timezone.utc)
        samples = [
            (
                "Jamie Chen <jamie@northstudio.example>",
                "A small idea for a slower Friday",
                "Hey Alex,\n\nI’ve been thinking about what you said — making a little more space for the work that matters.\n\nWhat if we set aside Friday morning for the new studio project? No meetings, no notifications. Just coffee, a few good ideas, and a fresh page.\n\nI put a short outline together. Nothing too polished yet, but I think there’s something good in here.\n\nLet me know what you think.\n\nJamie\n\n—\nNorth Studio\nThoughtful things, made together.",
                True,
                True,
            ),
            (
                "Lena Park <lena@fieldnotes.example>",
                "The first chapter is ready",
                "Hi Alex,\n\nThe first chapter is ready for your eyes. I’d love to hear what you think about the opening.\n\nThere’s no rush. Give it a quiet afternoon when you have one.\n\nLena",
                True,
                False,
            ),
            (
                "Sam Rivera <sam@example.com>",
                "Re: A place for our ideas",
                "This feels just right.\n\nI added a few notes to the shared outline. We can start with the simplest version and see where it takes us.\n\nTalk soon,\nSam",
                False,
                False,
            ),
            (
                "The Sunday Edit <hello@sunday.example>",
                "A few things worth keeping",
                "A small collection for your weekend.\n\n01. Take the long way home.\n02. Make something with your hands.\n03. Write to someone you miss.\n\nSee you next Sunday.",
                False,
                False,
            ),
            (
                "Noah Williams <noah@garden.example>",
                "Dinner next week?",
                "Hey!\n\nIt’s been too long. Are you around for dinner next Thursday?\n\nI found a little place I think you’d love.\n\nNoah",
                False,
                False,
            ),
            (
                "Morgan Lee <morgan@paper.example>",
                "Your prints are on their way",
                "Hello Alex,\n\nYour prints are packed and on their way. Thank you for supporting independent makers.\n\nWe hope they make your space feel a little more like you.\n\nMorgan",
                False,
                False,
            ),
            (
                "KagNode Mail <hello@example.com>",
                "KagNode Mail setup",
                "KagNode Mail preview.\n\nManage accounts and send-as permissions in People & access. Open Domains & DNS for domain records.\n\nThis preview cannot send mail or change server settings.",
                False,
                False,
            ),
        ]
        self.items = []
        for i, (sender, subject, body, unread, starred) in enumerate(samples):
            self.items.append(
                dict(
                    uid=str(100 - i),
                    sender=sender,
                    to="Alex Morgan <alex@example.com>",
                    subject=subject,
                    body=body,
                    preview=body.split("\n\n")[1],
                    date=(now - timedelta(hours=i * 7)).isoformat(),
                    unread=unread,
                    starred=starred,
                    attachments=[],
                )
            )

    def list(self, user, password, folder, page=1, search=""):
        items = self.items if folder == "inbox" else []
        if search:
            items = [
                r
                for r in items
                if search.lower() in (r["sender"] + r["subject"] + r["body"]).lower()
            ]
        return dict(items=items, total=len(items), page=1, pages=1)

    def read(self, user, password, folder, uid):
        for r in self.items:
            if r["uid"] == uid:
                return r
        raise ValueError("Message not found.")

    def attachment(self, user, password, folder, uid, index):
        r = self.read(user, password, folder, uid)
        if index != "original":
            raise ValueError("Attachment not found.")
        return (
            "From: "
            + r["sender"]
            + "\nTo: "
            + r["to"]
            + "\nSubject: "
            + r["subject"]
            + "\n\n"
            + r["body"]
        ).encode(), "preview-message.eml"


def demo_app(path):
    db.init(path)
    with db.connect(path) as conn:
        conn.execute("INSERT INTO domains VALUES(?)", ("example.com",))
        conn.execute("INSERT INTO domains VALUES(?)", ("northstudio.example",))
        for email, name, admin in [
            ("alex@example.com", "Alex Morgan", 1),
            ("sam@example.com", "Sam Rivera", 0),
            ("lena@example.com", "Lena Park", 0),
        ]:
            conn.execute(
                "INSERT INTO users(email,name,password,admin) VALUES(?,?,?,?)",
                (email, name, db.password_hash("KagNodePreview2026!"), admin),
            )
        conn.execute(
            "INSERT INTO aliases VALUES(?,?)", ("hello@example.com", "alex@example.com")
        )
        conn.execute(
            "INSERT INTO aliases VALUES(?,?)", ("studio@example.com", "sam@example.com")
        )
        conn.execute("INSERT INTO grants VALUES(?,?)", (1, "hello@example.com"))
        db.audit(conn, "alex@example.com", "user.create", "sam@example.com")
        db.audit(conn, "alex@example.com", "alias.create", "hello@example.com")
    app = create_app(
        dict(
            DATABASE=str(path),
            SECRET_KEY="demo-" + Fernet.generate_key().decode(),
            CREDENTIAL_KEY=Fernet.generate_key().decode(),
            SESSION_COOKIE_SECURE=False,
            DEMO=True,
            MAIL_HOSTNAME="mail.example.com",
            PUBLIC_IPV4="203.0.113.10",
            DKIM_PUBLIC="v=DKIM1; k=rsa; p=YOUR_SERVER_GENERATED_PUBLIC_KEY",
        )
    )
    app.extensions["mail"] = DemoMail()
    return app


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8025)
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="kagnode-preview-") as tmp:
        app = demo_app(Path(tmp) / "preview.sqlite3")
        print(
            f"Preview: http://127.0.0.1:{a.port} · alex@example.com / KagNodePreview2026!"
        )
        app.run(host="127.0.0.1", port=a.port, debug=False, use_reloader=False)
