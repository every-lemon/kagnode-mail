"""Local update-manager client. Caller must enforce local admin and CSRF checks."""

import http.client
import json
import os
from pathlib import Path
import socket


class UpdateError(ValueError):
    pass


def manage_update(project, action, data=None):
    names = {
        "kagnode-authenticator": "passwords",
        "kagnode-mail": "mail",
        "kagnode-vpn": "vpn",
        "kagnode-httpboot": "httpboot",
    }
    if project not in names or action not in ("state", "check", "apply"):
        raise UpdateError("Unknown application update request.")
    root = Path(os.environ.get("KAGNODE_ECOSYSTEM_ROOT", "/srv/kagnode"))
    if not root.is_absolute():
        raise UpdateError("The installation root must be absolute.")
    address = root / "run" / (names[project] + ".sock")
    if not address.exists():
        if action == "state":
            return {
                "enabled": False,
                "message": "Enable this application's update panel with KagNode CLI on the server.",
            }
        raise UpdateError("The local update manager is not running.")
    body = {"action": action}
    if action == "apply":
        value = (data or {}).get("release_id")
        if type(value) is not int or value <= 0:
            raise UpdateError("Check for updates before installing a release.")
        body["release_id"] = value
    connection = http.client.HTTPConnection("localhost", timeout=60)
    try:
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(60)
        connection.sock.connect(str(address))
        connection.request(
            "POST", "/", json.dumps(body), {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        raw = response.read(65537)
        if len(raw) > 65536:
            raise UpdateError("Update status was too large.")
        value = json.loads(raw)
        if response.status >= 400:
            raise UpdateError(value.get("error", "Update request failed."))
        return value
    except (OSError, ValueError, http.client.HTTPException) as error:
        if isinstance(error, UpdateError):
            raise
        raise UpdateError("The local update manager could not be reached.") from error
    finally:
        connection.close()
