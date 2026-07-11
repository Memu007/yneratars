import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from app.config import SettingsStore
from app.database import Database
from app.executor import MissionExecutor

DEFAULTS = {
    "version": "0.3.0", "provider": "google", "google_model": "gemini-test",
    "openai_model": "gpt-test", "browser_model": "gemini-test", "humor": 70,
    "auto_speak": True, "auto_execute": True, "browser_enabled": True,
    "browser_max_steps": 3, "worker_timeout_seconds": 30, "host": "127.0.0.1",
    "port": 8765, "allowed_domains": [], "personality": "test",
}


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        defaults = base / "defaults.json"
        defaults.write_text(json.dumps(DEFAULTS), encoding="utf-8")
        self.settings = SettingsStore(defaults, base / "settings.json")
        self.db = Database(base / "db.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, title="x"):
        return self.db.create_mission({"title": title, "description": "tarea pública", "worker": "TARS", "risk": "low"}, self.settings.get())

    def wait_status(self, mission_id, targets, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            value = self.db.get_mission(mission_id)
            if value["status"] in targets:
                return value
            time.sleep(.05)
        self.fail(f"timeout esperando {targets}: {self.db.get_mission(mission_id)}")

    def test_success_worker(self):
        code = "import json; print('TARS_RESULT='+json.dumps({'ok':True,'result':'hecho'}))"
        ex = MissionExecutor(self.db, self.settings, lambda _id: [sys.executable, "-c", code])
        mission = self.make()
        ex.start(); ex.notify()
        final = self.wait_status(mission["id"], {"completed", "failed"})
        ex.close()
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["result"], "hecho")

    def test_failed_worker(self):
        code = "import json,sys; print('TARS_RESULT='+json.dumps({'ok':False,'error':'fallo'})); sys.exit(1)"
        ex = MissionExecutor(self.db, self.settings, lambda _id: [sys.executable, "-c", code])
        mission = self.make()
        ex.start(); ex.notify()
        final = self.wait_status(mission["id"], {"completed", "failed"})
        ex.close()
        self.assertEqual(final["status"], "failed")
        self.assertIn("fallo", final["error"])

    def test_stop_terminates_process(self):
        code = "import time; time.sleep(20)"
        ex = MissionExecutor(self.db, self.settings, lambda _id: [sys.executable, "-c", code])
        mission = self.make()
        ex.start(); ex.notify()
        self.wait_status(mission["id"], {"running"})
        self.db.transition(mission["id"], "stop")
        ex.stop_mission(mission["id"])
        final = self.wait_status(mission["id"], {"stopped"})
        ex.close()
        self.assertEqual(final["status"], "stopped")

    def test_large_worker_output_does_not_deadlock(self):
        code = (
            "import json,sys; "
            "sys.stdout.write('x'*2500000+'\\n'); sys.stdout.flush(); "
            "print('TARS_RESULT='+json.dumps({'ok':True,'result':'salida grande procesada'}), flush=True)"
        )
        ex = MissionExecutor(self.db, self.settings, lambda _id: [sys.executable, "-c", code])
        mission = self.make("salida grande")
        ex.start(); ex.notify()
        final = self.wait_status(mission["id"], {"completed", "failed"}, timeout=10)
        ex.close()
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["result"], "salida grande procesada")


if __name__ == "__main__":
    unittest.main()
