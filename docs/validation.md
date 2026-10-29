# Validation and release scope

KagNode Mail 0.2.0 is implemented for native **Ubuntu Server 24.04**. It has no independently audited security certification, and external deliverability cannot be established without a real domain and provider-owned public IP.

## Recorded result — 2026-10-29

| Check | Result |
| --- | --- |
| Application/configuration regression suite | **69 passed**, using Ubuntu's Python, Flask and cryptography packages; disposable test databases on tmpfs |
| Real mail integration | **Passed** with actual Ubuntu Postfix, Dovecot, OpenDKIM, nginx, Gunicorn and sender guard |
| Desktop/mobile browser flows | **Passed**, including member permissions and HTML proxy-error handling |
| Shellcheck, Python compilation and selected Ruff correctness checks | **Passed** |
| systemd 255 static unit syntax validation | **Passed**, with generators/dependency recursion disabled in the minimal lab; runtime boot not tested |
| Core services after two minutes idle | **81.1 MiB proportional resident memory (PSS)** |
| CPU over the following 30-second idle sample | **No measurable service CPU ticks** |
| Baseline APT installed-size estimate for the unchanged package set | **180 MB additional**, including test tools |

The memory sample excludes the operating system, filesystem cache, measurement process, and **fail2ban**, which was installed but not running in the non-systemd lab. It used a small synthetic workload on an 8 GiB host; it is not a minimum VPS specification or a load-capacity guarantee. The default sender guard avoids loading the web framework, reducing its resident footprint.

See [machine-readable results and package versions](verification-results.json). The test network was isolated, and no test messages were sent to external recipients.

## Automated application checks

The regression suite covers:

- CSRF on login and mutations; Origin/Fetch Metadata checks; unauthenticated access rejection; admin/member separation.
- Host-scoped production cookies, five-session cap, administrator password freshness, bearer rotation, and fixed absolute session expiry.
- Session revocation on logout, account disable, and password change; encrypted credentials and hashed tokens.
- Last-admin preservation; invalid domains/quotas/passwords; mailbox/alias collisions.
- Send-as grants and revocation, unauthorized external identities, envelope and visible-header spoofing, duplicate From and injected headers.
- Bcc separation from transport headers; attachment count/size, JSON/multipart parser bounds, MIME object/header limits, and filename path/control/bidi cleanup.
- Secret-file symlink/FIFO/hardlink permissions, privilege dropping before SQLite, exclusive backup output, and refused legacy installations.
- HTML-only text extraction without active content, drafts, and explicit warning after a failed Sent append.
- Generated TLS/relay/listener settings, hostile installer values, and non-mutating dry runs.
- Admin-only public IP updates and resulting DNS instructions.

Browser checks exercise the real interface against a disposable read-only preview: login, inbox and reader, approved From selection, blocked preview writes, account management, overview and account settings, permissions, cancelled/failed/successful admin password confirmation, DNS, system theme synchronization, mobile navigation/layout, and a regular user's hidden/denied administration views. Screenshots in `docs/screenshots` contain fictional preview messages.

## Real mail integration

`tests/lab-install.sh` provisions the same rendered configuration on a disposable Ubuntu 24.04 system with actual Ubuntu packages. `tests/integration.py` uses real Postfix, Dovecot, OpenDKIM, nginx, Gunicorn, and the sender guard; mail never leaves the local test stack.

To reproduce with a development-only container:

```bash
docker run --rm -v "$PWD:/src:ro" ubuntu:24.04 bash -c '
  touch /.kagnode-lab
  bash /src/tests/lab-install.sh prepare
  bash /src/tests/lab-install.sh test
'
```

The lab marker is deliberate protection against accidentally provisioning your workstation. Never create it on a real mail host. Production does not require Docker.

Integration checks cover canonical hostname enforcement, uncompressed session responses, host-scoped cookies, admin reauthentication/bearer rotation, web account/alias creation, relay rejection on port 25, STARTTLS enforcement, forged sender rejection, granted/revoked aliases including revocation during DATA, LMTP delivery, IMAPS retrieval, cryptographic verification of the generated DKIM signature, web send and Sent copies, IMAP reading/archive, multipart drafts and attachment downloads, Trash deletion, disabled native logins, TLS certificate hostname/chain verification, and loopback-only internal ports.

## Deployment checks still required on your server

- A fresh native systemd installation, real ACME issuance and renewal, actual firewall/provider access, externally reachable TLS, and incoming/outgoing delivery through your own domain.
- Fail2ban journal matching and ban behavior in your native systemd environment.
- Five-minute idle resource measurements and load/concurrency/quota limits on your chosen VPS.
- A full backup/restore drill on separate disposable storage, including ownership remapping when restoring onto a different host.
- External review before relying on it for sensitive or large-scale mail.

The tests do not prove public delivery, inbox placement, resistance to every attack, or an exact RAM requirement. Default filtering is deliberately limited: no content spam/antivirus daemon or incoming DMARC enforcement is installed.

## Release limitations

Plain-text composition and reading (HTML-only mail is converted to text); no rich-text editor, message-thread grouping, contacts/calendar, rules/Sieve editor, or full-text index. Mail search currently accepts ASCII text. No SMTPUTF8, POP3, SMTP 465, MFA, or outbound-relay setup wizard. Administrative IP changes update instructions rather than provider DNS or network interfaces. Existing IMAP sessions require `doveadm kick` for immediate revocation. Web sends can have uncertain outcomes after an SMTP timeout; retry only after checking.

The initial installer manages a dedicated host and refuses preexisting mail data/configuration. Cross-version database migrations, automated uninstall, and unattended app upgrades are outside this release. See the operations guide for deliberate update and recovery procedures.
