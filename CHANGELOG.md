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
