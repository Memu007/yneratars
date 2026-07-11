import json
import os
import tempfile
import unittest
from pathlib import Path

from app.config import SettingsStore, _load_env_file
from app.secrets_store import SecretStore


DEFAULTS = {
    "version": "0.3.0", "provider": "google", "google_model": "gemini-3.5-flash",
    "openai_model": "gpt-5.6-luna", "browser_model": "gemini-3.5-flash", "humor": 70,
    "auto_speak": True, "auto_execute": True, "browser_enabled": True,
    "browser_max_steps": 18, "worker_timeout_seconds": 420,
    "host": "127.0.0.1", "port": 8765, "allowed_domains": [], "personality": "test",
}


class ConfigSecretTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.defaults = base / "defaults.json"
        self.settings_path = base / "settings.json"
        self.defaults.write_text(json.dumps(DEFAULTS), encoding="utf-8")
        self.store = SettingsStore(self.defaults, self.settings_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_load(self):
        self.assertEqual(self.store.get()["provider"], "google")

    def test_update_and_permissions(self):
        updated = self.store.update({"humor": 91, "allowed_domains": ["GitHub.com", "github.com"]})
        self.assertEqual(updated["humor"], 91)
        self.assertEqual(updated["allowed_domains"], ["github.com"])
        self.assertEqual(os.stat(self.settings_path).st_mode & 0o777, 0o600)

    def test_invalid_provider_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({"provider": "otro"})

    def test_invalid_humor_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({"humor": 101})

    def test_unknown_setting_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({"api_key": "no"})

    def test_secret_file_roundtrip_and_mode(self):
        path = Path(self.tmp.name) / "secret.json"
        secrets = SecretStore(path, force_file=True)
        secrets.set("google", "12345678-test-key")
        self.assertEqual(secrets.get("google"), "12345678-test-key")
        self.assertTrue(secrets.status()["google"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        secrets.delete("google")
        self.assertIsNone(secrets.get("google"))

    def test_short_secret_rejected(self):
        with self.assertRaises(ValueError):
            SecretStore(Path(self.tmp.name) / "s", force_file=True).set("google", "short")

    def test_non_loopback_host_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update({"host": "0.0.0.0"})

    def test_domain_normalization_and_path_rejection(self):
        updated = self.store.update({"allowed_domains": ["https://GitHub.com/"]})
        self.assertEqual(updated["allowed_domains"], ["github.com"])
        with self.assertRaises(ValueError):
            self.store.update({"allowed_domains": ["example.com/path"]})

    def test_env_file_loads_without_overwriting_existing(self):
        path = Path(self.tmp.name) / ".env"
        path.write_text("TARS_TEST_ALPHA=uno\nTARS_TEST_EXISTING=nuevo\nINVALID-KEY=no\n", encoding="utf-8")
        os.environ["TARS_TEST_EXISTING"] = "viejo"
        os.environ.pop("TARS_TEST_ALPHA", None)
        try:
            _load_env_file(path)
            self.assertEqual(os.environ.get("TARS_TEST_ALPHA"), "uno")
            self.assertEqual(os.environ.get("TARS_TEST_EXISTING"), "viejo")
            self.assertNotIn("INVALID-KEY", os.environ)
        finally:
            os.environ.pop("TARS_TEST_ALPHA", None)
            os.environ.pop("TARS_TEST_EXISTING", None)


if __name__ == "__main__":
    unittest.main()
