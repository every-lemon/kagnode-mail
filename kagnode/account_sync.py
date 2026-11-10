"""Optional read-only KagNode profile linking; local authentication remains independent."""

from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class AccountLinkError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AccountLinkError(
            "The KagNode account endpoint redirected unexpectedly.", 502
        )


class ProfileLinks:
    def __init__(self, directory, client, issuer=None, transport=None, clock=time.time):
        self.client = client
        self.issuer = (
            issuer
            or os.environ.get("KAGNODE_ACCOUNT_ISSUER", "https://nebulacoder.com")
        ).rstrip("/")
        parsed = urlsplit(self.issuer)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("KagNode Account issuer must be an HTTPS origin")
        self.clock = clock
        self.transport = transport or self.request
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise ValueError("Account-link storage must be a private directory")
        directory.chmod(0o700)
        self.path = directory / "profiles.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Account-link database must not be a symlink")
        if not self.path.exists():
            try:
                fd = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                os.close(fd)
            except FileExistsError:
                if self.path.is_symlink() or not self.path.is_file():
                    raise ValueError("Account-link storage is not a regular file")
        self.path.chmod(0o600)
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 1,generation INTEGER NOT NULL DEFAULT 1);
            INSERT OR IGNORE INTO policy(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS links(local_id TEXT PRIMARY KEY,subject TEXT NOT NULL UNIQUE,profile TEXT NOT NULL,refresh TEXT NOT NULL,updated REAL NOT NULL,busy REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pending(local_id TEXT PRIMARY KEY,device TEXT NOT NULL,user_code TEXT NOT NULL,verification TEXT NOT NULL,expires REAL NOT NULL,polled REAL NOT NULL DEFAULT 0,interval REAL NOT NULL DEFAULT 5,generation TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS link_epochs(local_id TEXT PRIMARY KEY,epoch INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS issuer_identity(id INTEGER PRIMARY KEY CHECK(id=1),issuer TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revocation_queue(token TEXT PRIMARY KEY,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,created REAL NOT NULL);""")

        with self.db() as db:
            stored = db.execute(
                "SELECT issuer FROM issuer_identity WHERE id=1"
            ).fetchone()
            if stored and stored[0] != self.issuer:
                db.execute("DELETE FROM links")
                db.execute("DELETE FROM pending")
                db.execute("DELETE FROM revocation_queue")
                db.execute(
                    "UPDATE policy SET enabled=0,generation=generation+1 WHERE id=1"
                )
                db.execute(
                    "INSERT INTO audit(actor,action,created) VALUES(?,?,?)",
                    ("configuration", "issuer.changed", self.clock()),
                )
            db.execute(
                "INSERT OR REPLACE INTO issuer_identity VALUES(1,?)", (self.issuer,)
            )

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def request(self, method, endpoint, data=None, token=None):
        headers = {
            "Accept": "application/json",
            "User-Agent": self.client + "/profile-sync",
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        request = Request(
            self.issuer + "/api/v1/account-sync/" + endpoint,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            try:
                response = build_opener(NoRedirect).open(request, timeout=8)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise AccountLinkError(
                        "The KagNode account response exceeded its size limit.", 502
                    )
                if response.status == 204:
                    return {}
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("Invalid response")
                if response.status >= 400 and "error" not in value:
                    raise ValueError("Failed response")
                return value
        except (
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            if isinstance(error, AccountLinkError):
                raise
            raise AccountLinkError(
                "KagNode Account could not be reached. Your local account is available.",
                502,
            ) from error

    def configured_off(self):
        with self.db() as db:
            stored = db.execute(
                "SELECT issuer FROM issuer_identity WHERE id=1"
            ).fetchone()
        return os.environ.get("KAGNODE_ACCOUNT_SYNC", "on").lower() in {
            "off",
            "false",
            "0",
        } or bool(stored and stored[0] != self.issuer)

    def enabled(self):
        with self.db() as db:
            row = db.execute(
                "SELECT enabled,generation FROM policy WHERE id=1"
            ).fetchone()
        return bool(row["enabled"]) and not self.configured_off(), row["generation"]

    def stamp(self, uid):
        with self.db() as db:
            generation = db.execute(
                "SELECT generation FROM policy WHERE id=1"
            ).fetchone()[0]
            row = db.execute(
                "SELECT epoch FROM link_epochs WHERE local_id=?", (str(uid),)
            ).fetchone()
        return str(generation) + ":" + str(row[0] if row else 0)

    def bump(self, uid):
        with self.db() as db:
            db.execute(
                "INSERT INTO link_epochs VALUES(?,1) ON CONFLICT(local_id) DO UPDATE SET epoch=epoch+1",
                (str(uid),),
            )
        return self.stamp(uid)

    def profile(self, value):
        if (
            value.get("issuer") != self.issuer
            or not isinstance(value.get("subject"), str)
            or not 1 <= len(value["subject"]) <= 200
        ):
            raise AccountLinkError(
                "The KagNode account profile could not be verified.", 502
            )
        name, photo = value.get("name"), value.get("photo", "")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 100
            or re.search(r"[\x00-\x1f]", name)
        ):
            raise AccountLinkError("The KagNode display name is invalid.", 502)
        if (
            not isinstance(photo, str)
            or len(photo) > 180000
            or photo
            and not re.fullmatch(r"data:image/webp;base64,[A-Za-z0-9+/=]+", photo)
        ):
            raise AccountLinkError("The KagNode profile photo is invalid.", 502)
        email = value.get("email", "") if value.get("email_verified") is True else ""
        if (
            not isinstance(email, str)
            or len(email) > 254
            or any(ord(char) < 32 or ord(char) == 127 for char in email)
            or (email and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email))
        ):
            raise AccountLinkError("The verified KagNode email is invalid.", 502)
        return {
            "email": email,
            "email_verified": bool(email),
            "issuer": self.issuer,
            "subject": value["subject"],
            "name": name,
            "photo": photo,
        }

    def state(self, uid, name, admin=False, refresh=True):
        enabled, _ = self.enabled()
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM links WHERE local_id=?", (str(uid),)
            ).fetchone()
        warning = ""
        if (
            enabled
            and row
            and refresh
            and row["refresh"]
            and self.clock() - row["updated"] > 900
        ):
            try:
                self.refresh(uid)
            except AccountLinkError as error:
                warning = str(error)
            with self.db() as db:
                row = db.execute(
                    "SELECT * FROM links WHERE local_id=?", (str(uid),)
                ).fetchone()
        with self.db() as db:
            revocations = (
                db.execute("SELECT COUNT(*) FROM revocation_queue").fetchone()[0]
                if admin
                else 0
            )
            pending = db.execute(
                "SELECT user_code,verification,expires,interval,generation FROM pending WHERE local_id=? AND expires>? AND generation=?",
                (str(uid), self.clock(), self.stamp(uid)),
            ).fetchone()
        connected = bool(enabled and row and row["refresh"])
        pending = pending if enabled and not connected else None
        return {
            "pending": bool(pending),
            **(
                {
                    "user_code": pending["user_code"],
                    "verification_uri": pending["verification"],
                    "expires_in": max(1, math.ceil(pending["expires"] - self.clock())),
                    "interval": pending["interval"],
                    "attempt_id": pending["generation"],
                }
                if pending
                else {}
            ),
            "revocations_pending": revocations,
            "enabled": enabled,
            "admin": bool(admin),
            "connected": connected,
            "profile": json.loads(row["profile"]) if enabled and row else None,
            "local_name": str(name),
            "warning": warning,
            "configured_off": self.configured_off(),
        }

    def handle(self, uid, name, admin, action="state", data=None, installation=""):
        if action.startswith("updates."):
            if not admin:
                raise AccountLinkError("Local administrator access is required.", 403)
            if __package__:
                from .ecosystem_updates import manage_update, UpdateError
            else:
                from ecosystem_updates import manage_update, UpdateError
            try:
                return manage_update(self.client, action.removeprefix("updates."), data)
            except UpdateError as error:
                raise AccountLinkError(str(error), 400) from error
        uid = str(uid)
        data = data or {}
        if action == "cancel":
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                pending = db.execute(
                    "SELECT generation FROM pending WHERE local_id=?", (uid,)
                ).fetchone()
                if (
                    not pending
                    or pending[0] != data.get("attempt_id")
                    or pending[0] != self.stamp(uid)
                ):
                    raise AccountLinkError(
                        "The account-link attempt changed. Reload its status.", 409
                    )
                db.execute(
                    "INSERT INTO link_epochs VALUES(?,1) ON CONFLICT(local_id) DO UPDATE SET epoch=epoch+1",
                    (uid,),
                )
                db.execute("DELETE FROM pending WHERE local_id=?", (uid,))
                db.execute(
                    "INSERT INTO audit(actor,action,created) VALUES(?,?,?)",
                    (uid, "profile.cancel", self.clock()),
                )
            return self.state(uid, name, admin, False)
        if action == "retry_disconnections":
            if not admin:
                raise AccountLinkError("Local administrator access is required.", 403)
            self.flush_revocations(2)
            return self.state(uid, name, admin, False)
        if action == "policy":
            if not admin:
                raise AccountLinkError("Local administrator access is required.", 403)
            if type(data.get("enabled")) is not bool:
                raise AccountLinkError("Choose whether account sync is enabled.")
            if data["enabled"] and self.configured_off():
                raise AccountLinkError(
                    "Account sync is disabled in this installation’s configuration.",
                    403,
                )
            with self.db() as db:
                tokens = (
                    [
                        r[0]
                        for r in db.execute(
                            'SELECT refresh FROM links WHERE refresh!=""'
                        )
                    ]
                    if not data["enabled"]
                    else []
                )
                db.execute(
                    "UPDATE policy SET enabled=?,generation=generation+1 WHERE id=1",
                    (int(data["enabled"]),),
                )
                db.execute("DELETE FROM pending")
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sign_in_attempts'"
                ).fetchone():
                    for attempt in db.execute(
                        "SELECT tokens FROM sign_in_attempts WHERE tokens!=''"
                    ).fetchall():
                        refresh = json.loads(attempt[0]).get("refresh_token")
                        if refresh:
                            tokens.append(refresh)
                    db.execute("DELETE FROM sign_in_attempts")
                for token in tokens:
                    db.execute(
                        "INSERT OR IGNORE INTO revocation_queue VALUES(?,?)",
                        (token, self.clock()),
                    )
                if not data["enabled"]:
                    db.execute("DELETE FROM links")
                db.execute(
                    "INSERT INTO audit(actor,action,created) VALUES(?,?,?)",
                    (
                        uid,
                        "policy.enable" if data["enabled"] else "policy.disable",
                        self.clock(),
                    ),
                )
            self.flush_revocations(1)
            return self.state(uid, name, admin, False)
        if action == "disconnect":
            self.bump(uid)
            with self.db() as db:
                row = db.execute(
                    "SELECT refresh FROM links WHERE local_id=?", (uid,)
                ).fetchone()
                db.execute("DELETE FROM pending WHERE local_id=?", (uid,))
                db.execute("DELETE FROM links WHERE local_id=?", (uid,))
                if row and row[0]:
                    db.execute(
                        "INSERT OR IGNORE INTO revocation_queue VALUES(?,?)",
                        (row[0], self.clock()),
                    )
                db.execute(
                    "INSERT INTO audit(actor,action,created) VALUES(?,?,?)",
                    (uid, "profile.disconnect", self.clock()),
                )
            self.flush_revocations(1)
            return self.state(uid, name, admin, False)
        enabled, generation = self.enabled()
        if action == "state":
            return self.state(uid, name, admin)
        if not enabled:
            raise AccountLinkError(
                "KagNode Account sync is disabled for this installation.", 403
            )
        if action == "start":
            expected_stamp = self.bump(uid)
            result = self.transport(
                "POST",
                "device",
                {
                    "client_id": self.client,
                    "installation": installation,
                    "scope": "profile.read email.read",
                },
            )
            if result.get("error"):
                raise AccountLinkError(
                    "The KagNode account connection could not be started.", 502
                )
            if not re.fullmatch(
                r"[A-Za-z0-9_-]{40,200}", str(result.get("device_code", ""))
            ) or not re.fullmatch(
                r"[A-F0-9]{5}-[A-F0-9]{5}", str(result.get("user_code", ""))
            ):
                raise AccountLinkError(
                    "The KagNode account connection response is invalid.", 502
                )
            verification = result.get("verification_uri_complete", "")
            if not verification.startswith(self.issuer + "/account/apps/device?code="):
                raise AccountLinkError(
                    "The KagNode verification address is invalid.", 502
                )
            if (
                self.enabled() != (True, generation)
                or self.stamp(uid) != expected_stamp
            ):
                raise AccountLinkError("Account sync settings changed. Try again.", 409)
            with self.db() as db:
                db.execute(
                    "INSERT OR REPLACE INTO pending VALUES(?,?,?,?,?,0,5,?)",
                    (
                        uid,
                        result["device_code"],
                        result["user_code"],
                        verification,
                        self.clock() + 600,
                        expected_stamp,
                    ),
                )
            return self.state(uid, name, admin, False)
        if action == "poll":
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM pending WHERE local_id=?", (uid,)
                ).fetchone()
                if (
                    not row
                    or row["expires"] < self.clock()
                    or str(row["generation"]) != self.stamp(uid)
                ):
                    raise AccountLinkError(
                        "The account-link code expired. Start again.", 410
                    )
                if self.clock() - row["polled"] < row["interval"]:
                    return {"pending": True, "interval": row["interval"]}
                db.execute(
                    "UPDATE pending SET polled=? WHERE local_id=?", (self.clock(), uid)
                )
            result = self.transport(
                "POST",
                "token",
                {
                    "client_id": self.client,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": row["device"],
                },
            )
            if result.get("error") in {"authorization_pending", "slow_down"}:
                interval = row["interval"] + (
                    5 if result["error"] == "slow_down" else 0
                )
                with self.db() as db:
                    db.execute(
                        "UPDATE pending SET interval=? WHERE local_id=? AND generation=?",
                        (interval, uid, row["generation"]),
                    )
                return {"pending": True, "interval": interval}
            if result.get("error"):
                raise AccountLinkError(
                    "The account link was denied or expired. Start again.", 410
                )
            self.save(uid, result, str(row["generation"]))
            with self.db() as db:
                db.execute(
                    "DELETE FROM pending WHERE local_id=? AND generation=?",
                    (uid, row["generation"]),
                )
            return self.state(uid, name, admin, False)
        if action == "refresh":
            self.refresh(uid, force=True)
            return self.state(uid, name, admin, False)
        raise AccountLinkError("Unknown account-link action.")

    def save(self, uid, result, expected_stamp):
        for key in ["access_token", "refresh_token"]:
            if not re.fullmatch(r"[A-Za-z0-9_-]{40,200}", str(result.get(key, ""))):
                raise AccountLinkError("Invalid KagNode account token response.", 502)
        try:
            profile = self.profile(
                self.transport(
                    "GET",
                    "profile?" + urlencode({"client_id": self.client}),
                    token=result["access_token"],
                )
            )
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                policy = db.execute(
                    "SELECT enabled,generation FROM policy WHERE id=1"
                ).fetchone()
                if (
                    not policy["enabled"]
                    or not self.enabled()[0]
                    or self.stamp(uid) != expected_stamp
                ):
                    raise AccountLinkError(
                        "Account sync settings changed. Try again.", 409
                    )
                other = db.execute(
                    "SELECT local_id FROM links WHERE subject=? AND local_id!=?",
                    (profile["subject"], str(uid)),
                ).fetchone()
                current = db.execute(
                    "SELECT subject FROM links WHERE local_id=?", (str(uid),)
                ).fetchone()
                if other or current and current[0] != profile["subject"]:
                    raise AccountLinkError(
                        "Disconnect the previous account link before linking a different account.",
                        409,
                    )
                db.execute(
                    "INSERT OR REPLACE INTO links VALUES(?,?,?,?,?,0)",
                    (
                        str(uid),
                        profile["subject"],
                        json.dumps(profile),
                        result["refresh_token"],
                        self.clock(),
                    ),
                )
                db.execute(
                    "INSERT INTO audit(actor,action,created) VALUES(?,?,?)",
                    (str(uid), "profile.sync", self.clock()),
                )
        except Exception:
            # Persist cleanup after releasing the transaction; the issuer may be offline.
            with self.db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO revocation_queue VALUES(?,?)",
                    (result["refresh_token"], self.clock()),
                )
            self.flush_revocations(1)
            raise

    def refresh(self, uid, force=False):
        expected_stamp = self.stamp(uid)
        enabled, generation = self.enabled()
        if not enabled:
            return
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM links WHERE local_id=?", (str(uid),)
            ).fetchone()
            if (
                not row
                or not row["refresh"]
                or row["busy"] > self.clock()
                or not force
                and self.clock() - row["updated"] <= 900
            ):
                return
            db.execute(
                "UPDATE links SET busy=? WHERE local_id=?",
                (self.clock() + 25, str(uid)),
            )
        try:
            result = self.transport(
                "POST",
                "token",
                {
                    "client_id": self.client,
                    "grant_type": "refresh_token",
                    "refresh_token": row["refresh"],
                },
            )
            if result.get("error"):
                with self.db() as db:
                    db.execute(
                        'UPDATE links SET refresh="" WHERE local_id=? AND refresh=?',
                        (str(uid), row["refresh"]),
                    )
                raise AccountLinkError(
                    "Reconnect your KagNode Account to resume profile sync.", 401
                )
            self.save(uid, result, expected_stamp)
        finally:
            with self.db() as db:
                db.execute("UPDATE links SET busy=0 WHERE local_id=?", (str(uid),))

    def revoke(self, token):
        if token:
            try:
                self.transport(
                    "POST", "revoke", {"client_id": self.client, "token": token}
                )
                return True
            except AccountLinkError:
                return False
        return True

    def flush_revocations(self, limit):
        with self.db() as db:
            tokens = [
                row[0]
                for row in db.execute(
                    "SELECT token FROM revocation_queue ORDER BY created LIMIT ?",
                    (limit,),
                )
            ]
        for token in tokens:
            if self.revoke(token):
                with self.db() as db:
                    db.execute("DELETE FROM revocation_queue WHERE token=?", (token,))
