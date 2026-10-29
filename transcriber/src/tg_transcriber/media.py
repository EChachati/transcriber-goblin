"""Utilidades de audio: extracción con ffmpeg y mezcla de streams WAV.

La diarización funciona mejor sobre un único canal mono de 16 kHz (es lo que
esperan tanto faster-whisper como pyannote). En grabaciones en vivo tenemos dos
WAV (micrófono y escritorio) con la misma base de tiempo: ``mix_wavs`` los suma
en un solo fichero para poder diarizar todas las voces con una única pasada.
"""

from __future__ import annotations

import array
import shutil
import subprocess
import wave
from pathlib import Path

SAMPLE_RATE = 16000


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_audio_to_wav(
    input_path: str | Path,
    output_path: str | Path,
    *,
    sample_rate: int = SAMPLE_RATE,
) -> Path:
    """Extrae (y downmixea a mono) la pista de audio de un fichero cualquiera.

    Sirve para mp4/mkv/mp3/... Usa ffmpeg, así que funciona con streaming.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg falló al extraer audio de {input_path}:\n{result.stderr.strip()}"
        )
    return output_path


def mix_wavs(paths: list[str | Path], output_path: str | Path) -> Path:
    """Suma varios WAV mono del mismo sample rate en uno solo (16 bits).

    Los samples se saturan a ``[-32768, 32767]``. Los ficheros pueden tener
    distinta longitud: se rellena con silencio hasta el más largo.
    """
    wavs: list[tuple[wave.Wave_read, array.array]] = []
    try:
        for path in paths:
            w = wave.open(str(path), "rb")
            if w.getsampwidth() != 2:
                raise RuntimeError(f"{path}: se esperaba PCM de 16 bits")
            data = array.array("h")
            data.frombytes(w.readframes(w.getnframes()))
            wavs.append((w, data))
        if not wavs:
            raise ValueError("mix_wavs necesita al menos un fichero")
        rate = wavs[0][0].getframerate()
        for w, _ in wavs:
            if w.getframerate() != rate:
                raise RuntimeError("mix_wavs: sample rates distintos")

        total = max(len(data) for _, data in wavs)
        mixed = array.array("h", bytes(2 * total))
        for _, data in wavs:
            for i, sample in enumerate(data):
                value = mixed[i] + sample
                mixed[i] = max(-32768, min(32767, value))

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(mixed.tobytes())
        return output_path
    finally:
        for w, _ in wavs:
            w.close()


def wav_duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate() or SAMPLE_RATE)
