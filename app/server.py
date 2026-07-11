from __future__ import annotations

import importlib.util
import json
import mimetypes
import os
import platform
import shutil
import subprocess
import sys
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ROOT, VERSION, SettingsStore
from .database import ConflictError, Database
from .executor import MissionExecutor
from .providers import ProviderError, get_provider
from .secrets_store import SecretStore
from .worker import system_prompt
from .voice import (MAX_AUDIO_BYTES, MAX_SDP_BYTES, VoiceError, create_realtime_session,
                    synthesize_speech, transcribe_audio, transcribe_audio_google)

STATIC_ROOT = ROOT / "static"


class TarsApplication:
    def __init__(self, db: Database | None = None, settings: SettingsStore | None = None,
                 secrets: SecretStore | None = None, start_executor: bool = True):
        self.db = db or Database()
        self.recovered_missions = self.db.recover_interrupted()
        self.settings = settings or SettingsStore()
        self.secrets = secrets or SecretStore()
        self.executor = MissionExecutor(self.db, self.settings)
        if start_executor:
            self.executor.start()

    def close(self) -> None:
        self.executor.close()

    def status(self, secret_status: dict[str, object] | None = None) -> dict[str, Any]:
        cfg = self.settings.get()
        secret_status = secret_status or self.secrets.status()
        ui_tars_paths = (
            Path("/Applications/UI TARS.app"),
            Path("/Applications/UI-TARS.app"),
            Path.home() / "Applications" / "UI TARS.app",
        )
        executor_status = self.executor.status()
        return {
            "ok": True,
            "service": "tars-local",
            "version": VERSION,
            "recovered_missions": len(self.recovered_missions),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "browser_use_installed": importlib.util.find_spec("browser_use") is not None,
            "ui_tars_installed": bool(shutil.which("ui-tars") or any(path.exists() for path in ui_tars_paths)),
            "native_voice_available": platform.system() == "Darwin" and shutil.which("say") is not None,
            "realtime_voice_available": bool(secret_status.get("openai")),
            "browser_microphone_supported": True,
            "key_storage": secret_status["storage"],
            "provider": cfg["provider"],
            "executor": executor_status,
        }

    def dashboard(self) -> dict[str, Any]:
        missions = self.db.list_missions(limit=100)
        secret_status = self.secrets.status()
        return {
            "health": self.status(secret_status),
            "settings": self.settings.get(),
            "secrets": secret_status,
            "stats": self.db.mission_stats(),
            "missions": missions,
            "activity": self.db.recent_events(limit=24),
        }

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message", "")).strip()
        if not message or len(message) > 20000:
            raise ValueError("mensaje vacío o demasiado largo")
        conversation_id = str(payload.get("conversation_id") or uuid.uuid4())[:120]
        cfg = self.settings.get()
        provider_name = str(payload.get("provider") or cfg["provider"])
        if provider_name not in {"google", "openai"}:
            raise ValueError("proveedor inválido")
        model = str(payload.get("model") or cfg.get(f"{provider_name}_model") or "").strip()
        if not model:
            raise ValueError("falta elegir un modelo")
        key = self.secrets.get(provider_name)
        if not key:
            raise ProviderError(f"falta configurar la API key de {provider_name}")
        previous = self.db.messages(conversation_id, limit=16)
        provider = get_provider(provider_name, key)
        self.db.add_message(conversation_id, "user", message)
        response = provider.generate(model, message, system_prompt("TARS", cfg), previous)
        self.db.add_message(conversation_id, "assistant", response.text)
        return {
            "conversation_id": conversation_id,
            "text": response.text,
            "provider": provider_name,
            "model": model,
            "usage": response.usage,
        }


def make_handler(app: TarsApplication):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"TARSLocal/{VERSION}"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _local_host_ok(self) -> bool:
            host = self.headers.get("Host", "").strip().lower()
            if host.startswith("["):
                end = host.find("]")
                hostname = host[1:end] if end > 0 else ""
            elif host.count(":") == 1:
                hostname = host.rsplit(":", 1)[0]
            else:
                hostname = host
            return hostname in {"127.0.0.1", "localhost", "::1"}

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "microphone=(self)")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self' https://api.openai.com wss://api.openai.com; "
                "media-src 'self' blob:; img-src 'self' data:; frame-ancestors 'none'",
            )

        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self._security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self, max_bytes: int) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Content-Length inválido") from exc
            if length < 0 or length > max_bytes:
                raise ValueError("payload demasiado grande")
            return self.rfile.read(length) if length else b""

        def _file(self, path: Path) -> None:
            if not path.exists() or not path.is_file():
                self._json({"error": "no encontrado"}, 404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self._security_headers()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith(("text/", "application/javascript")) else mime)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                raise ValueError("Content-Type debe ser application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("payload demasiado grande")
            raw = self.rfile.read(length) if length else b"{}"
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("se esperaba un objeto JSON")
            return parsed

        def _require_client_header(self) -> None:
            if self.headers.get("X-TARS-Client") != "dashboard":
                raise PermissionError("falta la cabecera de cliente local")

        def do_GET(self) -> None:  # noqa: N802
            if not self._local_host_ok():
                self._json({"error": "host no permitido"}, 403)
                return
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            query = parse_qs(parsed_url.query)
            try:
                if path == "/":
                    self._file(STATIC_ROOT / "index.html")
                elif path.startswith("/static/"):
                    name = Path(path.removeprefix("/static/")).name
                    self._file(STATIC_ROOT / name)
                elif path == "/api/health":
                    self._json(app.status())
                elif path == "/api/settings":
                    secret_status = app.secrets.status()
                    self._json({"settings": app.settings.get(), "secrets": secret_status, "health": app.status(secret_status)})
                elif path == "/api/dashboard":
                    self._json(app.dashboard())
                elif path == "/api/activity":
                    limit = int((query.get("limit") or ["30"])[0])
                    self._json(app.db.recent_events(limit=limit))
                elif path == "/api/messages":
                    conversation_id = str((query.get("conversation_id") or [""])[0])[:120]
                    if not conversation_id:
                        raise ValueError("falta conversation_id")
                    limit = int((query.get("limit") or ["100"])[0])
                    self._json(app.db.messages(conversation_id, limit=limit))
                elif path == "/api/conversations":
                    self._json(app.db.conversation_ids(limit=30))
                elif path == "/api/missions":
                    self._json(app.db.list_missions())
                elif path.startswith("/api/missions/"):
                    parts = path.strip("/").split("/")
                    if len(parts) == 4 and parts[3] == "events":
                        self._json(app.db.events(parts[2]))
                    elif len(parts) == 3:
                        mission = app.db.get_mission(parts[2])
                        self._json(mission if mission else {"error": "misión inexistente"}, 200 if mission else 404)
                    else:
                        self._json({"error": "no encontrado"}, 404)
                elif path.startswith("/api/providers/") and path.endswith("/models"):
                    self._require_client_header()
                    provider_name = path.split("/")[3]
                    key = app.secrets.get(provider_name)
                    if not key:
                        raise ProviderError(f"falta configurar la API key de {provider_name}")
                    models = get_provider(provider_name, key).list_models()
                    self._json({"provider": provider_name, "models": models})
                else:
                    self._json({"error": "no encontrado"}, 404)
            except Exception as exc:
                self._handle_error(exc)

        def do_POST(self) -> None:  # noqa: N802
            if not self._local_host_ok():
                self._json({"error": "host no permitido"}, 403)
                return
            try:
                self._require_client_header()
                path = urlparse(self.path).path
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()

                if path == "/api/voice/realtime/session":
                    if content_type != "application/sdp":
                        raise ValueError("Content-Type debe ser application/sdp")
                    sdp = self._read_body(MAX_SDP_BYTES)
                    key = app.secrets.get("openai")
                    if not key:
                        raise VoiceError("falta configurar una API key de OpenAI para voz Realtime")
                    voice = self.headers.get("X-TARS-Voice", "marin")
                    answer = create_realtime_session(key, sdp, app.settings.get(), voice)
                    self._bytes(answer.encode("utf-8"), "application/sdp; charset=utf-8")
                    return

                if path == "/api/voice/transcribe":
                    if not (content_type.startswith("audio/") or content_type == "application/octet-stream"):
                        raise ValueError("Content-Type debe ser audio/*")
                    audio = self._read_body(MAX_AUDIO_BYTES)
                    openai_key = app.secrets.get("openai")
                    google_key = app.secrets.get("google")
                    if openai_key:
                        text = transcribe_audio(openai_key, audio, content_type)
                        engine = "openai"
                    elif google_key:
                        cfg = app.settings.get()
                        text = transcribe_audio_google(google_key, audio, content_type, cfg.get("google_model", ""))
                        engine = "google"
                    else:
                        raise VoiceError("configurá una API key de OpenAI o Google para transcribir")
                    self._json({"text": text, "engine": engine})
                    return

                payload = self._read_json()
                if path == "/api/settings":
                    self._json({"settings": app.settings.update(payload)})
                    app.executor.notify()
                    return
                if path == "/api/chat":
                    self._json(app.chat(payload))
                    return
                if path == "/api/voice/message":
                    conversation_id = str(payload.get("conversation_id") or "").strip()[:120]
                    role = str(payload.get("role") or "").strip().lower()
                    content = str(payload.get("content") or "").strip()[:20000]
                    if not conversation_id or role not in {"user", "assistant"} or not content:
                        raise ValueError("mensaje de voz inválido")
                    app.db.add_message(conversation_id, role, content)
                    self._json({"ok": True})
                    return
                if path == "/api/missions":
                    mission = app.db.create_mission(payload, app.settings.get())
                    app.executor.notify()
                    self._json(mission, 201)
                    return
                if path == "/api/stop-all":
                    self._json({"stopped": app.executor.stop_all()})
                    return
                if path == "/api/voice/tts":
                    text = str(payload.get("text", "")).strip()
                    voice = str(payload.get("voice", "marin")).strip()
                    key = app.secrets.get("openai")
                    if not key:
                        raise VoiceError("falta configurar una API key de OpenAI para voz natural")
                    audio = synthesize_speech(key, text, voice, app.settings.get())
                    self._bytes(audio, "audio/mpeg")
                    return
                if path == "/api/voice/say":
                    text = str(payload.get("text", "")).strip()[:4000]
                    if not text:
                        raise ValueError("texto vacío")
                    if platform.system() != "Darwin":
                        raise ConflictError("la voz nativa requiere macOS")
                    rate = int(payload.get("rate", 190))
                    if not 80 <= rate <= 400:
                        raise ValueError("velocidad de voz fuera de rango")
                    voice = str(payload.get("voice", "")).strip()[:80]
                    command = ["say", "-r", str(rate)]
                    if voice:
                        command.extend(["-v", voice])
                    command.append(text)
                    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._json({"ok": True})
                    return
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "secrets"] and parts[3] == "save":
                    provider_name = parts[2]
                    app.secrets.set(provider_name, str(payload.get("api_key", "")))
                    self._json({"ok": True, "status": app.secrets.status()})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "secrets"] and parts[3] == "delete":
                    app.secrets.delete(parts[2])
                    self._json({"ok": True, "status": app.secrets.status()})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "providers"] and parts[3] == "test":
                    provider_name = parts[2]
                    key = app.secrets.get(provider_name)
                    if not key:
                        raise ProviderError(f"falta configurar la API key de {provider_name}")
                    models = get_provider(provider_name, key).list_models()
                    self._json({"ok": True, "provider": provider_name, "model_count": len(models), "sample": models[:12]})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "missions"]:
                    mission_id, action = parts[2], parts[3]
                    updated = app.db.transition(mission_id, action)
                    if action == "stop":
                        app.executor.stop_mission(mission_id)
                    else:
                        app.executor.notify()
                    self._json(updated)
                    return
                self._json({"error": "no encontrado"}, 404)
            except Exception as exc:
                self._handle_error(exc)

        def _handle_error(self, exc: Exception) -> None:
            if isinstance(exc, KeyError):
                self._json({"error": str(exc)}, 404)
            elif isinstance(exc, PermissionError):
                self._json({"error": str(exc)}, 403)
            elif isinstance(exc, ConflictError):
                self._json({"error": str(exc)}, 409)
            elif isinstance(exc, (ValueError, json.JSONDecodeError)):
                self._json({"error": str(exc)}, 400)
            elif isinstance(exc, (ProviderError, VoiceError)):
                self._json({"error": str(exc)}, 502)
            else:
                self._json({"error": f"error interno: {exc}"}, 500)

    return Handler


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def serve() -> None:
    app = TarsApplication()
    cfg = app.settings.get()
    server = ReusableThreadingHTTPServer((cfg["host"], int(cfg["port"])), make_handler(app))
    print(f"TARS Local {VERSION} activo en http://{cfg['host']}:{cfg['port']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        app.close()


if __name__ == "__main__":
    serve()
