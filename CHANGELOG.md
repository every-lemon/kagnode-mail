# 1.1.0

- Shared KagNode product styling, mobile controls, and persistent product branding.
- Prebuilt, fingerprinted UI assets with minified aliases and no source maps.
- Existing account data, credentials, and API behavior are retained.

# 1.0.2

- Recheck credentials under a database lock before issuing or refreshing web sessions.
- Revoke web sessions at the database boundary on password, disablement, or administrator-role changes.
- Reject malformed CSRF tokens without string-encoding errors.

# 1.0.1

Fix project-path migration from a root console using `umask 077`: new shared parent directories permit service traversal, while copied private files and existing directory permissions are preserved. Add regression checks for that boundary. Account, mailbox and mail-delivery behavior are unchanged.

# 1.0.0

First stable KagNode Mail release for Ubuntu 24.04. Includes the server-only Token API, plain-text and HTML account emails, responsive webmail, DNS guidance, and public website links from the shared service URL configuration. GitHub artwork now adapts to light and dark backgrounds. Existing account, mailbox and token data stay in their project-scoped locations. See [release scope](docs/validation.md) and [migration instructions](PATHS.md).

# 0.4.0

KagNode branding, project-scoped path defaults and an explicit migration tool. Source history and prior releases are retained. See PATHS.md and LICENSES/.

# Changelog

## Local preview — KagNode. wordmark (2026-10-30)

Add the period to the loading, sign-in and sidebar wordmarks. Standalone Mail icons remain unchanged. Tested against the fake mailbox only.

## Local preview — transparent favicon fallbacks (2026-10-30)

Keep the existing Mail mark on a transparent square SVG canvas; add transparent PNG, ICO and touch-icon fallbacks with versioned links. Mail remains a local fake preview and has not been deployed.

## Unreleased — navigation polish

- Searchable keyboard navigation (Ctrl/Cmd+K) for folders, account settings and allowed administration screens.
- Mobile drawer close control, focus containment, background interaction blocking and clearer current-location labels.
- Rounded navigation surfaces and short transitions with reduced-motion support.
- Tested with the disposable fake mailbox; no live Mail deployment or service configuration changes.


## 0.2.1 — 2026-10-30

Changed dark mode to black backgrounds and white primary text. KagNode blue (`#2861EC`) and its hover color now remain the same in both themes. Removed navy surfaces and lighter dark-mode blue overrides. Browser flows and computed light/dark colors verified.

## 0.2.0 — 2026-10-29

Renamed the project, package, installer paths, and services to **KagNode Mail**. Recreated the supplied KagNode mark as a lightweight SVG and redesigned the interface around its blue, with white/ink themes, system theme synchronization, and improved mobile navigation. Removed slogans, promotional panels, decorative labels, and repetitive copy from all interface views.

Hardened cookies, request origins, administrator reauthentication, session counts, MIME/request limits, SMTP authorization at DATA completion, TLS/nginx controls, service isolation, secret-file handling, privileged database access, and backup output creation. Added security regression and browser tests, native mail checks, commit-pinned CI actions, and dependency-update configuration. See [the review](docs/security-review.md).

The installer refuses earlier Morrow installations; automatic migration is not provided. Fresh production sign-in is required by the new cookie names.

## 0.1.0 — 2026-10-29

Initial native Ubuntu 24.04 implementation: installer and dry-run renderer; Postfix/Dovecot/OpenDKIM stack; custom responsive webmail; admin/member roles; account/alias/domain management; backend-enforced sender grants; DNS guidance; TLS and certificate renewal; session and abuse controls; read-only preview; backup/recovery tools; Python, browser, and real-mail integration tests.

## Historical README release notes

## KagNode 0.4 release

The Mail server is an independent, self-hosted open-source project. Its webmail remains available to mailbox users. KagNode's main and Developer websites use only the server-side send API for account and workflow emails; they do not expose a mailbox. The hosted KagNode cloud products remain planned.

New installations use `/opt/kagnode/mail`, `/etc/kagnode/mail` and `/var/lib/kagnode/mail`. Existing installations require the explicit, verified migration in [PATHS.md](PATHS.md); this release does not migrate a running server. Keep upstream Postfix, Dovecot, OpenDKIM and Maildir paths and their permissions. The existing 0.2-to-0.3 database/mail-configuration migration requirement still applies.

Current combined releases and new original work: **AGPL-3.0-only**, with retained MIT legacy and third-party notices in `LICENSES/` and `THIRD_PARTY_NOTICES.md`. Prior MIT grants remain valid. [Brand policy](BRAND-POLICY.md) is separate from software rights.

[KagNode](https://nebulacoder.com) · [Developer](https://developer.nebulacoder.com) · [Logo assets](https://github.com/every-lemon/kagnode-logos)

## KagNode 0.4.1

The configuration renderer supports a separate Mail service identity, read group, certificate directory, and one explicitly trusted reverse proxy. These options support reviewed installations alongside existing KagNode websites; the standard installer continues to require a fresh, dedicated host. See [cohosted configuration](docs/operations.md#reviewed-cohosted-configuration).

## KagNode 0.4.2

Navigation keeps mailbox and administration destinations in their own scroll area, with account controls and one KagNode Website link anchored below. Current destinations remain visible in the sidebar and quick navigation. The compact footer keeps source and license links beside the content; tablet and phone layouts use a navigation drawer. Mail protocols, API permissions and delivery behavior are unchanged. Explicit `TRUSTED_PROXIES` validation protects client addresses and HTTPS checks when an installation deliberately exposes its web listener to a LAN reverse proxy; see [operations](docs/operations.md).

## KagNode 0.4.3

Opening Mail through a direct HTTP/IP address now explains how to reach secure sign-in before asking for a password. Only the configured HTTPS Mail origin offers the production sign-in form. An incorrect proxy scheme gets a specific configuration message. Origin checks, CSRF, and secure session cookies remain enforced.

## KagNode 0.4.4

Compose validates external recipients separately from hosted mailbox names, accepting valid punctuation, quoted addresses, display names, and trailing commas. Invalid recipients identify the To, Cc, or Bcc field and prevent submission of the entire list. Mailbox connection failures show a direct explanation and a Try again button; send failures retain the warning to check delivery before retrying.

## KagNode 0.4.5

Phone and tablet inboxes have a Compose button beside Refresh. Opening the composer closes the navigation drawer so returning to mail leaves the inbox visible. Existing sidebar and keyboard shortcuts use the same composer.

## KagNode 1.0.1

- A clearer reading workspace, top-right account settings, responsive composer and message skeletons. Unread and starred filters apply to the current page and retain folder search.
- The server Token API accepts optional HTML with a required plain-text alternative. Existing sender permissions, per-recipient results and uncertain-send handling remain unchanged. Webmail continues to display safe text and block remote content from received messages.
- Updating an existing 0.4.5 installation needs matching application files and a web-service restart; this release adds no database or native mail configuration migration. Retain the existing environment, API tokens, mailboxes and TLS certificate synchronization.


UI assets
---------
The interface uses the shared KagNode black/white/blue design. Browser assets are minified and content-fingerprinted at build time; source maps are not published. Readable source and required license notices stay in this repository. Branding and hashes discourage casual reuse; they do not prevent inspection or copying.

To rebuild, install esbuild 0.28.2 as a development tool, then run:

```sh
ESBUILD=/path/to/esbuild python3 scripts/build-web.py --package kagnode --product Mail
```

Generated assets and their private package manifest ship with the application. Node.js is not required on the server.

## Account profile integration preview

Added optional KagNode Account display-name/photo linking, local administrator disable controls and private credential storage. Existing local account authentication and data remain independent.
