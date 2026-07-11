import asyncio
import unittest
from unittest.mock import patch

from app import worker


class FakeTools:
    last_excluded = None
    def __init__(self, exclude_actions=None):
        FakeTools.last_excluded = exclude_actions


class FakeLLM:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeHistory:
    def final_result(self): return "resultado web"
    def is_successful(self): return True
    def number_of_steps(self): return 4
    def urls(self): return ["https://example.com"]
    def errors(self): return [None]
    def total_duration_seconds(self): return 1.5


class FakeAgent:
    last_kwargs = None
    def __init__(self, **kwargs):
        FakeAgent.last_kwargs = kwargs
    async def run(self, max_steps):
        self.max_steps = max_steps
        return FakeHistory()


class WorkerContractTests(unittest.TestCase):
    def mission(self, risk="low", provider="google"):
        return {"description":"abrir https://example.com y leer el título", "risk":risk,
                "provider":provider, "model":"test-model", "worker":"CASE"}

    @patch("app.worker._browser_classes", return_value=(FakeAgent, FakeLLM, FakeLLM, FakeTools))
    def test_google_browser_contract_and_read_only_tools(self, _classes):
        result = asyncio.run(worker.run_browser(self.mission(), {"browser_enabled":True,"browser_max_steps":9,"allowed_domains":[]}, "secret-key"))
        self.assertEqual(result["text"], "resultado web")
        self.assertEqual(result["steps"], 4)
        self.assertIn("click", FakeTools.last_excluded)
        self.assertIn("input", FakeTools.last_excluded)
        self.assertEqual(FakeAgent.last_kwargs["max_actions_per_step"], 3)

    @patch("app.worker._browser_classes", return_value=(FakeAgent, FakeLLM, FakeLLM, FakeTools))
    def test_high_risk_allows_interaction_but_not_javascript(self, _classes):
        asyncio.run(worker.run_browser(self.mission(risk="high", provider="openai"), {"browser_enabled":True,"browser_max_steps":5,"allowed_domains":[]}, "secret-key"))
        self.assertNotIn("click", FakeTools.last_excluded)
        self.assertIn("evaluate", FakeTools.last_excluded)

    def test_disabled_browser_rejected(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(worker.run_browser(self.mission(), {"browser_enabled":False}, "secret-key"))


if __name__ == "__main__":
    unittest.main()
