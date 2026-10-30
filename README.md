# KagNode Mail

<img src="kagnode/static/logo.svg" alt="KagNode logo" width="56" height="50">

Open source mail server for Ubuntu Server 24.04, with webmail and administration.

![KagNode Mail inbox — fictional messages in the read-only preview](docs/screenshots/inbox.png)

KagNode Mail installs Postfix, Dovecot, OpenDKIM, nginx, and a small Python application. SQLite keeps accounts and permissions; Maildir stores messages. No Docker, Node.js runtime, Redis, external fonts, analytics, or database server is required on your mail host.

This is the **0.2.1 release**. Review [validation and limitations](docs/validation.md) before putting it on the public internet. Public mail delivery also depends on your IP reputation, provider rules, DNS, and open port 25.

## What works

- KagNode blue branding with the supplied logo recreated as a small SVG, responsive webmail, system-synced light/dark themes with unchanged KagNode blue, search, pagination, reading, reply, forwarding, attachments, drafts, stars, archive, junk, and trash.
- Safe message rendering: plain text, with readable text extracted from HTML-only mail. No remote tracking images, scripts, or HTML execution.
- Regular members and administrators. Administrators have a normal mailbox too.
- Account creation, disabling, password reset/change, mailbox quotas, and multiple domains.
- Receiving aliases and explicit **send-as grants**, managed separately. Every member can send as their own address; extra identities require an administrator's grant.
- Sender authorization enforced by Postfix and a fail-closed submission milter, including the visible `From` header, for webmail **and native mail clients**.
- DNS records generated for each domain: A, optional AAAA, MX, SPF, DKIM, DMARC, and provider-managed PTR. Copy records, check published DNS, and update the public IPv4 used in the instructions.
- Let's Encrypt TLS, automatic certificate renewal, fail2ban, rate limits, host-scoped secure cookies, CSRF/origin checks, recent-password confirmation for admin changes, encrypted server-side credentials, an audit trail, and backup/recovery tools.

## Preview locally

Python **3.12** is required; this release targets Ubuntu 24.04 and its Python/Dovecot versions.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python scripts/demo.py
```

Open **http://127.0.0.1:8025**. Sign in with `alex@example.com` / `KagNodePreview2026!`.

The preview uses disposable fictional data and cannot send messages or change accounts. It binds only to loopback. The preview password is never provisioned on a real installation. For a remote development host, use `ssh -L 8025:127.0.0.1:8025 your-host` and open the same URL on your computer.

## Install on Ubuntu Server 24.04

Use a **fresh, dedicated server** with a static public IPv4 address. A 1 GiB VPS is the recommended starting point; leave at least 2 GiB free for installation, plus your mailbox and backup space. Read [the DNS guide](docs/dns.md) and check that your provider allows inbound and outbound TCP **25** before buying or provisioning a host.

1. Point `mail.example.com` to your real public IP using an A record, with any HTTP proxy disabled.
2. Allow inbound TCP **25, 80, 443, 587, 993** in your provider/network firewall. Leave SSH accessible. KagNode Mail does not change or enable your firewall.
3. Clone/download this repository onto the server. Review the configuration without root or installation:

   ```bash
   ./install.sh --dry-run --domain example.com --hostname mail.example.com \
     --ipv4 203.0.113.10 --admin admin@example.com --output ./review
   ```

4. Replace the example names and documentation IP with your values, then run:

   ```bash
   sudo ./install.sh --domain example.com --hostname mail.example.com \
     --ipv4 203.0.113.10 --admin admin@example.com
   ```

   The installer prompts twice for a password without echoing it. For unattended setup, supply `--password-file /root/kagnode-admin-password`: a root-owned `0600` or `0400` regular file containing a 12–256 character password. Passwords are never command-line arguments. Remove that file after successful setup.

5. Sign in at `https://mail.example.com`. Open **Domains & DNS**, publish its records, and configure PTR with your IP provider. Create members and aliases under **People & access**. Use **Send as** to assign extra identities.

The installer obtains a real certificate before starting the configured mail stack. It refuses existing mail configuration/data and occupied service ports. If an owned installation stops partway through, fix the reported error and rerun the **same options with `--resume`**. Account data, DKIM keys, and application secrets are retained; managed service configuration is regenerated with backups. Do not use `--resume` as a migration tool.

`--test-certificate` creates an untrusted seven-day certificate for isolated lab testing only. Do not use this option on a public deployment.

This release uses the `kagnode` package, service account, systemd units, and `/etc/kagnode`, `/opt/kagnode`, `/var/lib/kagnode` paths. It does **not** migrate the earlier Morrow prototype in place. Existing installations must be preserved and migrated deliberately; the installer refuses them. See [operations](docs/operations.md).

## Mail clients

| Setting | Value |
| --- | --- |
| Username | Full email address |
| IMAP | Your mail hostname, port **993**, SSL/TLS |
| SMTP | Your mail hostname, port **587**, STARTTLS, authentication required |
| Password | The same password used for webmail |
| Extra sender address | Must be an explicitly granted mailbox or alias |

POP3 and port 465 are intentionally not enabled. This release accepts ASCII domains with alphabetic top-level domains and ASCII mailbox addresses; SMTPUTF8 is not supported. Local-part spelling is normalized to lowercase.

## Resource use

There is one Gunicorn worker with two threads, demand-started Dovecot processes, bounded Postfix concurrency, and no persistent web polling or mail indexing daemon. Package installation uses `--no-install-recommends` and Ubuntu's maintained Python packages rather than a second production virtual environment.

**Measured core idle footprint: 81.1 MiB PSS** after two minutes idle in the Ubuntu 24.04 lab, excluding the OS, filesystem cache, and fail2ban. No CPU ticks were observed in a subsequent 30-second sample. A universal RAM/idle/disk claim would be misleading. Use `sudo python3 scripts/resources.py` to measure proportional resident memory on your server, `systemd-cgtop` for CPU, and `du -sh /opt/kagnode /var/lib/kagnode /var/vmail` for application/data size. See [validation](docs/validation.md) for measured results and their scope. The web service has a 384 MiB hard memory cap; the sender guard has 128 MiB. These caps are ceilings, not idle estimates.

The small default stack **does not include a content spam filter or antivirus engine**. Junk is a manual folder. OpenDKIM signs outgoing mail; it is not an inbound spam/DMARC enforcement system. Read [security](SECURITY.md) before deciding whether to add filtering or move beyond a small trusted team.

## Publish on GitHub

Review the source and validation notes, then create an empty public repository in your GitHub account. From this project directory, connect it and push:

```bash
git remote add origin https://github.com/YOUR-NAME/kagnode-mail.git
git push -u origin main
```

The repository includes an MIT license, screenshots, security policy, commit-pinned GitHub Actions tests, and Dependabot update configuration. No account secrets, server data, or test root filesystem belong in a public repository.

## Development and checks

```bash
.venv/bin/python -m pytest -q
shellcheck install.sh scripts/*.sh tests/*.sh
# With the preview running in a separate terminal:
.venv/bin/playwright install chromium
.venv/bin/python tests/browser_check.py
```

The [GitHub Actions workflow](.github/workflows/test.yml) includes Python/shell tests and a disposable Ubuntu 24.04 mail integration test. Test containers are a development tool; production is native systemd.

- [DNS and deliverability](docs/dns.md)
- [Architecture and permission model](docs/architecture.md)
- [Operations, backups, upgrades, and recovery](docs/operations.md)
- [Validation results and known limitations](docs/validation.md)
- [Security policy](SECURITY.md)
- [Security review and hardening details](docs/security-review.md)
- [Contributing](CONTRIBUTING.md)

MIT licensed. Ready to publish as a repository; no remote repository or public service is created by running the preview or dry run.
