import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import editor_connection as connection
import 대본선택 as app

class ConnectionTest(unittest.TestCase):
    def test_existing_editor_is_reused_and_recorded(self):
        with tempfile.TemporaryDirectory() as td, patch.object(connection, "STATE", Path(td) / "port.json"), patch.object(connection, "is_editor", side_effect=lambda port: port == 8767):
            self.assertEqual(connection.select_editor_port(), 8767)
            self.assertEqual(connection.editor_url(), "http://127.0.0.1:8767")

    def test_occupied_port_chooses_next_free_port(self):
        with tempfile.TemporaryDirectory() as td:
            socket = Mock()
            socket.__enter__ = Mock(return_value=socket)
            socket.__exit__ = Mock(return_value=False)
            socket.bind.side_effect = [OSError("occupied"), None]
            with patch.object(connection, "STATE", Path(td) / "port.json"), patch.object(connection, "is_editor", return_value=False), patch.object(connection.socket, "socket", return_value=socket):
                self.assertEqual(connection.select_editor_port(), 8767)

    def test_invalid_port_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "port.json"
            state.write_text(json.dumps({"port": 80}))
            with patch.object(connection, "STATE", state):
                self.assertEqual(connection.editor_port(), 8765)

    def test_other_program_is_not_an_editor(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(connection.urllib.request, "urlopen", return_value=response), patch.object(connection.json, "load", return_value={"ok": True}):
            self.assertFalse(connection.is_editor(8765))

    def test_proxy_does_not_send_key_to_unrelated_program(self):
        handler = Mock()
        with patch.object(app, "is_editor", return_value=False), patch.object(app._rq, "request") as request:
            app.H._editor_proxy(handler, "/api/config", {"kie_api_key": "fake-test-key"})
            request.assert_not_called()
            self.assertEqual(handler._json.call_args.args[1], 503)
