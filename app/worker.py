from __future__ import annotations

import argparse
import asyncio
import json
import sys
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


async def run_browser(mission: dict, settings: dict, api_key: str) -> dict:
    if not settings.get("browser_enabled", True):
        raise RuntimeError("el operador web está desactivado en Configuración")
    Agent, ChatGoogle, ChatOpenAI, Tools = _browser_classes()
    if mission["provider"] == "google":
        llm = ChatGoogle(model=mission["model"], api_key=api_key, temperature=0.1, max_retries=3)
    else:
        llm = ChatOpenAI(model=mission["model"], api_key=api_key, temperature=0.1, max_retries=3)

    task = f"""MISIÓN DEL USUARIO:
{mission['description']}

{browser_guardrails(mission['risk'])}
Al finalizar, resumí exactamente qué hiciste, qué páginas visitaste y qué quedó pendiente.
"""
    tools = Tools(exclude_actions=excluded_browser_actions(mission["risk"]))
    agent_kwargs = {
        "task": task,
        "tools": tools,
        "llm": llm,
        "use_vision": True,
        "max_actions_per_step": 3,
        "max_failures": 3,
        "calculate_cost": True,
        "extend_system_message": browser_guardrails(mission["risk"]),
    }
    browser_session = None
    allowed = settings.get("allowed_domains") or []
    if allowed:
        try:
            from browser_use.browser import BrowserProfile, BrowserSession
            browser_session = BrowserSession(
                browser_profile=BrowserProfile(
                    allowed_domains=allowed,
                    user_data_dir=str(ROOT / "data" / "browser-profile"),
                )
            )
            agent_kwargs["browser_session"] = browser_session
        except Exception as exc:
            raise RuntimeError(f"no se pudo configurar la lista de dominios permitidos: {exc}") from exc

    agent = Agent(**agent_kwargs)
    try:
        history = await agent.run(max_steps=int(settings.get("browser_max_steps", 18)))
        final = history.final_result() or "La misión terminó sin texto final."
        return {
            "text": str(final),
            "successful": history.is_successful(),
            "steps": history.number_of_steps(),
            "urls": history.urls(),
            "errors": [x for x in history.errors() if x],
            "duration_seconds": history.total_duration_seconds(),
        }
    finally:
        if browser_session is not None:
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
