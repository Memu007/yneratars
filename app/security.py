from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "blocked": 3}
WORKERS = {"TARS", "CASE", "KIPP"}

BLOCKED_TERMS = (
    "comprar", "purchase", "pagar", "payment", "tarjeta", "credit card",
    "transferir dinero", "transfer money", "transferencia bancaria", "wire transfer",
    "cambiar contrasena", "cambia la contrasena", "change password", "desactivar seguridad", "disable security",
    "publicar campana", "publish ad campaign", "cuenta bancaria", "bank account",
    "borrar fuera", "delete outside", "vaciar papelera", "empty trash",
    "confirmar compra", "finalizar compra", "checkout", "hacer una transferencia",
)
HIGH_TERMS = (
    "enviar correo", "mandar correo", "manda un correo", "envia un correo", "send email",
    "publicar contenido", "publica contenido", "publish content",
    "subir archivo", "subi un archivo", "upload file", "instalar software", "install software",
    "confirmar formulario", "confirmar un formulario", "enviar formulario", "enviar un formulario", "manda el formulario", "submit form", "sudo ",
    "mensaje privado", "direct message", "cambiar configuracion de cuenta",
    "publicar post", "publicar comentario", "enviar mensaje",
)
MEDIUM_TERMS = (
    "editar archivo", "modify file", "completar formulario", "completar un formulario", "rellenar formulario", "rellenar un formulario", "fill form",
    "iniciar sesion", "login", "loguear", "acceder a una cuenta", "usar mi sesion",
    "modificar configuracion", "editar configuracion",
)


@dataclass(frozen=True)
class RiskAssessment:
    risk: str
    reason: str


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _term_is_asserted(text: str, term: str) -> bool:
    """Return True when at least one occurrence is not explicitly negated nearby."""
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return False
        prefix = text[max(0, index - 60):index]
        # Covers instructions such as "sin enviar formularios" or "no comprar".
        # Keep the window small so an earlier unrelated negation cannot mask an action.
        negated = re.search(
            r"(?:^|\b)(?:no|sin|nunca|evitar|evita|evite)\s+(?:(?:de|que|se)\s+)?(?:\w+\s+){0,2}$",
            prefix,
        )
        if not negated:
            return True
        start = index + max(1, len(term))


def infer_risk(title: str, description: str) -> RiskAssessment:
    text = _normalize(f"{title} {description}")
    for term in BLOCKED_TERMS:
        if _term_is_asserted(text, term):
            return RiskAssessment("blocked", f"acción bloqueada detectada: {term}")
    for term in HIGH_TERMS:
        if _term_is_asserted(text, term):
            return RiskAssessment("high", f"acción sensible detectada: {term}")
    for term in MEDIUM_TERMS:
        if _term_is_asserted(text, term):
            return RiskAssessment("medium", f"acción con cuenta o edición detectada: {term}")
    return RiskAssessment("low", "sin acciones sensibles detectadas")


def effective_risk(requested: str, inferred: str) -> str:
    if requested not in RISK_ORDER:
        raise ValueError("risk debe ser low, medium, high o blocked")
    return max((requested, inferred), key=RISK_ORDER.get)


def initial_status(risk: str) -> str:
    if risk == "blocked":
        return "blocked"
    if risk in {"medium", "high"}:
        return "awaiting_approval"
    return "queued"


def browser_guardrails(risk: str) -> str:
    base = (
        "REGLAS DE SEGURIDAD OBLIGATORIAS: no compres, no pagues, no transfieras dinero, "
        "no cambies contraseñas, no desactives seguridad y no borres datos. "
        "No inventes resultados. Si una página exige CAPTCHA, 2FA o información sensible, detenete y explicalo."
    )
    if risk == "low":
        return base + " No envíes formularios, mensajes, publicaciones ni archivos; frená antes de la confirmación final."
    if risk == "medium":
        return base + " Podés preparar y completar datos autorizados, pero no confirmar ni enviar nada."
    return base + " La misión fue aprobada por el usuario, pero las acciones bloqueadas siguen prohibidas."


def excluded_browser_actions(risk: str) -> list[str]:
    """Disable destructive actions while preserving the interactions needed to browse.

    A read-only web mission still needs click/input/select to follow links, search and
    filter public pages. Submission safety is enforced by the risk prompt and human
    approval; uploads, arbitrary JavaScript and filesystem writes remain hard-blocked.
    """
    blocked = {"evaluate", "write_file", "replace_file"}
    if risk == "low":
        blocked.update({"upload_file", "send_keys"})
    elif risk == "medium":
        blocked.add("upload_file")
    return sorted(blocked)
