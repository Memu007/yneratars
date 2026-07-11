import unittest
from unittest.mock import patch

from app.providers import GoogleProvider, OpenAIProvider, ProviderError


class ProviderTests(unittest.TestCase):
    @patch("app.providers._request_json")
    def test_google_models(self, request):
        request.return_value = {"models": [
            {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
        ]}
        self.assertEqual(GoogleProvider("12345678").list_models(), ["gemini-a"])

    @patch("app.providers._request_json")
    def test_google_generation_parsing(self, request):
        request.return_value = {"candidates": [{"content": {"parts": [{"text": "Hola"}, {"text": "mundo"}]}}], "usageMetadata": {"totalTokenCount": 3}}
        result = GoogleProvider("12345678").generate("gemini-test", "hola", "sistema")
        self.assertEqual(result.text, "Hola\nmundo")
        self.assertEqual(result.usage["totalTokenCount"], 3)

    @patch("app.providers._request_json")
    def test_google_empty_response_rejected(self, request):
        request.return_value = {"candidates": []}
        with self.assertRaises(ProviderError):
            GoogleProvider("12345678").generate("gemini-test", "hola", "sistema")

    @patch("app.providers._request_json")
    def test_openai_models_filtered(self, request):
        request.return_value = {"data": [{"id": "gpt-one"}, {"id": "whisper-one"}, {"id": "o3"}]}
        self.assertEqual(OpenAIProvider("12345678").list_models(), ["gpt-one", "o3"])

    @patch("app.providers._request_json")
    def test_openai_output_text(self, request):
        request.return_value = {"output_text": "respuesta", "usage": {"total_tokens": 5}}
        result = OpenAIProvider("12345678").generate("gpt-test", "hola", "sistema")
        self.assertEqual(result.text, "respuesta")

    @patch("app.providers._request_json")
    def test_openai_nested_output(self, request):
        request.return_value = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "anidada"}]}]}
        result = OpenAIProvider("12345678").generate("gpt-test", "hola", "sistema")
        self.assertEqual(result.text, "anidada")


if __name__ == "__main__":
    unittest.main()
