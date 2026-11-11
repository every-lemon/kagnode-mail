"""Serve prebuilt UI assets without a runtime Node dependency or source maps."""

import hashlib
import json
import re
from pathlib import Path
from flask import request, send_from_directory


def register_assets(app):
    static = Path(app.static_folder)
    manifest_path = static.parent / "web-assets.json"
    manifest = json.loads(manifest_path.read_text())
    built = static / "built"
    for name, item in manifest.items():
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+\.(?:css|js)", name):
            raise RuntimeError("Invalid built UI manifest")
        filename = item["file"]
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+\.[a-f0-9]{16}\.(?:css|js)", filename):
            raise RuntimeError("Invalid built UI asset")
        if (
            hashlib.sha256((built / filename).read_bytes()).hexdigest()
            != item["sha256"]
        ):
            raise RuntimeError("Built UI asset integrity check failed")

    @app.before_request
    def built_alias():
        if request.path.startswith("/static/") and request.path.endswith(".map"):
            return "", 404
        name = request.path.removeprefix("/static/")
        if request.path.startswith("/static/") and name in manifest:
            if request.method not in ("GET", "HEAD"):
                return "", 405
            response = send_from_directory(built, manifest[name]["file"], max_age=3600)
            response.headers["X-Robots-Tag"] = "noindex"
            return response

    pattern = re.compile(
        r"""((?:src|href)=["'])(/static/)([a-zA-Z0-9_.-]+\.(?:css|js))(?:\?[^"']*)?(["'])"""
    )

    @app.after_request
    def ui_response(response):
        if response.status_code == 200 and response.mimetype == "text/html":
            response.direct_passthrough = False
            body = response.get_data(as_text=True)
            body = pattern.sub(
                lambda match: (
                    match[1] + "/static/built/" + manifest[match[3]]["file"] + match[4]
                    if match[3] in manifest
                    else match[0]
                ),
                body,
            )
            response.set_data(body)
            response.headers.pop("ETag", None)
            response.headers.pop("Last-Modified", None)
        if (
            re.fullmatch(
                r"/static/built/[a-zA-Z0-9_.-]+\.[a-f0-9]{16}\.(?:css|js)", request.path
            )
            and response.status_code == 200
        ):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            response.headers["X-Robots-Tag"] = "noindex"
        return response
