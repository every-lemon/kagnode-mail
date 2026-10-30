# Changelog

## 0.2.1 — 2026-10-30

Changed dark mode to black backgrounds and white primary text. KagNode blue (`#2861EC`) and its hover color now remain the same in both themes. Removed navy surfaces and lighter dark-mode blue overrides. Browser flows and computed light/dark colors verified.

## 0.2.0 — 2026-10-29

Renamed the project, package, installer paths, and services to **KagNode Mail**. Recreated the supplied KagNode mark as a lightweight SVG and redesigned the interface around its blue, with white/ink themes, system theme synchronization, and improved mobile navigation. Removed slogans, promotional panels, decorative labels, and repetitive copy from all interface views.

Hardened cookies, request origins, administrator reauthentication, session counts, MIME/request limits, SMTP authorization at DATA completion, TLS/nginx controls, service isolation, secret-file handling, privileged database access, and backup output creation. Added security regression and browser tests, native mail checks, commit-pinned CI actions, and dependency-update configuration. See [the review](docs/security-review.md).

The installer refuses earlier Morrow installations; automatic migration is not provided. Fresh production sign-in is required by the new cookie names.

## 0.1.0 — 2026-10-29

Initial native Ubuntu 24.04 implementation: installer and dry-run renderer; Postfix/Dovecot/OpenDKIM stack; custom responsive webmail; admin/member roles; account/alias/domain management; backend-enforced sender grants; DNS guidance; TLS and certificate renewal; session and abuse controls; read-only preview; backup/recovery tools; Python, browser, and real-mail integration tests.
