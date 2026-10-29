import json
import time
from pathlib import Path

from .capture import AudioStreamRecorder, resolve_targets
from .diarize import Diarizer, assign_speakers
from .media import mix_wavs
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
        diarize: bool = False,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ):
        self.name = name
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.model_size = model_size
        self.language = language
        self.diarize = diarize
        self.num_speakers = num_speakers
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        mic_target, monitor_target = resolve_targets()
        if mic_target is None and monitor_target is None:
            raise RuntimeError(
                "no se pudo resolver fuentes de audio (¿pactl/pipewire instalado?)"
            )
        self.mic_rec = (
            AudioStreamRecorder(mic_target, "TU", self.out_dir / "mic.wav")
            if mic_target
            else None
        )
        self.desk_rec = (
            AudioStreamRecorder(monitor_target, "REMOTO", self.out_dir / "desktop.wav")
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
            print(
                f"[stt] transcribiendo stream {rec.label} ({durations[rec.label]:.0f}s)..."
            )
            segments.extend(
                model.transcribe_file(
                    str(rec.wav_path), source=rec.label, word_timestamps=self.diarize
                )
            )
        segments.sort(key=lambda s: s.start)

        if self.diarize and segments:
            segments = self._apply_diarization(segments)

        transcript = {
            "session": self.name,
            "started_at": self.started_at,
            "durations_s": durations,
            "segments": [s.to_dict() for s in segments],
        }
        with open(self.out_dir / "transcript.json", "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        return segments

    def _apply_diarization(self, segments: list[Segment]) -> list[Segment]:
        """Diariza la mezcla de los streams y reetiqueta los segmentos S1..Sn."""
        wavs = [
            rec.wav_path
            for rec in (self.mic_rec, self.desk_rec)
            if rec is not None and rec.wav_path.exists() and rec.duration() > 0
        ]
        if not wavs:
            return segments
        if len(wavs) == 1:
            mixed = wavs[0]
        else:
            mixed = mix_wavs(wavs, self.out_dir / "mixed.wav")
            print(f"[diar] mezcla de {len(wavs)} streams -> {mixed.name}")
        print("[diar] diarizando voces (esto tarda un poco)...")
        diarizer = Diarizer()
        turns = diarizer.diarize(
            mixed,
            num_speakers=self.num_speakers,
            min_speakers=self.min_speakers,
            max_speakers=self.max_speakers,
        )
        speakers = sorted({t.label for t in turns})
        print(f"[diar] {len(turns)} turnos, hablantes: {', '.join(speakers) or '—'}")
        return assign_speakers(segments, turns)
