"""Optional profile linking uses isolated storage and mocked HTTPS responses."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [
    "kagnode/account_sync.py",
    "authentactor/account_sync.py",
    "src/vpn_account_sync.py",
    "httpboot/account_sync.py",
]
source = next(ROOT / name for name in CANDIDATES if (ROOT / name).exists())
spec = importlib.util.spec_from_file_location("account_link_test_subject", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProfileSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.calls = []
        self.subject = "central-user"
        self.name = "KagNode display name"
        self.token_response = None
        self.links = module.ProfileLinks(
            Path(self.temp.name) / "profiles",
            "kagnode-mail",
            transport=self.transport,
            clock=lambda: self.now,
        )

    def transport(self, method, endpoint, data=None, token=None):
        self.calls.append((method, endpoint, data, token))
        if endpoint == "device":
            return {
                "device_code": "D" * 43,
                "user_code": "ABCDE-12345",
                "verification_uri_complete": "https://nebulacoder.com/account/apps/device?code=ABCDE12345",
            }
        if endpoint == "token":
            return self.token_response or {
                "access_token": "A" * 43,
                "refresh_token": "R" * 43,
            }
        if endpoint.startswith("profile?"):
            return {
                "issuer": "https://nebulacoder.com",
                "subject": self.subject,
                "name": self.name,
                "photo": "",
            }
        return {}

    def link(self, uid="local-a"):
        self.links.handle(
            uid, "Local name", False, "start", installation="https://mail.example.com"
        )
        return self.links.handle(uid, "Local name", False, "poll")

    def test_updates_require_local_admin_even_when_profile_sync_is_disabled(self):
        self.links.handle("owner", "Owner", True, "policy", {"enabled": False})
        for action in ("updates.state", "updates.check", "updates.apply"):
            with self.assertRaises(module.AccountLinkError):
                self.links.handle("viewer", "Viewer", False, action, {"release_id": 42})
        self.assertEqual(self.calls, [])

    def test_explicit_link_returns_profile_without_exposing_credentials(self):
        state = self.link()
        self.assertTrue(state["connected"])
        self.assertEqual(state["profile"]["name"], self.name)
        self.assertEqual(state["local_name"], "Local name")
        self.assertNotIn("A" * 43, str(state))
        self.assertNotIn("R" * 43, str(state))
        self.assertEqual(self.links.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.links.path.parent.stat().st_mode & 0o777, 0o700)

    def test_local_admin_disable_clears_links_and_prevents_future_profile_requests(
        self,
    ):
        self.link()
        with self.assertRaises(module.AccountLinkError):
            self.links.handle(
                "local-a", "Local name", False, "policy", {"enabled": False}
            )
        state = self.links.handle(
            "owner", "Local owner", True, "policy", {"enabled": False}
        )
        self.assertFalse(state["enabled"])
        count = len(self.calls)
        self.assertIsNone(self.links.state("local-a", "Local name")["profile"])
        with self.assertRaises(module.AccountLinkError):
            self.links.handle(
                "local-a",
                "Local name",
                False,
                "start",
                installation="https://mail.example.com",
            )
        self.assertEqual(len(self.calls), count)
        self.links.handle("owner", "Local owner", True, "policy", {"enabled": True})
        self.assertFalse(self.links.state("local-a", "Local name")["connected"])

    def test_same_email_or_display_name_never_links_another_local_account(self):
        self.link()
        with self.assertRaisesRegex(module.AccountLinkError, "Disconnect"):
            self.link("local-b")
        self.subject = "other-central-user"
        with self.assertRaisesRegex(module.AccountLinkError, "Disconnect"):
            self.link()

    def test_refresh_updates_overlay_and_revocation_does_not_change_local_name(self):
        self.link()
        self.now += 1000
        self.name = "Updated KagNode name"
        state = self.links.state("local-a", "Local name")
        self.assertEqual(state["profile"]["name"], "Updated KagNode name")
        self.token_response = {"error": "invalid_grant"}
        self.now += 1000
        state = self.links.state("local-a", "Local name")
        self.assertFalse(state["connected"])
        self.assertEqual(state["local_name"], "Local name")

    def test_pending_slow_down_and_expiry_preserve_private_device_code(self):
        self.token_response = {"error": "slow_down"}
        result = self.link()
        self.assertTrue(result["pending"])
        self.assertEqual(result["interval"], 10)
        self.now += 601
        with self.assertRaises(module.AccountLinkError):
            self.links.handle("local-a", "Local name", False, "poll")

    def test_disconnect_invalidates_an_in_flight_refresh(self):
        self.link()
        original = self.links.transport

        def racing(method, endpoint, data=None, token=None):
            if endpoint == "token":
                self.links.handle("local-a", "Local name", False, "disconnect")
            return original(method, endpoint, data, token)

        self.links.transport = racing
        with self.assertRaises(module.AccountLinkError):
            self.links.handle("local-a", "Local name", False, "refresh")
        self.assertFalse(self.links.state("local-a", "Local name")["connected"])
        self.assertIsNone(self.links.state("local-a", "Local name")["profile"])

    def test_profile_photos_and_issuer_cannot_redirect_or_execute_content(self):
        with self.assertRaises(ValueError):
            module.ProfileLinks(
                Path(self.temp.name) / "bad", "kagnode-mail", issuer="http://localhost"
            )
        with self.assertRaises(module.AccountLinkError):
            self.links.profile(
                {
                    "issuer": self.links.issuer,
                    "subject": "id",
                    "name": "Name",
                    "photo": "data:image/svg+xml,<script/>",
                }
            )
        with self.assertRaises(module.AccountLinkError):
            self.links.profile(
                {
                    "issuer": "https://other.example",
                    "subject": "id",
                    "name": "Name",
                    "photo": "",
                }
            )


if __name__ == "__main__":
    unittest.main()
