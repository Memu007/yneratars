from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
from pathlib import Path

from .config import ROOT

SERVICE = "TARS Local"
VALID_NAMES = {"google", "openai"}


class SecretStore:
    def __init__(self, fallback_path: Path | None = None, force_file: bool = False):
        self.fallback_path = fallback_path or (ROOT / "config" / ".secrets.json")
        self.force_file = force_file
        self._lock = threading.RLock()

    @property
    def uses_keychain(self) -> bool:
        return not self.force_file and platform.system() == "Darwin" and bool(self._which("security"))

    @staticmethod
    def _which(command: str) -> str | None:
        from shutil import which
        return which(command)

    def _validate(self, name: str) -> str:
        name = name.lower().strip()
        if name not in VALID_NAMES:
            raise ValueError("proveedor de secreto inválido")
        return name

    def set(self, name: str, value: str) -> None:
        name = self._validate(name)
        value = value.strip()
        if len(value) < 8:
            raise ValueError("la API key parece demasiado corta")
        with self._lock:
            if self.uses_keychain:
                proc = subprocess.run(
                    ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", value],
                    text=True, capture_output=True, timeout=10,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip() or "no se pudo guardar en Keychain")
            else:
                data = self._read_file()
                data[name] = value
                self._write_file(data)

    def get(self, name: str) -> str | None:
        name = self._validate(name)
        env_name = "GOOGLE_API_KEY" if name == "google" else "OPENAI_API_KEY"
        if os.getenv(env_name):
            return os.environ[env_name].strip()
        with self._lock:
            if self.uses_keychain:
                proc = subprocess.run(
                    ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                    text=True, capture_output=True, timeout=10,
                )
                return proc.stdout.strip() if proc.returncode == 0 else None
            return self._read_file().get(name)

    def delete(self, name: str) -> None:
        name = self._validate(name)
        with self._lock:
            if self.uses_keychain:
                subprocess.run(
                    ["security", "delete-generic-password", "-s", SERVICE, "-a", name],
                    text=True, capture_output=True, timeout=10,
                )
            else:
                data = self._read_file()
                data.pop(name, None)
                self._write_file(data)

    def status(self) -> dict[str, object]:
        return {
            "storage": "macOS Keychain" if self.uses_keychain else "archivo local 0600",
            "google": bool(self.get("google")),
            "openai": bool(self.get("openai")),
        }

    def _read_file(self) -> dict[str, str]:
        try:
            raw = json.loads(self.fallback_path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write_file(self, data: dict[str, str]) -> None:
        self.fallback_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fallback_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.fallback_path)
        os.chmod(self.fallback_path, 0o600)
