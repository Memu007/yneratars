from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import sys
import time
import traceback
from pathlib import Path

from .config import ROOT, SettingsStore
from .database import Database
from .providers import get_provider
from .secrets_store import SecretStore
from .security import browser_guardrails, excluded_browser_actions


def system_prompt(worker: str, settings: dict) -> str:
    humor = int(settings.get("humor", 70))
    base = str(settings.get("personality", "TARS es un asistente táctico confiable."))
    role = {
        "TARS": "Sos el comandante: analizá, respondé y decidí el siguiente paso sin inventar acciones.",
        "CASE": "Sos el operador web: ejecutá sólo la misión autorizada y reportá cada limitación.",
        "KIPP": "Sos el programador: entregá soluciones técnicas concretas, verificables y conservadoras.",
    }.get(worker, "Sos un asistente técnico.")
    return f"{base}\n{role}\nNivel de humor: {humor}/100. La exactitud y la seguridad tienen prioridad sobre el humor."


def _browser_classes():
    try:
        from browser_use import Agent, ChatGoogle, ChatOpenAI, Tools
    except ImportError:
        try:
            from browser_use import Agent, Tools
            from browser_use.llm.google.chat import ChatGoogle
            from browser_use.llm.openai.chat import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Browser Use no está instalado. Ejecutá scripts/setup_mac.command antes de usar CASE."
            ) from exc
    return Agent, ChatGoogle, ChatOpenAI, Tools


def _browser_session_classes():
    try:
        from browser_use.browser import BrowserProfile, BrowserSession
    except ImportError as exc:
        raise RuntimeError(
            "La instalación de Browser Use está incompleta. Ejecutá scripts/setup_mac.command nuevamente."
        ) from exc
    return BrowserProfile, BrowserSession


def brave_executable_candidates() -> list[Path]:
    """Return Brave executable candidates without considering Chrome or Playwright Chromium."""
    candidates: list[Path] = []
    custom = os.getenv("TARS_BRAVE_PATH", "").strip()
    if custom:
        candidates.append(Path(custom).expanduser())

    system = platform.system()
    if system == "Darwin":
        candidates.extend([
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            Path.home() / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ])
    elif system == "Windows":
        for root_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            system_root = os.getenv(root_name, "").strip()
            if system_root:
                candidates.append(Path(system_root) / "BraveSoftware/Brave-Browser/Application/brave.exe")
    else:
        for command in ("brave-browser", "brave-browser-stable", "brave"):
            resolved = shutil.which(command)
            if resolved:
                candidates.append(Path(resolved))
    return candidates


def find_brave_executable() -> Path | None:
    for candidate in brave_executable_candidates():
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
        except OSError:
            continue
    return None


def resolve_browser_model(mission: dict, settings: dict) -> str:
    """Keep the selected browser model compatible with the chosen provider."""
    provider = str(mission.get("provider", "google"))
    requested = str(mission.get("model", "")).strip()
    if provider == "google" and requested and not requested.lower().startswith("gemini"):
        return str(settings.get("google_model") or requested).strip()
    if provider == "openai" and requested.lower().startswith("gemini"):
        return str(settings.get("openai_model") or requested).strip()
    return requested or str(settings.get(f"{provider}_model") or "").strip()


def _mission_event(db: Database, mission_id: str, kind: str, detail: str) -> None:
    db.add_event(mission_id, kind, detail)
    # Touch the mission so the existing dashboard signature notices progress events.
    try:
        conn = db.connect()
        try:
            conn.execute("UPDATE missions SET updated_at=? WHERE id=?", (time.time(), mission_id))
        finally:
            conn.close()
    except Exception:
        pass
    print(f"TARS_PROGRESS={kind}:{detail}", flush=True)


async def run_browser(mission: dict, settings: dict, api_key: str) -> dict:
    if not settings.get("browser_enabled", True):
        raise RuntimeError("el operador web está desactivado en Configuración")

    brave_path = find_brave_executable()
    if brave_path is None:
        raise RuntimeError(
            "Brave Browser no está instalado o no se encontró su ejecutable. "
            "Instalalo con 'brew install --cask brave-browser' o definí TARS_BRAVE_PATH. "
            "CASE no hará fallback a Chrome ni a Chromium."
        )

    Agent, ChatGoogle, ChatOpenAI, Tools = _browser_classes()
    BrowserProfile, BrowserSession = _browser_session_classes()
    model = resolve_browser_model(mission, settings)
    if not model:
        raise RuntimeError("no hay un modelo compatible configurado para CASE")
    if mission["provider"] == "google":
        llm = ChatGoogle(model=model, api_key=api_key, temperature=0.0, max_retries=1)
    else:
        llm = ChatOpenAI(model=model, api_key=api_key, temperature=0.0, max_retries=1)

    task = f"""MISIÓN DEL USUARIO:
{mission['description']}

{browser_guardrails(mission['risk'])}
Usá exclusivamente Brave Browser. No abras Google Chrome ni el Chromium incluido con Playwright.
Podés hacer clic, escribir búsquedas y usar filtros para cumplir la misión. No confirmes envíos ni acciones sensibles sin autorización.
Si una página no responde, probá una alternativa razonable y reportá el bloqueo con precisión.
Al finalizar, resumí exactamente qué hiciste, qué páginas visitaste y qué quedó pendiente.
"""
    tools = Tools(exclude_actions=excluded_browser_actions(mission["risk"]))
    allowed = settings.get("allowed_domains") or None
    try:
        browser_profile = BrowserProfile(
            executable_path=str(brave_path),
            headless=False,
            allowed_domains=allowed,
            user_data_dir=None,
            keep_alive=False,
            enable_default_extensions=False,
            args=["--no-first-run", "--no-default-browser-check", "--disable-brave-update"],
        )
        browser_session = BrowserSession(browser_profile=browser_profile)
    except Exception as exc:
        raise RuntimeError(f"no se pudo preparar Brave para CASE: {exc}") from exc

    db = Database()
    mission_id = str(mission.get("id") or "")
    step_number = 0

    async def on_step_start(agent):
        nonlocal step_number
        step_number += 1
        if mission_id and step_number == 1:
            _mission_event(db, mission_id, "browser_ready", "Brave listo; CASE comienza la misión")
        url = "pantalla actual"
        try:
            url = await asyncio.wait_for(agent.browser_session.get_current_page_url(), timeout=3)
        except Exception:
            pass
        if mission_id:
            _mission_event(db, mission_id, "progress", f"Paso {step_number}: analizando {url}")

    async def on_step_end(agent):
        action = "acción completada"
        try:
            actions = agent.history.action_names()
            if actions:
                action = str(actions[-1])
        except Exception:
            pass
        if mission_id:
            _mission_event(db, mission_id, "progress", f"Paso {step_number}: {action}")

    agent = Agent(
        task=task,
        tools=tools,
        llm=llm,
        browser_session=browser_session,
        use_vision=True,
        vision_detail_level="low",
        llm_screenshot_size=(1280, 720),
        flash_mode=True,
        use_thinking=False,
        use_judge=False,
        enable_planning=False,
        final_response_after_failure=False,
        directly_open_url=True,
        max_history_items=6,
        max_actions_per_step=5,
        max_failures=2,
        llm_timeout=45,
        step_timeout=60,
        calculate_cost=False,
        extend_system_message=browser_guardrails(mission["risk"]),
    )
    try:
        if mission_id:
            _mission_event(db, mission_id, "browser_launch", f"Abriendo Brave con {model}")
        history = await agent.run(
            max_steps=int(settings.get("browser_max_steps", 18)),
            on_step_start=on_step_start,
            on_step_end=on_step_end,
        )
        final = history.final_result() or "La misión terminó sin texto final."
        return {
            "text": str(final),
            "successful": history.is_successful(),
            "steps": history.number_of_steps(),
            "urls": history.urls(),
            "errors": [x for x in history.errors() if x],
            "duration_seconds": history.total_duration_seconds(),
            "browser": "Brave",
            "model": model,
        }
    finally:
        try:
            await browser_session.kill()
        except Exception:
            pass


def execute(mission_id: str) -> dict:
    db = Database()
    settings = SettingsStore().get()
    mission = db.get_mission(mission_id)
    if not mission:
        raise RuntimeError("misión inexistente")
    if mission["status"] != "running":
        raise RuntimeError(f"la misión no está en ejecución: {mission['status']}")
    secret = SecretStore().get(mission["provider"])
    if not secret:
        raise RuntimeError(f"falta configurar la API key de {mission['provider']}")

    if mission["tool"] == "browser":
        result = asyncio.run(run_browser(mission, settings, secret))
        return {"ok": bool(result.get("successful") is not False), "result": json.dumps(result, ensure_ascii=False, indent=2)}

    provider = get_provider(mission["provider"], secret)
    response = provider.generate(
        mission["model"], mission["description"], system_prompt(mission["worker"], settings), history=[]
    )
    return {"ok": True, "result": response.text, "usage": response.usage}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission-id", required=True)
    args = parser.parse_args()
    try:
        result = execute(args.mission_id)
        print("TARS_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result.get("ok") else 1
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "trace": traceback.format_exc(limit=8)}
        print("TARS_RESULT=" + json.dumps(payload, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
