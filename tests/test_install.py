import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render", ROOT / "scripts/render.py")
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


def test_dryrun_no_secrets(tmp_path):
    r = subprocess.run(
        [
            str(ROOT / "install.sh"),
            "--dry-run",
            "--domain",
            "example.com",
            "--hostname",
            "mail.example.com",
            "--ipv4",
            "203.0.113.10",
            "--admin",
            "admin@example.com",
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "etc/postfix/main.cf").exists()
    assert not (tmp_path / "etc/morrow/config.json").exists()


def test_generated_relay_tls_and_binding(tmp_path):
    a = SimpleNamespace(
        domain="example.com",
        hostname="mail.example.com",
        ipv4="203.0.113.10",
        ipv6="",
        admin="admin@example.com",
        uid=5000,
        gid=5000,
        output=str(tmp_path),
    )
    files = render.render(a)
    master = files["etc/postfix/master.cf"]
    assert "smtpd_relay_restrictions=permit_sasl_authenticated,reject" in master
    assert (
        master.count(
            "smtpd_sender_restrictions=reject_authenticated_sender_login_mismatch"
        )
        == 2
    )
    assert master.count("smtpd_milters=inet:127.0.0.1:8892,inet:127.0.0.1:8891") == 2
    assert "127.0.0.1:1587 inet" in master
    assert "inet_protocols = ipv4" in files["etc/postfix/main.cf"]
    assert "include conf.d" not in files["etc/dovecot/dovecot.conf"]
    assert (
        "proxy_set_header X-Forwarded-For $remote_addr;"
        in files["etc/nginx/morrow-proxy.conf"]
    )


@pytest.mark.parametrize(
    "hostname",
    [
        "mail.example.com;echo hi",
        "mail.example.com\nfoo",
        "-evil.example.com",
        "example.com",
    ],
)
def test_installer_hostname_injection(hostname):
    with pytest.raises(ValueError):
        render.validate(
            "example.com", hostname, "203.0.113.10", "", "admin@example.com"
        )
