from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .config import LOG_PATH, ROOT, SettingsStore
from .database import Database

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("tars.executor")


class MissionExecutor:
    def __init__(self, db: Database, settings: SettingsStore,
                 command_factory: Callable[[str], list[str]] | None = None):
        self.db = db
        self.settings = settings
        self.command_factory = command_factory or (
            lambda mission_id: [sys.executable, "-m", "app.worker", "--mission-id", mission_id]
        )
        self._shutdown = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_lock = threading.RLock()
        self._active: tuple[str, subprocess.Popen[bytes]] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._loop, name="tars-executor", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._shutdown.set()
        self._wake.set()
        with self._active_lock:
            if self._active and self._active[1].poll() is None:
                self._terminate_process(self._active[1])
        if self._thread:
            self._thread.join(timeout=5)

    def notify(self) -> None:
        self._wake.set()

    def status(self) -> dict:
        with self._active_lock:
            active = self._active
            return {
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "active_mission_id": active[0] if active else None,
                "active_pid": active[1].pid if active and active[1].poll() is None else None,
                "shutdown": self._shutdown.is_set(),
            }

    @staticmethod
    def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                if os.name == "posix" and proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                elif proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass

    def stop_mission(self, mission_id: str) -> None:
        with self._active_lock:
            if self._active and self._active[0] == mission_id:
                self._terminate_process(self._active[1])
        self._wake.set()

    def stop_all(self) -> list[str]:
        stopped: list[str] = []
        for mission in self.db.list_missions(limit=500):
            if mission["status"] in {"queued", "awaiting_approval", "running"}:
                try:
                    self.db.transition(mission["id"], "stop")
                    stopped.append(mission["id"])
                except Exception:
                    pass
        with self._active_lock:
            if self._active:
                self._terminate_process(self._active[1])
        self._wake.set()
        return stopped

    def _loop(self) -> None:
        while not self._shutdown.is_set():
            cfg = self.settings.get()
            if not cfg.get("auto_execute", True):
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            mission = self.db.claim_next()
            if not mission:
                self._wake.wait(0.8)
                self._wake.clear()
                continue
            self._run(mission, cfg)

    def _run(self, mission: dict, cfg: dict) -> None:
        command = self.command_factory(mission["id"])
        env = os.environ.copy()
        env["TARS_ROOT"] = str(ROOT)
        mission_log = ROOT / "logs" / f"mission-{mission['id']}.log"
        mission_log.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Ejecutando %s con %s", mission["id"], command)
        proc: subprocess.Popen[bytes] | None = None
        try:
            with mission_log.open("wb") as output:
                proc = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=(os.name == "posix"),
                )
                with self._active_lock:
                    self._active = (mission["id"], proc)
                timeout = int(cfg.get("worker_timeout_seconds", 420))
                started = time.monotonic()
                while proc.poll() is None and not self._shutdown.is_set():
                    current = self.db.get_mission(mission["id"])
                    if not current or current["status"] == "stopped":
                        self._terminate_process(proc)
                        break
                    if time.monotonic() - started > timeout:
                        self._terminate_process(proc)
                        raise TimeoutError(f"la misión superó {timeout} segundos")
                    time.sleep(0.25)
                if self._shutdown.is_set() and proc.poll() is None:
                    self._terminate_process(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._terminate_process(proc)

            output_text = self._read_log_tail(mission_log)
            payload = self._parse_result(output_text)
            current = self.db.get_mission(mission["id"])
            if current and current["status"] == "running":
                if proc is not None and proc.returncode == 0 and payload.get("ok"):
                    self.db.finish(mission["id"], True, result=str(payload.get("result", "")))
                else:
                    error = str(payload.get("error") or output_text or f"worker terminó con código {getattr(proc, 'returncode', '?')}")
                    self.db.finish(mission["id"], False, error=error)
        except Exception as exc:
            logger.exception("Falló la misión %s", mission["id"])
            if proc is not None and proc.poll() is None:
                self._terminate_process(proc)
            current = self.db.get_mission(mission["id"])
            if current and current["status"] == "running":
                self.db.finish(mission["id"], False, error=str(exc))
        finally:
            with self._active_lock:
                self._active = None

    @staticmethod
    def _read_log_tail(path: Path, limit: int = 2_000_000) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit))
                return handle.read().decode("utf-8", errors="replace")
        except OSError as exc:
            return f"No se pudo leer el log de la misión: {exc}"

    @staticmethod
    def _parse_result(stdout: str) -> dict:
        for line in reversed(stdout.splitlines()):
            if line.startswith("TARS_RESULT="):
                try:
                    value = json.loads(line.removeprefix("TARS_RESULT="))
                    return value if isinstance(value, dict) else {"ok": False, "error": "resultado inválido"}
                except json.JSONDecodeError:
                    break
        return {"ok": False, "error": "el worker no devolvió un resultado reconocible", "stdout": stdout[-4000:]}
