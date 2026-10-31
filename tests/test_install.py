import importlib.util
import os
import subprocess
import sys
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
    assert not (tmp_path / "etc/kagnode/mail/config.json").exists()


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
        in files["etc/nginx/kagnode-proxy.conf"]
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


def test_server_security_policy(tmp_path):
    files = render.render(
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
    postfix = files["etc/postfix/main.cf"]
    for setting in (
        "smtpd_tls_protocols",
        "smtp_tls_protocols",
        "smtpd_tls_mandatory_protocols",
    ):
        assert f"{setting} = >=TLSv1.2" in postfix
    nginx = files["etc/nginx/sites-available/kagnode"]
    assert nginx.count("if ($host != mail.example.com) { return 421; }") == 2
    assert "limit_conn kagnode_connections 12;" in nginx
    assert "gzip off;" in files["etc/nginx/nginx.conf"]
    proxy = files["etc/nginx/kagnode-proxy.conf"]
    assert "proxy_set_header Host mail.example.com;" in proxy
    assert 'proxy_set_header Accept-Encoding "";' in proxy
    for name in ("web", "guard", "maintenance"):
        unit = files[f"etc/systemd/system/kagnode-{name}.service"]
        for setting in (
            "NoNewPrivileges=true",
            "PrivateDevices=true",
            "ProtectSystem=strict",
            "CapabilityBoundingSet=\n",
            "RestrictNamespaces=true",
            "ProtectProc=invisible",
        ):
            assert setting in unit


def shell_python(script, invocation):
    """Execute the actual embedded script without running host mutations."""
    source = (ROOT / script).read_text()
    return source.split(invocation + " <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


@pytest.mark.parametrize("kind", ["symlink", "dangling", "existing"])
def test_backup_never_follows_or_overwrites_destination(tmp_path, kind):
    target = tmp_path / "private"
    target.write_text("keep me")
    output = tmp_path / "backup.tar.gz"
    if kind == "existing":
        output.write_text("existing backup")
    else:
        output.symlink_to(target if kind == "symlink" else tmp_path / "missing")
    code = shell_python("scripts/backup.sh", 'python3 - "$OUTPUT" "$STAGE"')
    result = subprocess.run(
        [sys.executable, "-c", code, str(output), str(tmp_path)], capture_output=True
    )
    assert result.returncode != 0
    assert target.read_text() == "keep me"
    if kind == "existing":
        assert output.read_text() == "existing backup"
    assert not (tmp_path / "missing").exists()


def test_backup_is_private_from_creation(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tar = fake_bin / "tar"
    tar.write_text("#!/bin/sh\nprintf 'isolated backup fixture'\n")
    tar.chmod(0o755)
    output = tmp_path / "backup.tar.gz"
    code = shell_python("scripts/backup.sh", 'python3 - "$OUTPUT" "$STAGE"')
    result = subprocess.run(
        [sys.executable, "-c", code, str(output), str(tmp_path)],
        env={**os.environ, "PATH": str(fake_bin)},
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == "isolated backup fixture"
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "readable"])
def test_installer_secret_rejects_unsafe_files(tmp_path, kind):
    password = tmp_path / "password"
    password.write_text("ValidPassword123!")
    password.chmod(0o600)
    candidate = tmp_path / "candidate"
    if kind == "symlink":
        candidate.symlink_to(password)
    elif kind == "fifo":
        os.mkfifo(candidate, 0o600)
    elif kind == "hardlink":
        os.link(password, candidate)
    else:
        candidate.write_text("ValidPassword123!")
        candidate.chmod(0o644)
    code = shell_python(
        "install.sh", 'python3 - "$PASSWORD_FILE" "$STAGE/admin-password"'
    )
    output = tmp_path / "copy"
    result = subprocess.run(
        [sys.executable, "-c", code, str(candidate), str(output)],
        capture_output=True,
        timeout=3,
    )
    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.skipif(
    os.geteuid() != 0, reason="Installer only accepts root-owned password files"
)
def test_installer_copies_private_password(tmp_path):
    password = tmp_path / "password"
    password.write_text("ValidPassword123!\n")
    password.chmod(0o600)
    output = tmp_path / "copy"
    code = shell_python(
        "install.sh", 'python3 - "$PASSWORD_FILE" "$STAGE/admin-password"'
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(password), str(output)], capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == "ValidPassword123!"
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "host, missing, accepted",
    [
        ("127.0.0.1", False, True),
        ("[::1]", False, True),
        ("0.0.0.0", False, False),
        ("[::]", False, False),
        ("127.0.0.1", True, False),
    ],
)
def test_health_detects_missing_or_exposed_private_listener(
    tmp_path, host, missing, accepted
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ss = fake_bin / "ss"
    rows = "\n".join(
        f"LISTEN 0 128 {host}:{port} 0.0.0.0:*"
        for port in (8000, 1143, 1587, 8891, 8892)
        if not (missing and port == 8000)
    )
    ss.write_text("#!/bin/sh\ncat <<'LISTENERS'\n" + rows + "\nLISTENERS\n")
    ss.chmod(0o755)
    code = shell_python(
        "scripts/health.sh", "check 'private listeners stay on loopback' python3 -"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PATH": str(fake_bin) + ":/usr/bin:/bin"},
        capture_output=True,
    )
    assert (result.returncode == 0) is accepted, result.stderr


@pytest.mark.parametrize("web_host, private_host, opt_in, accepted", [
    ("0.0.0.0", "127.0.0.1", "0.0.0.0", True),
    ("0.0.0.0", "[::1]", "0.0.0.0", True),
    ("0.0.0.0", "0.0.0.0", "0.0.0.0", False),
    ("127.0.0.1", "127.0.0.1", "0.0.0.0", False),
    ("[::]", "127.0.0.1", "0.0.0.0", False),
    ("0.0.0.0", "127.0.0.1", "*", False),
])
def test_health_web_bind_opt_in_only_changes_web_listener(
    tmp_path, web_host, private_host, opt_in, accepted
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ss = fake_bin / "ss"
    rows = "\n".join(
        f"LISTEN 0 128 {web_host if port == 8000 else private_host}:{port} 0.0.0.0:*"
        for port in (8000, 1143, 1587, 8891, 8892)
    )
    ss.write_text("#!/bin/sh\ncat <<'LISTENERS'\n" + rows + "\nLISTENERS\n")
    ss.chmod(0o755)
    code = shell_python(
        "scripts/health.sh", "check 'private listeners stay on loopback' python3 -"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PATH": str(fake_bin) + ":/usr/bin:/bin", "KAGNODE_WEB_BIND": opt_in},
        capture_output=True,
    )
    assert (result.returncode == 0) is accepted, result.stderr


def test_cohost_render_keeps_mail_identity_and_proxy_trust_separate(tmp_path):
    args = render.parser().parse_args([
        '--domain', 'example.com', '--hostname', 'mail.example.com',
        '--ipv4', '203.0.113.10', '--admin', 'caleb@example.com', '--output', str(tmp_path),
        '--service-user', 'kagnode-mail', '--read-group', 'kagnode-mail-read',
        '--certificate-dir', '/etc/kagnode/mail/tls/current', '--trusted-proxy', '192.168.0.6'])
    files = render.render(args)
    for name in ('web', 'guard', 'maintenance'):
        unit = files[f'etc/systemd/system/kagnode-{name}.service']
        assert 'User=kagnode-mail\nGroup=kagnode-mail\n' in unit
        assert 'Environment=KAGNODE_SERVICE_USER=kagnode-mail' in unit
    assert 'extra_groups = kagnode-mail-read' in files['etc/dovecot/dovecot.conf']
    assert 'set_real_ip_from 192.168.0.6;' in files['etc/nginx/sites-available/kagnode']
    assert 'set_real_ip_from 0.0.0.0' not in files['etc/nginx/sites-available/kagnode']
    assert '/etc/kagnode/mail/tls/current/privkey.pem' in files['etc/postfix/main.cf']
    assert '127.0.0.1:8000' in files['etc/systemd/system/kagnode-web.service']
    args.service_user = 'root\nExecStart=/bin/false'
    with pytest.raises(ValueError): render.render(args)
    args.service_user = 'kagnode-mail'
    args.certificate_dir = '/etc/../tmp/key'
    with pytest.raises(ValueError): render.render(args)
