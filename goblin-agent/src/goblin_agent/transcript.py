"""Lectura de transcripciones.

Formatos soportados:

* ``transcript.json`` — el que genera ``tg-transcriber`` (campos ``session``,
  ``started_at`` y ``segments`` con ``start``/``end``/``source``/``text``).
* ``transcript.md`` — markdown con líneas ``**[mm:ss] SOURCE:** texto``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass
class Segment:
    start: float
    end: float
    source: str
    text: str


@dataclass
class Transcript:
    session: str
    started_at: float | None
    segments: list[Segment]


_MD_LINE = re.compile(r"^\*\*\[(\d{1,2}):(\d{2})\]\s*([^:]+):\*\*\s*(.*)$")


def load_transcript(path: str | Path) -> Transcript:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Transcripción no encontrada: {p}")
    if p.suffix.lower() == ".json":
        return _load_json(p)
    return _load_markdown(p)


def drop_leading_minutes(transcript: Transcript, minutes: float) -> Transcript:
    """Devuelve una copia sin los segmentos que terminan antes de ``minutes``.

    Sirve para saltar la cháchara previa a la partida (setup del directo, charla
    de mesa) que aparece al principio de muchas grabaciones.
    """
    if not minutes or minutes <= 0:
        return transcript
    cutoff = float(minutes) * 60.0
    return replace(
        transcript,
        segments=[seg for seg in transcript.segments if seg.end > cutoff],
    )


def drop_trailing_minutes(transcript: Transcript, minutes: float) -> Transcript:
    """Copia sin los segmentos que empiezan en los últimos ``minutes`` minutos.

    Para el cierre del directo (despedidas, agradecimientos, charla post-partida)
    que hay al final de muchas grabaciones.
    """
    if not minutes or minutes <= 0 or not transcript.segments:
        return transcript
    duration = max(seg.end for seg in transcript.segments)
    cutoff = duration - float(minutes) * 60.0
    return replace(
        transcript,
        segments=[seg for seg in transcript.segments if seg.start < cutoff],
    )


def drop_minutes_range(
    transcript: Transcript, start_minutes: float, end_minutes: float
) -> Transcript:
    """Copia sin los segmentos contenidos en el rango ``[inicio, fin]`` (minutos).

    Para pausas y charla fuera de juego en mitad de la grabación. Los segmentos
    que tocan los bordes se conservan para no cortar la partida.
    """
    start = float(start_minutes) * 60.0
    end = float(end_minutes) * 60.0
    if end <= start:
        return transcript
    return replace(
        transcript,
        segments=[
            seg
            for seg in transcript.segments
            if not (seg.start >= start and seg.end <= end)
        ],
    )


def parse_minutes(value: str | float) -> float:
    """«90», «94.5», «94:50» (mm:ss) o «1:34:50» (h:mm:ss) → minutos."""
    text = str(value).strip()
    if ":" in text:
        seconds = 0.0
        for part in text.split(":"):
            seconds = seconds * 60.0 + float(part)
        return seconds / 60.0
    return float(text)


def _load_json(p: Path) -> Transcript:
    data = json.loads(p.read_text(encoding="utf-8"))
    segments = [
        Segment(
            start=float(s.get("start", 0.0)),
            end=float(s.get("end", s.get("start", 0.0))),
            source=str(s.get("source", "?")),
            text=str(s.get("text", "")).strip(),
        )
        for s in data.get("segments", [])
        if str(s.get("text", "")).strip()
    ]
    return Transcript(
        session=str(data.get("session", p.stem)),
        started_at=data.get("started_at"),
        segments=segments,
    )


def _load_markdown(p: Path) -> Transcript:
    segments: list[Segment] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        m = _MD_LINE.match(raw.strip())
        if not m:
            continue
        mm, ss, source, text = m.groups()
        seconds = int(mm) * 60 + int(ss)
        segments.append(
            Segment(start=seconds, end=seconds, source=source.strip(), text=text.strip())
        )
    return Transcript(session=p.stem, started_at=None, segments=segments)
