#!/usr/bin/env python3
"""Render native Ubuntu 24.04 configuration without modifying the host."""

import argparse
import ipaddress
import json
import re
from pathlib import Path


def validate(domain, hostname, ipv4, ipv6, admin):
    pattern = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
    for value in (domain, hostname):
        if len(value) > 253 or not re.fullmatch(pattern, value):
            raise ValueError(
                "Domain and hostname must be lower-case fully qualified DNS names."
            )
    if hostname == domain:
        raise ValueError("Use a separate mail hostname, such as mail." + domain)
    if ipaddress.ip_address(ipv4).version != 4:
        raise ValueError("--ipv4 must be an IPv4 address.")
    if ipv6 and ipaddress.ip_address(ipv6).version != 6:
        raise ValueError("--ipv6 must be an IPv6 address.")
    if (
        not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?@" + re.escape(domain), admin
        )
        or ".." in admin
    ):
        raise ValueError("Administrator address must belong to the initial domain.")


def render(a):
    validate(a.domain, a.hostname, a.ipv4, a.ipv6, a.admin)
    root = Path(a.output)
    cert = "/etc/letsencrypt/live/" + a.hostname
    database = "/var/lib/kagnode/directory.sqlite3"
    files = {}
    files[
        "etc/postfix/main.cf"
    ] = f"""# Managed by KagNode Mail. Ubuntu 24.04 / Postfix 3.8.
compatibility_level = 3.6
myhostname = {a.hostname}
mydomain = {a.domain}
myorigin = $mydomain
mydestination = localhost
inet_interfaces = all
inet_protocols = {"all" if a.ipv6 else "ipv4"}
mynetworks = 127.0.0.0/8 [::1]/128
relay_domains =
relayhost =
smtpd_banner = $myhostname ESMTP
biff = no
append_dot_mydomain = no
readme_directory = no
alias_maps = hash:/etc/aliases
alias_database = hash:/etc/aliases
mailbox_size_limit = 0
message_size_limit = 26214400
smtputf8_enable = no
recipient_delimiter = +
virtual_mailbox_domains = sqlite:/etc/postfix/kagnode-domains.cf
virtual_mailbox_maps = sqlite:/etc/postfix/kagnode-users.cf
virtual_alias_maps = sqlite:/etc/postfix/kagnode-aliases.cf
virtual_transport = lmtp:unix:private/dovecot-lmtp
smtpd_sender_login_maps = sqlite:/etc/postfix/kagnode-senders.cf
smtpd_relay_restrictions = permit_sasl_authenticated, reject_unauth_destination
smtpd_recipient_restrictions = reject_non_fqdn_recipient, reject_unknown_recipient_domain, reject_unlisted_recipient
smtpd_helo_required = yes
smtpd_sasl_type = dovecot
smtpd_sasl_path = private/auth
smtpd_sasl_auth_enable = no
smtpd_sasl_security_options = noanonymous
smtpd_tls_cert_file = {cert}/fullchain.pem
smtpd_tls_key_file = {cert}/privkey.pem
smtpd_tls_security_level = may
smtpd_tls_auth_only = yes
smtpd_tls_mandatory_protocols = >=TLSv1.2
smtpd_tls_protocols = >=TLSv1.2
smtp_tls_protocols = >=TLSv1.2
smtp_tls_mandatory_protocols = >=TLSv1.2
smtp_tls_security_level = may
smtp_tls_CAfile = /etc/ssl/certs/ca-certificates.crt
smtp_tls_loglevel = 1
smtpd_tls_loglevel = 1
milter_protocol = 6
milter_default_action = tempfail
# Signing is on submission only; inbound port 25 does not trust local-network senders.
smtpd_milters =
non_smtpd_milters =
milter_mail_macros = i {{auth_type}} {{auth_authen}} {{auth_author}} {{mail_addr}}
disable_vrfy_command = yes
smtpd_delay_reject = yes
smtpd_client_connection_count_limit = 10
smtpd_client_connection_rate_limit = 30
smtpd_client_message_rate_limit = 60
smtpd_recipient_limit = 50
default_process_limit = 20
smtp_destination_concurrency_limit = 5
lmtp_destination_concurrency_limit = 5
maximal_queue_lifetime = 3d
bounce_queue_lifetime = 1d
"""
    files[
        "etc/postfix/master.cf"
    ] = """# service type private unpriv chroot wakeup maxproc command
smtp      inet  n       -       n       -       12      smtpd
submission inet n      -       n       -       8       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_sender_restrictions=reject_authenticated_sender_login_mismatch
  -o smtpd_relay_restrictions=permit_sasl_authenticated,reject
  -o smtpd_recipient_restrictions=permit_sasl_authenticated,reject
  -o smtpd_milters=inet:127.0.0.1:8892,inet:127.0.0.1:8891
127.0.0.1:1587 inet n  -       n       -       4       smtpd
  -o syslog_name=postfix/webmail
  -o smtpd_sasl_service=kagnode-private
  -o smtpd_tls_security_level=none
  -o smtpd_tls_auth_only=no
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_sender_restrictions=reject_authenticated_sender_login_mismatch
  -o smtpd_relay_restrictions=permit_sasl_authenticated,reject
  -o smtpd_recipient_restrictions=permit_sasl_authenticated,reject
  -o smtpd_milters=inet:127.0.0.1:8892,inet:127.0.0.1:8891
pickup    unix  n       -       n       60      1       pickup
cleanup   unix  n       -       n       -       0       cleanup
qmgr      unix  n       -       n       300     1       qmgr
tlsmgr    unix  -       -       n       1000?   1       tlsmgr
rewrite   unix  -       -       n       -       -       trivial-rewrite
bounce    unix  -       -       n       -       0       bounce
defer     unix  -       -       n       -       0       bounce
trace     unix  -       -       n       -       0       bounce
verify    unix  -       -       n       -       1       verify
flush     unix  n       -       n       1000?   0       flush
proxymap  unix  -       -       n       -       -       proxymap
proxywrite unix -      -       n       -       1       proxymap
smtp      unix  -       -       n       -       -       smtp
relay     unix  -       -       n       -       -       smtp
showq     unix  n       -       n       -       -       showq
error     unix  -       -       n       -       -       error
retry     unix  -       -       n       -       -       error
discard   unix  -       -       n       -       -       discard
local     unix  -       n       n       -       -       local
virtual   unix  -       n       n       -       -       virtual
lmtp      unix  -       -       n       -       -       lmtp
anvil     unix  -       -       n       -       1       anvil
scache    unix  -       -       n       -       1       scache
postlog   unix-dgram n  -       n       -       1       postlogd
"""
    queries = {
        "domains": "SELECT name FROM domains WHERE name='%s'",
        "users": "SELECT email FROM users WHERE email='%s' AND active=1",
        "aliases": "SELECT a.destination FROM aliases a JOIN users u ON u.email=a.destination WHERE a.email='%s' AND u.active=1",
        "senders": "SELECT owner FROM sender_owners WHERE email='%s' UNION SELECT 'api-' || t.id || '@api.invalid' FROM api_tokens t JOIN users u ON u.id=t.user_id JOIN sender_owners o ON o.owner=u.email WHERE o.email='%s' AND u.active=1 AND t.revoked IS NULL AND (t.expires IS NULL OR t.expires>CAST(strftime('%%s','now') AS INTEGER)) AND EXISTS (SELECT 1 FROM json_each(t.scopes) WHERE value='send') AND EXISTS (SELECT 1 FROM json_each(t.senders) WHERE value=o.email)",
    }
    for key, query in queries.items():
        files[f"etc/postfix/kagnode-{key}.cf"] = (
            f"dbpath = {database}\nquery = {query}\n"
        )
    files[
        "etc/dovecot/dovecot.conf"
    ] = f"""# Standalone config, intentionally excludes distribution auth-system defaults.
protocols = imap lmtp
listen = *, ::
ssl = required
ssl_min_protocol = TLSv1.2
ssl_cert = <{cert}/fullchain.pem
ssl_key = <{cert}/privkey.pem
ssl_dh = </usr/share/dovecot/dh.pem
disable_plaintext_auth = yes
auth_mechanisms = plain login
auth_username_format = %Lu
auth_cache_size = 0
mail_home = /var/vmail/%d/%n
mail_location = maildir:~/Maildir
mail_uid = {a.uid}
mail_gid = {a.gid}
first_valid_uid = {a.uid}
mail_privileged_group = vmail
mail_plugins = quota
passdb {{
 driver = sql
 args = /etc/dovecot/kagnode-sql.conf.ext
}}
userdb {{
 driver = sql
 args = /etc/dovecot/kagnode-sql.conf.ext
}}
service auth {{
 extra_groups = kagnode-read
 unix_listener /var/spool/postfix/private/auth {{
  mode = 0660
  user = postfix
  group = postfix
 }}
}}
service auth-worker {{
 extra_groups = kagnode-read
 user = dovecot
}}
service imap-login {{
 inet_listener imap {{
  port = 0
 }}
 inet_listener imaps {{
  port = 993
  ssl = yes
 }}
 inet_listener webmail {{
  address = 127.0.0.1
  port = 1143
  ssl = no
 }}
 process_min_avail = 0
 process_limit = 24
}}
# Local loopback webmail is private to this host; public clients require TLS.
remote 127.0.0.1 {{
 ssl = yes
 disable_plaintext_auth = no
}}
service imap {{
 process_limit = 24
 vsz_limit = 256M
}}
service lmtp {{
 unix_listener /var/spool/postfix/private/dovecot-lmtp {{
  mode = 0600
  user = postfix
  group = postfix
 }}
 process_min_avail = 0
 process_limit = 4
}}
protocol imap {{
 mail_plugins = $mail_plugins imap_quota
}}
protocol lmtp {{
 postmaster_address = {a.admin}
}}
namespace inbox {{
 inbox = yes
 separator = /
 mailbox Sent {{
  auto = subscribe
  special_use = \\Sent
 }}
 mailbox Drafts {{
  auto = subscribe
  special_use = \\Drafts
 }}
 mailbox Archive {{
  auto = subscribe
  special_use = \\Archive
 }}
 mailbox Junk {{
  auto = subscribe
  special_use = \\Junk
 }}
 mailbox Trash {{
  auto = subscribe
  special_use = \\Trash
 }}
}}
plugin {{
 quota = maildir:User quota
 quota_rule = *:storage=1G
}}
mail_max_userip_connections = 10
log_path = syslog
"""
    files["etc/dovecot/kagnode-sql.conf.ext"] = f"""driver = sqlite
connect = {database}
default_pass_scheme = SHA512-CRYPT
# Postfix does not pass lport to Dovecot. Only its private 1587 listener uses
# service=kagnode-private. IMAP supplies lport; loopback public 993 still fails.
# Keep token logins intact so the submission guard can recheck their restrictions.
password_query = SELECT email AS user, password FROM users WHERE email='%u' AND active=1 AND email NOT GLOB 'api-*@api.invalid' UNION ALL SELECT 'api-' || t.id || '@api.invalid' AS user, t.backend_hash AS password FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE 'api-' || t.id || '@api.invalid'='%u' AND u.active=1 AND t.revoked IS NULL AND (t.expires IS NULL OR t.expires>CAST(strftime('%%s','now') AS INTEGER)) AND '%l'='127.0.0.1' AND '%r'='127.0.0.1' AND (('%s'='imap' AND '%a'='1143') OR ('%s'='kagnode-private' AND EXISTS (SELECT 1 FROM json_each(t.scopes) WHERE value='send')))
user_query = SELECT '/var/vmail/' || substr(email,instr(email,'@')+1) || '/' || substr(email,1,instr(email,'@')-1) AS home, {a.uid} AS uid, {a.gid} AS gid, '*:bytes=' || (quota_mb*1048576) AS quota_rule FROM users WHERE email='%u' AND active=1 AND email NOT GLOB 'api-*@api.invalid' UNION ALL SELECT '/var/vmail/' || substr(u.email,instr(u.email,'@')+1) || '/' || substr(u.email,1,instr(u.email,'@')-1) AS home, {a.uid} AS uid, {a.gid} AS gid, '*:bytes=' || (u.quota_mb*1048576) AS quota_rule FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE 'api-' || t.id || '@api.invalid'='%u' AND u.active=1 AND t.revoked IS NULL AND (t.expires IS NULL OR t.expires>CAST(strftime('%%s','now') AS INTEGER)) AND '%s'='imap' AND '%a'='1143' AND '%l'='127.0.0.1' AND '%r'='127.0.0.1'
iterate_query = SELECT email AS username FROM users WHERE active=1
"""
    files["etc/opendkim.conf"] = """Syslog yes
UMask 007
UserID opendkim
Socket inet:8891@127.0.0.1
PidFile /run/opendkim/opendkim.pid
Mode s
Canonicalization relaxed/simple
SignatureAlgorithm rsa-sha256
OversignHeaders From
KeyTable file:/etc/opendkim/KeyTable
SigningTable refile:/etc/opendkim/SigningTable
InternalHosts 127.0.0.1,::1
MacroList {auth_type}
RequireSafeKeys yes
"""
    files["etc/opendkim/KeyTable"] = "mail %:mail:/etc/opendkim/keys/mail.private\n"
    files["etc/opendkim/SigningTable"] = "*@* mail\n"
    files["etc/nginx/nginx.conf"] = """user www-data;
worker_processes 1;
pid /run/nginx.pid;
include /etc/nginx/modules-enabled/*.conf;
events { worker_connections 256; }
http {
 include /etc/nginx/mime.types;
 default_type application/octet-stream;
 sendfile on;
 tcp_nopush on;
 keepalive_timeout 30;
 server_tokens off;
 # Authenticated responses contain secrets; do not compress them (BREACH).
 gzip off;
 client_header_timeout 15s;
 client_body_timeout 30s;
 send_timeout 30s;
 reset_timedout_connection on;
 access_log /var/log/nginx/access.log;
 error_log /var/log/nginx/error.log;
 include /etc/nginx/conf.d/*.conf;
 include /etc/nginx/sites-enabled/*;
}
"""
    files[
        "etc/nginx/sites-available/kagnode"
    ] = f"""# The mail host must be DNS-only, not behind an HTTP CDN proxy.
limit_req_zone $binary_remote_addr zone=kagnode_login:1m rate=5r/m;
limit_req_zone $binary_remote_addr zone=kagnode_api:1m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=kagnode_connections:1m;
server {{
 listen 80;
 listen [::]:80;
 server_name {a.hostname};
 if ($host != {a.hostname}) {{ return 421; }}
 location ^~ /.well-known/acme-challenge/ {{ root /var/www/kagnode-acme; }}
 location / {{ return 301 https://{a.hostname}$request_uri; }}
}}
server {{
 listen 443 ssl;
 listen [::]:443 ssl;
 server_name {a.hostname};
 if ($host != {a.hostname}) {{ return 421; }}
 ssl_certificate {cert}/fullchain.pem;
 ssl_certificate_key {cert}/privkey.pem;
 ssl_protocols TLSv1.2 TLSv1.3;
 ssl_session_cache shared:KAGNODE:1m;
 ssl_session_tickets off;
 ssl_ciphers ECDHE+AESGCM:ECDHE+CHACHA20;
 ssl_prefer_server_ciphers off;
 limit_conn kagnode_connections 12;
 limit_conn_status 429;
 server_tokens off;
 client_max_body_size 24m;
 add_header Strict-Transport-Security "max-age=31536000" always;
 access_log /var/log/nginx/kagnode-access.log;
 error_log /var/log/nginx/kagnode-error.log;
 location /static/ {{
  alias /opt/kagnode/kagnode/static/;
  expires 1h;
  add_header X-Content-Type-Options nosniff always;
  add_header Strict-Transport-Security "max-age=31536000" always;
  gzip on;
  gzip_types text/css application/javascript image/svg+xml;
 }}
 location = /api/login {{
  limit_req zone=kagnode_login burst=5 nodelay;
  limit_req_status 429;
  proxy_pass http://127.0.0.1:8000;
  include /etc/nginx/kagnode-proxy.conf;
 }}
 location / {{
  limit_req zone=kagnode_api burst=30 nodelay;
  limit_req_status 429;
  proxy_pass http://127.0.0.1:8000;
  include /etc/nginx/kagnode-proxy.conf;
 }}
}}
"""
    files["etc/nginx/kagnode-proxy.conf"] = f"""proxy_set_header Host {a.hostname};
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Accept-Encoding "";
proxy_connect_timeout 5s;
proxy_read_timeout 90s;
"""
    base_service = """User=kagnode
Group=kagnode
SupplementaryGroups=kagnode-read
WorkingDirectory=/opt/kagnode
Environment=PYTHONDONTWRITEBYTECODE=1
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectClock=true
ProtectKernelLogs=true
ProtectProc=invisible
ProcSubset=pid
CapabilityBoundingSet=
AmbientCapabilities=
RestrictRealtime=true
RestrictNamespaces=true
RemoveIPC=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/kagnode
RestrictSUIDSGID=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
Restart=on-failure
RestartSec=3
"""
    files["etc/systemd/system/kagnode-web.service"] = (
        """[Unit]
Description=KagNode Mail web interface
After=network.target dovecot.service postfix.service

[Service]
"""
        + base_service
        + """Environment=KAGNODE_CONFIG=/etc/kagnode/config.json
ExecStart=/usr/bin/gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 2 --timeout 90 --worker-tmp-dir /run/kagnode-web --access-logfile - kagnode.wsgi:app
RuntimeDirectory=kagnode-web
MemoryHigh=256M
MemoryMax=384M
TasksMax=32

[Install]
WantedBy=multi-user.target
"""
    )
    files["etc/systemd/system/kagnode-guard.service"] = (
        """[Unit]
Description=KagNode Mail sender permission guard
After=network.target
Before=postfix.service

[Service]
"""
        + base_service
        + """ExecStart=/usr/bin/python3 -m kagnode.guard
MemoryHigh=64M
MemoryMax=128M
TasksMax=32

[Install]
WantedBy=multi-user.target
"""
    )
    files["etc/letsencrypt/renewal-hooks/deploy/kagnode-reload"] = """#!/bin/sh
set -eu
/usr/sbin/nginx -t
/usr/sbin/postfix check
/usr/bin/doveconf -n >/dev/null
systemctl reload nginx postfix dovecot
"""
    files["etc/fail2ban/jail.d/kagnode.conf"] = """[sshd]
enabled = true
backend = systemd

[postfix-sasl]
enabled = true
backend = systemd
port = 587
maxretry = 5
findtime = 10m
bantime = 1h

[dovecot]
enabled = true
backend = systemd
port = 993
maxretry = 5
findtime = 10m
bantime = 1h
"""
    files["etc/systemd/system/kagnode-maintenance.service"] = (
        """[Unit]
Description=Expire KagNode Mail web sessions and throttles

[Service]
"""
        + base_service.replace("Restart=on-failure\nRestartSec=3\n", "")
        + """Type=oneshot
ExecStart=/usr/bin/python3 -m kagnode.cli cleanup
"""
    )
    files["etc/systemd/system/kagnode-maintenance.timer"] = """[Unit]
Description=Daily KagNode Mail session cleanup

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
"""
    for path, contents in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    (root / "manifest.json").write_text(json.dumps(sorted(files), indent=2) + "\n")
    return files


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ("domain", "hostname", "ipv4", "admin", "output"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--ipv6", default="")
    p.add_argument("--uid", type=int, default=5000)
    p.add_argument("--gid", type=int, default=5000)
    return p


if __name__ == "__main__":
    try:
        args = parser().parse_args()
        render(args)
        print("Rendered configuration to " + str(Path(args.output).resolve()))
    except ValueError as exc:
        raise SystemExit(str(exc))
