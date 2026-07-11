from __future__ import annotations

import json
import os
import ssl
import time
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


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderResponse:
    text: str
    raw: dict[str, Any]
    usage: dict[str, Any] | None = None


def _request_json(url: str, method: str = "GET", headers: dict[str, str] | None = None,
                  payload: dict[str, Any] | None = None, timeout: int = 75,
                  retries: int = 2) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    ssl_context = ssl.create_default_context(cafile=_CA_BUNDLE)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, data=data, method=method, headers=request_headers)
            with urlopen(req, timeout=timeout, context=ssl_context) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = body[:2000] or str(exc)
            last_error = ProviderError(f"HTTP {exc.code}: {message}")
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                raise last_error
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = ProviderError(str(exc))
            if attempt >= retries:
                raise last_error
        time.sleep(0.8 * (2 ** attempt))
    raise ProviderError(str(last_error or "error de red"))


class GoogleProvider:
    name = "google"

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderError("falta GOOGLE_API_KEY")
        self.api_key = api_key

    def list_models(self) -> list[str]:
        data = _request_json(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": self.api_key}, timeout=30,
        )
        names: list[str] = []
        for item in data.get("models", []):
            methods = item.get("supportedGenerationMethods", [])
            if "generateContent" in methods or "generateContent" in str(methods):
                name = str(item.get("name", "")).removeprefix("models/")
                if name:
                    names.append(name)
        return sorted(set(names))

    def generate(self, model: str, prompt: str, system: str, history: list[dict[str, Any]] | None = None) -> ProviderResponse:
        if not model:
            raise ProviderError("falta el modelo de Gemini")
        contents: list[dict[str, Any]] = []
        for msg in (history or [])[-12:]:
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(msg.get("content", ""))}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent"
        data = _request_json(
            url, method="POST", headers={"x-goog-api-key": self.api_key},
            payload={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 4096},
            }, timeout=90,
        )
        pieces: list[str] = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if isinstance(part, dict) and part.get("text"):
                    pieces.append(str(part["text"]))
        text = "\n".join(pieces).strip()
        if not text:
            feedback = data.get("promptFeedback") or data
            raise ProviderError(f"Gemini no devolvió texto: {str(feedback)[:1000]}")
        return ProviderResponse(text=text, raw=data, usage=data.get("usageMetadata"))


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderError("falta OPENAI_API_KEY")
        self.api_key = api_key

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def list_models(self) -> list[str]:
        data = _request_json("https://api.openai.com/v1/models", headers=self.headers, timeout=30)
        ids = [str(x.get("id")) for x in data.get("data", []) if x.get("id")]
        useful = [x for x in ids if x.startswith(("gpt-", "o1", "o3", "o4", "computer-use"))]
        return sorted(set(useful or ids))

    def generate(self, model: str, prompt: str, system: str, history: list[dict[str, Any]] | None = None) -> ProviderResponse:
        if not model:
            raise ProviderError("falta el modelo de OpenAI")
        transcript: list[dict[str, str]] = []
        for msg in (history or [])[-12:]:
            transcript.append({"role": str(msg.get("role", "user")), "content": str(msg.get("content", ""))})
        transcript.append({"role": "user", "content": prompt})
        data = _request_json(
            "https://api.openai.com/v1/responses", method="POST", headers=self.headers,
            payload={"model": model, "instructions": system, "input": transcript, "max_output_tokens": 4096},
            timeout=90,
        )
        text = str(data.get("output_text") or "").strip()
        if not text:
            parts: list[str] = []
            for item in data.get("output", []):
                for content in item.get("content", []) if isinstance(item, dict) else []:
                    if isinstance(content, dict) and content.get("text"):
                        value = str(content["text"])
                        if not parts or parts[-1] != value:
                            parts.append(value)
            text = "\n".join(parts).strip()
        if not text:
            raise ProviderError(f"OpenAI no devolvió texto: {str(data)[:1000]}")
        return ProviderResponse(text=text, raw=data, usage=data.get("usage"))


def get_provider(name: str, api_key: str):
    if name == "google":
        return GoogleProvider(api_key)
    if name == "openai":
        return OpenAIProvider(api_key)
    raise ProviderError("proveedor no soportado")
