import json
import unittest
from unittest.mock import patch

from app.voice import (
    BinaryResponse,
    VoiceError,
    create_realtime_session,
    realtime_session_config,
    synthesize_speech,
    transcribe_audio,
    transcribe_audio_google,
)


SETTINGS = {
    "personality": "TARS directo y confiable.",
    "humor": 65,
    "google_model": "gemini-test",
}


class VoiceTests(unittest.TestCase):
    def test_realtime_config_enables_semantic_vad_interruptions_and_tools(self):
        config = realtime_session_config(SETTINGS, "marin")
        self.assertEqual(config["type"], "realtime")
        self.assertEqual(config["audio"]["output"]["voice"], "marin")
        turn = config["audio"]["input"]["turn_detection"]
        self.assertEqual(turn["type"], "semantic_vad")
        self.assertTrue(turn["create_response"])
        self.assertTrue(turn["interrupt_response"])
        names = {tool["name"] for tool in config["tools"]}
        self.assertEqual(names, {"dispatch_mission", "stop_all", "get_system_status"})
        self.assertIn("español rioplatense", config["instructions"].lower())

    def test_invalid_realtime_voice_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "voz inválida"):
            realtime_session_config(SETTINGS, "inventada")

    def test_realtime_proxy_uses_multipart_and_standard_key(self):
        with patch("app.voice._request_bytes", return_value=BinaryResponse(b"v=0\r\no=openai", "application/sdp")) as mocked:
            answer = create_realtime_session("secret-key", b"v=0\r\no=browser", SETTINGS, "cedar")
        self.assertTrue(answer.startswith("v=0"))
        _, kwargs = mocked.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret-key")
        self.assertIn("multipart/form-data", kwargs["headers"]["Content-Type"])
        self.assertIn(b'name="sdp"', kwargs["body"])
        self.assertIn(b'name="session"', kwargs["body"])
        self.assertIn(b"semantic_vad", kwargs["body"])
        self.assertIn(b"dispatch_mission", kwargs["body"])
        self.assertNotIn(b"secret-key", kwargs["body"])

    def test_realtime_rejects_non_sdp_response(self):
        with patch("app.voice._request_bytes", return_value=BinaryResponse(b'{"error":"bad"}', "application/json")):
            with self.assertRaisesRegex(VoiceError, "SDP válida"):
                create_realtime_session("secret-key", b"v=0", SETTINGS, "marin")

    def test_openai_transcription_multipart_contract(self):
        response = BinaryResponse(json.dumps({"text": "hola TARS"}).encode(), "application/json")
        with patch("app.voice._request_bytes", return_value=response) as mocked:
            text = transcribe_audio("secret-key", b"audio-bytes", "audio/webm;codecs=opus")
        self.assertEqual(text, "hola TARS")
        _, kwargs = mocked.call_args
        self.assertIn("audio/transcriptions", mocked.call_args.args[0])
        self.assertIn(b"gpt-4o-mini-transcribe", kwargs["body"])
        self.assertIn(b"audio/webm", kwargs["body"])
        self.assertIn(b"audio-bytes", kwargs["body"])

    def test_google_transcription_contract(self):
        response = BinaryResponse(json.dumps({
            "candidates": [{"content": {"parts": [{"text": "hola por Gemini"}]}}]
        }).encode(), "application/json")
        with patch("app.voice._request_bytes", return_value=response) as mocked:
            text = transcribe_audio_google("google-key", b"audio", "audio/webm", "gemini-test")
        self.assertEqual(text, "hola por Gemini")
        url = mocked.call_args.args[0]
        _, kwargs = mocked.call_args
        self.assertIn("gemini-test:generateContent", url)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "google-key")
        payload = json.loads(kwargs["body"])
        inline = payload["contents"][0]["parts"][1]["inline_data"]
        self.assertEqual(inline["mime_type"], "audio/webm")
        self.assertTrue(inline["data"])

    def test_natural_tts_contract_and_prompt(self):
        with patch("app.voice._request_bytes", return_value=BinaryResponse(b"ID3-audio", "audio/mpeg")) as mocked:
            audio = synthesize_speech("secret-key", "Buenas, operativo.", "marin", SETTINGS)
        self.assertEqual(audio, b"ID3-audio")
        self.assertIn("audio/speech", mocked.call_args.args[0])
        _, kwargs = mocked.call_args
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["model"], "gpt-4o-mini-tts")
        self.assertEqual(payload["voice"], "marin")
        self.assertIn("rioplatense", payload["instructions"])


if __name__ == "__main__":
    unittest.main()
