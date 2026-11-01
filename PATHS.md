# KagNode mail paths

New installations use project-scoped application, configuration, state and log directories. Upstream operating-system locations are retained.

- `/opt/kagnode` → `/opt/kagnode/mail`
- `/etc/kagnode` → `/etc/kagnode/mail`
- `/var/lib/kagnode` → `/var/lib/kagnode/mail`

Existing installations are not migrated automatically. Stop the listed application and data-writing services, take your normal external backup, and inspect `python3 scripts/migrate-paths.py` (dry-run). Then run with `--apply` only after reviewing its exact paths. The journal permits resuming an interrupted copy. The tool retains original directories and verifies copied content, ownership and modes before adding compatibility links. Install the matching new application release and review service configuration before starting it.

`--rollback` restores retained originals and preserves any post-migration writes in a separately named tree for review; it does not silently merge newer data into the old state. `--root` is for disposable fixture tests. No servers, images, Client sessions or user saves were migrated by publishing this release. Never run this migrator against another project that happens to use a similarly named directory.

New shared parent directories are created with mode `0755`, including when the administrator uses `umask 077`. Copied project directories and private files retain their original ownership and permissions. Existing parent permissions are left unchanged; review service-user access to those parents before restarting an existing installation.
