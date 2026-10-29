# Security policy and deployment boundaries

Morrow 0.1 is an initial implementation for a small, trusted group on a dedicated Ubuntu 24.04 host. It has automated security regression tests and a local real-mail integration harness, but has **not received an independent security audit**.

## Report a vulnerability

When this repository is published, enable GitHub **Private vulnerability reporting** under Security settings and use that channel. Until then, contact the repository owner privately. Do not include real passwords, mailbox content, keys, or active session cookies in public issues. Never send test messages to third parties without their permission.

## Protections

- Root-only provisioning; the web service and sender guard run as the unprivileged `morrow` account with systemd filesystem restrictions.
- TLS for public web, IMAP, and submission; credentials are not advertised before STARTTLS on port 587.
- Authenticated relay only on submission. Port 25 accepts local delivery, not arbitrary relay.
- Mailbox-specific authentication, server-side role checks, transactional last-admin protection, and parameterized SQL.
- SMTP envelope and visible From ownership checks for every submitted message, plus per-account hourly limits.
- Secure/HttpOnly/SameSite session cookies, session rotation, CSRF checks, eight-hour expiration, server-side encrypted credentials, and bounded authentication rate counters.
- CSP, blocked framing, no executable email HTML, no external tracking content, and download-only attachments.
- Fail2ban for native Dovecot/Postfix logins and SSH, plus nginx/application login limits. Verify your host's fail2ban journal matching and SSH port configuration after setup.
- Explicit IPv4-first configuration to avoid accidentally advertising broken IPv6 mail delivery.

## Deliberate limits

- No MFA, OAuth, external identity provider, S/MIME/PGP, at-rest mailbox encryption, antivirus scanning, automatic spam classification, or incoming SPF/DMARC enforcement. The configured OpenDKIM instance signs outbound submission only.
- Do not run an open signup service, sell untrusted accounts, or use the default stack for bulk mail. Add filtering, stronger identity controls, and abuse response appropriate to your environment before broader exposure.
- Regular users can choose display names in native clients and compose arbitrary message text; sender grants control email addresses, not whether a display name resembles another person's name.
- Administrators can reset user passwords. Root can read mail. A compromised web application can modify the directory and access active encrypted credentials using its application key.
- Existing native IMAP sessions are not forcibly disconnected by a web password change or account disable. Root should run `doveadm kick EMAIL` when immediate revocation is needed. A disabled account cannot make new logins or pass new submission ownership checks.
- Internal loopback credentials are plaintext between local processes. The dedicated host is the trust boundary; untrusted local users/containers are outside the supported deployment model.
- SMTP delivery can be uncertain after a timeout. Never retry repeatedly without checking the queue, Sent, and recipient status. Per-recipient partial refusals are reported by the API/UI.
- Only the supported Ubuntu 24.04 package/API versions are targeted. Python 3.13 removed `crypt`; Dovecot 2.4 changed configuration syntax. Do not change the OS/version check to force an unsupported upgrade.

Backups and logs require their own access controls and retention policy. Production requires ongoing OS patching, TLS renewal monitoring, disk monitoring, and account/abuse administration.
