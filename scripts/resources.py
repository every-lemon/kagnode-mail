#!/usr/bin/env python3
"""Measure proportional resident memory, optionally sampling CPU briefly."""

import argparse
import json
import os
import time
from pathlib import Path


def snapshot():
    rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            exe = os.readlink(entry / "exe")
            cmd = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode(errors="replace")
            )
            cwd = os.readlink(entry / "cwd")
            app = (
                cwd == "/opt/kagnode/mail"
                and "python" in exe
                and ("kagnode" in cmd or "gunicorn" in cmd)
            )
            relevant = (
                exe.startswith(("/usr/lib/postfix/", "/usr/lib/dovecot/"))
                or exe in ("/usr/sbin/dovecot", "/usr/sbin/nginx", "/usr/sbin/opendkim")
                or app
                or "fail2ban-server" in cmd
            )
            if not relevant:
                continue
            stats = {
                line.split(":")[0]: int(line.split()[1])
                for line in (entry / "smaps_rollup").read_text().splitlines()
                if line.startswith(("Pss:", "Rss:"))
            }
            cpu = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            rows.append(
                dict(
                    pid=int(entry.name),
                    command=cmd[:100],
                    pss_kib=stats["Pss"],
                    rss_kib=stats["Rss"],
                    cpu_ticks=int(cpu[11]) + int(cpu[12]),
                )
            )
        except (PermissionError, ProcessLookupError, FileNotFoundError):
            pass
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-seconds", type=int, choices=range(0, 61), default=0, metavar="0..60"
    )
    args = parser.parse_args()
    before = snapshot()
    elapsed = 0
    if args.sample_seconds:
        start = time.monotonic()
        time.sleep(args.sample_seconds)
        elapsed = time.monotonic() - start
        rows = snapshot()
    else:
        rows = before
    result = dict(
        processes=rows,
        total_pss_mib=round(sum(r["pss_kib"] for r in rows) / 1024, 1),
        note="Point-in-time proportional resident memory. Includes fail2ban if running; excludes OS, filesystem cache and this measurement process. CPU is percentage of one core for surviving observed service processes.",
    )
    if elapsed:
        prior = {r["pid"]: r["cpu_ticks"] for r in before}
        ticks = sum(
            max(0, r["cpu_ticks"] - prior.get(r["pid"], r["cpu_ticks"])) for r in rows
        )
        result.update(
            sample_seconds=round(elapsed, 1),
            cpu_percent_one_core=round(
                ticks / os.sysconf("SC_CLK_TCK") / elapsed * 100, 3
            ),
        )
    for r in rows:
        del r["cpu_ticks"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
