"""Browser-bound, fresh KagNode authorization; never a replacement for app policy.

Device/access/refresh secrets remain in the private profile database. Applications
must separately enforce local signup, role, MFA, mailbox and vault requirements.
"""

import hashlib
import hmac
import json
import math
import re
import secrets
from urllib.parse import urlencode

from .account_sync import AccountLinkError


class AccountSignIns:
    def __init__(self, profiles):
        self.profiles = profiles
        self.clock = profiles.clock
        with profiles.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS sign_in_attempts(
              id TEXT PRIMARY KEY,browser TEXT NOT NULL,nonce TEXT NOT NULL,
              installation TEXT NOT NULL,started REAL NOT NULL,expires REAL NOT NULL,
              generation INTEGER NOT NULL,status TEXT NOT NULL,device TEXT NOT NULL DEFAULT '',
              user_code TEXT NOT NULL DEFAULT '',verification TEXT NOT NULL DEFAULT '',
              polled REAL NOT NULL DEFAULT 0,interval REAL NOT NULL DEFAULT 5,
              profile TEXT NOT NULL DEFAULT '',tokens TEXT NOT NULL DEFAULT '',
              local_id TEXT NOT NULL DEFAULT '',link_stamp TEXT NOT NULL DEFAULT '');
              CREATE INDEX IF NOT EXISTS sign_in_browser ON sign_in_attempts(browser);
              CREATE TABLE IF NOT EXISTS sign_in_limits(key TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS sign_in_secret(id INTEGER PRIMARY KEY CHECK(id=1),secret TEXT NOT NULL);
            """)

        with profiles.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO sign_in_secret VALUES(1,?)",
                (secrets.token_hex(32),),
            )

    def csrf(self, browser):
        self.browser_key(browser)
        with self.profiles.db() as db:
            secret = db.execute(
                "SELECT secret FROM sign_in_secret WHERE id=1"
            ).fetchone()[0]
        return hmac.new(
            bytes.fromhex(secret), browser.encode(), hashlib.sha256
        ).hexdigest()

    def rate(self, source, maximum=12, seconds=900):
        key = hashlib.sha256(str(source).encode()).hexdigest()
        with self.profiles.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM sign_in_limits WHERE until<?", (self.clock(),))
            if (
                db.execute("SELECT COUNT(*) FROM sign_in_limits").fetchone()[0] >= 1024
                and not db.execute(
                    "SELECT 1 FROM sign_in_limits WHERE key=?", (key,)
                ).fetchone()
            ):
                raise AccountLinkError("Sign-in is busy. Try again later.", 429)
            db.execute(
                "INSERT INTO sign_in_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1",
                (key, self.clock() + seconds),
            )
            count = db.execute(
                "SELECT count FROM sign_in_limits WHERE key=?", (key,)
            ).fetchone()[0]
        if count > maximum:
            raise AccountLinkError("Too many sign-in requests. Try again later.", 429)

    def enabled(self):
        return self.profiles.enabled()[0]

    @staticmethod
    def browser_key(browser):
        if not isinstance(browser, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{32,200}", browser
        ):
            raise AccountLinkError("Reload the sign-in page and try again.", 403)
        return hashlib.sha256(browser.encode()).hexdigest()

    def forget(self, db, row):
        if row["tokens"]:
            token = json.loads(row["tokens"]).get("refresh_token")
            if token:
                db.execute(
                    "INSERT OR IGNORE INTO revocation_queue VALUES(?,?)",
                    (token, self.clock()),
                )
        db.execute("DELETE FROM sign_in_attempts WHERE id=?", (row["id"],))

    def cleanup(self):
        with self.profiles.db() as db:
            for row in db.execute(
                "SELECT * FROM sign_in_attempts WHERE expires<=? OR generation!=?",
                (self.clock(), self.profiles.enabled()[1]),
            ).fetchall():
                self.forget(db, row)

    def row(self, browser, attempt):
        enabled, generation = self.profiles.enabled()
        if not enabled:
            raise AccountLinkError(
                "KagNode Account is disabled for this installation. Use local sign-in.",
                403,
            )
        with self.profiles.db() as db:
            row = db.execute(
                "SELECT * FROM sign_in_attempts WHERE id=? AND browser=?",
                (attempt, self.browser_key(browser)),
            ).fetchone()
        if not row or row["expires"] <= self.clock() or row["generation"] != generation:
            raise AccountLinkError(
                "The KagNode sign-in attempt expired or changed. Start again.", 410
            )
        return row

    def public(self, row):
        return {
            "pending": row["status"] != "ready",
            "authorized": row["status"] == "ready",
            "attempt_id": row["id"],
            "user_code": row["user_code"],
            "verification_uri": row["verification"],
            "expires_in": max(1, math.ceil(row["expires"] - self.clock())),
            "interval": row["interval"],
        }

    def start(self, browser, installation):
        enabled, generation = self.profiles.enabled()
        if not enabled:
            raise AccountLinkError(
                "KagNode Account is disabled for this installation. Use local sign-in.",
                403,
            )
        browser_key = self.browser_key(browser)
        self.cleanup()
        attempt, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.profiles.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM sign_in_attempts WHERE browser=?", (browser_key,)
            ).fetchall()
            for row in previous:
                self.forget(db, row)
            if db.execute("SELECT COUNT(*) FROM sign_in_attempts").fetchone()[0] >= 128:
                raise AccountLinkError(
                    "Sign-in is busy. Try again shortly, or sign in locally.", 429
                )
            db.execute(
                "INSERT INTO sign_in_attempts(id,browser,nonce,installation,started,expires,generation,status) VALUES(?,?,?,?,?,?,?,'starting')",
                (
                    attempt,
                    browser_key,
                    nonce,
                    installation,
                    self.clock(),
                    self.clock() + 600,
                    generation,
                ),
            )
        # Cancellation is best-effort; the local attempt has already been invalidated.
        for row in previous:
            self.cancel_remote(row)
        value = {}
        try:
            value = self.profiles.transport(
                "POST",
                "device",
                {
                    "client_id": self.profiles.client,
                    "installation": installation,
                    "scope": "profile.read email.read",
                    "purpose": "sign_in",
                    "nonce": nonce,
                },
            )
            if (
                value.get("error")
                or not re.fullmatch(
                    r"[A-Za-z0-9_-]{40,200}", str(value.get("device_code", ""))
                )
                or not re.fullmatch(
                    r"[A-F0-9]{5}-[A-F0-9]{5}", str(value.get("user_code", ""))
                )
            ):
                raise AccountLinkError(
                    "KagNode sign-in could not be started. Try again or sign in locally.",
                    502,
                )
            uri = value.get("verification_uri_complete", "")
            if not isinstance(uri, str) or not uri.startswith(
                self.profiles.issuer + "/account/apps/device?code="
            ):
                raise AccountLinkError(
                    "KagNode returned an invalid confirmation address.", 502
                )
            self.row(browser, attempt)
            with self.profiles.db() as db:
                changed = db.execute(
                    "UPDATE sign_in_attempts SET status='pending',device=?,user_code=?,verification=? WHERE id=? AND status='starting'",
                    (value["device_code"], value["user_code"], uri, attempt),
                ).rowcount
            if not changed:
                self.cancel_remote({"device": value["device_code"]})
                raise AccountLinkError("The sign-in attempt changed. Start again.", 409)
            return self.public(self.row(browser, attempt))
        except Exception:
            if value.get("device_code"):
                self.cancel_remote({"device": value["device_code"]})
            with self.profiles.db() as db:
                db.execute("DELETE FROM sign_in_attempts WHERE id=?", (attempt,))
            raise

    def cancel_remote(self, row):
        if row["device"]:
            try:
                self.profiles.transport(
                    "POST",
                    "device/cancel",
                    {"client_id": self.profiles.client, "device_code": row["device"]},
                )
            except AccountLinkError:
                pass

    def cancel(self, browser, attempt):
        with self.profiles.db() as db:
            row = db.execute(
                "SELECT * FROM sign_in_attempts WHERE id=? AND browser=?",
                (attempt, self.browser_key(browser)),
            ).fetchone()
            if not row:
                return {"pending": False, "authorized": False}
            self.forget(db, row)
        self.cancel_remote(row)
        self.profiles.flush_revocations(1)
        return {"pending": False, "authorized": False}

    def validate(self, row, value):
        proof = value.get("authorization")
        if not isinstance(proof, dict):
            raise AccountLinkError(
                "KagNode did not return a fresh sign-in authorization.", 502
            )
        expected = {
            "issuer": self.profiles.issuer,
            "client_id": self.profiles.client,
            "installation": row["installation"],
            "purpose": "sign_in",
            "nonce": row["nonce"],
        }
        if any(proof.get(k) != v for k, v in expected.items()):
            raise AccountLinkError(
                "KagNode sign-in authorization did not match this browser attempt.", 502
            )
        for name in ["auth_time", "approved_at", "issued_at", "expires_at"]:
            if type(proof.get(name)) not in (int, float) or not math.isfinite(
                proof[name]
            ):
                raise AccountLinkError(
                    "KagNode sign-in authorization has invalid timing.", 502
                )
        now = self.clock()
        if not (
            0 < proof["auth_time"] <= proof["approved_at"] + 30
            and row["started"] - 30
            <= proof["approved_at"]
            <= proof["issued_at"]
            <= now + 30
            and now < proof["expires_at"] <= proof["issued_at"] + 120
        ):
            raise AccountLinkError(
                "KagNode sign-in authorization expired. Start again.", 410
            )
        for key in ["access_token", "refresh_token"]:
            if not re.fullmatch(r"[A-Za-z0-9_-]{40,200}", str(value.get(key, ""))):
                raise AccountLinkError(
                    "KagNode returned an invalid sign-in token response.", 502
                )
        scopes = str(value.get("scope", "")).split()
        if "profile.read" not in scopes:
            raise AccountLinkError(
                "KagNode did not grant the requested identity access.", 502
            )
        profile = self.profiles.profile(
            self.profiles.transport(
                "GET",
                "profile?" + urlencode({"client_id": self.profiles.client}),
                token=value["access_token"],
            )
        )
        if "email.read" not in scopes:
            profile["email"], profile["email_verified"] = "", False
        if profile["subject"] != proof.get("subject"):
            raise AccountLinkError(
                "KagNode sign-in identity did not match its authorization.", 502
            )
        return profile, proof["expires_at"]

    def status(self, browser, attempt):
        row = self.row(browser, attempt)
        if row["status"] == "ready":
            return self.public(row)
        if row["status"] == "polling":
            return self.public(row)
        if row["status"] != "pending":
            raise AccountLinkError("Sign-in is still starting. Try again shortly.", 409)
        with self.profiles.db() as db:
            db.execute("BEGIN IMMEDIATE")
            claimed = db.execute(
                "UPDATE sign_in_attempts SET status='polling',polled=? WHERE id=? AND status='pending' AND polled<=?",
                (self.clock(), attempt, self.clock() - row["interval"]),
            ).rowcount
        if not claimed:
            return self.public(self.row(browser, attempt))
        try:
            value = self.profiles.transport(
                "POST",
                "token",
                {
                    "client_id": self.profiles.client,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": row["device"],
                },
            )
        except Exception:
            # The exchange may have consumed the one-use authorization. A lost
            # response is not retried as though no exchange happened.
            with self.profiles.db() as db:
                db.execute("DELETE FROM sign_in_attempts WHERE id=?", (attempt,))
            self.cancel_remote(row)
            raise AccountLinkError(
                "KagNode could not confirm sign-in. Start again or sign in locally.",
                502,
            )
        if value.get("error") in ("authorization_pending", "slow_down"):
            with self.profiles.db() as db:
                db.execute(
                    "UPDATE sign_in_attempts SET status='pending',interval=? WHERE id=? AND status='polling'",
                    (
                        min(
                            30,
                            row["interval"]
                            + (5 if value["error"] == "slow_down" else 0),
                        ),
                        attempt,
                    ),
                )
            return self.public(self.row(browser, attempt))
        try:
            if value.get("error"):
                raise AccountLinkError(
                    "KagNode sign-in was denied or expired. Start again.", 410
                )
            profile, expires = self.validate(row, value)
            self.row(browser, attempt)
            with self.profiles.db() as db:
                linked = db.execute(
                    "SELECT local_id FROM links WHERE subject=?", (profile["subject"],)
                ).fetchone()
                local_id = linked[0] if linked else ""
                stamp = self.profiles.stamp(local_id) if local_id else ""
                changed = db.execute(
                    "UPDATE sign_in_attempts SET status='ready',profile=?,tokens=?,expires=?,local_id=?,link_stamp=? WHERE id=? AND status='polling'",
                    (
                        json.dumps(profile),
                        json.dumps(
                            {k: value[k] for k in ["access_token", "refresh_token"]}
                        ),
                        min(row["expires"], expires),
                        local_id,
                        stamp,
                        attempt,
                    ),
                ).rowcount
            if not changed:
                raise AccountLinkError("The sign-in attempt changed. Start again.", 409)
            return self.public(self.row(browser, attempt))
        except Exception:
            token = value.get("refresh_token", "")
            with self.profiles.db() as db:
                db.execute("DELETE FROM sign_in_attempts WHERE id=?", (attempt,))
                if isinstance(token, str) and re.fullmatch(
                    r"[A-Za-z0-9_-]{40,200}", token
                ):
                    db.execute(
                        "INSERT OR IGNORE INTO revocation_queue VALUES(?,?)",
                        (token, self.clock()),
                    )
            self.profiles.flush_revocations(1)
            raise

    def verified(self, browser, attempt):
        row = self.row(browser, attempt)
        if row["status"] != "ready":
            raise AccountLinkError("Confirm your KagNode sign-in first.", 409)
        if (
            row["local_id"]
            and self.profiles.stamp(row["local_id"]) != row["link_stamp"]
        ):
            raise AccountLinkError(
                "The account link changed. Sign in locally or start again.", 409
            )
        return {"profile": json.loads(row["profile"]), "local_id": row["local_id"]}

    def finish(self, browser, attempt, local_id):
        """Bind only after the caller enforces local account/unlock policy."""
        row = self.row(browser, attempt)
        self.verified(browser, attempt)
        profile = json.loads(row["profile"])
        tokens = json.loads(row["tokens"])
        uid = str(local_id)
        with self.profiles.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM sign_in_attempts WHERE id=?", (attempt,)
            ).fetchone()
            if (
                not current
                or not self.enabled()
                or current["generation"] != self.profiles.enabled()[1]
                or current["expires"] <= self.clock()
            ):
                raise AccountLinkError("The sign-in attempt changed. Start again.", 409)
            existing = db.execute(
                "SELECT local_id,subject,refresh FROM links WHERE local_id=? OR subject=?",
                (uid, profile["subject"]),
            ).fetchall()
            if (
                row["local_id"]
                and row["local_id"] != uid
                or any(
                    r["local_id"] != uid or r["subject"] != profile["subject"]
                    for r in existing
                )
            ):
                raise AccountLinkError(
                    "Sign in locally to link this KagNode Account.", 409
                )
            if row["local_id"] and self.profiles.stamp(uid) != row["link_stamp"]:
                raise AccountLinkError("The account link changed. Start again.", 409)
            for linked in existing:
                if linked["refresh"] and linked["refresh"] != tokens["refresh_token"]:
                    db.execute(
                        "INSERT OR IGNORE INTO revocation_queue VALUES(?,?)",
                        (linked["refresh"], self.clock()),
                    )
            db.execute(
                "INSERT OR REPLACE INTO links VALUES(?,?,?,?,?,0)",
                (
                    uid,
                    profile["subject"],
                    json.dumps(profile),
                    tokens["refresh_token"],
                    self.clock(),
                ),
            )
            db.execute("DELETE FROM sign_in_attempts WHERE id=?", (attempt,))
            db.execute(
                "INSERT INTO audit(actor,action,created) VALUES(?,?,?)",
                (uid, "account.sign_in", self.clock()),
            )
        return profile
