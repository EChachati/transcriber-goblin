"""Pipeline offline: transcribe + diariza un fichero de audio/vídeo existente.

Pensado para VODs (mp4/mkv) o WAVs ya grabados: extrae el audio con ffmpeg,
transcribe con faster-whisper, diariza con pyannote y cruza ambos resultados
para que cada segmento lleve la etiqueta del hablante (S1, S2…).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .diarize import Diarizer, assign_speakers
from .media import extract_audio_to_wav
from .session import _get_model
from .stt import Segment


@dataclass
class FileResult:
    out_dir: Path
    segments: list[Segment]
    transcript_json: Path
    audio_path: Path


def process_file(
    input_path: str | Path,
    out_dir: str | Path,
    *,
    session_name: str | None = None,
    model_size: str = "small",
    language: str | None = None,
    diarize: bool = True,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> FileResult:
    input_path = Path(input_path).expanduser()
    if not input_path.is_file():
        raise FileNotFoundError(f"No existe el fichero de entrada: {input_path}")
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    name = session_name or input_path.stem
    audio_path = out_dir / "audio.wav"
    t0 = time.time()
    print(f"[file] extrayendo audio de {input_path.name} -> {audio_path.name}")
    extract_audio_to_wav(input_path, audio_path)
    print(f"[file] audio extraído en {time.time() - t0:.1f}s")

    model = _get_model(model_size, language)
    print("[stt] transcribiendo...")
    t0 = time.time()
    segments = model.transcribe_file(
        str(audio_path), source="?", word_timestamps=diarize
    )
    segments.sort(key=lambda s: s.start)
    print(f"[stt] {len(segments)} segmentos en {time.time() - t0:.1f}s")

    if diarize and segments:
        print("[diar] diarizando voces (esto tarda un poco)...")
        t0 = time.time()
        turns = Diarizer().diarize(
            audio_path,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        speakers = sorted({t.label for t in turns})
        print(
            f"[diar] {len(turns)} turnos en {time.time() - t0:.1f}s, "
            f"hablantes: {', '.join(speakers) or '—'}"
        )
        segments = assign_speakers(segments, turns)
        segments.sort(key=lambda s: s.start)

    transcript_json = out_dir / "transcript.json"
    payload = {
        "session": name,
        "started_at": None,
        "source_file": str(input_path),
        "segments": [s.to_dict() for s in segments],
    }
    with open(transcript_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[file] transcripción guardada: {transcript_json}")
    return FileResult(out_dir, segments, transcript_json, audio_path)
