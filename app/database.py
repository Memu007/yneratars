from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from .config import DB_PATH
from .security import WORKERS, effective_risk, infer_risk, initial_status


class ConflictError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def init(self) -> None:
        with closing(self.connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    worker TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    risk_reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    approved_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_missions_status_created ON missions(status, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(mission_id) REFERENCES missions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(missions)").fetchall()}
            if "approved_at" not in columns:
                conn.execute("ALTER TABLE missions ADD COLUMN approved_at REAL")

    def add_event(self, mission_id: str, kind: str, detail: str) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                "INSERT INTO events(mission_id,kind,detail,created_at) VALUES(?,?,?,?)",
                (mission_id, kind, detail[:4000], time.time()),
            )

    def create_mission(self, payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "Misión sin título")).strip()[:160]
        description = str(payload.get("description", "")).strip()[:12000]
        worker = str(payload.get("worker", "CASE")).strip().upper()
        requested_risk = str(payload.get("risk", "low")).strip().lower()
        if not title or not description:
            raise ValueError("título y descripción son obligatorios")
        if worker not in WORKERS:
            raise ValueError("worker debe ser TARS, CASE o KIPP")
        assessment = infer_risk(title, description)
        risk = effective_risk(requested_risk, assessment.risk)
        tool = "browser" if worker == "CASE" else "chat"
        provider = str(payload.get("provider") or settings.get("provider", "google"))
        if provider not in {"google", "openai"}:
            raise ValueError("provider inválido")
        model = str(payload.get("model") or (
            settings.get("browser_model") if tool == "browser" else settings.get(f"{provider}_model")
        ) or "").strip()
        if not model:
            raise ValueError("falta elegir un modelo")
        now = time.time()
        status = initial_status(risk)
        row = {
            "id": str(uuid.uuid4()), "title": title, "description": description,
            "worker": worker, "tool": tool, "provider": provider, "model": model,
            "risk": risk, "risk_reason": assessment.reason,
            "status": status, "result": None, "error": None,
            "created_at": now, "updated_at": now, "started_at": None,
            "completed_at": now if status == "blocked" else None, "approved_at": None,
        }
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO missions
                (id,title,description,worker,tool,provider,model,risk,risk_reason,status,result,error,
                 created_at,updated_at,started_at,completed_at,approved_at)
                VALUES(:id,:title,:description,:worker,:tool,:provider,:model,:risk,:risk_reason,:status,
                       :result,:error,:created_at,:updated_at,:started_at,:completed_at,:approved_at)""", row,
            )
            conn.commit()
        detail = f"Creada para {worker} con {provider}/{model}; riesgo {risk}: {assessment.reason}"
        if risk != requested_risk:
            detail += f"; elevado desde {requested_risk}"
        self.add_event(row["id"], "created", detail)
        return row

    def list_missions(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM missions ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 500),)
            ).fetchall()
        return [dict(x) for x in rows]

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        return dict(row) if row else None

    def events(self, mission_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT id,kind,detail,created_at FROM events WHERE mission_id=? ORDER BY id", (mission_id,)
            ).fetchall()
        return [dict(x) for x in rows]

    def transition(self, mission_id: str, action: str) -> dict[str, Any]:
        allowed_by_action = {
            "approve": {"awaiting_approval"},
            "reject": {"awaiting_approval"},
            "retry": {"failed", "stopped"},
            "stop": {"queued", "awaiting_approval", "running"},
        }
        if action not in allowed_by_action:
            raise ValueError("acción inválida")
        now = time.time()
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
            if not row:
                conn.rollback()
                raise KeyError("misión inexistente")
            if row["status"] not in allowed_by_action[action]:
                conn.rollback()
                raise ConflictError(f"no se puede ejecutar {action} desde {row['status']}")

            approved_at = row["approved_at"]
            if action == "approve":
                target = "queued"
                approved_at = now
            elif action == "reject":
                target = "rejected"
            elif action == "stop":
                target = "stopped"
            else:  # retry
                requires_approval = row["risk"] in {"medium", "high"} and not approved_at
                target = "awaiting_approval" if requires_approval else "queued"

            completed_at = now if target in {"rejected", "stopped"} else None
            conn.execute(
                """UPDATE missions
                   SET status=?, updated_at=?, completed_at=?, approved_at=?,
                       started_at=CASE WHEN ? IN ('queued','awaiting_approval') THEN NULL ELSE started_at END,
                       result=CASE WHEN ? IN ('queued','awaiting_approval') THEN NULL ELSE result END,
                       error=CASE WHEN ? IN ('queued','awaiting_approval') THEN NULL ELSE error END
                   WHERE id=?""",
                (target, now, completed_at, approved_at, target, target, target, mission_id),
            )
            updated = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
            conn.commit()
        self.add_event(mission_id, action, f"Estado {row['status']} → {target}")
        return dict(updated)

    def claim_next(self) -> dict[str, Any] | None:
        now = time.time()
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT id FROM missions WHERE status='running' LIMIT 1").fetchone()
            if active:
                conn.rollback()
                return None
            row = conn.execute(
                "SELECT * FROM missions WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute(
                "UPDATE missions SET status='running',started_at=?,updated_at=?,error=NULL WHERE id=? AND status='queued'",
                (now, now, row["id"]),
            )
            updated = conn.execute("SELECT * FROM missions WHERE id=?", (row["id"],)).fetchone()
            conn.commit()
        self.add_event(row["id"], "started", "El ejecutor tomó la misión")
        return dict(updated)

    def finish(self, mission_id: str, success: bool, result: str = "", error: str = "") -> dict[str, Any]:
        target = "completed" if success else "failed"
        now = time.time()
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM missions WHERE id=?", (mission_id,)).fetchone()
            if not row:
                conn.rollback()
                raise KeyError("misión inexistente")
            if row["status"] != "running":
                conn.rollback()
                current = self.get_mission(mission_id)
                return current or {}
            conn.execute(
                "UPDATE missions SET status=?,result=?,error=?,updated_at=?,completed_at=? WHERE id=?",
                (target, result[:100000] or None, error[:20000] or None, now, now, mission_id),
            )
            updated = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
            conn.commit()
        self.add_event(mission_id, target, result[:1000] if success else error[:1000])
        return dict(updated)

    def recover_interrupted(self) -> list[str]:
        """Fail jobs left running after a crash so the queue cannot stay blocked."""
        now = time.time()
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id FROM missions WHERE status='running'").fetchall()
            ids = [str(row["id"]) for row in rows]
            if ids:
                conn.execute(
                    """UPDATE missions SET status='failed', error=?, updated_at=?, completed_at=?
                       WHERE status='running'""",
                    ("La ejecución fue interrumpida por un cierre o reinicio de TARS.", now, now),
                )
            conn.commit()
        for mission_id in ids:
            self.add_event(mission_id, "recovered", "Misión marcada como fallida tras reinicio")
        return ids

    def mission_stats(self) -> dict[str, int]:
        """Return compact mission counters for the dashboard."""
        statuses = (
            "queued", "awaiting_approval", "running", "completed",
            "failed", "stopped", "blocked", "rejected",
        )
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM missions GROUP BY status"
            ).fetchall()
        counts = {status: 0 for status in statuses}
        for row in rows:
            counts[str(row["status"])] = int(row["total"])
        counts["all"] = sum(counts.values())
        counts["active"] = counts["queued"] + counts["running"]
        counts["attention"] = (
            counts["awaiting_approval"] + counts["failed"] + counts["blocked"]
        )
        return counts

    def recent_events(self, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent mission activity with enough context for the UI feed."""
        safe_limit = min(max(int(limit), 1), 200)
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """SELECT e.id,e.mission_id,e.kind,e.detail,e.created_at,
                          m.title,m.worker,m.status
                   FROM events e
                   JOIN missions m ON m.id=e.mission_id
                   ORDER BY e.id DESC LIMIT ?""",
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def conversation_ids(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recently active conversations without exposing provider secrets."""
        safe_limit = min(max(int(limit), 1), 100)
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """SELECT conversation_id, MAX(created_at) AS updated_at, COUNT(*) AS message_count
                   FROM messages GROUP BY conversation_id
                   ORDER BY updated_at DESC LIMIT ?""",
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("rol inválido")
        with closing(self.connect()) as conn:
            conn.execute(
                "INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                (conversation_id, role, content[:100000], time.time()),
            )

    def messages(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT role,content,created_at FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                (conversation_id, min(max(limit, 1), 100)),
            ).fetchall()
        return [dict(x) for x in reversed(rows)]
