# -*- coding: utf-8 -*-
import json
import http.client
import tempfile
import threading
import unittest
from pathlib import Path

import 대본선택 as app
from http.server import ThreadingHTTPServer


class LocalSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.H)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, origin):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request(method, path, headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        conn.close()
        return response.status, headers, body

    def test_only_local_ui_can_call_general_api(self) -> None:
        self.assertTrue(app.browser_origin_allowed("http://127.0.0.1:8766", "/api/config"))
        self.assertTrue(app.browser_origin_allowed("http://localhost:8766", "/api/reset-all"))
        self.assertFalse(app.browser_origin_allowed("https://attacker.example", "/api/config"))
        self.assertFalse(app.browser_origin_allowed("https://attacker.example", "/api/reset-all"))
        self.assertTrue(app.local_host_allowed("127.0.0.1:8766"))
        self.assertTrue(app.local_host_allowed("localhost:8766"))
        self.assertFalse(app.local_host_allowed("attacker.example:8766"))

    def test_deepseek_and_extension_are_limited_to_bridge_endpoints(self) -> None:
        self.assertTrue(app.browser_origin_allowed("https://chat.deepseek.com", "/api/web/next"))
        self.assertTrue(app.browser_origin_allowed("chrome-extension://test", "/api/web/status"))
        self.assertFalse(app.browser_origin_allowed("https://chat.deepseek.com", "/api/config"))
        self.assertFalse(app.browser_origin_allowed("chrome-extension://test", "/api/reset-all"))

    def test_http_server_rejects_hostile_browser_origin(self) -> None:
        status, headers, _ = self.request("OPTIONS", "/api/reset-all", "https://attacker.example")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_http_server_rejects_dns_rebinding_host(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request("GET", "/api/version", headers={"Host": "attacker.example"})
        response = conn.getresponse()
        response.read(); conn.close()
        self.assertEqual(response.status, 403)

    def test_http_server_reflects_only_allowed_origin(self) -> None:
        origin = "https://chat.deepseek.com"
        status, headers, _ = self.request("OPTIONS", "/api/web/result", origin)
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_similar_prefix_is_not_inside_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "work"
            root.mkdir()
            inside = root / "file.txt"
            sibling = Path(td) / "work-evil" / "file.txt"
            self.assertTrue(app.path_inside(root, inside))
            self.assertFalse(app.path_inside(root, sibling))

    def test_atomic_json_replaces_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "settings.json"
            target.write_text('{"old": true}', encoding="utf-8")
            app.atomic_write_json(target, {"new": "값"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": "값"})
            self.assertEqual(list(Path(td).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
