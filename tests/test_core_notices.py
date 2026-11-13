"""Mailbox users may dismiss a summary without gaining Core administration."""

from unittest.mock import patch
from conftest import login

EVENT = {
    "available": True,
    "notice": {
        "id": "a" * 64,
        "version": "5.0.6",
        "title": "Interface updated",
        "items": ["Improved shared controls."],
    },
}
URL = "/api/v1/kagnode-core/whats-new"


def test_member_receipts_require_csrf_and_remain_private(app):
    member, other = app.test_client(), app.test_client()
    assert member.get(URL).status_code == 401
    headers = login(member, "member")
    login(other, "other")
    with patch("kagnode.kagnode_core.manager_request", return_value=EVENT):
        assert member.get(URL).json == EVENT
        assert member.post(URL + "/dismiss", json={"id": "a" * 64}).status_code == 403
        assert (
            member.post(
                URL + "/dismiss", json={"id": "b" * 64}, headers=headers
            ).status_code
            == 400
        )
        assert member.post(
            URL + "/dismiss", json={"id": "a" * 64}, headers=headers
        ).json == {"dismissed": True}
        assert member.get(URL).json == {"available": True, "notice": None}
        assert other.get(URL).json == EVENT
        assert member.get("/api/v1/kagnode-core/status").status_code == 403


def test_unavailable_helper_returns_no_privileged_details(app):
    client = app.test_client()
    login(client, "member")
    with patch(
        "kagnode.kagnode_core.manager_request",
        side_effect=OSError("private config path"),
    ):
        assert client.get(URL).json == {"available": False, "notice": None}
