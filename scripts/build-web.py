#!/usr/bin/env python3
"""Build product UI assets. ESBUILD selects the build-time esbuild executable."""

import argparse, hashlib, json, os, subprocess, tempfile
from pathlib import Path

import runpy

# Shared login markup is generated only from the pinned Core template.
runpy.run_path(
    str(Path(__file__).with_name("build-login.py")), run_name="core_login_build"
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--package", required=True, help="Application package containing static/"
)
parser.add_argument("--product", required=True)
args = parser.parse_args()
package = Path(args.package).resolve()
static = package / "static"
output = static / "built"
output.mkdir(parents=True, exist_ok=True)
builder = os.environ.get("ESBUILD", "esbuild")
manifest = {}
with tempfile.TemporaryDirectory(prefix="kagnode-ui-") as scratch:
    for source in sorted(static.iterdir()):
        if not source.is_file() or source.suffix not in (".js", ".css"):
            continue
        extension = source.suffix[1:]
        target = Path(scratch) / source.name
        banner = (
            "/*! KagNode. "
            + args.product
            + " | Source and license: see project repository. */"
        )
        subprocess.run(
            [
                builder,
                str(source),
                "--minify",
                "--target=es2020",
                "--charset=utf8",
                "--legal-comments=inline",
                "--banner:" + extension + "=" + banner,
                "--outfile=" + str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        name = source.stem + "." + digest[:16] + source.suffix
        (output / name).write_bytes(data)
        manifest[source.name] = {"file": name, "sha256": digest, "bytes": len(data)}
path = package / "web-assets.json"
temporary = path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(manifest, indent=2) + "\n")
temporary.replace(path)
# Content-addressed outputs absent from the complete manifest are obsolete.
retained = {entry["file"] for entry in manifest.values()}
for stale in output.iterdir():
    if (
        stale.is_file()
        and stale.name not in retained
        and __import__("re").fullmatch(r".+\.[0-9a-f]{16}\.(?:js|css)", stale.name)
    ):
        stale.unlink()
print(
    "Built",
    len(manifest),
    "fingerprinted assets for KagNode.",
    args.product,
    "without source maps.",
)
