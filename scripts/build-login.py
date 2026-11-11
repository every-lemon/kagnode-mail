#!/usr/bin/env python3
"""Generate the login frame from the pinned KagNode Core template, preserving host forms."""

import argparse, json, re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = json.loads((root / "web/login.json").read_text())
template = json.loads(
    (root / "vendor/core-source/packages/templates.json").read_text()
)["login-frame"]
slots = {key: config[key] for key in ("product", "heading", "description")}
slots.update(
    content=(root / "web/login-content.html").read_text(),
    provider=(root / "web/login-provider.html").read_text(),
)
markup = re.sub(r"\{\{([a-z]+)\}\}", lambda match: slots.get(match[1], ""), template)
# These fixed hooks are local form behavior, not application styling.
for key in ("id", "heading_id", "description_id"):
    if config.get(key) and not re.fullmatch(r"[a-z][a-z0-9-]*", config[key]):
        raise ValueError("Invalid login hook")
markup = markup.replace(
    "<main ",
    '<main id="' + config["id"] + '" ' + ("hidden " if config["hidden"] else ""),
    1,
)
markup = markup.replace(
    '<div class="kn-core-login-provider">',
    '<div class="kn-core-login-provider" id="provider-login" hidden>',
    1,
)
if config.get("heading_id"):
    markup = markup.replace("<h1>", '<h1 id="' + config["heading_id"] + '">', 1)
if config.get("description_id"):
    markup = markup.replace(
        "<p>" + config["description"],
        '<p id="' + config["description_id"] + '">' + config["description"],
        1,
    )
path = root / config["target"]
old = path.read_text()
result, count = re.subn(
    r"<!-- core-login:start -->.*?<!-- core-login:end -->",
    lambda _: "<!-- core-login:start -->\n" + markup + "\n<!-- core-login:end -->",
    old,
    flags=re.S,
)
if count != 1:
    raise ValueError("Expected one canonical login frame")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args() if __name__ == "__main__" else parser.parse_args([])
if args.check:
    if result != old:
        raise SystemExit("Login template is stale; run scripts/build-login.py")
else:
    path.write_text(result)
