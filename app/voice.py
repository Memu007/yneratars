from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import ssl
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    import certifi
    _CA_BUNDLE = certifi.where()
except Exception:
    _CA_BUNDLE = os.environ.get("SSL_CERT_FILE") or ssl.get_default_verify_paths().openssl_cafile


class VoiceError(RuntimeError):
    """A safe, user-facing error raised by the voice providers."""


REALTIME_MODEL = os.getenv("TARS_REALTIME_MODEL", "gpt-realtime-2.1").strip() or "gpt-realtime-2.1"
TRANSCRIBE_MODEL = os.getenv("TARS_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe").strip() or "gpt-4o-mini-transcribe"
TTS_MODEL = os.getenv("TARS_TTS_MODEL", "gpt-4o-mini-tts").strip() or "gpt-4o-mini-tts"
REALTIME_VOICES = frozenset({"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"})
TTS_VOICES = frozenset({"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse", "marin", "cedar"})
MAX_SDP_BYTES = 1_000_000
MAX_AUDIO_BYTES = 25_000_000
MAX_TTS_CHARS = 8_000


@dataclass(frozen=True)
class BinaryResponse:
    body: bytes
    content_type: str


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=_CA_BUNDLE)


def _request_bytes(url: str, *, method: str = "POST", headers: dict[str, str] | None = None,
                   body: bytes | None = None, timeout: int = 60) -> BinaryResponse:
    request_headers = {"Accept": "*/*", **(headers or {})}
    try:
        request = Request(url, data=body, method=method, headers=request_headers)
        with urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            payload = response.read()
            content_type = response.headers.get_content_type() if response.headers else "application/octet-stream"
            return BinaryResponse(payload, content_type)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:3000]
        raise VoiceError(f"OpenAI voz respondió HTTP {exc.code}: {detail or exc.reason}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise VoiceError(f"No se pudo conectar con el servicio de voz: {exc}") from exc


def _multipart(fields: list[tuple[str, str | bytes, str | None, str | None]]) -> tuple[bytes, str]:
    boundary = f"----TARSVoice{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value, filename, content_type in fields:
        chunks.append(f"--{boundary}\r\n".encode())
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename.replace(chr(34), "")}"'
        chunks.append((disposition + "\r\n").encode())
        if content_type:
            chunks.append(f"Content-Type: {content_type}\r\n".encode())
        chunks.append(b"\r\n")
        chunks.append(value if isinstance(value, bytes) else value.encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _voice_name(value: str | None, allowed: frozenset[str], default: str = "marin") -> str:
    selected = str(value or default).strip().lower()
    if selected not in allowed:
        raise ValueError(f"voz inválida: {selected}")
    return selected


def realtime_instructions(settings: dict[str, Any]) -> str:
    personality = str(settings.get("personality") or "TARS es un asistente táctico confiable.").strip()
    humor = int(settings.get("humor", 70))
    return (
        f"{personality}\n"
        "Conversá por voz en español rioplatense claro y natural. Respondé breve, directo y con pausas humanas. "
        f"Nivel de humor seco: {humor}/100. "
        "No digas que ejecutaste una acción si no recibiste el resultado de una herramienta. "
        "Para navegar o investigar en la web usá dispatch_mission con worker CASE. "
        "Para análisis o programación usá dispatch_mission con worker KIPP. "
        "Si el usuario dice detener, parar todo o una emergencia equivalente, usá stop_all. "
        "Compras, pagos, transferencias, cambios de contraseña y desactivación de seguridad están prohibidos. "
        "Las acciones sensibles deben quedar sujetas al sistema local de aprobación."
    )


def realtime_session_config(settings: dict[str, Any], voice: str | None = None) -> dict[str, Any]:
    selected_voice = _voice_name(voice, REALTIME_VOICES)
    return {
        "type": "realtime",
        "model": REALTIME_MODEL,
        "instructions": realtime_instructions(settings),
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "transcription": {
                    "model": TRANSCRIBE_MODEL,
                    "language": "es",
                    "prompt": "Español rioplatense. Nombres frecuentes: TARS, CASE, KIPP, Brave, Gemini, OpenAI.",
                },
                "noise_reduction": {"type": "near_field"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "medium",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": selected_voice},
        },
        "tools": [
            {
                "type": "function",
                "name": "dispatch_mission",
                "description": "Despacha una misión al sistema local TARS. CASE opera la web; KIPP realiza análisis o programación.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "worker": {"type": "string", "enum": ["CASE", "KIPP", "TARS"]},
                        "description": {"type": "string", "minLength": 1},
                        "title": {"type": "string"},
                        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["worker", "description"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "stop_all",
                "description": "Detiene inmediatamente todas las misiones y procesos activos de TARS.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "type": "function",
                "name": "get_system_status",
                "description": "Obtiene el estado actual del servidor, la cola y las aprobaciones.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ],
        "tool_choice": "auto",
    }


def create_realtime_session(api_key: str, sdp: bytes, settings: dict[str, Any], voice: str | None = None) -> str:
    if not api_key:
        raise VoiceError("falta configurar la API key de OpenAI para el modo de voz natural")
    if not sdp or len(sdp) > MAX_SDP_BYTES:
        raise ValueError("oferta SDP vacía o demasiado grande")
    config = realtime_session_config(settings, voice)
    payload, content_type = _multipart([
        ("sdp", sdp, None, "application/sdp"),
        ("session", json.dumps(config, ensure_ascii=False), None, "application/json"),
    ])
    safety_id = hashlib.sha256(b"tars-local-voice").hexdigest()[:32]
    response = _request_bytes(
        "https://api.openai.com/v1/realtime/calls",
        headers={
            "Authorization": f"Bearer {api_key}",
            "OpenAI-Safety-Identifier": safety_id,
            "Content-Type": content_type,
        },
        body=payload,
        timeout=45,
    )
    answer = response.body.decode("utf-8", errors="strict").strip()
    if not answer.startswith("v="):
        raise VoiceError(f"OpenAI no devolvió una respuesta SDP válida: {answer[:500]}")
    return answer


def transcribe_audio(api_key: str, audio: bytes, content_type: str) -> str:
    if not api_key:
        raise VoiceError("falta configurar la API key de OpenAI para transcribir audio")
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("audio vacío o demasiado grande")
    mime = (content_type or "audio/webm").split(";", 1)[0].strip().lower()
    extension = mimetypes.guess_extension(mime) or ".webm"
    payload, multipart_type = _multipart([
        ("model", TRANSCRIBE_MODEL, None, None),
        ("response_format", "json", None, None),
        ("language", "es", None, None),
        ("prompt", "Español rioplatense. TARS, CASE, KIPP, Brave, Gemini, OpenAI.", None, None),
        ("file", audio, f"microfono{extension}", mime),
    ])
    response = _request_bytes(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": multipart_type},
        body=payload,
        timeout=60,
    )
    try:
        data = json.loads(response.body.decode("utf-8"))
        text = str(data.get("text") or "").strip()
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise VoiceError("la transcripción devolvió un formato inválido") from exc
    if not text:
        raise VoiceError("no se detectó voz en la grabación")
    return text



def transcribe_audio_google(api_key: str, audio: bytes, content_type: str, model: str) -> str:
    if not api_key:
        raise VoiceError("falta configurar la API key de Google para transcribir audio")
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("audio vacío o demasiado grande")
    selected_model = str(model or "").strip()
    if not selected_model:
        raise VoiceError("falta configurar un modelo Gemini compatible con audio")
    mime = (content_type or "audio/webm").split(";", 1)[0].strip().lower()
    payload = json.dumps({
        "contents": [{
            "role": "user",
            "parts": [
                {"text": "Transcribí exactamente este audio en español rioplatense. Devolvé sólo la transcripción, sin comentarios."},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode("ascii")}},
            ],
        }],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1200},
    }, ensure_ascii=False).encode("utf-8")
    response = _request_bytes(
        f"https://generativelanguage.googleapis.com/v1beta/models/{quote(selected_model, safe='')}:generateContent",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        body=payload,
        timeout=60,
    )
    try:
        data = json.loads(response.body.decode("utf-8"))
        pieces = [
            str(part.get("text"))
            for candidate in data.get("candidates", [])
            for part in candidate.get("content", {}).get("parts", [])
            if isinstance(part, dict) and part.get("text")
        ]
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise VoiceError("Gemini devolvió una transcripción inválida") from exc
    text = "\n".join(pieces).strip().strip('"')
    if not text:
        raise VoiceError("Gemini no detectó voz en la grabación")
    return text


def synthesize_speech(api_key: str, text: str, voice: str | None, settings: dict[str, Any]) -> bytes:
    if not api_key:
        raise VoiceError("falta configurar la API key de OpenAI para la voz natural")
    clean = str(text or "").strip()
    if not clean:
        raise ValueError("texto vacío")
    if len(clean) > MAX_TTS_CHARS:
        clean = clean[:MAX_TTS_CHARS]
    selected_voice = _voice_name(voice, TTS_VOICES)
    humor = int(settings.get("humor", 70))
    payload = json.dumps({
        "model": TTS_MODEL,
        "voice": selected_voice,
        "input": clean,
        "instructions": (
            "Hablá en español rioplatense natural, con dicción clara, ritmo conversacional y pausas humanas. "
            f"Tono táctico, confiable y sereno; humor seco {humor}/100. Evitá sonar como locutor o robot."
        ),
        "response_format": "mp3",
    }, ensure_ascii=False).encode("utf-8")
    response = _request_bytes(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        body=payload,
        timeout=60,
    )
    if not response.body:
        raise VoiceError("OpenAI no devolvió audio")
    return response.body
