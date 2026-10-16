"""Diarización de hablantes (¿quién habla cuándo?) con pyannote.audio.

Usa el pipeline ``pyannote/speaker-diarization-community-1`` (open source,
requiere token de HuggingFace con las condiciones aceptadas) y devuelve turnos
etiquetados como ``S1``, ``S2``… ordenados por primera aparición.

El objetivo es enriquecer la transcripción: cada segmento de faster-whisper se
asigna al hablante con el que solapa, de modo que ``goblin-agent`` pueda saber
que varias voces remotas son personas distintas.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_PIPELINE = "pyannote/speaker-diarization-community-1"
# Overlap mínimo (segundos) para considerar que un segmento pertenece a un turno.
MIN_OVERLAP_S = 0.10


@dataclass
class SpeakerTurn:
    start: float
    end: float
    label: str  # S1, S2, ...

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _huggingface_token(explicit: str | None = None) -> str | None:
    """Busca el token en: argumento, env, transcriber/.env o root/.env."""
    if explicit:
        return explicit.strip()
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        value = os.environ.get(key)
        if value:
            return value.strip().strip('"').strip("'")
    try:
        from dotenv import dotenv_values
    except ImportError:
        return None
    here = Path(__file__).resolve()
    candidates = [
        Path.cwd() / ".env",
        here.parent.parent.parent / ".env",  # transcriber/.env
        here.parent.parent.parent.parent / ".env",  # repo/.env
    ]
    for path in candidates:
        if not path.is_file():
            continue
        value = (
            (dotenv_values(path).get("HF_TOKEN") or "").strip().strip('"').strip("'")
        )
        if value:
            return value
    return None


def relabel_by_first_appearance(turns: Iterable[SpeakerTurn]) -> list[SpeakerTurn]:
    """Renombra los hablantes a S1..Sn en orden de primera aparición."""

    ordered = sorted(turns, key=lambda t: (t.start, t.end))
    mapping: dict[str, str] = {}
    result: list[SpeakerTurn] = []
    for turn in ordered:
        if turn.label not in mapping:
            mapping[turn.label] = f"S{len(mapping) + 1}"
        result.append(
            SpeakerTurn(start=turn.start, end=turn.end, label=mapping[turn.label])
        )
    return result


def merge_adjacent(
    turns: Sequence[SpeakerTurn], gap_s: float = 0.25
) -> list[SpeakerTurn]:
    """Une turnos consecutivos del mismo hablante separados por menos de ``gap_s``."""
    merged: list[SpeakerTurn] = []
    for turn in sorted(turns, key=lambda t: t.start):
        if (
            merged
            and merged[-1].label == turn.label
            and turn.start - merged[-1].end <= gap_s
        ):
            merged[-1] = SpeakerTurn(
                start=merged[-1].start,
                end=max(merged[-1].end, turn.end),
                label=turn.label,
            )
        else:
            merged.append(SpeakerTurn(turn.start, turn.end, turn.label))
    return merged


def drop_short_turns(
    turns: Sequence[SpeakerTurn], min_s: float = 0.20
) -> list[SpeakerTurn]:
    """Absorbe turnos muy cortos (falsos cambios de voz) en el turno anterior.

    Los turnos de menos de ``min_s`` segundos son casi siempre ruido del
    diarizador; se fusionan con el turno anterior para no crear microcambios.
    """
    cleaned: list[SpeakerTurn] = []
    for turn in sorted(turns, key=lambda t: t.start):
        if turn.duration < min_s and cleaned:
            cleaned[-1] = SpeakerTurn(
                start=cleaned[-1].start,
                end=max(cleaned[-1].end, turn.end),
                label=cleaned[-1].label,
            )
        else:
            cleaned.append(SpeakerTurn(turn.start, turn.end, turn.label))
    return cleaned


class Diarizer:
    """Envoltorio perezoso del pipeline de pyannote (carga el modelo una vez)."""

    def __init__(
        self,
        pipeline_name: str = DEFAULT_PIPELINE,
        token: str | None = None,
        device: str | None = None,
    ):
        self.pipeline_name = pipeline_name
        self.token = _huggingface_token(token)
        self.device = device or os.environ.get("TG_DIARIZE_DEVICE")
        self._pipeline = None

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        if not self.token:
            raise RuntimeError(
                "Falta HF_TOKEN para pyannote (acepta las condiciones de "
                f"{self.pipeline_name} en HuggingFace y añade HF_TOKEN al .env)"
            )
        print(f"[diar] cargando pipeline '{self.pipeline_name}'...")
        from pyannote.audio import Pipeline

        pipeline = Pipeline.from_pretrained(self.pipeline_name, token=self.token)
        device = self.device
        if device is None:
            device = self._pick_device()
        if device and device != "cpu":
            try:
                import torch

                pipeline.to(torch.device(device))
                print(f"[diar] pipeline en {device}")
            except Exception as exc:  # noqa: BLE001
                print(f"[diar] no se pudo usar {device} ({exc!r}); se queda en CPU")
        self._pipeline = pipeline
        return pipeline

    @staticmethod
    def _pick_device() -> str:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:  # noqa: BLE001
            pass
        return "cpu"

    def diarize(
        self,
        wav_path: str | Path,
        *,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        min_turn_s: float = 0.20,
        merge_gap_s: float = 0.25,
    ) -> list[SpeakerTurn]:
        """Diariza ``wav_path`` y devuelve turnos con etiquetas S1..Sn.

        Si no se fija ``num_speakers``, pyannote estima el número solo. Se usa la
        diarización *exclusiva* (sin solapes) para alinear mejor con el STT.
        """
        pipeline = self._load()
        kwargs: dict = {}
        if num_speakers:
            kwargs["num_speakers"] = int(num_speakers)
        else:
            if min_speakers:
                kwargs["min_speakers"] = int(min_speakers)
            if max_speakers:
                kwargs["max_speakers"] = int(max_speakers)
        output = pipeline(str(wav_path), **kwargs)
        annotation = getattr(output, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = output.speaker_diarization
        raw = [
            SpeakerTurn(start=float(seg.start), end=float(seg.end), label=str(label))
            for seg, _track, label in annotation.itertracks(yield_label=True)
        ]
        turns = drop_short_turns(raw, min_s=min_turn_s)
        turns = merge_adjacent(turns, gap_s=merge_gap_s)
        return relabel_by_first_appearance(turns)


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def nearest_turn(
    seg_start: float, seg_end: float, turns: Sequence[SpeakerTurn]
) -> SpeakerTurn | None:
    """Turno más cercano por solape; si ninguno solapa, el más próximo en tiempo."""
    if not turns:
        return None
    best: tuple[float, SpeakerTurn] | None = None
    for turn in turns:
        overlap = _overlap(seg_start, seg_end, turn.start, turn.end)
        if overlap > 0:
            score = overlap
        else:
            mid = (seg_start + seg_end) / 2
            distance = min(abs(mid - turn.start), abs(mid - turn.end))
            score = -distance
        if best is None or score > best[0]:
            best = (score, turn)
    return best[1] if best else None


def assign_speakers(segments: Sequence, turns: Sequence[SpeakerTurn]) -> list:
    """Copia de ``segments`` con ``source`` sustituido por el hablante asignado.

    Cada segmento se asigna al turno con mayor solape temporal. Si un segmento
    abarca varios turnos, se parte en subsegmentos para no mezclar voces.
    """
    if not turns:
        return list(segments)
    result: list = []
    for seg in sorted(segments, key=lambda s: s.start):
        pieces = split_segment(seg, turns)
        result.extend(pieces)
    return result


def split_segment(seg, turns: Sequence[SpeakerTurn]) -> list:
    """Divide un segmento en trozos por hablante (si abarca varios turnos).

    Si el segmento trae timestamps por palabra (``seg.words``), se usa una
    asignación exacta palabra→turno. Si no, se reparte el texto proporcional a
    la duración de cada trozo (aproximación suficiente para segmentos cortos).
    Devuelve al menos un segmento.
    """
    overlapping = [
        t
        for t in sorted(turns, key=lambda x: x.start)
        if _overlap(seg.start, seg.end, t.start, t.end) > MIN_OVERLAP_S
    ]
    if not overlapping:
        turn = nearest_turn(seg.start, seg.end, turns)
        return [replace(seg, source=turn.label)] if turn else [seg]

    # Recorta cada turno al rango del segmento y calcula su porción.
    spans: list[tuple[float, float, str]] = []
    for turn in overlapping:
        start = max(seg.start, turn.start)
        end = min(seg.end, turn.end)
        if end - start > MIN_OVERLAP_S:
            spans.append((start, end, turn.label))
    if not spans:
        turn = nearest_turn(seg.start, seg.end, turns)
        return [replace(seg, source=turn.label)] if turn else [seg]
    if len(spans) == 1:
        return [replace(seg, source=spans[0][2])]

    words = getattr(seg, "words", None)
    if words:
        return _split_by_words(seg, words, turns, spans)

    total = sum(end - start for start, end, _ in spans)
    tokens = seg.text.split()
    if len(tokens) <= 1 or total <= 0:
        return [replace(seg, source=spans[0][2])]

    pieces: list = []
    consumed = 0
    for index, (start, end, label) in enumerate(spans):
        if index == len(spans) - 1:
            chunk_words = tokens[consumed:]
        else:
            share = (end - start) / total
            count = max(1, round(len(tokens) * share))
            count = min(count, len(tokens) - consumed - (len(spans) - index - 1))
            chunk_words = tokens[consumed : consumed + count]
            consumed += count
        text = " ".join(chunk_words).strip()
        if not text:
            continue
        pieces.append(
            replace(seg, start=start, end=end, source=label, text=text, words=None)
        )
    return pieces or [replace(seg, source=spans[0][2])]


def _split_by_words(seg, words, turns: Sequence[SpeakerTurn], spans) -> list:
    """Reparte palabra a palabra usando sus timestamps reales."""
    groups: list[tuple[str, list]] = []
    for word in words:
        w_start, w_end, _text = word[0], word[1], word[2]
        turn = nearest_turn(w_start, w_end, turns)
        label = turn.label if turn else spans[0][2]
        if groups and groups[-1][0] == label:
            groups[-1][1].append(word)
        else:
            groups.append((label, [word]))
    pieces: list = []
    for label, group in groups:
        text = " ".join(w[2] for w in group).strip()
        if not text:
            continue
        pieces.append(
            replace(
                seg,
                start=group[0][0],
                end=group[-1][1],
                source=label,
                text=text,
                words=None,
            )
        )
    return pieces or [replace(seg, source=spans[0][2])]
