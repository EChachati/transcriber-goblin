import json
import time
from dataclasses import asdict
from pathlib import Path

from .capture import StreamRecorder, resolve_targets
from .stt import Segment, Transcriber

_MODEL_CACHE: dict = {}


def _get_model(model_size: str, language: str | None) -> Transcriber:
    key = (model_size, language)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = Transcriber(model_size, language)
    return _MODEL_CACHE[key]


class Session:
    def __init__(
        self,
        name: str,
        out_dir: Path,
        model_size: str = "small",
        language: str | None = None,
    ):
        self.name = name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.model_size = model_size
        self.language = language
        mic_target, monitor_target = resolve_targets()
        if mic_target is None and monitor_target is None:
            raise RuntimeError(
                "no se pudo resolver fuentes de audio (¿pactl/pipewire instalado?)"
            )
        self.mic_rec = (
            StreamRecorder(mic_target, "TU", self.out_dir / "mic.wav")
            if mic_target
            else None
        )
        self.desk_rec = (
            StreamRecorder(monitor_target, "REMOTO", self.out_dir / "desktop.wav")
            if monitor_target
            else None
        )
        self.started_at: float | None = None

    def start(self) -> None:
        self.started_at = time.time()
        for rec in (self.mic_rec, self.desk_rec):
            if rec is not None:
                rec.start()
                print(f"[rec] capturando {rec.label}: {rec.wav_path.name}")

    def stop_and_transcribe(self) -> list[Segment]:
        durations: dict[str, float] = {}
        for rec in (self.mic_rec, self.desk_rec):
            if rec is not None:
                durations[rec.label] = rec.stop()

        model = _get_model(self.model_size, self.language)
        segments: list[Segment] = []
        for rec in (self.mic_rec, self.desk_rec):
            if rec is None:
                continue
            if durations[rec.label] < 1.0:
                continue
            print(f"[stt] transcribiendo stream {rec.label} ({durations[rec.label]:.0f}s)...")
            segments.extend(
                model.transcribe_file(str(rec.wav_path), source=rec.label)
            )
        segments.sort(key=lambda s: s.start)

        transcript = {
            "session": self.name,
            "started_at": self.started_at,
            "durations_s": durations,
            "segments": [asdict(s) for s in segments],
        }
        with open(self.out_dir / "transcript.json", "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        return segments
