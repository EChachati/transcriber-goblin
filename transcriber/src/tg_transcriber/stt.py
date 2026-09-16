import ctypes
import glob
import os
import sysconfig
from dataclasses import dataclass


@dataclass
class Segment:
    start: float
    end: float
    source: str
    text: str


def _preload_cuda_libs() -> bool:
    purelib = sysconfig.get_paths()["purelib"]
    lib_dirs = glob.glob(os.path.join(purelib, "nvidia", "*", "lib"))
    if not lib_dirs:
        return False
    try:
        for directory in sorted(lib_dirs):
            for so in sorted(glob.glob(os.path.join(directory, "*.so*"))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _pick_device_config() -> tuple[str, str]:
    if _preload_cuda_libs():
        return "cuda", "int8_float16"
    return "cpu", "int8"


class Transcriber:
    def __init__(self, model_size: str = "small", language: str | None = None):
        self.model_size = model_size
        self.language = language
        device, compute_type = _pick_device_config()
        print(f"[stt] cargando modelo '{model_size}' en {device} ({compute_type})...")
        from faster_whisper import WhisperModel

        try:
            self.model = WhisperModel(
                model_size, device=device, compute_type=compute_type
            )
        except Exception as exc:
            print(f"[stt] GPU falló ({exc!r}); usando CPU")
            self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe_file(self, wav_path: str, source: str) -> list[Segment]:
        if not os.path.exists(wav_path):
            return []
        kwargs: dict = {
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 500},
            "condition_on_previous_text": False,
        }
        if self.language:
            kwargs["language"] = self.language
        segments, _info = self.model.transcribe(wav_path, **kwargs)
        return [
            Segment(start=seg.start, end=seg.end, source=source, text=seg.text.strip())
            for seg in segments
            if seg.text.strip()
        ]
