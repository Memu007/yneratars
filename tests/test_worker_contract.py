import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import worker


class FakeTools:
    last_excluded = None
    def __init__(self, exclude_actions=None):
        FakeTools.last_excluded = exclude_actions


class FakeLLM:
    last_kwargs = None
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeLLM.last_kwargs = kwargs


class FakeHistory:
    def final_result(self): return "resultado web"
    def is_successful(self): return True
    def number_of_steps(self): return 4
    def urls(self): return ["https://example.com"]
    def errors(self): return [None]
    def total_duration_seconds(self): return 1.5
    def action_names(self): return ["navigate", "click"]


class FakeAgent:
    last_kwargs = None
    def __init__(self, **kwargs):
        FakeAgent.last_kwargs = kwargs
        self.browser_session = kwargs["browser_session"]
        self.history = FakeHistory()
    async def run(self, max_steps, on_step_start=None, on_step_end=None):
        self.max_steps = max_steps
        if on_step_start:
            await on_step_start(self)
        if on_step_end:
            await on_step_end(self)
        return self.history


class FakeBrowserProfile:
    last_kwargs = None
    def __init__(self, **kwargs):
        FakeBrowserProfile.last_kwargs = kwargs


class FakeBrowserSession:
    last_profile = None
    killed = False
    started = False
    def __init__(self, browser_profile):
        FakeBrowserSession.last_profile = browser_profile
        FakeBrowserSession.killed = False
        FakeBrowserSession.started = False
    async def start(self):
        FakeBrowserSession.started = True
    async def get_current_page_url(self):
        return "https://example.com"
    async def kill(self):
        FakeBrowserSession.killed = True


class WorkerContractTests(unittest.TestCase):
    def mission(self, risk="low", provider="google"):
        return {"id":"mission-1", "description":"abrir https://example.com y leer el título", "risk":risk,
                "provider":provider, "model":"gemini-test" if provider == "google" else "gpt-test", "worker":"CASE"}

    def run_case(self, mission=None, settings=None):
        mission = mission or self.mission()
        settings = settings or {"browser_enabled":True,"browser_max_steps":9,"allowed_domains":[]}
        fake_db = unittest.mock.Mock()
        with patch("app.worker._browser_classes", return_value=(FakeAgent, FakeLLM, FakeLLM, FakeTools)), \
             patch("app.worker._browser_session_classes", return_value=(FakeBrowserProfile, FakeBrowserSession)), \
             patch("app.worker.Database", return_value=fake_db), \
             patch("app.worker.find_brave_executable", return_value=Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")):
            result = asyncio.run(worker.run_browser(mission, settings, "secret-key"))
        return result, fake_db

    def test_google_browser_contract_and_read_only_tools(self):
        result, fake_db = self.run_case()
        self.assertEqual(result["text"], "resultado web")
        self.assertEqual(result["steps"], 4)
        self.assertEqual(result["browser"], "Brave")
        self.assertNotIn("click", FakeTools.last_excluded)
        self.assertNotIn("input", FakeTools.last_excluded)
        self.assertIn("upload_file", FakeTools.last_excluded)
        self.assertIn("send_keys", FakeTools.last_excluded)
        self.assertEqual(FakeAgent.last_kwargs["max_actions_per_step"], 5)
        self.assertTrue(FakeAgent.last_kwargs["flash_mode"])
        self.assertFalse(FakeAgent.last_kwargs["use_thinking"])
        self.assertEqual(FakeAgent.last_kwargs["llm_timeout"], 45)
        self.assertFalse(FakeAgent.last_kwargs["use_judge"])
        self.assertFalse(FakeAgent.last_kwargs["enable_planning"])
        self.assertFalse(FakeAgent.last_kwargs["final_response_after_failure"])
        self.assertIsInstance(FakeAgent.last_kwargs["browser_session"], FakeBrowserSession)
        self.assertTrue(FakeBrowserSession.killed)
        self.assertGreaterEqual(fake_db.add_event.call_count, 4)

    def test_brave_is_forced_even_without_domain_allowlist(self):
        self.run_case(settings={"browser_enabled":True,"browser_max_steps":5,"allowed_domains":[]})
        self.assertEqual(
            FakeBrowserProfile.last_kwargs["executable_path"],
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        )
        self.assertFalse(FakeBrowserProfile.last_kwargs["headless"])
        self.assertIsNone(FakeBrowserProfile.last_kwargs["allowed_domains"])
        self.assertIsNone(FakeBrowserProfile.last_kwargs["user_data_dir"])
        self.assertFalse(FakeBrowserProfile.last_kwargs["enable_default_extensions"])

    def test_domain_allowlist_is_applied_to_brave_profile(self):
        self.run_case(settings={"browser_enabled":True,"browser_max_steps":5,"allowed_domains":["example.com"]})
        self.assertEqual(FakeBrowserProfile.last_kwargs["allowed_domains"], ["example.com"])

    def test_high_risk_allows_interaction_but_not_javascript(self):
        self.run_case(self.mission(risk="high", provider="openai"), {"browser_enabled":True,"browser_max_steps":5,"allowed_domains":[],"openai_model":"gpt-test"})
        self.assertNotIn("click", FakeTools.last_excluded)
        self.assertIn("evaluate", FakeTools.last_excluded)

    def test_mismatched_browser_model_falls_back_to_provider_model(self):
        mission = self.mission(provider="openai")
        mission["model"] = "gemini-wrong"
        result, _ = self.run_case(mission, {"browser_enabled":True,"browser_max_steps":5,"allowed_domains":[],"openai_model":"gpt-fast"})
        self.assertEqual(result["model"], "gpt-fast")
        self.assertEqual(FakeLLM.last_kwargs["model"], "gpt-fast")

    def test_missing_brave_rejected_without_fallback(self):
        with patch("app.worker.find_brave_executable", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "no hará fallback"):
                asyncio.run(worker.run_browser(self.mission(), {"browser_enabled":True}, "secret-key"))

    def test_custom_brave_path_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "brave"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
            with patch.dict(os.environ, {"TARS_BRAVE_PATH": str(executable)}):
                with patch("app.worker.platform.system", return_value="Darwin"):
                    self.assertEqual(worker.find_brave_executable(), executable.resolve())

    def test_start_scripts_open_dashboard_with_brave(self):
        for name in ("start.command", "dev.command"):
            script = (worker.ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn('open -a "Brave Browser"', script, name)
            self.assertNotIn('open "http://127.0.0.1', script, name)

    def test_disabled_browser_rejected(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(worker.run_browser(self.mission(), {"browser_enabled":False}, "secret-key"))


if __name__ == "__main__":
    unittest.main()
