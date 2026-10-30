# Contributing

Use Python 3.12. Install development dependencies with `pip install -e '.[test]'` in a virtual environment. The production installer intentionally uses Ubuntu packages instead of that environment.

Run Python tests and shellcheck for every backend/provisioning change. Run `tests/browser_check.py` against `scripts/demo.py` for interface changes. For mail-policy changes, run the real-stack test in a disposable Ubuntu 24.04 container/chroot; see the workflow and `tests/lab-install.sh`. Never run the lab provisioner on your workstation or an active mail server.

Keep production services native and small. Avoid introducing a frontend runtime, resident cache server, periodic inbox polling, analytics, remote assets, or a second database. Preserve the distinction between receiving aliases and permission to send. Never rely on hidden UI controls to enforce access.

Use fictional addresses in tests. Do not commit keys, real mail, config secrets, SQLite databases, password files, test root filesystems, or virtual environments. Review `git diff --cached` before publication. Tests must not send public mail or alter provider DNS.

Submit changes through [pull requests](https://github.com/every-lemon/kagnode-mail/pulls). Report security issues through the private channel in [SECURITY.md](SECURITY.md).
