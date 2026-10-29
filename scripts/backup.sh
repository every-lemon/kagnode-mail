#!/usr/bin/env bash
# Consistent backup with a short, explicit mail-service outage.
set -Eeuo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# == 1 ]] || { echo 'Usage: sudo scripts/backup.sh /absolute/path/morrow-backup.tar.gz' >&2; exit 2; }
[[ $1 == /* && ! -e $1 ]] || { echo 'Choose a new absolute output path.' >&2; exit 2; }
[[ -f /etc/morrow/config.json ]] || { echo 'Morrow is not installed.' >&2; exit 1; }
exec 9>/run/lock/morrow-install.lock
flock -n 9 || { echo 'Another Morrow maintenance operation is running.' >&2; exit 1; }
OUTPUT=$1
mkdir -p -- "$(dirname -- "$OUTPUT")"
STAGE=$(mktemp -d /var/lib/morrow-backup.XXXXXXXX)
ACTIVE=()
for UNIT in morrow-web morrow-guard postfix dovecot; do
 if systemctl is-active --quiet "$UNIT"; then ACTIVE+=("$UNIT"); fi
done
cleanup() {
 if ((${#ACTIVE[@]})); then systemctl start "${ACTIVE[@]}"; fi
 rm -rf -- "$STAGE"
}
trap cleanup EXIT
printf 'Pausing active mail services for a consistent mailbox and SQLite snapshot.\n'
if ((${#ACTIVE[@]})); then systemctl stop "${ACTIVE[@]}"; fi
# Exclude transient sessions: a restore must require fresh authentication.
install -d -m 700 "$STAGE/var/lib/morrow"
python3 - "$STAGE/var/lib/morrow/directory.sqlite3" <<'PY'
import sqlite3,sys
with sqlite3.connect('/var/lib/morrow/directory.sqlite3') as src, sqlite3.connect(sys.argv[1]) as dst:
 src.backup(dst)
 dst.execute('DELETE FROM sessions')
PY
# Store the snapshot database at its normal archive path, then configuration,
# mailbox files and queued mail. Numeric owners are preserved for this server.
tar --numeric-owner -czf "$OUTPUT" -C "$STAGE" var/lib/morrow/directory.sqlite3 \
 -C / etc/morrow etc/postfix etc/dovecot etc/opendkim etc/opendkim.conf \
 etc/letsencrypt etc/nginx etc/systemd/system/morrow-web.service \
 etc/systemd/system/morrow-guard.service etc/systemd/system/morrow-maintenance.service \
 etc/systemd/system/morrow-maintenance.timer etc/fail2ban/jail.d/morrow.conf \
 var/vmail var/spool/postfix opt/morrow
chmod 600 "$OUTPUT"
printf 'Backup saved: %s\nContains passwords, private keys, queued mail and private messages. Store encrypted off-server.\n' "$OUTPUT"
