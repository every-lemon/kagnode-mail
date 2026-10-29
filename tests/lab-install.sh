#!/usr/bin/env bash
# For a disposable Ubuntu 24.04 container/chroot ONLY. Never run on a real server.
set -Eeuo pipefail
[[ -f /.morrow-lab ]] || { echo 'Refusing: disposable lab marker /.morrow-lab is missing.' >&2; exit 1; }
MODE=${1:-prepare}
cd /src
export PYTHONPATH=/opt/morrow
if [[ $MODE == prepare ]]; then
 export DEBIAN_FRONTEND=noninteractive
 printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d
 chmod 755 /usr/sbin/policy-rc.d
 printf 'postfix postfix/main_mailer_type select No configuration\n' | debconf-set-selections
 apt-get update
 apt-get install -y --no-install-recommends postfix postfix-sqlite dovecot-core dovecot-imapd dovecot-lmtpd dovecot-sqlite nginx-light opendkim opendkim-tools certbot python3-flask python3-cryptography python3-milter gunicorn sqlite3 dnsutils ca-certificates openssl fail2ban python3-systemd iproute2 busybox procps python3-pytest python3-dkim
 groupadd --system morrow-read
 useradd --system --user-group --home-dir /var/lib/morrow --shell /usr/sbin/nologin morrow
 useradd --system --user-group --home-dir /var/vmail --shell /usr/sbin/nologin vmail
 for ACCOUNT in morrow postfix dovecot; do usermod -a -G morrow-read "$ACCOUNT"; done
 install -d -o morrow -g morrow-read -m 2750 /var/lib/morrow
 install -d -o vmail -g vmail -m 750 /var/vmail
 install -d -m 755 /opt/morrow /etc/morrow /var/www/morrow-acme
 cp -a morrow scripts /opt/morrow/
 find /opt/morrow -type d -exec chmod 755 {} +
 find /opt/morrow -type f -exec chmod 644 {} +
 install -d -o opendkim -g opendkim -m 750 /etc/opendkim/keys
 opendkim-genkey -b 2048 -s mail -d example.test -D /etc/opendkim/keys
 chown -R opendkim:opendkim /etc/opendkim/keys
 chmod 600 /etc/opendkim/keys/mail.private
 python3 - <<'PY'
import re
from pathlib import Path
Path('/etc/morrow/dkim-public.txt').write_text(''.join(re.findall(r'"([^"\n]*)"',Path('/etc/opendkim/keys/mail.txt').read_text())))
PY
 printf 'LabOnlyPassword123!' >/tmp/lab-password
 chmod 600 /tmp/lab-password
 python3 -m morrow.cli bootstrap --domain example.test --hostname mail.example.test --ipv4 192.0.2.10 --admin admin@example.test --password-file /tmp/lab-password --dkim-public-file /etc/morrow/dkim-public.txt
 rm /tmp/lab-password
 chown root:morrow /etc/morrow/config.json
 chmod 640 /etc/morrow/config.json
 chown morrow:morrow-read /var/lib/morrow/directory.sqlite3
 python3 scripts/render.py --domain example.test --hostname mail.example.test --ipv4 192.0.2.10 --admin admin@example.test --uid "$(id -u vmail)" --gid "$(id -g vmail)" --output /tmp/rendered
 python3 - <<'FIXMODES'
import json, shutil
from pathlib import Path
for name in json.loads(Path('/tmp/rendered/manifest.json').read_text()):
    target=Path('/')/name
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(Path('/tmp/rendered')/name,target)
    target.chmod(0o644)
FIXMODES
 chown root:dovecot /etc/dovecot/morrow-sql.conf.ext
 chmod 640 /etc/dovecot/morrow-sql.conf.ext
 install -d -m 750 /etc/letsencrypt/live/mail.example.test
 openssl req -x509 -nodes -newkey rsa:2048 -days 1 -subj /CN=mail.example.test -addext subjectAltName=DNS:mail.example.test -keyout /etc/letsencrypt/live/mail.example.test/privkey.pem -out /etc/letsencrypt/live/mail.example.test/fullchain.pem >/dev/null 2>&1
 chmod 600 /etc/letsencrypt/live/mail.example.test/privkey.pem
 ln -sfn /etc/nginx/sites-available/morrow /etc/nginx/sites-enabled/morrow
 rm -f /etc/nginx/sites-enabled/default
 printf '\n192.0.2.10 mail.example.test\n' >>/etc/hosts
 echo 'Lab prepared. Run tests/lab-install.sh test inside a private network namespace.'
elif [[ $MODE == test ]]; then
 cleanup() {
  postfix stop || true
  dovecot stop || true
  nginx -s quit || true
  [[ -z ${WEB_PID:-} ]] || kill "$WEB_PID" || true
  [[ -z ${GUARD_PID:-} ]] || kill "$GUARD_PID" || true
  [[ -z ${DKIM_PID:-} ]] || kill "$DKIM_PID" || true
 }
 trap cleanup EXIT
 ip link set lo up || true
 /bin/busybox syslogd -O /tmp/mail.log
 install -d -o opendkim -g opendkim -m 755 /run/opendkim
 install -d -m 755 /var/spool/postfix/private
 install -d -o morrow -g morrow -m 750 /run/morrow-web
 newaliases
 postfix check
 doveconf -n >/tmp/dovecot-effective.conf
 opendkim -n -x /etc/opendkim.conf
 nginx -t
 /usr/sbin/opendkim -f -x /etc/opendkim.conf >/tmp/opendkim.log 2>&1 & DKIM_PID=$!
 cd /opt/morrow
 runuser -u morrow -- python3 -m morrow.guard >/tmp/guard.log 2>&1 & GUARD_PID=$!
 dovecot
 postfix start
 runuser -u morrow -- gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 2 --timeout 90 --worker-tmp-dir /run/morrow-web morrow.wsgi:app >/tmp/web.log 2>&1 & WEB_PID=$!
 nginx
 python3 /src/tests/integration.py
 if [[ ${MORROW_IDLE_CHECK:-0} == 1 ]]; then
  printf 'Holding the isolated stack idle for five minutes before sampling.\n'
  for _ in {1..10}; do sleep 30; done
  python3 /opt/morrow/scripts/resources.py --sample-seconds 30 >/tmp/morrow-resources.json
  cat /tmp/morrow-resources.json
 else
  python3 /opt/morrow/scripts/resources.py
 fi
else
 echo 'Use prepare or test.' >&2; exit 2
fi
