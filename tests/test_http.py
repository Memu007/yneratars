import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.config import SettingsStore
from app.database import Database
from app.secrets_store import SecretStore
from app.providers import ProviderResponse
from app.server import TarsApplication, ThreadingHTTPServer, make_handler

DEFAULTS = {
    "version": "0.3.0", "provider": "google", "google_model": "gemini-test",
    "openai_model": "", "browser_model": "gemini-test", "humor": 70,
    "auto_speak": True, "auto_execute": False, "browser_enabled": True,
    "browser_max_steps": 3, "worker_timeout_seconds": 30, "host": "127.0.0.1",
    "port": 8765, "allowed_domains": [], "personality": "test",
}


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        defaults = base / "defaults.json"
        defaults.write_text(json.dumps(DEFAULTS), encoding="utf-8")
        self.app = TarsApplication(
            db=Database(base / "db.sqlite"),
            settings=SettingsStore(defaults, base / "settings.json"),
            secrets=SecretStore(base / "secrets.json", force_file=True),
            start_executor=False,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.app.close(); self.tmp.cleanup()

    def request(self, path, method="GET", payload=None, expected=200, client=True, extra_headers=None):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if client: headers["X-TARS-Client"] = "dashboard"
        headers.update(extra_headers or {})
        req = Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=3) as response:
                status = response.status; body = response.read().decode()
        except HTTPError as exc:
            status = exc.code; body = exc.read().decode()
        self.assertEqual(status, expected, body)
        return json.loads(body) if body else {}

    def test_health_and_dashboard(self):
        self.assertTrue(self.request("/api/health")["ok"])
        with urlopen(self.base + "/", timeout=3) as response:
            html = response.read().decode()
            self.assertIn("TARS Local", html)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_dashboard_payload_contains_live_operational_state(self):
        self.request("/api/missions", "POST", {
            "title": "Resumen", "description": "resumir un texto",
            "worker": "TARS", "risk": "low", "model": "gemini-test",
        }, 201)
        body = self.request("/api/dashboard")
        self.assertEqual(body["health"]["version"], "0.3.0")
        self.assertEqual(body["stats"]["queued"], 1)
        self.assertEqual(body["missions"][0]["title"], "Resumen")
        self.assertIn("thread_alive", body["health"]["executor"])
        self.assertNotIn("api_key", json.dumps(body).lower())

    def test_messages_conversations_and_activity_endpoints(self):
        self.app.db.add_message("conv-dashboard", "user", "hola")
        self.app.db.add_message("conv-dashboard", "assistant", "respuesta")
        messages = self.request("/api/messages?conversation_id=conv-dashboard")
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        conversations = self.request("/api/conversations")
        self.assertEqual(conversations[0]["conversation_id"], "conv-dashboard")
        self.assertEqual(conversations[0]["message_count"], 2)

        mission = self.request("/api/missions", "POST", {
            "title": "Actividad", "description": "leer una página pública",
            "worker": "CASE", "risk": "low", "model": "gemini-test",
        }, 201)
        activity = self.request("/api/activity?limit=5")
        self.assertEqual(activity[0]["mission_id"], mission["id"])
        self.assertEqual(activity[0]["worker"], "CASE")

    def test_messages_requires_conversation_id(self):
        body = self.request("/api/messages", expected=400)
        self.assertIn("conversation_id", body["error"])

    def test_dashboard_html_has_all_product_views(self):
        with urlopen(self.base + "/", timeout=3) as response:
            html = response.read().decode()
        for view in ("view-control", "view-missions", "view-agents", "view-outputs", "view-settings"):
            self.assertIn(view, html)
        self.assertIn("DETENER TODO", html)
        self.assertIn("Compartir pantalla", html)

    def test_write_requires_custom_header(self):
        body = self.request("/api/settings", "POST", {"humor": 80}, 403, client=False)
        self.assertIn("cabecera", body["error"])

    def test_save_secret_never_returns_value(self):
        body = self.request("/api/secrets/google/save", "POST", {"api_key": "12345678-secret"})
        self.assertTrue(body["status"]["google"])
        self.assertNotIn("12345678-secret", json.dumps(body))

    def test_settings_update(self):
        body = self.request("/api/settings", "POST", {"humor": 88})
        self.assertEqual(body["settings"]["humor"], 88)

    def test_create_and_approve_mission(self):
        mission = self.request("/api/missions", "POST", {"title": "Login", "description": "iniciar sesión", "worker": "CASE", "risk": "low", "model": "gemini-test"}, 201)
        self.assertEqual(mission["status"], "awaiting_approval")
        approved = self.request(f"/api/missions/{mission['id']}/approve", "POST", {})
        self.assertEqual(approved["status"], "queued")

    def test_blocked_purchase(self):
        mission = self.request("/api/missions", "POST", {"title": "Compra", "description": "comprar y pagar", "worker": "CASE", "risk": "low", "model": "gemini-test"}, 201)
        self.assertEqual(mission["status"], "blocked")


    def test_chat_with_mock_provider_persists_history(self):
        class FakeProvider:
            def generate(self, model, prompt, system, history):
                self.received_history = history
                return ProviderResponse(text=f"respuesta:{prompt}", raw={}, usage={"total_tokens": 3})

        self.app.secrets.set("google", "12345678-secret")
        fake = FakeProvider()
        with patch("app.server.get_provider", return_value=fake):
            body = self.request("/api/chat", "POST", {
                "message": "hola", "conversation_id": "conv-test",
                "provider": "google", "model": "gemini-test",
            })
        self.assertEqual(body["text"], "respuesta:hola")
        messages = self.app.db.messages("conv-test")
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["content"], "respuesta:hola")

    def test_stop_all_stops_queued_mission(self):
        mission = self.request("/api/missions", "POST", {
            "title": "Resumen", "description": "resumir un texto",
            "worker": "TARS", "risk": "low", "model": "gemini-test",
        }, 201)
        self.assertEqual(mission["status"], "queued")
        body = self.request("/api/stop-all", "POST", {})
        self.assertIn(mission["id"], body["stopped"])
        updated = self.request(f"/api/missions/{mission['id']}")
        self.assertEqual(updated["status"], "stopped")

    def test_static_js_contains_voice_and_stop(self):
        with urlopen(self.base + "/static/app.js", timeout=3) as response:
            js = response.read().decode()
        self.assertIn("SpeechRecognition", js)
        self.assertIn("/api/stop-all", js)
        self.assertIn("tarsHandsFree", js)
        self.assertIn("requestSubmit", js)
        self.assertIn("dispatchPrefixedMission", js)
        self.assertIn("CASE|KIPP", js)

    def test_sensitive_models_get_requires_client_header(self):
        body = self.request("/api/providers/google/models", expected=403, client=False)
        self.assertIn("cabecera", body["error"])

    def test_ipv6_loopback_host_header_is_accepted(self):
        body = self.request("/api/health", extra_headers={"Host": f"[::1]:{self.server.server_address[1]}"})
        self.assertTrue(body["ok"])


if __name__ == "__main__":
    unittest.main()
