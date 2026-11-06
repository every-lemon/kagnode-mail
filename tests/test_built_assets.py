import unittest, hashlib, json
from pathlib import Path
from flask import Flask
from kagnode.web_assets import register_assets


class BuiltAssetsTests(unittest.TestCase):
    def setUp(self):
        self.package = Path(__file__).resolve().parents[1] / "kagnode"
        self.app = Flask(__name__, static_folder=str(self.package / "static"))
        register_assets(self.app)
        self.manifest = json.loads((self.package / "web-assets.json").read_text())

        @self.app.get("/")
        def page():
            return '<!doctype html><link rel="stylesheet" href="/static/kagnode-ui.css?v=old"><script src="/static/app.js?v=old"></script>'

        self.client = self.app.test_client()

    def test_html_uses_fingerprinted_assets_and_aliases_serve_identical_bytes(self):
        body = self.client.get("/").get_data(as_text=True)
        for name in ["kagnode-ui.css", "app.js"]:
            item = self.manifest[name]
            url = "/static/built/" + item["file"]
            self.assertIn(url, body)
            hashed = self.client.get(url)
            alias = self.client.get("/static/" + name)
            self.assertEqual(hashed.status_code, 200)
            self.assertEqual(alias.data, hashed.data)
            self.assertEqual(hashlib.sha256(hashed.data).hexdigest(), item["sha256"])
            self.assertIn("immutable", hashed.headers["Cache-Control"])
            self.assertNotIn(b"sourceMappingURL", hashed.data)
            hashed.close()
            alias.close()
        self.assertNotIn("?v=old", body)

    def test_maps_and_non_get_asset_requests_are_denied(self):
        self.assertEqual(self.client.get("/static/app.js.map").status_code, 404)
        self.assertEqual(self.client.post("/static/app.js").status_code, 405)
        response = self.client.head("/static/app.js")
        self.assertEqual(response.status_code, 200)
        response.close()
