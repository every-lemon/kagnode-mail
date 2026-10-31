# Operations

## Health and logs

```bash
sudo bash /opt/kagnode/mail/scripts/health.sh
sudo journalctl -u kagnode-web -u kagnode-guard --since '1 hour ago'
sudo journalctl -u postfix -u dovecot -u opendkim --since '1 hour ago'
sudo postqueue -p
sudo python3 /opt/kagnode/mail/scripts/resources.py
```

Some Postfix child processes use syslog identifiers such as `postfix/smtp`, so also inspect the system journal or `/var/log/mail.log` when your host has rsyslog. nginx logs are `/var/log/nginx/kagnode-{access,error}.log` and use the distribution's nginx log rotation. System journals are managed by the OS; set an appropriate journald retention cap on a dedicated small disk.

Private listeners 8000, 1143, 1587, 8891, and 8892 must remain on 127.0.0.1. `health.sh` displays them. A successful local health check does not prove external DNS, port reachability, or deliverability.

## Recover a password

Use a root terminal on the mail server:

```bash
cd /opt/kagnode/mail
sudo python3 -m kagnode.cli reset-password --admin person@example.com
sudo doveadm kick person@example.com
```

The password is prompted without echo. This invalidates web sessions and permanently revokes every API token for that mailbox. Dovecot does not automatically disconnect existing native IMAP sessions when a database password/active flag changes. **For immediate native-session revocation after disabling an account or resetting a password, run `doveadm kick FULL_EMAIL`.** New authentications are denied after disabling; submission identity checks consult current active status, including already-authenticated SMTP connections. A password reset alone does not terminate an existing authenticated SMTP connection. For a compromised account, disable it first, kick its IMAP sessions, and have the operator terminate any existing SMTP client connections before re-enabling it.

Admin **Edit** disables accounts without deleting their messages. Keep at least one active administrator. Re-enable a preserved account to restore access; do not recycle disabled addresses without a deliberate data-retention decision.

## Back up

Keep encrypted copies on a different machine/storage provider and test restoration. A backup stored only on the mail server is not enough.

```bash
sudo bash /opt/kagnode/mail/scripts/backup.sh /var/backups/kagnode-2026-10-29.tar.gz
```

This script **briefly stops active web, SMTP, guard, and IMAP services** to take a consistent snapshot, then starts the services that were active. It preserves mail, the Postfix queue, SQLite state, configuration, app code, TLS keys, and DKIM keys. Session and API token records are removed from the backup so restoration requires fresh logins and newly issued integration tokens. The archive is created exclusively with mode 0600 from its first byte and cannot overwrite an existing file or destination symlink. It contains highly sensitive material. The script does not encrypt or upload it.

If the stopped SQLite database still has a rollback or WAL journal, backup refuses to recover it as root. Start the application so recovery occurs as the `kagnode` user, investigate any database errors, then retry the backup. A failed archive is incomplete; preserve it only for diagnosis and choose a new output filename for the next attempt.

Allow enough space for the archive and a small database staging copy. Do not delete active queues or mailbox data to make room. The script does not automatically schedule backups; choose a maintenance window and retention policy suited to your data.

## Restore

Restoration is an operator-controlled recovery, not an installer flag:

1. Verify archive integrity with `gzip -t` and inspect paths with `tar -tzf`. Keep the original archive intact.
2. Prepare a dedicated Ubuntu 24.04 host with the same packages and service accounts. Record the original numeric UIDs/GIDs or remap restored ownership to the new `vmail`, `kagnode`, and other package accounts. Numeric IDs are not guaranteed to match across fresh hosts.
3. Stop the mail/web services. Restore into a staging directory first, inspect it, then copy the intended configuration, application, database, mail, and queue back to their documented paths. Do not overwrite an unrelated host's nginx or mail setup.
4. Restore `/var/lib/kagnode/mail` as `kagnode:kagnode-read`, directory mode 2750, database mode 0640; `/var/vmail` as `vmail:vmail`; `/etc/kagnode/mail/config.json` as `root:kagnode` 0640; DKIM private key as `opendkim:opendkim` 0600. Update Dovecot's UID/GID settings if IDs differ. Preserve Maildir and queue permissions.
5. Verify `postfix check`, `doveconf -n`, `opendkim -n -x /etc/opendkim.conf`, and `nginx -t`. Reload systemd, start services, and check queues and a local delivery.
6. Update A/PTR if the public IP changed, verify certificate validity/renewal, and test external delivery before considering recovery complete.

DKIM keys must match published DNS. The database and application encryption key must be restored as a pair, although saved sessions are deliberately omitted. Root-owned configuration backups created by installation live under `/var/backups/kagnode/config-TIMESTAMP`; these are configuration snapshots, **not mail backups**.

## Rename and older installations

KagNode Mail 0.3 uses the `kagnode` Python package and service account, `kagnode-web`/`kagnode-guard` units, and `/etc/kagnode/mail`, `/opt/kagnode/mail`, `/var/lib/kagnode/mail` paths. The installer refuses an existing Morrow prototype; renaming files alone is not a supported migration. Preserve a verified backup and export mail/accounts before a deliberate move to a fresh host. Never delete old mail data or keys simply to bypass this refusal. The new cookie names require a fresh sign-in.

## Update

This release has no automatic updater or database migration framework. Take a tested backup first and review release notes. Apply Ubuntu security updates through your normal server maintenance process. **0.3.0 requires a fresh installation:** token tables, revocation triggers, and mail authentication configuration changed together. Copying application code alone from 0.2 is not supported. Existing installs require a separately reviewed migration; preserve accounts, mail, and keys. The GitHub repository rename to `mail` does not change server paths.

For compatible same-schema KagNode Mail source updates, replace `/opt/kagnode/mail/kagnode` and `/opt/kagnode/mail/scripts` from a reviewed release as root, preserving `/etc/kagnode/mail`, the database, Maildir, queue, and DKIM/TLS keys; then restart `kagnode-web` and `kagnode-guard` and run health checks.

The installer `--resume` reapplies generated service configurations and creates config backups. It is for resuming/reapplying this same installation, and will overwrite manual edits in managed files. It does not upgrade schema across future releases or merge local changes. Always review changes to renderer output before reapplying. Never use `git pull` as root from an unreviewed remote and immediately execute it.

## Certificates

Production uses certbot's systemd timer. A deploy hook validates config and reloads nginx/Postfix/Dovecot after renewal. Check:

```bash
systemctl list-timers certbot.timer
sudo certbot renew --dry-run
```

Keep HTTP port 80 and the ACME webroot route available. The isolated `--test-certificate` path is deliberately not renewed and expires in seven days. To move a lab to production, obtain a real certificate and verify the full production setup on a fresh dedicated host; do not assume a browser exception is sufficient.

## Storage and quotas

Default mailbox quota: 1024 MiB. Administrators can set 64 MiB–1 TiB per account. Quotas include Sent, Drafts, Archive, Junk, and Trash. Moving to Trash does not recover disk until permanently deleted. Quota changes apply on new Dovecot user sessions; use `doveadm kick` for immediate application to connected clients and `doveadm quota recalc -u EMAIL` if counters need rebuilding.

Audit retention is bounded; transient session/counter cleanup runs daily. Mail and queued outbound messages are not automatically erased. Install cache removal is intentionally left to the OS administrator; the installer does not remove unrelated caches or files. Maildir stores what arrives; actual disk requirements are driven primarily by attachment volume and retention.
