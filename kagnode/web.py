"""KagNode Mail webmail and administration."""

import hashlib
import hmac
import imaplib
import io
import ipaddress
from urllib.parse import urlsplit
import json
import os
import secrets
import smtplib
import sqlite3
import subprocess
import time
from functools import wraps
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import (
    Flask,
    Request,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
from werkzeug.exceptions import HTTPException

from . import db
from .mail import Mail, build_message
from .proxy import TrustedProxyFix


class BoundedRequest(Request):
    """Parser limits also work with Ubuntu 24.04's Flask 3.0/Werkzeug 3.0."""

    max_form_memory_size = 2 * 1024 * 1024
    max_form_parts = 40

    @property
    def max_content_length(self):
        if self.path in ("/api/send", "/api/drafts", "/api/v1/send", "/api/v1/drafts"):
            return (
                24 * 1024 * 1024
                if self.mimetype == "multipart/form-data"
                else 2 * 1024 * 1024
            )
        return 64 * 1024


def kagnode_website_url():
    """Read a public-only URL file; no tokens or local Operator address."""
    value = os.environ.get("KAGNODE_WEBSITE_URL", "https://nebulacoder.com")
    filename = os.environ.get("KAGNODE_SERVICE_LINKS_FILE")
    if filename:
        try:
            with open(filename, encoding="utf-8") as source:
                value = json.load(source)["public"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        url = urlsplit(value)
        if (not isinstance(value, str) or any(ch.isspace() for ch in value) or "\\" in value
                or url.scheme != "https" or not url.hostname or "." not in url.hostname
                or url.hostname.rsplit(".", 1)[-1].isdigit()
                or url.username or url.password or url.path not in ("", "/")
                or url.query or url.fragment or url.hostname.endswith((".local", ".lan", ".localhost"))):
            raise ValueError("Invalid public origin")
        try:
            ipaddress.ip_address(url.hostname)
        except ValueError:
            return value.rstrip("/")
    except (ValueError, TypeError):
        pass
    return "https://nebulacoder.com"


def create_app(overrides=None):
    app = Flask(__name__)
    app.request_class = BoundedRequest
    app.config.update(
        DATABASE="/var/lib/kagnode/mail/directory.sqlite3",
        IMAP_HOST="127.0.0.1",
        IMAP_PORT=1143,
        SMTP_HOST="127.0.0.1",
        SMTP_PORT=1587,
        MAIL_HOSTNAME="mail.example.com",
        PUBLIC_IPV4="",
        PUBLIC_IPV6="",
        DKIM_PUBLIC="",
        MAX_CONTENT_LENGTH=24 * 1024 * 1024,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=28800,
        DEMO=False,
        TRUSTED_PROXIES=["127.0.0.1", "::1"],
    )
    path = os.environ.get("KAGNODE_CONFIG", "/etc/kagnode/mail/config.json")
    if Path(path).is_file():
        app.config.update(json.loads(Path(path).read_text()))
    if overrides:
        app.config.update(overrides)
    if not app.config.get("SECRET_KEY") or not app.config.get("CREDENTIAL_KEY"):
        raise RuntimeError(
            "Missing application secrets. Run the installer or scripts/demo.py."
        )
    # Host-only, HTTPS-only cookies cannot be injected by sibling subdomains.
    cookie_prefix = "__Host-" if app.config["SESSION_COOKIE_SECURE"] else ""
    app.config["SESSION_COOKIE_NAME"] = cookie_prefix + "kagnode_csrf"
    app.config["SESSION_COOKIE_DOMAIN"] = None
    app.config["SESSION_COOKIE_PATH"] = "/"
    auth_cookie = cookie_prefix + "kagnode_sid"
    cipher = Fernet(app.config["CREDENTIAL_KEY"])
    app.wsgi_app = TrustedProxyFix(app.wsgi_app, app.config["TRUSTED_PROXIES"])
    app.extensions["mail"] = Mail(app.config)

    def getdb():
        if "db" not in g:
            g.db = db.connect(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(_):
        if "db" in g:
            g.db.close()

    def public_user(u):
        return {k: u[k] for k in ("id", "email", "name", "admin", "active", "quota_mb")}

    def login_required(f):
        @wraps(f)
        def inner(*args, **kwargs):
            if g.user is None:
                return jsonify(error="Please sign in again."), 401
            return f(*args, **kwargs)

        return inner

    def admin_required(f):
        @wraps(f)
        @login_required
        def inner(*args, **kwargs):
            if not g.user["admin"]:
                return jsonify(error="Administrator access required."), 403
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                try:
                    authenticated = cipher.extract_timestamp(
                        g.auth["password"].encode()
                    )
                except InvalidToken:
                    authenticated = 0
                if authenticated < int(time.time()) - 900:
                    return jsonify(
                        error="Confirm your password to continue with this administrator change.",
                        code="reauth_required",
                    ), 403
            return f(*args, **kwargs)

        return inner

    def credential():
        try:
            return cipher.decrypt(g.auth["password"].encode(), ttl=28800).decode()
        except InvalidToken:
            raise PermissionError("Session expired. Please sign in again.")

    def limit(key, maximum, seconds):
        now = int(time.time())
        conn = getdb()
        # Single atomic statement makes limits apply across workers and concurrent requests.
        with conn:
            conn.execute("DELETE FROM throttle WHERE until < ?", (now,))
            conn.execute(
                "INSERT INTO throttle VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1",
                (key, now + seconds),
            )
            row = conn.execute(
                "SELECT count FROM throttle WHERE key=?", (key,)
            ).fetchone()
        return row[0] <= maximum

    @app.before_request
    def authenticate():
        g.user, g.auth = None, None
        if request.path == "/api/v1" or request.path.startswith("/api/v1/"):
            return None  # The versioned API authenticates bearer tokens independently.
        token = request.cookies.get(auth_cookie, "")
        if token:
            g.auth = (
                getdb()
                .execute(
                    "SELECT * FROM sessions WHERE token=? AND expires>?",
                    (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
                )
                .fetchone()
            )
            if g.auth:
                g.user = (
                    getdb()
                    .execute(
                        "SELECT * FROM users WHERE id=? AND active=1",
                        (g.auth["user_id"],),
                    )
                    .fetchone()
                )
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # CSRF remains mandatory for clients without browser metadata. When
            # provided, reject cross-origin requests, including sibling subdomains.
            origin = request.headers.get("Origin")
            expected_origin = (
                request.host_url.rstrip("/")
                if app.config["DEMO"] or app.config["TESTING"]
                else "https://" + app.config["MAIL_HOSTNAME"]
            )
            if request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site") or (
                origin is not None and origin != expected_origin
            ):
                return jsonify(error="Cross-origin requests are not allowed."), 403
            if request.is_json and not isinstance(request.get_json(), dict):
                return jsonify(error="Request body must be a JSON object."), 400
            expected = g.auth["csrf"] if g.user else session.get("csrf", "")
            provided = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, provided):
                return jsonify(
                    error="This page has expired. Refresh and try again."
                ), 403
            if app.config["DEMO"] and request.path not in ("/api/login", "/api/logout"):
                return jsonify(
                    error="This is a read-only preview. Install KagNode Mail to make changes or send mail."
                ), 403

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    def set_auth_cookie(response, token, lifetime=28800):
        response.set_cookie(
            auth_cookie,
            token,
            max_age=lifetime,
            secure=app.config["SESSION_COOKIE_SECURE"],
            httponly=True,
            samesite="Strict",
            path="/",
        )
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if request.path == "/api/v1" or request.path.startswith("/api/v1/"):
            from .api import api_error

            return api_error(exc)
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        if isinstance(exc, PermissionError):
            return jsonify(error=str(exc)), 403
        if isinstance(exc, (ValueError, TypeError)):
            return jsonify(
                error=str(exc)
                if isinstance(exc, ValueError)
                else "Invalid request values."
            ), 400
        if isinstance(exc, sqlite3.IntegrityError):
            return jsonify(
                error="That address already exists, or is still in use."
            ), 409
        if isinstance(exc, (OSError, imaplib.IMAP4.error, smtplib.SMTPException)):
            app.logger.warning("Mail operation failed: %s", type(exc).__name__)
            # Do not encourage blind resend after an uncertain SMTP timeout.
            return jsonify(
                error=(
                    "Mail service unavailable or request rejected. If sending timed out, check Sent and delivery before retrying."
                    if request.path == "/api/send"
                    else "Could not connect to your mailbox. Try again shortly. If this continues, contact your mail administrator."
                )
            ), 502
        app.logger.exception("Unhandled request error")
        return jsonify(error="Something went wrong. Check the server journal."), 500

    @app.get("/")
    def index():
        return render_template("index.html", kagnode_website=kagnode_website_url())

    @app.get("/api/session")
    def current_session():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        login_url = "https://" + app.config["MAIL_HOSTNAME"] + "/"
        login_allowed = (
            app.config["DEMO"]
            or app.config["TESTING"]
            or (request.is_secure and request.host_url == login_url)
        )
        return jsonify(
            user=public_user(g.user) if g.user else None,
            csrf=g.auth["csrf"] if g.user else session["csrf"],
            demo=app.config["DEMO"],
            identities=db.identities(getdb(), g.user["id"]) if g.user else [],
            hostname=app.config["MAIL_HOSTNAME"],
            login_url=login_url,
            login_allowed=bool(login_allowed),
        )

    @app.post("/api/login")
    def login():
        data = request.get_json() or {}
        email = str(data.get("email", "")).strip().lower()[:254]
        ipkey = "login-ip:" + hashlib.sha256(request.remote_addr.encode()).hexdigest()
        userkey = "login-user:" + hashlib.sha256(email.encode()).hexdigest()
        if not limit(ipkey, 30, 900) or not limit(userkey, 10, 900):
            return jsonify(
                error="Too many sign-in attempts. Try again in 15 minutes."
            ), 429
        u = (
            getdb()
            .execute("SELECT * FROM users WHERE email=? AND active=1", (email,))
            .fetchone()
        )
        pw = data.get("password", "")
        # Comparable work for unknown accounts to avoid a cheap account-existence oracle.
        fallback = "{SHA512-CRYPT}$6$rounds=100000$kagnodedummysalt$BpJLW4Z3LeEvwyAElAeQ9oqLCYEEERjKy/fj1NP7ZCLYHHyA6AFub3TOi9kL6bXBYAcKf8eGhQqBKsktza4rK0"
        valid = db.verify(pw, u["password"] if u else fallback)
        if not u or not valid:
            return jsonify(error="Email or password is incorrect."), 401
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        conn = getdb()
        with conn:
            conn.execute("DELETE FROM sessions WHERE expires<?", (int(time.time()),))
            if g.auth:
                conn.execute("DELETE FROM sessions WHERE token=?", (g.auth["token"],))
            conn.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    u["id"],
                    cipher.encrypt(pw.encode()).decode(),
                    csrf,
                    int(time.time()) + 28800,
                ),
            )
            # A forgotten browser cannot retain indefinite access; bound the
            # account to its five newest sessions as well as the eight-hour TTL.
            conn.execute(
                "DELETE FROM sessions WHERE user_id=? AND rowid NOT IN "
                "(SELECT rowid FROM sessions WHERE user_id=? ORDER BY rowid DESC LIMIT 5)",
                (u["id"], u["id"]),
            )
            db.audit(conn, email, "login", email)
        session.clear()
        response = jsonify(
            user=public_user(u), csrf=csrf, identities=db.identities(conn, u["id"])
        )
        return set_auth_cookie(response, token)

    @app.post("/api/account/reauth")
    @login_required
    def reauthenticate():
        if not limit("reauth:" + str(g.user["id"]), 10, 900):
            return jsonify(error="Too many attempts. Try again in 15 minutes."), 429
        password = (request.get_json() or {}).get("password", "")
        if not db.verify(password, g.user["password"]):
            return jsonify(error="Password is incorrect."), 403
        token = secrets.token_urlsafe(32)
        with getdb() as conn:
            conn.execute(
                "UPDATE sessions SET token=?,password=? WHERE token=?",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    cipher.encrypt(password.encode()).decode(),
                    g.auth["token"],
                ),
            )
            db.audit(conn, g.user["email"], "session.reauthenticate", g.user["email"])
        # Rotate the bearer token, preserving CSRF and the original absolute expiry.
        return set_auth_cookie(
            jsonify(ok=True), token, max(0, g.auth["expires"] - int(time.time()))
        )

    @app.post("/api/logout")
    def logout():
        if g.auth:
            with getdb() as conn:
                conn.execute("DELETE FROM sessions WHERE token=?", (g.auth["token"],))
        session.clear()
        response = jsonify(ok=True)
        response.delete_cookie(
            auth_cookie,
            secure=app.config["SESSION_COOKIE_SECURE"],
            httponly=True,
            samesite="Strict",
            path="/",
        )
        return response

    @app.get("/api/messages")
    @login_required
    def messages():
        page = max(1, min(int(request.args.get("page", 1)), 100000))
        search = request.args.get("q", "")[:200]
        return jsonify(
            app.extensions["mail"].list(
                g.user, credential(), request.args.get("folder", "inbox"), page, search
            )
        )

    @app.get("/api/messages/<uid>")
    @login_required
    def message(uid):
        return jsonify(
            app.extensions["mail"].read(
                g.user, credential(), request.args.get("folder", "inbox"), uid
            )
        )

    @app.get("/api/messages/<uid>/attachments/<index>")
    @login_required
    def attachment(uid, index):
        raw, name = app.extensions["mail"].attachment(
            g.user, credential(), request.args.get("folder", "inbox"), uid, index
        )
        return send_file(
            io.BytesIO(raw),
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=name,
            max_age=0,
        )

    @app.post("/api/messages/<uid>/action")
    @login_required
    def message_action(uid):
        data = request.get_json() or {}
        app.extensions["mail"].action(
            g.user,
            credential(),
            data.get("folder", "inbox"),
            uid,
            data.get("action", ""),
        )
        return jsonify(ok=True)

    @app.post("/api/send")
    @login_required
    def send():
        if not limit("send:" + str(g.user["id"]), 60, 3600):
            return jsonify(error="Hourly sending limit reached. Try again later."), 429
        data = (
            request.form
            if request.mimetype == "multipart/form-data"
            else request.get_json() or {}
        )
        msg, sender, targets = build_message(
            data,
            g.user,
            db.identities(getdb(), g.user["id"]),
            request.files.getlist("attachments"),
        )
        result = app.extensions["mail"].send(g.user, credential(), msg, sender, targets)
        with getdb() as conn:
            db.audit(conn, g.user["email"], "send", sender)
        return jsonify(result)

    @app.post("/api/drafts")
    @login_required
    def save_draft():
        data = request.form
        msg, _, _ = build_message(
            data,
            g.user,
            db.identities(getdb(), g.user["id"]),
            request.files.getlist("attachments"),
            draft=True,
        )
        app.extensions["mail"].draft(g.user, credential(), msg, data.get("draft_uid"))
        return jsonify(ok=True)

    @app.post("/api/account/password")
    @login_required
    def password():
        data = request.get_json() or {}
        if not limit("password:" + str(g.user["id"]), 10, 900):
            return jsonify(error="Too many attempts. Try again later."), 429
        if not db.verify(data.get("current", ""), g.user["password"]):
            raise ValueError("Current password is incorrect.")
        hashed = db.password_hash(data.get("password", ""))
        with getdb() as conn:
            conn.execute(
                "UPDATE users SET password=? WHERE id=?", (hashed, g.user["id"])
            )
            conn.execute("DELETE FROM sessions WHERE user_id=?", (g.user["id"],))
            db.audit(conn, g.user["email"], "password.change", g.user["email"])
        return jsonify(ok=True)

    def public_ip(key):
        row = (
            getdb().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        )
        return row[0] if row else app.config[key]

    @app.put("/api/admin/network")
    @admin_required
    def update_network():
        data = request.get_json() or {}
        value = str(data.get("ipv4", "")).strip()
        if ipaddress.ip_address(value).version != 4:
            raise ValueError("Enter a valid public IPv4 address.")
        with getdb() as conn:
            conn.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("PUBLIC_IPV4", value),
            )
            db.audit(conn, g.user["email"], "dns.ipv4.update", value)
        return jsonify(ok=True)

    @app.get("/api/admin")
    @admin_required
    def admin():
        conn = getdb()
        users = [
            public_user(r) for r in conn.execute("SELECT * FROM users ORDER BY email")
        ]
        for u in users:
            u["identities"] = db.identities(conn, u["id"])
        return jsonify(
            users=users,
            domains=[
                r[0] for r in conn.execute("SELECT name FROM domains ORDER BY name")
            ],
            aliases=[
                dict(r) for r in conn.execute("SELECT * FROM aliases ORDER BY email")
            ],
            audit=[
                dict(r)
                for r in conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 30")
            ],
            hostname=app.config["MAIL_HOSTNAME"],
            ipv4=public_ip("PUBLIC_IPV4"),
            ipv6=app.config["PUBLIC_IPV6"],
        )

    def local_address(value):
        value = db.address(value)
        if (
            not getdb()
            .execute("SELECT 1 FROM domains WHERE name=?", (value.split("@")[1],))
            .fetchone()
        ):
            raise ValueError("Add this domain first.")
        return value

    @app.post("/api/admin/users")
    @admin_required
    def create_user():
        data = request.get_json() or {}
        email = local_address(data.get("email", ""))
        name = str(data.get("name", "")).strip()[:100]
        if not name:
            raise ValueError("A display name is required.")
        quota = int(data.get("quota_mb", 1024))
        if not 64 <= quota <= 1048576:
            raise ValueError("Quota must be between 64 MiB and 1 TiB.")
        hashed = db.password_hash(data.get("password", ""))
        with getdb() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM aliases WHERE email=?", (email,)).fetchone():
                raise ValueError("This address is already an alias.")
            conn.execute(
                "INSERT INTO users(email,name,password,admin,quota_mb) VALUES(?,?,?,?,?)",
                (email, name, hashed, int(data.get("admin") is True), quota),
            )
            db.audit(conn, g.user["email"], "user.create", email)
        return jsonify(ok=True), 201

    @app.patch("/api/admin/users/<int:uid>")
    @admin_required
    def update_user(uid):
        data = request.get_json() or {}
        conn = getdb()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not u:
                return jsonify(error="User not found."), 404
            admin = int(data.get("admin", bool(u["admin"])) is True)
            active = int(data.get("active", bool(u["active"])) is True)
            if u["admin"] and u["active"] and (not admin or not active):
                if (
                    conn.execute(
                        "SELECT count(*) FROM users WHERE admin=1 AND active=1"
                    ).fetchone()[0]
                    <= 1
                ):
                    raise ValueError("Keep at least one active administrator.")
            quota = int(data.get("quota_mb", u["quota_mb"]))
            if not 64 <= quota <= 1048576:
                raise ValueError("Quota must be between 64 MiB and 1 TiB.")
            name = str(data.get("name", u["name"])).strip()[:100]
            if not name:
                raise ValueError("A display name is required.")
            hashed = (
                db.password_hash(data["password"])
                if data.get("password")
                else u["password"]
            )
            conn.execute(
                "UPDATE users SET name=?,admin=?,active=?,quota_mb=?,password=? WHERE id=?",
                (name, admin, active, quota, hashed, uid),
            )
            # All admin changes invalidate web sessions. Existing native IMAP sessions are documented.
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            db.audit(conn, g.user["email"], "user.update", u["email"])
        return jsonify(ok=True)

    @app.put("/api/admin/users/<int:uid>/identities")
    @admin_required
    def update_identities(uid):
        values = (request.get_json() or {}).get("identities", [])
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError("Provide at most 50 sender addresses.")
        conn = getdb()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not u:
                return jsonify(error="User not found."), 404
            cleaned = {local_address(v) for v in values} - {u["email"]}
            for value in cleaned:
                if not conn.execute(
                    "SELECT 1 FROM users WHERE email=? UNION SELECT 1 FROM aliases WHERE email=?",
                    (value, value),
                ).fetchone():
                    raise ValueError(
                        "Sender addresses must be an existing mailbox or alias."
                    )
            conn.execute("DELETE FROM grants WHERE user_id=?", (uid,))
            conn.executemany(
                "INSERT INTO grants VALUES(?,?)", [(uid, v) for v in cleaned]
            )
            db.audit(conn, g.user["email"], "identities.update", u["email"])
        return jsonify(ok=True)

    @app.post("/api/admin/aliases")
    @admin_required
    def alias():
        data = request.get_json() or {}
        email = local_address(data.get("email", ""))
        destination = db.address(data.get("destination", ""))
        with getdb() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise ValueError("This address is already a mailbox.")
            if not conn.execute(
                "SELECT 1 FROM users WHERE email=?", (destination,)
            ).fetchone():
                raise ValueError("Choose an existing mailbox as the destination.")
            conn.execute("INSERT INTO aliases VALUES(?,?)", (email, destination))
            db.audit(conn, g.user["email"], "alias.create", email)
        return jsonify(ok=True), 201

    @app.delete("/api/admin/aliases/<path:email>")
    @admin_required
    def delete_alias(email):
        with getdb() as conn:
            conn.execute("DELETE FROM grants WHERE email=?", (email,))
            conn.execute("DELETE FROM aliases WHERE email=?", (email,))
            db.audit(conn, g.user["email"], "alias.delete", email)
        return jsonify(ok=True)

    @app.post("/api/admin/domains")
    @admin_required
    def create_domain():
        name = db.domain((request.get_json() or {}).get("name", ""))
        with getdb() as conn:
            conn.execute("INSERT INTO domains VALUES(?)", (name,))
            db.audit(conn, g.user["email"], "domain.create", name)
        return jsonify(ok=True), 201

    @app.get("/api/admin/dns/<name>")
    @admin_required
    def dns(name):
        name = db.domain(name)
        if (
            not getdb()
            .execute("SELECT 1 FROM domains WHERE name=?", (name,))
            .fetchone()
        ):
            raise ValueError("Unknown domain.")
        host = app.config["MAIL_HOSTNAME"]
        rows = [
            dict(
                type="A",
                host=host,
                value=public_ip("PUBLIC_IPV4"),
                note="DNS only. Disable CDN / HTTP proxying.",
            ),
            dict(
                type="MX",
                host=name,
                value="10 " + host + ".",
                note="Route incoming mail to this server.",
            ),
            dict(
                type="TXT",
                host=name,
                value="v=spf1 mx -all",
                note="Publish one SPF record. Merge other senders if needed.",
            ),
            dict(
                type="TXT",
                host="mail._domainkey." + name,
                value=app.config["DKIM_PUBLIC"],
                note="Use the complete key as one TXT record.",
            ),
            dict(
                type="TXT",
                host="_dmarc." + name,
                value="v=DMARC1; p=none; adkim=s; aspf=s",
                note="Start in monitoring mode; move to quarantine/reject after verifying all senders.",
            ),
        ]
        if app.config["PUBLIC_IPV6"]:
            rows.insert(
                1,
                dict(
                    type="AAAA",
                    host=host,
                    value=app.config["PUBLIC_IPV6"],
                    note="Publish only with working IPv6 delivery and matching PTR.",
                ),
            )
        rows.append(
            dict(
                type="PTR",
                host=public_ip("PUBLIC_IPV4"),
                value=host + ".",
                note="Set reverse DNS in your VPS / IP provider dashboard, not the domain registrar.",
            )
        )
        if request.args.get("check") == "1" and not app.config["DEMO"]:
            for row in rows:
                if not row["value"]:
                    row.update(status="missing", observed=[])
                    continue
                args = ["dig", "+short", "+time=2", "+tries=1"]
                args += (
                    ["-x", row["host"]]
                    if row["type"] == "PTR"
                    else [row["host"], row["type"]]
                )
                try:
                    out = subprocess.run(
                        args, capture_output=True, text=True, timeout=3, check=True
                    ).stdout.strip()
                    observed = out.splitlines()
                    normalized = [v.replace('" "', "").strip('"') for v in observed]
                    expected = row["value"]
                    if row["type"] in ("MX", "PTR"):
                        normalized = [v.lower() for v in normalized]
                        expected = expected.lower()
                    row.update(
                        status="matched" if expected in normalized else "review",
                        observed=observed,
                    )
                except (OSError, subprocess.SubprocessError):
                    row.update(status="unavailable", observed=[])
        return jsonify(records=rows)

    from .api import register_api

    register_api(app, getdb, admin_required, limit, cipher)
    return app
