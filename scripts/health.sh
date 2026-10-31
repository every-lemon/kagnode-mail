#!/usr/bin/env bash
set -uo pipefail
FAILED=0
check() {
 local LABEL=$1
 shift
 if "$@"; then printf 'PASS  %s\n' "$LABEL"; else printf 'FAIL  %s\n' "$LABEL"; FAILED=1; fi
}
for UNIT in postfix dovecot opendkim kagnode-guard kagnode-web nginx fail2ban; do
 check "$UNIT service" systemctl is-active --quiet "$UNIT"
done
check 'Postfix master process' postfix status
check 'Postfix configuration' postfix check
check 'Dovecot configuration' sh -c 'doveconf -n >/dev/null'
check 'OpenDKIM configuration' opendkim -n -x /etc/opendkim.conf
check 'nginx configuration' nginx -t
check 'web application' python3 - <<'PY'
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/api/session',timeout=5) as r:
 assert r.status==200
PY
check 'private listeners stay on loopback' python3 - <<'PY'
import ipaddress, os, subprocess
expected = {8000,1143,1587,8891,8892}
web_bind = os.environ.get('KAGNODE_WEB_BIND', 'loopback')
if web_bind not in ('loopback', '0.0.0.0'):
 raise SystemExit('KAGNODE_WEB_BIND must be loopback or 0.0.0.0')
seen = set()
for row in subprocess.check_output(['ss','-H','-ltn'], text=True).splitlines():
 address, _, port = row.split()[3].rpartition(':')
 if port.isdecimal() and int(port) in expected:
  address = address.strip('[]')
  try:
   private = ipaddress.ip_address(address).is_loopback
  except ValueError:
   private = False
  if int(port) == 8000 and web_bind == '0.0.0.0':
   if address != '0.0.0.0':
    raise SystemExit(f'Expected web listener 0.0.0.0:8000, found {address}:{port}')
  elif not private:
   raise SystemExit(f'Private service is exposed: {address}:{port}')
  seen.add(int(port))
if seen != expected:
 raise SystemExit(f'Missing private listeners: {sorted(expected-seen)}')
PY
printf '\nListeners (8000: %s; private ports 1143,1587,8891,8892: loopback only):\n' "${KAGNODE_WEB_BIND:-loopback}"
ss -ltn | awk 'NR==1 || $4 ~ /:(25|80|443|587|993|8000|1143|1587|8891|8892)$/'
printf '\nDNS records: sign in and open Administration → Domains & DNS → Check published DNS.\n'
exit "$FAILED"
