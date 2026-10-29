#!/usr/bin/env bash
# Morrow Mail: native Ubuntu Server 24.04, dedicated fresh server.
set -Eeuo pipefail
umask 027
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DOMAIN='' MAIL_HOST='' IPV4='' IPV6='' ADMIN='' PASSWORD_FILE='' OUTPUT='' DRY_RUN=0 RESUME=0 TEST_CERT=0
usage() {
 cat <<'HELP'
Morrow Mail — native Ubuntu 24.04 mail server

sudo ./install.sh --domain example.com --hostname mail.example.com \
  --ipv4 203.0.113.10 --admin admin@example.com \
  --password-file /root/morrow-admin-password

Required: --domain, --hostname, --ipv4, --admin
Password: --password-file FILE (root-owned mode 0600), or a hidden terminal prompt
Optional: --ipv6 ADDRESS       Enable IPv6 delivery (requires working PTR + routing)
          --dry-run            Render and review configuration without installing
          --output DIRECTORY   Destination for dry-run output
          --resume             Resume a Morrow-owned incomplete installation
          --test-certificate   Self-signed certificate for an isolated lab ONLY
          --help

Requires a dedicated, fresh server. Open TCP 25,80,443,587,993 at your provider.
Set the mail hostname A record before running. Production obtains Let's Encrypt
certificates; --test-certificate is never trusted by public clients.
HELP
}
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
while (($#)); do
 case "$1" in
  --domain|--hostname|--ipv4|--ipv6|--admin|--password-file|--output)
   (($# >= 2)) || die "Missing value for $1"
   case "$1" in
    --domain) DOMAIN=$2;; --hostname) MAIL_HOST=$2;; --ipv4) IPV4=$2;; --ipv6) IPV6=$2;;
    --admin) ADMIN=$2;; --password-file) PASSWORD_FILE=$2;; --output) OUTPUT=$2;;
   esac
   shift 2;;
  --dry-run) DRY_RUN=1; shift;; --resume) RESUME=1; shift;; --test-certificate) TEST_CERT=1; shift;;
  -h|--help) usage; exit 0;; *) die "Unknown option $1";;
 esac
done
[[ -n $DOMAIN && -n $MAIL_HOST && -n $IPV4 && -n $ADMIN ]] || { usage; exit 2; }
command -v python3 >/dev/null || die 'Python 3 is required (included with Ubuntu Server).'
ARGS=(--domain "$DOMAIN" --hostname "$MAIL_HOST" --ipv4 "$IPV4" --ipv6 "$IPV6" --admin "$ADMIN")
if ((DRY_RUN)); then
 [[ -n $OUTPUT ]] || OUTPUT=$(mktemp -d -t morrow-review.XXXXXXXX)
 python3 "$SOURCE_DIR/scripts/render.py" "${ARGS[@]}" --output "$OUTPUT"
 printf '\nDry run only: no packages, accounts, services, firewall, or DNS changed.\nReview: %s\n' "$OUTPUT"
 exit 0
fi
((EUID==0)) || die 'Run the installation with sudo.'
# shellcheck source=/dev/null
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || die 'Only native Ubuntu Server 24.04 is supported.'
[[ -d /run/systemd/system ]] || die 'A running systemd host is required. For isolated lab testing use tests/lab-install.sh.'
exec 9>/run/lock/morrow-install.lock
flock -n 9 || die 'Another Morrow installation is running.'
STAGE=$(mktemp -d -t morrow-install.XXXXXXXX)
PROMPT_SECRET=''
POLICY_ACTIVE=0
restore_policy() {
 if ((POLICY_ACTIVE)); then
  rm -f /usr/sbin/policy-rc.d
  if [[ -e $STAGE/original-policy-rc.d || -L $STAGE/original-policy-rc.d ]]; then mv "$STAGE/original-policy-rc.d" /usr/sbin/policy-rc.d; fi
  POLICY_ACTIVE=0
 fi
}
cleanup() { restore_policy; rm -rf -- "$STAGE"; if [[ -n $PROMPT_SECRET ]]; then rm -f -- "$PROMPT_SECRET"; fi; }
trap cleanup EXIT
trap 'printf "\nInstallation stopped at line %s. No mail or account data was deleted. Review the error, then resume with the same options and --resume.\n" "$LINENO" >&2' ERR
python3 "$SOURCE_DIR/scripts/render.py" "${ARGS[@]}" --output "$STAGE"
if [[ -e /etc/morrow/install-request.json ]]; then
 ((RESUME)) || die 'Morrow already owns this host. Use --resume with the original settings; see docs/operations.md for updates.'
 python3 - "$DOMAIN" "$MAIL_HOST" "$IPV4" "$IPV6" "$ADMIN" "$TEST_CERT" <<'PY'
import json,sys
old=json.load(open('/etc/morrow/install-request.json'))
if old != sys.argv[1:]:
    raise SystemExit('Resume must use the same domain, hostname, IPs, admin, and certificate mode.')
PY
else
 for p in /etc/postfix/main.cf /etc/dovecot/dovecot.conf /etc/opendkim.conf /opt/morrow /var/lib/morrow /var/vmail; do
  [[ ! -e $p ]] || die "Existing mail configuration or data found: $p. Use a fresh dedicated server."
 done
 if [[ -d /etc/nginx/sites-enabled ]] && find /etc/nginx/sites-enabled -mindepth 1 ! -name default -print -quit | read -r _; then
  die 'Existing nginx sites found. Use a dedicated server.'
 fi
 for port in 25 80 443 587 993 8000 1143 1587 8891 8892; do
  if ss -H -ltn "sport = :$port" | read -r _; then die "Port $port is already in use. Use a dedicated server."; fi
 done
fi
AVAILABLE_MB=$(awk '/MemAvailable:/{print int($2/1024)}' /proc/meminfo)
((AVAILABLE_MB>=384)) || die 'At least 384 MiB available RAM is required for installation; 1 GiB server recommended.'
FREE_MB=$(df -Pm / | awk 'NR==2{print $4}')
((FREE_MB>=2048)) || die 'Leave at least 2 GiB free for packages and initial mail storage.'
if [[ -z $PASSWORD_FILE ]]; then
 [[ -t 0 ]] || die 'Noninteractive installation requires --password-file.'
 read -r -s -p 'Initial administrator password (12+ characters): ' PW; printf '\n'
 read -r -s -p 'Confirm password: ' PW_CONFIRM; printf '\n'
 [[ $PW == "$PW_CONFIRM" ]] || die 'Passwords do not match.'
 PROMPT_SECRET=$(mktemp /run/morrow-password.XXXXXXXX)
 chmod 600 "$PROMPT_SECRET"
 printf '%s' "$PW" >"$PROMPT_SECRET"
 unset PW PW_CONFIRM
 PASSWORD_FILE=$PROMPT_SECRET
fi
PASSWORD_FILE=$(realpath -e -- "$PASSWORD_FILE")
python3 - "$PASSWORD_FILE" <<'PY'
import os,stat,sys
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW)
with os.fdopen(fd) as f:
 s=os.fstat(f.fileno()); p=f.read(1024).rstrip('\r\n')
 if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_mode & 0o077 or not 12<=len(p)<=256 or '\x00' in p:
  raise SystemExit('Password file must be root-owned, mode 0600/0400, and contain a 12–256 character password.')
PY
install -d -m 750 /etc/morrow
python3 - "$DOMAIN" "$MAIL_HOST" "$IPV4" "$IPV6" "$ADMIN" "$TEST_CERT" <<'PY'
import json,sys
with open('/etc/morrow/install-request.json','w') as f: json.dump(sys.argv[1:],f)
PY
export DEBIAN_FRONTEND=noninteractive
printf 'postfix postfix/main_mailer_type select No configuration\n' | debconf-set-selections
apt-get update
# Keep distribution defaults from opening mail listeners before Morrow is configured.
if [[ -e /usr/sbin/policy-rc.d || -L /usr/sbin/policy-rc.d ]]; then mv /usr/sbin/policy-rc.d "$STAGE/original-policy-rc.d"; fi
POLICY_ACTIVE=1
printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
install -d -m 755 "$STAGE/apt-archives/partial"
chmod 755 "$STAGE"
apt-get -o "Dir::Cache::archives=$STAGE/apt-archives" install -y --no-install-recommends postfix postfix-sqlite dovecot-core dovecot-imapd dovecot-lmtpd dovecot-sqlite nginx-light opendkim opendkim-tools certbot python3-flask python3-cryptography python3-milter gunicorn sqlite3 dnsutils ca-certificates openssl fail2ban python3-systemd
restore_policy
getent group morrow-read >/dev/null || groupadd --system morrow-read
id morrow >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/morrow --shell /usr/sbin/nologin morrow
id vmail >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/vmail --shell /usr/sbin/nologin vmail
for USER in morrow postfix dovecot; do usermod -a -G morrow-read "$USER"; done
install -d -o morrow -g morrow-read -m 2750 /var/lib/morrow
install -d -o vmail -g vmail -m 750 /var/vmail
install -d -m 755 /opt/morrow /var/www/morrow-acme
cp -a "$SOURCE_DIR/morrow" /opt/morrow/
cp -a "$SOURCE_DIR/scripts" /opt/morrow/
chown -R root:root /opt/morrow
find /opt/morrow -type d -exec chmod 755 {} +
find /opt/morrow -type f -exec chmod 644 {} +
install -d -o opendkim -g opendkim -m 750 /etc/opendkim/keys
if [[ ! -f /etc/opendkim/keys/mail.private ]]; then
 opendkim-genkey -b 2048 -s mail -d "$DOMAIN" -D /etc/opendkim/keys
fi
chown opendkim:opendkim /etc/opendkim/keys/mail.private
chmod 600 /etc/opendkim/keys/mail.private
python3 - <<'PY'
import re
from pathlib import Path
p=Path('/etc/opendkim/keys/mail.txt')
Path('/etc/morrow/dkim-public.txt').write_text(''.join(re.findall(r'"([^"\n]*)"',p.read_text()))+'\n')
PY
PYTHONPATH=/opt/morrow python3 -m morrow.cli bootstrap "${ARGS[@]}" --password-file "$PASSWORD_FILE" --dkim-public-file /etc/morrow/dkim-public.txt
chown root:morrow /etc/morrow/config.json
chmod 640 /etc/morrow/config.json
chmod 751 /etc/morrow
chown morrow:morrow-read /var/lib/morrow/directory.sqlite3
# ACME-only nginx configuration. No mail listener starts before TLS is ready.
if [[ ! -f /etc/letsencrypt/live/$MAIL_HOST/fullchain.pem ]]; then
 cat > /etc/nginx/sites-available/morrow <<EOF
server {
 listen 80;
 listen [::]:80;
 server_name $MAIL_HOST;
 location ^~ /.well-known/acme-challenge/ { root /var/www/morrow-acme; }
 location / { return 503; }
}
EOF
 rm -f /etc/nginx/sites-enabled/default
 ln -sfn /etc/nginx/sites-available/morrow /etc/nginx/sites-enabled/morrow
 nginx -t
 systemctl enable --now nginx
 systemctl reload nginx
 if ((TEST_CERT)); then
  install -d -m 750 "/etc/letsencrypt/live/$MAIL_HOST"
  openssl req -x509 -nodes -newkey rsa:2048 -days 7 -subj "/CN=$MAIL_HOST" -addext "subjectAltName=DNS:$MAIL_HOST" -keyout "/etc/letsencrypt/live/$MAIL_HOST/privkey.pem" -out "/etc/letsencrypt/live/$MAIL_HOST/fullchain.pem"
  chmod 600 "/etc/letsencrypt/live/$MAIL_HOST/privkey.pem"
 else
  certbot certonly --webroot -w /var/www/morrow-acme -d "$MAIL_HOST" --non-interactive --agree-tos --email "$ADMIN"
 fi
fi
python3 "$SOURCE_DIR/scripts/render.py" "${ARGS[@]}" --uid "$(id -u vmail)" --gid "$(id -g vmail)" --output "$STAGE"
BACKUP="/var/backups/morrow/config-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$BACKUP"
python3 - "$STAGE" "$BACKUP" <<'PY'
import json,shutil,sys
from pathlib import Path
stage,backup=map(Path,sys.argv[1:])
for relative in json.loads((stage/'manifest.json').read_text()):
 target=Path('/')/relative
 if target.exists():
  dest=backup/relative; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(target,dest)
 target.parent.mkdir(parents=True,exist_ok=True)
 shutil.copy2(stage/relative,target)
 target.chmod(0o644)
PY
chown root:opendkim /etc/opendkim/KeyTable
chmod 640 /etc/opendkim/KeyTable
chown root:dovecot /etc/dovecot/morrow-sql.conf.ext
chmod 640 /etc/dovecot/morrow-sql.conf.ext
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/morrow-reload
rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/morrow /etc/nginx/sites-enabled/morrow
# Ubuntu's opendkim unit uses a runtime directory owned by its service user.
install -d -o opendkim -g opendkim -m 755 /run/opendkim
newaliases
postfix check
doveconf -n >/dev/null
opendkim -n -x /etc/opendkim.conf
nginx -t
systemctl daemon-reload
systemctl enable morrow-guard morrow-web morrow-maintenance.timer postfix dovecot opendkim nginx fail2ban
systemctl restart dovecot opendkim morrow-guard postfix morrow-web nginx fail2ban
systemctl start morrow-maintenance.timer
if (( ! TEST_CERT )); then systemctl enable --now certbot.timer; fi
for UNIT in morrow-guard morrow-web postfix dovecot opendkim nginx fail2ban; do systemctl is-active --quiet "$UNIT" || die "Service failed: $UNIT (see journalctl -u $UNIT)"; done
postfix status
python3 - <<'READY'
import json, time, urllib.request
for attempt in range(15):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8000/api/session', timeout=2) as response:
   if response.status == 200 and 'csrf' in json.load(response):
    break
 except (OSError, ValueError):
  pass
 time.sleep(1)
else:
 raise SystemExit('Web application did not become ready. Check journalctl -u morrow-web.')
READY
printf 'complete\n' > /etc/morrow/install-status
printf '\nMorrow is ready at https://%s\nSign in as %s using your chosen password.\nOpen Domains & DNS to finish setup.\n\nRequired public TCP ports: 25, 80, 443, 587, 993.\nNo firewall rules were changed. Set your provider PTR to %s.\nConfiguration backup: %s\n' "$MAIL_HOST" "$ADMIN" "$MAIL_HOST" "$BACKUP"
if ((TEST_CERT)); then printf '\nLAB MODE: self-signed certificate expires in 7 days. Not for public deployment.\n'; fi
