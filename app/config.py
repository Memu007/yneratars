from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("TARS_ROOT", Path(__file__).resolve().parents[1])).resolve()
VERSION = "0.3.0"
DEFAULTS_PATH = ROOT / "config" / "defaults.json"
SETTINGS_PATH = ROOT / "config" / "settings.json"
DB_PATH = ROOT / "data" / "tars.db"
LOG_PATH = ROOT / "logs" / "tars.log"
ENV_PATH = ROOT / ".env"

_ALLOWED = {
    "provider", "google_model", "openai_model", "browser_model", "humor",
    "auto_speak", "auto_execute", "browser_enabled", "browser_max_steps",
    "worker_timeout_seconds", "host", "port", "allowed_domains", "personality",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _load_env_file(path: Path = ENV_PATH) -> None:
    """Load a minimal KEY=VALUE .env file without executing shell code."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.replace("_", "").isalnum() or not key[0].isalpha():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_env_file()


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else deepcopy(fallback)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return deepcopy(fallback)


class SettingsStore:
    def __init__(self, defaults_path: Path = DEFAULTS_PATH, settings_path: Path = SETTINGS_PATH):
        self.defaults_path = defaults_path
        self.settings_path = settings_path
        self._lock = threading.RLock()

    def get(self) -> dict[str, Any]:
        with self._lock:
            defaults = _load_json(self.defaults_path, {})
            saved = _load_json(self.settings_path, {})
            merged = {**defaults, **saved}
            merged["host"] = os.getenv("TARS_HOST", str(merged.get("host", "127.0.0.1")))
            merged["port"] = int(os.getenv("TARS_PORT", str(merged.get("port", 8765))))
            return self.validate(merged, partial=False)

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("configuración inválida")
        unknown = set(changes) - _ALLOWED
        if unknown:
            raise ValueError(f"campos desconocidos: {', '.join(sorted(unknown))}")
        with self._lock:
            current = self.get()
            current.update(self.validate(changes, partial=True))
            validated = self.validate(current, partial=False)
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.settings_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.settings_path)
            return validated

    @staticmethod
    def validate(data: dict[str, Any], partial: bool) -> dict[str, Any]:
        out = dict(data)
        if "provider" in out and out["provider"] not in {"google", "openai"}:
            raise ValueError("provider debe ser google u openai")
        for key in ("google_model", "openai_model", "browser_model", "personality", "host"):
            if key in out:
                if not isinstance(out[key], str):
                    raise ValueError(f"{key} debe ser texto")
                out[key] = out[key].strip()
        if "host" in out and out["host"].lower() not in _LOOPBACK_HOSTS:
            raise ValueError("por seguridad, host debe ser 127.0.0.1, localhost o ::1")
        if "humor" in out:
            out["humor"] = int(out["humor"])
            if not 0 <= out["humor"] <= 100:
                raise ValueError("humor debe estar entre 0 y 100")
        for key in ("auto_speak", "auto_execute", "browser_enabled"):
            if key in out and not isinstance(out[key], bool):
                raise ValueError(f"{key} debe ser booleano")
        if "browser_max_steps" in out:
            out["browser_max_steps"] = int(out["browser_max_steps"])
            if not 1 <= out["browser_max_steps"] <= 100:
                raise ValueError("browser_max_steps fuera de rango")
        if "worker_timeout_seconds" in out:
            out["worker_timeout_seconds"] = int(out["worker_timeout_seconds"])
            if not 30 <= out["worker_timeout_seconds"] <= 3600:
                raise ValueError("worker_timeout_seconds fuera de rango")
        if "port" in out:
            out["port"] = int(out["port"])
            if not 1 <= out["port"] <= 65535:
                raise ValueError("puerto inválido")
        if "allowed_domains" in out:
            if not isinstance(out["allowed_domains"], list) or not all(isinstance(x, str) for x in out["allowed_domains"]):
                raise ValueError("allowed_domains debe ser una lista de dominios")
            cleaned: set[str] = set()
            for raw in out["allowed_domains"]:
                domain = raw.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
                if not domain:
                    continue
                if "/" in domain or " " in domain:
                    raise ValueError(f"dominio inválido: {raw}")
                cleaned.add(domain)
            out["allowed_domains"] = sorted(cleaned)
        if not partial:
            required = {"provider", "humor", "auto_speak", "auto_execute", "browser_enabled", "port"}
            missing = required - set(out)
            if missing:
                raise ValueError(f"faltan configuraciones: {', '.join(sorted(missing))}")
        return out
