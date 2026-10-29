# KagNode Mail 0.2 security review

This release applies a focused code/configuration review and regression tests. It is not an independent audit or a claim of complete protection. The production design remains native Ubuntu 24.04 with SQLite and small unprivileged services.

## Changes and their purpose

| Area | Change | Risk reduced |
| --- | --- | --- |
| Browser sessions | Production `__Host-` cookies; no Domain attribute; Secure, HttpOnly, Strict, Path=/ | Cookie injection from sibling subdomains and unintended cross-site cookie use |
| Request origin | Required CSRF token plus supplied Origin and Fetch Metadata checks | Cross-origin/sibling-site state changes |
| Administration | 15-minute password freshness; rate-limited confirmation; bearer rotation with fixed eight-hour absolute expiry | Sensitive changes through an unattended or older stolen session |
| Session storage | Five newest sessions per account; existing revocation and expiry retained | Unbounded accumulation of active encrypted credentials |
| Parsing | Limits on JSON, multipart uploads, attachments, MIME objects, headers, and filenames | Excessive object/memory allocation and unsafe download metadata |
| SMTP permissions | Bounded visible From; current authorization checked at DATA completion | Oversized headers and grants revoked during an in-progress message |
| Web front door | Canonical hostname, request/connection/time limits, no dynamic response compression | Host-header confusion, low-effort resource abuse, compression side channels |
| Service isolation | Empty capabilities; restricted namespaces/devices/process visibility and filesystem access | Impact of a service-process compromise |
| Password file | Open without following links; require regular single-link 0400/0600 file; copy validated descriptor | Symlink/FIFO misuse and reopen races |
| Privileged SQLite access | Bootstrap/recovery drops permanently to the service UID; backup uses stopped immutable read-only source | Root-side journal recovery through service-controlled files |
| Backups | Exclusive no-follow output descriptor, mode 0600 from creation | Destination substitution/overwrite races and transient public permissions |
| Installation | Refuse old Morrow paths and conflicting service identities/configuration | Accidental destructive rename or adoption of unrelated accounts |
| Repository | Pinned action commits, read-only workflow permission, no persisted checkout credentials, Dependabot config | Unreviewed action-tag movement and stale dependency awareness |

These protections add no resident service or production Python dependency. The interface displays a password confirmation dialog only when the server requires recent administrative authentication. A cancelled or failed confirmation does not retry the blocked change.

## Evidence

See [validation results](validation.md) and [machine-readable results](verification-results.json) for the final test count and environment. Regression tests cover origin/cookie/session policies, expired administrator authentication, credential rotation and expiry, input and MIME limits, sender revocation, safe secret files, exclusive backup output, installer refusal paths, and private listener checks. Browser checks exercise cancellation, failed confirmation, successful confirmation with one retry, mobile layouts, both themes, and regular-member restrictions. The native lab exercises real SMTP, IMAP, DKIM, TLS, and webmail.

The native lab does not exercise a full systemd boot, live ACME, actual fail2ban bans, public deliverability, or a restore onto another host. Static systemd 255 unit verification checks syntax; it does not replace a runtime deployment test.

## Remaining boundaries

Authentication is password-only; MFA/passkeys and external identity providers are not implemented. The service must be maintained with Ubuntu security updates. Message files are not encrypted at rest, administrators can reset account passwords, and root can read mail. Existing native connections require operator action for immediate termination after password reset. Disabling an account or removing a grant prevents acceptance when the guard checks at the end of DATA; a message already accepted into the queue is not recalled.

A compromised web process can access active credentials and change account data using its own privileges. This is not an adversarial multi-tenant hosting product. The default small stack has no antivirus, content spam classification, or inbound DMARC enforcement. Backup archives contain secrets and must be encrypted and stored separately by the operator.

Design references: [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), [Flask web security](https://flask.palletsprojects.com/en/stable/web-security/), [nginx compression warning](https://nginx.org/en/docs/http/ngx_http_gzip_module.html), [systemd execution sandbox](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).
