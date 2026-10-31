#!/usr/bin/env bash
# Consistent backup with a short, explicit mail-service outage.
set -Eeuo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# == 1 ]] || { echo 'Usage: sudo scripts/backup.sh /absolute/path/kagnode-backup.tar.gz' >&2; exit 2; }
[[ $1 == /* && ! -e $1 && ! -L $1 ]] || { echo 'Choose a new absolute output path.' >&2; exit 2; }
[[ -f /etc/kagnode/mail/config.json ]] || { echo 'KagNode Mail is not installed.' >&2; exit 1; }
exec 9>/run/lock/kagnode-install.lock
flock -n 9 || { echo 'Another KagNode Mail maintenance operation is running.' >&2; exit 1; }
OUTPUT=$1
mkdir -p -- "$(dirname -- "$OUTPUT")"
STAGE=$(mktemp -d /var/lib/kagnode-backup.XXXXXXXX)
ACTIVE=()
for UNIT in kagnode-web kagnode-guard postfix dovecot; do
 if systemctl is-active --quiet "$UNIT"; then ACTIVE+=("$UNIT"); fi
done
cleanup() {
 if ((${#ACTIVE[@]})); then systemctl start "${ACTIVE[@]}"; fi
 rm -rf -- "$STAGE"
}
trap cleanup EXIT
printf 'Pausing active mail services for a consistent mailbox and SQLite snapshot.\n'
if ((${#ACTIVE[@]})); then systemctl stop "${ACTIVE[@]}"; fi
# Exclude sessions and API credentials: a restore requires fresh authentication.
install -d -m 700 "$STAGE/var/lib/kagnode/mail"
python3 - "$STAGE/var/lib/kagnode/mail/directory.sqlite3" <<'PY'
import os,sqlite3,sys
database = '/var/lib/kagnode/mail/directory.sqlite3'
# Services have closed the DELETE-journal database. Refuse recovery artifacts
# rather than allowing root SQLite to follow a service-controlled journal path.
if any(os.path.lexists(database + suffix) for suffix in ('-journal','-wal','-shm')):
 raise SystemExit('Database recovery artifacts remain. Recover it as the kagnode service user and retry the backup.')
with sqlite3.connect('file:' + database + '?mode=ro&immutable=1', uri=True) as src, sqlite3.connect(sys.argv[1]) as dst:
 src.backup(dst)
 dst.execute('DELETE FROM sessions')
 dst.execute('DELETE FROM api_tokens')
PY
# Store the snapshot database at its normal archive path, then configuration,
# mailbox files and queued mail. Numeric owners are preserved for this server.
python3 - "$OUTPUT" "$STAGE" <<'PY'
import os, subprocess, sys
# Exclusive, no-follow creation closes the check/use race and guarantees mode
# 0600 from the first byte. Tar writes the already-open descriptor, never a
# pathname that could have been replaced with a symlink.
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as output:
 subprocess.run([
  'tar', '--numeric-owner', '-cz', '-C', sys.argv[2],
  'var/lib/kagnode/mail/directory.sqlite3', '-C', '/',
  'etc/kagnode/mail', 'etc/postfix', 'etc/dovecot', 'etc/opendkim', 'etc/opendkim.conf',
  'etc/letsencrypt', 'etc/nginx', 'etc/systemd/system/kagnode-web.service',
  'etc/systemd/system/kagnode-guard.service', 'etc/systemd/system/kagnode-maintenance.service',
  'etc/systemd/system/kagnode-maintenance.timer', 'etc/fail2ban/jail.d/kagnode.conf',
  'var/vmail', 'var/spool/postfix', 'opt/kagnode/mail'
 ], stdout=output, check=True)
 output.flush()
 os.fsync(output.fileno())
PY
printf 'Backup saved: %s\nContains passwords, private keys, queued mail and private messages. Store encrypted off-server.\n' "$OUTPUT"
