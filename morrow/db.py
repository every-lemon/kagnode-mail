"""Small, shared SQLite directory; no database daemon or mailbox access."""

import crypt
import hmac
import re
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=DELETE;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS domains (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 password TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, quota_mb INTEGER NOT NULL DEFAULT 1024,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS aliases (
 email TEXT PRIMARY KEY, destination TEXT NOT NULL REFERENCES users(email));
CREATE TABLE IF NOT EXISTS grants (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 email TEXT NOT NULL, PRIMARY KEY(user_id,email));
CREATE TABLE IF NOT EXISTS sessions (
 token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 password TEXT NOT NULL, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS throttle (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
CREATE INDEX IF NOT EXISTS throttle_expiry ON throttle(until);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
 target TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE VIEW IF NOT EXISTS sender_owners AS
 SELECT email, email AS owner FROM users WHERE active=1
 UNION SELECT g.email, u.email AS owner FROM grants g JOIN users u ON u.id=g.user_id WHERE u.active=1;
"""


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
        db.executescript(SCHEMA)


def domain(value):
    value = str(value).strip().lower()
    if len(value) > 253 or not re.fullmatch(
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value
    ):
        raise ValueError(
            "Use a full domain name, such as example.com (ASCII / punycode)."
        )
    return value


def address(value):
    value = str(value).strip().lower()
    if len(value) > 254 or value.count("@") != 1:
        raise ValueError("Enter a valid email address.")
    local, host = value.split("@")
    if (
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?", local)
        or ".." in local
    ):
        raise ValueError("Use letters, numbers, dots, +, - or _ in the email address.")
    return local + "@" + domain(host)


def password_hash(password):
    if (
        not isinstance(password, str)
        or not 12 <= len(password) <= 256
        or "\x00" in password
    ):
        raise ValueError("Use a password between 12 and 256 characters.")
    return "{SHA512-CRYPT}" + crypt.crypt(
        password, crypt.mksalt(crypt.METHOD_SHA512, rounds=100000)
    )


def verify(password, encoded):
    if not isinstance(password, str) or len(password) > 256 or "\x00" in password:
        return False
    raw = encoded.removeprefix("{SHA512-CRYPT}")
    return hmac.compare_digest(crypt.crypt(password, raw) or "", raw)


def identities(db, user_id):
    return [
        r[0]
        for r in db.execute(
            "SELECT email FROM sender_owners WHERE owner=(SELECT email FROM users WHERE id=?) ORDER BY email",
            (user_id,),
        )
    ]


def audit(db, actor, action, target):
    db.execute(
        "INSERT INTO audit(actor,action,target) VALUES(?,?,?)", (actor, action, target)
    )
    db.execute(
        "DELETE FROM audit WHERE id < (SELECT COALESCE(MAX(id),0)-10000 FROM audit)"
    )
