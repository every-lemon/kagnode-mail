# Validation and release scope

KagNode Mail 1.0.0 targets native **Ubuntu Server 24.04**. It has no independent security certification; external deliverability requires testing with a real domain and provider-owned public IP.

## 1.0.0 acceptance

The current application suite passes 196 checks, including token and mailbox access boundaries, secure login handling and public-only website URL configuration. The release also requires the native-mail job and browser checks in [CI](https://github.com/every-lemon/kagnode-mail/actions/workflows/test.yml). External recipient delivery is a deployment check, not guaranteed by releasing this package.

Existing installations must follow PATHS.md; replacing application files does not migrate an older database or mail configuration. Historical test evidence below remains labeled with its original scope.

## Earlier release checks — 2026-10-30

| Check | Result |
| --- | --- |
| Application/configuration regression suite | **137 passed**, using Ubuntu's Python 3.12, Flask, and cryptography packages; test databases on private tmpfs |
| Real mail integration | **Passed** with actual Ubuntu Postfix, Dovecot, OpenDKIM, nginx, Gunicorn, and sender guard |
| Desktop/mobile browser flows | **Passed**, including token creation/replacement/revocation, permissions, reauthentication, and both themes |
| Disposable real-backend browser smoke | **Passed** token creation, replacement, revocation, and one-time secret handling |
| OpenAPI 3.1 validation | **Passed** with openapi-spec-validator 0.7.2 |
| Shellcheck, Python compilation, selected Ruff correctness checks | **Passed** |

The token checks cover public SMTP/IMAP rejection of internal credentials, authenticated API sends and partial recipient refusals, folder search and reads without changing Seen, UIDVALIDITY mismatch rejection, multipart drafts and replacement, Bcc and attachment downloads, flags/moves/permanent deletion, per-token sender limits, revocation during SMTP DATA, and revoked API/private IMAP authentication. Tests sent no mail outside the isolated stack.

The 0.3 core stack measured **82.2 MiB PSS** after the native/API workload and two minutes idle. A subsequent 30-second sample measured **0.033% of one CPU core**. This excludes the operating system, filesystem cache, measurement process, and fail2ban (not running in the non-systemd lab); it is not a minimum VPS specification or load-capacity guarantee. The unchanged package set previously had an APT estimate of 180 MB additional including test tools, excluding OS, mail storage, backups, and download caches. See [machine-readable results and package versions](verification-results.json).

Static systemd unit syntax was validated previously on systemd 255 with generators/dependency recursion disabled; this isolated lab does not validate native systemd boot or fail2ban bans.

## Automated application checks

The regression suite covers:

- Admin-issued token scope/expiry/revocation, password-reset and disablement triggers, current sender permissions, body limits, rate limits, and bearer/session separation.
- Same-connection UIDVALIDITY checks, read-only API reads, and explicit accepted/uncertain send outcomes, including errors after SMTP acceptance.
- CSRF on browser login and mutations; Origin/Fetch Metadata checks; unauthenticated access rejection; admin/member separation.
- Host-scoped production cookies, five-session cap, administrator password freshness, bearer rotation, and fixed absolute session expiry.
- Session revocation on logout, account disable, and password change; encrypted credentials and hashed tokens.
- Last-admin preservation; invalid domains/quotas/passwords; mailbox/alias collisions.
- Send-as grants and revocation, unauthorized external identities, envelope and visible-header spoofing, duplicate From and injected headers.
- Bcc separation from transport headers; attachment count/size, JSON/multipart parser bounds, MIME object/header limits, and filename path/control/bidi cleanup.
- Secret-file symlink/FIFO/hardlink permissions, privilege dropping before SQLite, exclusive backup output, and refused legacy installations.
- HTML-only text extraction without active content, drafts, and explicit warning after a failed Sent append.
- Generated TLS/relay/listener settings, hostile installer values, and non-mutating dry runs.
- Admin-only public IP updates and resulting DNS instructions.

Browser checks exercise the real interface against a disposable read-only preview: login, inbox and reader, approved From selection, blocked preview writes, account management, overview and account settings, permissions, cancelled/failed/successful admin password confirmation, DNS, system theme synchronization, mobile navigation/layout, and a regular user's hidden/denied administration views. Token checks also cover injected labels/sender text, failed clipboard access, one-time secret removal, and cancelled/confirmed password reauthentication without duplicate token creation. Browser token fixtures are mocked for deterministic error paths; a separate disposable real-backend smoke checked issuance/replacement/revocation. Screenshots in `docs/screenshots` contain fictional data.

## Real mail integration

`tests/lab-install.sh` provisions the same rendered configuration on a disposable Ubuntu 24.04 system with actual Ubuntu packages. `tests/integration.py` and `tests/integration_api.py` use real Postfix, Dovecot, OpenDKIM, nginx, Gunicorn, and the sender guard; mail never leaves the local test stack.

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

## Release checks — 0.5.0, 2026-10-31

- Mail adapter and Token API: **90 passed**, including HTML/text MIME alternatives, attachment composition, UTF-8 limits, sender authorization and uncertain outcomes.
- Application, origin, proxy, transport and installer regression: **104 passed**. Disposable databases ran on tmpfs after local disk journal stalls caused slow test setup.
- Browser acceptance passed reading, composing, sender selection, error recovery, admin password confirmation, API token replacement/revocation, keyboard navigation and mobile menus.
- **44** extra layout checks passed: mailbox, reader, composer and account at 320, 390, 768, 1024 and 1440 pixels in both themes, plus four widths for the account email design.
- These checks use fictional accounts and mock delivery. No messages were sent to real recipients. Native SMTP/IMAP settings and certificate synchronization are outside this source-only update and are retained.
