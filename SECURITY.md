# Security policy and deployment boundaries

KagNode Mail 0.2 is an initial implementation for a small, trusted group on a dedicated Ubuntu 24.04 host. It has automated security regression tests and a local real-mail integration harness, but has **not received an independent security audit**.

## Report a vulnerability

When this repository is published, enable GitHub **Private vulnerability reporting** under Security settings and use that channel. Until then, contact the repository owner privately. Do not include real passwords, mailbox content, keys, or active session cookies in public issues. Never send test messages to third parties without their permission.

## Protections

- Root-only provisioning; the web service and sender guard run as the unprivileged `kagnode` account with systemd filesystem restrictions.
- TLS for public web, IMAP, and submission; credentials are not advertised before STARTTLS on port 587.
- Authenticated relay only on submission. Port 25 accepts local delivery, not arbitrary relay.
- Mailbox-specific authentication, server-side role checks, transactional last-admin protection, and parameterized SQL.
- SMTP envelope and visible From ownership checks for every submitted message, plus per-account hourly limits.
- Production `__Host-` Secure/HttpOnly/SameSite=Strict cookies, bearer rotation, eight-hour absolute expiry, a cap of five active sessions per account, encrypted server-side mail credentials, and bounded login/reauthentication rate counters.
- Mandatory CSRF tokens plus same-origin and Fetch Metadata checks. Admin mutations require a password verified within the last 15 minutes; reauthentication rotates the bearer token without extending the original session expiry.
- Bounded request bodies, multipart fields/attachments, MIME object counts, and message headers; untrusted download filenames have control/bidirectional characters and path components removed.
- CSP, blocked framing, same-origin opener/resource policies, no executable email HTML, no external tracking content, and download-only attachments.
- Canonical HTTPS hostname enforcement, TLS 1.2/1.3, HSTS, request/time/connection limits, and disabled response compression for session-bearing pages/APIs. Static CSS, JavaScript, and SVG may be compressed.
- Privilege-restricted systemd units, no service capabilities, safer password-file handling, and exclusive mode-0600 backup output that cannot overwrite an existing file.
- Fail2ban for native Dovecot/Postfix logins and SSH, plus nginx/application login limits. Verify your host's fail2ban journal matching and SSH port configuration after setup.
- Explicit IPv4-first configuration to avoid accidentally advertising broken IPv6 mail delivery.

## Deliberate limits

- No MFA, OAuth, external identity provider, S/MIME/PGP, at-rest mailbox encryption, antivirus scanning, automatic spam classification, or incoming SPF/DMARC enforcement. The configured OpenDKIM instance signs outbound submission only.
- Do not run an open signup service, sell untrusted accounts, or use the default stack for bulk mail. Add filtering, stronger identity controls, and abuse response appropriate to your environment before broader exposure.
- Regular users can choose display names in native clients and compose arbitrary message text; sender grants control email addresses, not whether a display name resembles another person's name.
- Administrators can reset user passwords. Root can read mail. A compromised web application can modify the directory and access active encrypted credentials using its application key.
- Existing native IMAP sessions are not forcibly disconnected by a web password change or account disable. Root should run `doveadm kick EMAIL` for immediate IMAP revocation. A password reset also does not disconnect already-authenticated SMTP connections; disable a compromised account to make new submission ownership checks fail, and have the operator terminate existing client connections when needed. A disabled account cannot make new logins.
- Internal loopback credentials are plaintext between local processes. The dedicated host is the trust boundary; untrusted local users/containers are outside the supported deployment model.
- SMTP delivery can be uncertain after a timeout. Never retry repeatedly without checking the queue, Sent, and recipient status. Per-recipient partial refusals are reported by the API/UI.
- Only the supported Ubuntu 24.04 package/API versions are targeted. Python 3.13 removed `crypt`; Dovecot 2.4 changed configuration syntax. Do not change the OS/version check to force an unsupported upgrade.

Backups and logs require their own access controls and retention policy. Production requires ongoing OS patching, TLS renewal monitoring, disk monitoring, and account/abuse administration.

## Maintenance and verification

Review [validation](docs/validation.md) for executed tests and [the 0.2 security review](docs/security-review.md) for changes. These are concrete hardening measures, not a guarantee against every attack. Native Ubuntu security updates and tested encrypted off-server backups remain part of operating a server. GitHub workflows pin action revisions, disable persisted checkout credentials, and include Dependabot update configuration.

Design references: [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), [nginx compression warning](https://nginx.org/en/docs/http/ngx_http_gzip_module.html), and [systemd execution sandbox](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).
