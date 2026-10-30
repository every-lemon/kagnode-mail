"""Transport authorization, using the actual generated SQL and sender guard."""

import json
import re
import sys
import time
from types import SimpleNamespace

import pytest

from kagnode import db, guard
from test_install import render

TOKEN_ID = "a" * 32
LOGIN = f"api-{TOKEN_ID}@api.invalid"


@pytest.fixture
def token_db(app):
    path = app.config["DATABASE"]
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO api_tokens(id,user_id,label,token_hash,scopes,senders,expires,created,backend_password,backend_hash) VALUES(?,2,?,?,?,?,?,?,?,?)",
            (
                TOKEN_ID,
                "worker",
                "hash",
                json.dumps(["read", "send"]),
                json.dumps(["member@example.com"]),
                int(time.time()) + 3600,
                int(time.time()),
                "encrypted",
                "backend-hash",
            ),
        )
    return path


@pytest.fixture
def configs(tmp_path):
    return render.render(
        SimpleNamespace(
            domain="example.com",
            hostname="mail.example.com",
            ipv4="203.0.113.10",
            ipv6="",
            admin="admin@example.com",
            uid=5000,
            gid=5000,
            output=str(tmp_path),
        )
    )


def expand(query, **values):
    return re.sub(r"%([%a-z])", lambda m: "%" if m[1] == "%" else values[m[1]], query)


def query(configs, key):
    return next(
        line.split(" = ", 1)[1]
        for line in configs["etc/dovecot/kagnode-sql.conf.ext"].splitlines()
        if line.startswith(key + " = ")
    )


@pytest.mark.parametrize(
    "service,port,lip,rip,accepted",
    [
        ("imap", "1143", "127.0.0.1", "127.0.0.1", True),
        ("imap", "993", "127.0.0.1", "127.0.0.1", False),
        ("imap", "1143", "192.0.2.10", "127.0.0.1", False),
        ("imap", "1143", "127.0.0.1", "192.0.2.1", False),
        ("smtp", "", "127.0.0.1", "127.0.0.1", False),
        ("kagnode-private", "", "127.0.0.1", "127.0.0.1", True),
        ("kagnode-private", "", "127.0.0.1", "192.0.2.1", False),
        ("doveadm", "", "127.0.0.1", "127.0.0.1", False),
    ],
)
def test_password_query_restricts_token_transport(
    token_db, configs, service, port, lip, rip, accepted
):
    sql = expand(
        query(configs, "password_query"), u=LOGIN, s=service, a=port, l=lip, r=rip
    )
    with db.connect(token_db) as conn:
        row = conn.execute(sql).fetchone()
        assert bool(row) is accepted
        if row:
            assert row["user"] == LOGIN
            assert row["password"] == "backend-hash"


def test_userdb_maps_only_private_imap_to_real_mailbox(token_db, configs):
    with db.connect(token_db) as conn:
        sql = query(configs, "user_query")
        row = conn.execute(
            expand(sql, u=LOGIN, s="imap", a="1143", l="127.0.0.1", r="127.0.0.1")
        ).fetchone()
        assert row["home"] == "/var/vmail/example.com/member"
        assert row["uid"] == 5000
        assert not conn.execute(
            expand(sql, u=LOGIN, s="imap", a="993", l="127.0.0.1", r="127.0.0.1")
        ).fetchone()


def test_postfix_token_sender_mapping_tracks_live_permissions(token_db, configs):
    raw = configs["etc/postfix/kagnode-senders.cf"].split("query = ", 1)[1].strip()
    sql = expand(raw, s="member@example.com")
    with db.connect(token_db) as conn:
        assert LOGIN in [r[0] for r in conn.execute(sql)]
        conn.execute("UPDATE api_tokens SET senders=?", ("[]",))
        assert LOGIN not in [r[0] for r in conn.execute(sql)]


def test_private_service_name_only_on_private_listener(configs):
    public, private = configs["etc/postfix/master.cf"].split("127.0.0.1:1587 inet", 1)
    assert "smtpd_sasl_service=kagnode-private" not in public
    assert private.count("smtpd_sasl_service=kagnode-private") == 1


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE api_tokens SET revoked=1",
        "UPDATE api_tokens SET expires=1",
        "UPDATE api_tokens SET scopes='[\"read\"]'",
        "UPDATE api_tokens SET senders='[]'",
        "UPDATE users SET active=0 WHERE id=2",
        "UPDATE users SET password='changed' WHERE id=2",
    ],
)
def test_guard_rechecks_token_at_end_of_upload(token_db, monkeypatch, change):
    class Base:
        def getsymval(self, name):
            return LOGIN

        def setreply(self, *args):
            self.reply = args

    fake = SimpleNamespace(
        Base=Base, CONTINUE=0, REJECT=1, TEMPFAIL=2, runmilter=lambda *args: None
    )
    monkeypatch.setitem(sys.modules, "Milter", fake)
    monkeypatch.setenv("KAGNODE_DATABASE", token_db)
    guard.run()
    instance = fake.factory()
    assert instance.envfrom("<member@example.com>") == fake.CONTINUE
    assert instance.header("From", "member@example.com") == fake.CONTINUE
    assert instance.eoh() == fake.CONTINUE
    with db.connect(token_db) as conn:
        conn.execute(change)
    assert instance.eom() == fake.REJECT
    assert instance.reply[0] == "550"


def test_token_and_password_smtp_share_account_quota(token_db, monkeypatch):
    class Base:
        def getsymval(self, name):
            return LOGIN

        def setreply(self, *args):
            self.reply = args

    fake = SimpleNamespace(
        Base=Base, CONTINUE=0, REJECT=1, TEMPFAIL=2, runmilter=lambda *args: None
    )
    monkeypatch.setitem(sys.modules, "Milter", fake)
    monkeypatch.setenv("KAGNODE_DATABASE", token_db)
    guard.run()
    instance = fake.factory()
    assert instance.envfrom("<member@example.com>") == fake.CONTINUE
    assert instance.header("From", "member@example.com") == fake.CONTINUE
    assert instance.eoh() == fake.CONTINUE
    with db.connect(token_db) as conn:
        conn.execute(
            "INSERT INTO throttle VALUES(?,60,?)",
            ("smtp:member@example.com", int(time.time()) + 3600),
        )
    assert instance.eom() == fake.TEMPFAIL
    assert instance.reply[0] == "451"


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE api_tokens SET revoked=1",
        "UPDATE api_tokens SET expires=1",
        "UPDATE users SET active=0 WHERE id=2",
        "UPDATE users SET password='changed' WHERE id=2",
    ],
)
def test_token_transport_revocation(token_db, configs, change):
    with db.connect(token_db) as conn:
        conn.execute(change)
        sql = query(configs, "password_query")
        for service, port in [("imap", "1143"), ("kagnode-private", "")]:
            assert not conn.execute(
                expand(sql, u=LOGIN, s=service, a=port, l="127.0.0.1", r="127.0.0.1")
            ).fetchone()


def test_read_only_token_cannot_authenticate_smtp(token_db, configs):
    with db.connect(token_db) as conn:
        conn.execute("UPDATE api_tokens SET scopes=?", ('["read"]',))
        sql = query(configs, "password_query")
        assert not conn.execute(
            expand(
                sql, u=LOGIN, s="kagnode-private", a="", l="127.0.0.1", r="127.0.0.1"
            )
        ).fetchone()
        assert conn.execute(
            expand(sql, u=LOGIN, s="imap", a="1143", l="127.0.0.1", r="127.0.0.1")
        ).fetchone()


def test_cli_cleanup_wipes_expired_credentials_and_bounds_history(
    token_db, tmp_path, monkeypatch
):
    from kagnode import cli

    now = int(time.time())
    with db.connect(token_db) as conn:
        for marker, expires, revoked in (
            ("b", now - 91 * 86400, None),
            ("c", now + 86400, now - 91 * 86400),
            ("d", None, None),
        ):
            conn.execute(
                "INSERT INTO api_tokens(id,user_id,label,token_hash,scopes,senders,expires,created,revoked,backend_password,backend_hash) "
                "SELECT ?,user_id,label,?,scopes,senders,?,created,?,backend_password,backend_hash FROM api_tokens WHERE id=?",
                (marker * 32, marker, expires, revoked, TOKEN_ID),
            )
        conn.execute("UPDATE api_tokens SET expires=? WHERE id=?", (now - 10, TOKEN_ID))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"DATABASE": token_db}))
    monkeypatch.setattr(sys, "argv", ["kagnode", "cleanup", "--config", str(config)])
    monkeypatch.setattr(cli, "drop_database_privileges", lambda: None)
    cli.main()
    with db.connect(token_db) as conn:
        rows = {row["id"]: row for row in conn.execute("SELECT * FROM api_tokens")}
    assert set(rows) == {TOKEN_ID, "d" * 32}
    assert rows[TOKEN_ID]["backend_password"] == rows[TOKEN_ID]["backend_hash"] == ""
    assert rows["d" * 32]["backend_password"] == "encrypted"
    assert rows["d" * 32]["backend_hash"] == "backend-hash"
