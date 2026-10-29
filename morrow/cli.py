"""Root bootstrap and account recovery; never accepts passwords on argv."""

import argparse
import getpass
import json
import os
import secrets
import stat
import time
from pathlib import Path

from cryptography.fernet import Fernet

from . import db


def private_password(path):
    # Do not follow symlinks or consume a world-readable secret.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        info = os.fstat(f.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.geteuid()
        ):
            raise ValueError(
                "Password file must be a regular file owned by the current user with mode 0600 or 0400."
            )
        value = f.read(1024).rstrip("\r\n")
    db.password_hash(value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["bootstrap", "reset-password", "cleanup"])
    p.add_argument(
        "--config", default=os.environ.get("MORROW_CONFIG", "/etc/morrow/config.json")
    )
    p.add_argument("--database", default="/var/lib/morrow/directory.sqlite3")
    p.add_argument("--domain")
    p.add_argument("--hostname")
    p.add_argument("--ipv4", default="")
    p.add_argument("--ipv6", default="")
    p.add_argument("--admin")
    p.add_argument("--password-file")
    p.add_argument("--dkim-public-file")
    a = p.parse_args()
    os.umask(0o027)
    if a.action == "bootstrap":
        if not all(
            (a.domain, a.hostname, a.admin, a.password_file, a.dkim_public_file)
        ):
            p.error(
                "bootstrap requires domain, hostname, admin, password-file and dkim-public-file"
            )
        path = Path(a.config)
        if path.exists():
            cfg = json.loads(path.read_text())
            if (
                cfg["MAIL_HOSTNAME"] != a.hostname
                or cfg["PUBLIC_IPV4"] != a.ipv4
                or cfg.get("PUBLIC_IPV6", "") != a.ipv6
            ):
                raise ValueError(
                    "Resume settings differ from the existing installation."
                )
        else:
            cfg = dict(
                SECRET_KEY=secrets.token_hex(32),
                CREDENTIAL_KEY=Fernet.generate_key().decode(),
                DATABASE=a.database,
                MAIL_HOSTNAME=a.hostname,
                PUBLIC_IPV4=a.ipv4,
                PUBLIC_IPV6=a.ipv6,
                DKIM_PUBLIC=Path(a.dkim_public_file).read_text().strip(),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cfg, indent=2) + "\n")
            path.chmod(0o640)
        db.init(a.database)
        with db.connect(a.database) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO domains VALUES(?)", (db.domain(a.domain),)
            )
            if not conn.execute(
                "SELECT 1 FROM users WHERE email=?", (a.admin,)
            ).fetchone():
                conn.execute(
                    "INSERT INTO users(email,name,password,admin) VALUES(?,?,?,1)",
                    (
                        db.address(a.admin),
                        "Administrator",
                        db.password_hash(private_password(a.password_file)),
                    ),
                )
            for local in ("postmaster", "abuse"):
                alias = local + "@" + a.domain
                if (
                    alias != a.admin
                    and not conn.execute(
                        "SELECT 1 FROM users WHERE email=?", (alias,)
                    ).fetchone()
                ):
                    conn.execute(
                        "INSERT OR IGNORE INTO aliases VALUES(?,?)", (alias, a.admin)
                    )
        Path(a.database).chmod(0o640)
        print("Application directory ready. Existing accounts and secrets preserved.")
    else:
        cfg = json.loads(Path(a.config).read_text())
        with db.connect(cfg["DATABASE"]) as conn:
            if a.action == "cleanup":
                conn.execute(
                    "DELETE FROM sessions WHERE expires < ?", (int(time.time()),)
                )
                conn.execute(
                    "DELETE FROM throttle WHERE until < ?", (int(time.time()),)
                )
            else:
                email = db.address(a.admin or input("Email: "))
                u = conn.execute(
                    "SELECT id FROM users WHERE email=?", (email,)
                ).fetchone()
                if not u:
                    raise ValueError("Account does not exist.")
                password = (
                    private_password(a.password_file)
                    if a.password_file
                    else getpass.getpass("New password: ")
                )
                if not a.password_file and password != getpass.getpass(
                    "Confirm new password: "
                ):
                    raise ValueError("Passwords do not match.")
                conn.execute(
                    "UPDATE users SET password=? WHERE id=?",
                    (db.password_hash(password), u[0]),
                )
                conn.execute("DELETE FROM sessions WHERE user_id=?", (u[0],))
                db.audit(conn, "local administrator", "password.reset", email)
                print(
                    "Password reset. Web sessions revoked. Run doveadm kick for any existing IMAP sessions."
                )


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        raise SystemExit(str(e))
