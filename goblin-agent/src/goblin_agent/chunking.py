"""Troceo híbrido de la transcripción.

Estrategia (modo híbrido): se acumulan segmentos hasta acercarse a un tamaño
objetivo y, en lugar de cortar por tamaño exacto, se busca el **mayor hueco de
silencio** cerca de ese punto. Así los cortes caen en pausas naturales y no en
mitad de una conversación. Los tramos se solapan unos pocos segmentos.
"""

from __future__ import annotations

from dataclasses import dataclass

from .transcript import Segment


@dataclass
class Chunk:
    index: int
    start: float
    end: float
    text: str


def fmt_ts(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def render_segment(seg: Segment) -> str:
    return f"[{fmt_ts(seg.start)}] {seg.source}: {seg.text}"


def chunk_segments(
    segments: list[Segment],
    target_chars: int = 4000,
    overlap: int = 2,
    lookahead: int = 6,
) -> list[Chunk]:
    """Divide ``segments`` en tramos cortando por silencios cerca de ``target_chars``."""
    n = len(segments)
    if n == 0:
        return []

    rendered = [render_segment(seg) for seg in segments]
    chunks: list[Chunk] = []
    i = 0

    while i < n:
        chars = 0
        j = i
        while j < n and chars < target_chars:
            chars += len(rendered[j]) + 1
            j += 1

        if j >= n:
            end_idx = n - 1
        else:
            cut = j - 1  # último segmento que entra por tamaño
            lo = max(i, cut - lookahead)
            hi = min(n - 1, cut + lookahead)
            upper = min(hi, n - 1)
            best = cut
            best_gap = -1.0
            for k in range(lo, upper):  # cortar DESPUÉS de k
                gap = segments[k + 1].start - segments[k].end
                if gap > best_gap:
                    best_gap = gap
                    best = k
            end_idx = best

        chunks.append(
            Chunk(
                index=len(chunks),
                start=segments[i].start,
                end=segments[end_idx].end,
                text="\n".join(rendered[i : end_idx + 1]),
            )
        )

        if end_idx >= n - 1:
            break
        i = max(i + 1, end_idx + 1 - overlap)

    return chunks
