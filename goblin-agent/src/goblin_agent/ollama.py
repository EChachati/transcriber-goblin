"""Cliente mínimo para la API HTTP de Ollama."""

from __future__ import annotations

import httpx


class OllamaError(RuntimeError):
    """Error de comunicación o de respuesta con Ollama."""


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        num_ctx: int = 8192,
        num_predict: int = 3072,
        temperature: float = 0.3,
        timeout: float = 900.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.temperature = temperature
        self.timeout = timeout

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
                "temperature": self.temperature,
            },
        }
        if system:
            payload["system"] = system
        try:
            response = httpx.post(
                f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"No se pudo hablar con Ollama en {self.base_url}: {exc}"
            ) from exc
        return response.json().get("response", "").strip()

    def available_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=15)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"No se pudo consultar Ollama en {self.base_url}: {exc}"
            ) from exc
        return [m.get("name", "") for m in response.json().get("models", [])]
