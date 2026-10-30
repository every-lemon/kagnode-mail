"""Versioned, mailbox-scoped bearer API and session-only token administration."""

import hashlib
import imaplib
import io
import json
import re
import secrets
import smtplib
import sqlite3
import time

from cryptography.fernet import InvalidToken
from flask import Blueprint, current_app, g, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from . import db
from .mail import FOLDERS, build_message

SCOPES = frozenset(("read", "send", "write", "delete"))
TOKEN_FORMAT = re.compile(r"kgn_([a-f0-9]{32})_[A-Za-z0-9_-]{43}\Z")


class APIError(Exception):
    def __init__(self, status, code, message, **details):
        self.status, self.code, self.message, self.details = (
            status,
            code,
            message,
            details,
        )


def api_error(exc):
    # Imported here so the adapter remains shared with the existing web interface.
    from .mail import MessageNotFound, SendUncertain, StaleMailbox

    if isinstance(exc, APIError):
        status, code, message, details = exc.status, exc.code, exc.message, exc.details
    elif isinstance(exc, StaleMailbox):
        status, code, message, details = 409, "stale_mailbox", str(exc), {}
    elif isinstance(exc, MessageNotFound):
        status, code, message, details = 404, "message_not_found", str(exc), {}
    elif isinstance(exc, SendUncertain):
        status, code, message, details = (
            502,
            "send_uncertain",
            "Submission outcome is unknown. Check delivery before retrying.",
            {"do_not_retry": True},
        )
    elif isinstance(exc, HTTPException):
        status, code, message, details = (
            exc.code,
            "http_" + str(exc.code),
            exc.description,
            {},
        )
    elif isinstance(exc, PermissionError):
        status, code, message, details = 403, "forbidden", str(exc), {}
    elif isinstance(exc, (ValueError, TypeError)):
        status, code, message, details = (
            400,
            "invalid_request",
            str(exc) if isinstance(exc, ValueError) else "Invalid request values.",
            {},
        )
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        status, code, message, details = (
            422,
            "recipients_refused",
            "No recipients were accepted.",
            {},
        )
    elif isinstance(exc, (OSError, imaplib.IMAP4.error, smtplib.SMTPException)):
        current_app.logger.warning("API mail operation failed: %s", type(exc).__name__)
        status, code, message, details = (
            502,
            "mail_unavailable",
            "The mail service is unavailable or rejected the request.",
            {},
        )
    else:
        current_app.logger.error("API request failed: %s", type(exc).__name__)
        status, code, message, details = (
            500,
            "internal_error",
            "The request could not be completed.",
            {},
        )
    response = jsonify(error={"code": code, "message": message, **details})
    response.status_code = status
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="KagNode Mail"'
    if status == 429:
        response.headers["Retry-After"] = str(details.get("retry_after", 60))
    return response


def metadata(row):
    value = {
        k: row[k]
        for k in (
            "id",
            "user_id",
            "email",
            "label",
            "expires",
            "created",
            "last_used",
            "revoked",
        )
    }
    value["scopes"], value["senders"] = (
        json.loads(row["scopes"]),
        json.loads(row["senders"]),
    )
    return value


def register_api(app, getdb, admin_required, limit, cipher):
    def token_row(token_id):
        return (
            getdb()
            .execute(
                "SELECT t.*,u.email FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE t.id=?",
                (token_id,),
            )
            .fetchone()
        )

    @app.get("/api/admin/tokens")
    @admin_required
    def tokens_list():
        conn = getdb()
        rows = conn.execute(
            "SELECT t.*,u.email FROM api_tokens t JOIN users u ON u.id=t.user_id ORDER BY t.created DESC,t.id LIMIT 1000"
        ).fetchall()
        users = []
        for row in conn.execute(
            "SELECT id,email,name,active FROM users ORDER BY email"
        ):
            user = dict(row)
            user["identities"] = db.identities(conn, row["id"])
            users.append(user)
        return jsonify(tokens=[metadata(row) for row in rows], users=users)

    @app.post("/api/admin/tokens")
    @admin_required
    def token_create():
        data = request.get_json() or {}
        user_id = data.get("user_id")
        label, scopes, senders = (
            data.get("label", ""),
            data.get("scopes", []),
            data.get("senders", []),
        )
        if type(user_id) is not int:
            raise ValueError("Select a mailbox.")
        if (
            not isinstance(label, str)
            or not 1 <= len(label.strip()) <= 80
            or any(ord(c) < 32 for c in label)
        ):
            raise ValueError("Use a token name of 1–80 characters.")
        if (
            not isinstance(scopes, list)
            or not scopes
            or any(not isinstance(v, str) or v not in SCOPES for v in scopes)
        ):
            raise ValueError("Select valid token permissions.")
        if (
            not isinstance(senders, list)
            or len(senders) > 50
            or any(not isinstance(v, str) for v in senders)
        ):
            raise ValueError("Select at most 50 sender addresses.")
        scopes, senders = sorted(set(scopes)), sorted({db.address(v) for v in senders})
        if "send" in scopes and not senders:
            raise ValueError("Select at least one sender address.")
        days = data.get("expires_days", 90)
        if days is not None and (type(days) is not int or not 1 <= days <= 365):
            raise ValueError("Expiry must be 1–365 days, or null for never.")
        now = int(time.time())
        expires = now + days * 86400 if days is not None else None
        identifier, backend = secrets.token_hex(16), secrets.token_urlsafe(32)
        secret = "kgn_" + identifier + "_" + secrets.token_urlsafe(32)
        # Hashing is intentionally outside the write transaction.
        backend_hash = db.password_hash(backend)
        conn = getdb()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute(
                "SELECT * FROM users WHERE id=? AND active=1", (user_id,)
            ).fetchone()
            if not user:
                raise ValueError("Select an active mailbox.")
            if not set(senders) <= set(db.identities(conn, user_id)):
                raise ValueError("Sender addresses must be permitted for this mailbox.")
            active = conn.execute(
                "SELECT COUNT(*) FROM api_tokens WHERE user_id=? AND revoked IS NULL AND (expires IS NULL OR expires>?)",
                (user_id, now),
            ).fetchone()[0]
            if active >= 20:
                raise ValueError(
                    "Revoke an existing token first; each mailbox supports 20 active tokens."
                )
            # Keep bounded historical metadata; never retain old encrypted credentials.
            conn.execute(
                "DELETE FROM api_tokens WHERE COALESCE(revoked,expires,?)<?",
                (now, now - 90 * 86400),
            )
            conn.execute(
                "INSERT INTO api_tokens(id,user_id,label,token_hash,scopes,senders,expires,created,backend_password,backend_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    user_id,
                    label.strip(),
                    hashlib.sha256(secret.encode()).hexdigest(),
                    json.dumps(scopes),
                    json.dumps(senders),
                    expires,
                    now,
                    cipher.encrypt(backend.encode()).decode(),
                    backend_hash,
                ),
            )
            db.audit(
                conn, g.user["email"], "token.create", identifier + ":" + user["email"]
            )
        return jsonify(token=metadata(token_row(identifier)), secret=secret), 201

    @app.delete("/api/admin/tokens/<token_id>")
    @admin_required
    def token_revoke(token_id):
        row = token_row(token_id)
        if not row:
            return jsonify(error="Token not found."), 404
        with getdb() as conn:
            conn.execute(
                "UPDATE api_tokens SET revoked=COALESCE(revoked,?),backend_password='',backend_hash='' WHERE id=?",
                (int(time.time()), token_id),
            )
            db.audit(
                conn, g.user["email"], "token.revoke", token_id + ":" + row["email"]
            )
        return jsonify(ok=True)

    api = Blueprint("token_api", __name__, url_prefix="/api/v1")

    @api.before_request
    def authenticate():
        if app.config["DEMO"]:
            raise APIError(
                403, "preview_read_only", "The preview does not support API tokens."
            )
        if not app.config["TESTING"] and not request.is_secure:
            raise APIError(403, "https_required", "Use HTTPS.")
        if request.headers.get("Origin") is not None or request.headers.get(
            "Sec-Fetch-Site"
        ):
            raise APIError(
                403, "browser_access_denied", "Use this API from a server or script."
            )
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        match = TOKEN_FORMAT.fullmatch(token)
        row = None
        now = int(time.time())
        if match:
            row = (
                getdb()
                .execute(
                    "SELECT t.* FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE t.id=? AND t.token_hash=? AND t.revoked IS NULL AND (t.expires IS NULL OR t.expires>?) AND u.active=1",
                    (match[1], hashlib.sha256(token.encode()).hexdigest(), now),
                )
                .fetchone()
            )
        if row is None:
            key = (
                "api-auth:"
                + hashlib.sha256(
                    (request.remote_addr or "unknown").encode()
                ).hexdigest()
            )
            if not limit(key, 60, 60):
                raise APIError(
                    429,
                    "rate_limited",
                    "Too many authentication attempts.",
                    retry_after=60,
                )
            raise APIError(401, "invalid_token", "A valid bearer token is required.")
        if not limit("api:" + row["id"], 120, 60):
            raise APIError(
                429, "rate_limited", "Token request limit reached.", retry_after=60
            )
        g.api_token = row
        g.api_scopes = set(json.loads(row["scopes"]))
        g.api_user = dict(
            getdb()
            .execute("SELECT * FROM users WHERE id=?", (row["user_id"],))
            .fetchone()
        )
        g.api_user["auth_username"] = "api-" + row["id"] + "@api.invalid"
        try:
            g.api_password = cipher.decrypt(row["backend_password"].encode()).decode()
        except InvalidToken:
            raise APIError(
                401, "invalid_token", "A valid bearer token is required."
            ) from None
        if row["last_used"] is None or row["last_used"] < now - 60:
            with getdb() as conn:
                conn.execute(
                    "UPDATE api_tokens SET last_used=? WHERE id=?", (now, row["id"])
                )
        if (
            request.method in ("POST", "PUT", "PATCH", "DELETE")
            and request.is_json
            and not isinstance(request.get_json(), dict)
        ):
            raise ValueError("Request body must be a JSON object.")

    api.register_error_handler(Exception, api_error)

    def require(scope):
        if scope not in g.api_scopes:
            raise APIError(
                403,
                "insufficient_scope",
                "This token requires the " + scope + " permission.",
            )
        # Recheck immediately before dispatch, including after request-body parsing.
        if (
            not getdb()
            .execute(
                "SELECT 1 FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE t.id=? AND t.revoked IS NULL AND (t.expires IS NULL OR t.expires>?) AND u.active=1",
                (g.api_token["id"], int(time.time())),
            )
            .fetchone()
        ):
            raise APIError(401, "invalid_token", "A valid bearer token is required.")

    def reference(data):
        folder = data.get("folder", "inbox")
        if not isinstance(folder, str) or folder not in FOLDERS:
            raise ValueError("Unknown folder.")
        validity = data.get("uidvalidity", "")
        if (
            not re.fullmatch(r"[1-9][0-9]{0,9}", str(validity))
            or int(validity) > 4294967295
        ):
            raise ValueError("Provide uidvalidity from the message listing.")
        return folder, str(validity)

    def mail():
        return app.extensions["mail"]

    def record_completed(action_name, target):
        try:
            with getdb() as conn:
                db.audit(conn, g.api_user["email"], action_name, target)
        except sqlite3.Error as exc:
            current_app.logger.warning(
                "Completed API operation audit failed: %s", type(exc).__name__
            )
            return "Operation completed, but its audit entry could not be saved."
        return None

    @api.get("/folders")
    def folders():
        require("read")
        return jsonify(
            items=[{"id": key, "name": name} for key, name in FOLDERS.items()]
        )

    @api.get("/messages")
    def messages():
        require("read")
        folder = request.args.get("folder", "inbox")
        if not isinstance(folder, str) or folder not in FOLDERS:
            raise ValueError("Unknown folder.")
        page = int(request.args.get("page", 1))
        search = request.args.get("q", "")
        if not 1 <= page <= 100000 or len(search) > 200:
            raise ValueError(
                "Use page 1–100000 and a search of at most 200 characters."
            )
        return jsonify(mail().list(g.api_user, g.api_password, folder, page, search))

    @api.get("/messages/<uid>")
    def message(uid):
        folder, validity = reference(request.args)
        require("read")
        return jsonify(
            mail().read(
                g.api_user,
                g.api_password,
                folder,
                uid,
                uidvalidity=validity,
                mark_seen=False,
            )
        )

    @api.get("/messages/<uid>/attachments/<index>")
    def attachment(uid, index):
        folder, validity = reference(request.args)
        require("read")
        raw, name = mail().attachment(
            g.api_user, g.api_password, folder, uid, index, uidvalidity=validity
        )
        return send_file(
            io.BytesIO(raw),
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=name,
            max_age=0,
        )

    @api.post("/messages/<uid>/action")
    def action(uid):
        data = request.get_json() or {}
        folder, validity = reference(data)
        action_name = data.get("action", "")
        if action_name not in (
            "read",
            "unread",
            "star",
            "unstar",
            "archive",
            "trash",
            "inbox",
            "junk",
            "delete",
        ):
            raise ValueError("Unknown action.")
        if action_name == "delete" and folder != "trash":
            raise ValueError(
                "Move the message to Trash before deleting it permanently."
            )
        require("delete" if action_name == "delete" else "write")
        mail().action(
            g.api_user, g.api_password, folder, uid, action_name, uidvalidity=validity
        )
        warning = record_completed(
            "api.message.action", g.api_token["id"] + ":" + str(action_name)
        )
        return jsonify(ok=True, warning=warning)

    def message_data():
        return (
            request.form
            if request.mimetype == "multipart/form-data"
            else request.get_json() or {}
        )

    @api.post("/send")
    def send():
        data = message_data()
        require("send")
        if not limit("send:" + str(g.api_user["id"]), 60, 3600):
            raise APIError(
                429,
                "send_rate_limited",
                "Mailbox hourly sending limit reached.",
                retry_after=3600,
            )
        allowed = set(json.loads(g.api_token["senders"])) & set(
            db.identities(getdb(), g.api_user["id"])
        )
        msg, sender, targets = build_message(
            data, g.api_user, allowed, request.files.getlist("attachments")
        )
        result = mail().send(g.api_user, g.api_password, msg, sender, targets)
        warning = record_completed("api.send", g.api_token["id"] + ":" + sender)
        if warning:
            result["warning"] = " ".join(filter(None, (result.get("warning"), warning)))
        result.update(status="accepted", message_id=msg["Message-ID"])
        return jsonify(result), 202

    @api.post("/drafts")
    def draft():
        data = message_data()
        require("write")
        replace_uid = data.get("draft_uid")
        validity = (
            reference({"folder": "drafts", "uidvalidity": data.get("uidvalidity")})[1]
            if replace_uid
            else None
        )
        msg, _, _ = build_message(
            data,
            g.api_user,
            db.identities(getdb(), g.api_user["id"]),
            request.files.getlist("attachments"),
            draft=True,
        )
        result = mail().draft(
            g.api_user,
            g.api_password,
            msg,
            replace_uid=replace_uid,
            uidvalidity=validity,
        )
        result = result or {}
        warning = record_completed("api.draft.save", g.api_token["id"])
        if warning:
            result["warning"] = " ".join(filter(None, (result.get("warning"), warning)))
        return jsonify(ok=True, **result), 201

    app.register_blueprint(api)
