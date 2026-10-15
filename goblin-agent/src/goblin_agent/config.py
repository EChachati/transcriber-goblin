"""Configuración del goblin-agent (variables de entorno + flags de CLI)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def load_env() -> None:
    """Carga el ``.env`` del directorio actual y el del paquete (goblin-agent/.env)."""
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def _path(key: str, default: str) -> Path:
    return Path(os.environ.get(key, default)).expanduser()


def _int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


def _float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw else default


@dataclass
class Config:
    """Parámetros de ejecución del agente."""

    # Rutas
    source_vault: Path  # vault original (SOLO LECTURA)
    work_vault: Path  # copia de trabajo (aquí se escribe)
    campaign_name: str

    # Ollama
    ollama_url: str
    model: str
    num_ctx: int
    num_predict: int
    temperature: float

    # Idiomas / resumen
    language: str

    # Troceo
    chunk_chars: int
    chunk_overlap: int
    lookahead: int

    # Referencias del vault
    max_ref_notes: int
    max_ref_chars: int
    max_ref_total: int

    # Generación del cuerpo por lotes (cada lote -> una sección de la nota).
    # Es el tope de caracteres de notas que se mandan en una sola llamada, para
    # no desbordar el contexto del modelo y no perder contenido.
    body_batch_chars: int

    @classmethod
    def from_env(cls) -> "Config":
        load_env()
        return cls(
            source_vault=_path("TG_GOBLIN_SOURCE_VAULT", "~/Documents/CurseOfStrahd"),
            work_vault=_path(
                "TG_GOBLIN_WORK_VAULT",
                "~/Documents/TranscriberGoblin/CurseOfStrahd-Goblin",
            ),
            campaign_name=os.environ.get("TG_GOBLIN_CAMPAIGN", "Curse of Strahd"),
            ollama_url=os.environ.get("TG_GOBLIN_OLLAMA_URL", "http://localhost:11434"),
            model=os.environ.get("TG_GOBLIN_MODEL", "qwen2.5:7b"),
            num_ctx=_int("TG_GOBLIN_NUM_CTX", 8192),
            num_predict=_int("TG_GOBLIN_NUM_PREDICT", 3072),
            temperature=_float("TG_GOBLIN_TEMPERATURE", 0.3),
            language=os.environ.get("TG_GOBLIN_LANGUAGE", "es"),
            chunk_chars=_int("TG_GOBLIN_CHUNK_CHARS", 4000),
            chunk_overlap=_int("TG_GOBLIN_CHUNK_OVERLAP", 2),
            lookahead=_int("TG_GOBLIN_LOOKAHEAD", 6),
            max_ref_notes=_int("TG_GOBLIN_MAX_REF_NOTES", 6),
            max_ref_chars=_int("TG_GOBLIN_MAX_REF_CHARS", 1200),
            max_ref_total=_int("TG_GOBLIN_MAX_REF_TOTAL", 5000),
            body_batch_chars=_int("TG_GOBLIN_BODY_BATCH_CHARS", 6000),
        )
