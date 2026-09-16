import shutil
import subprocess
import time
import wave
from pathlib import Path

SAMPLE_RATE = 16000


def _pactl(*args: str) -> str:
    result = subprocess.run(
        ["pactl", *args], capture_output=True, text=True
    )
    return result.stdout.strip()


def resolve_targets() -> tuple[str | None, str | None]:
    try:
        subprocess.run(["pactl", "info"], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None, None
    sink = _pactl("get-default-sink").splitlines()[-1].strip()
    source = _pactl("get-default-source")
    monitor = f"{sink}.monitor" if sink else None
    return (source or None), monitor


def list_sources() -> list[str]:
    return [
        line.split("\t")[1]
        for line in _pactl("list", "short", "sources").splitlines()
        if line.strip()
    ]


class StreamRecorder:
    def __init__(self, target: str, label: str, wav_path: str | Path):
        self.target = target
        self.label = label
        self.wav_path = Path(wav_path)
        self.raw_path = self.wav_path.with_suffix(".raw")
        self.proc: subprocess.Popen | None = None
        self.t0: float | None = None
        self.error: str | None = None

    def start(self) -> None:
        self.t0 = time.time()
        self._stdout = open(self.raw_path, "wb")
        self.proc = subprocess.Popen(
            [
                "parec",
                "-d",
                self.target,
                "--rate=16000",
                "--channels=1",
                "--format=s16le",
                "--latency-msec=100",
            ],
            stdout=self._stdout,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> float:
        if self.proc is None:
            return 0.0
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self._stdout.close()
        duration = self._wrap_wav()
        self.raw_path.unlink(missing_ok=True)
        return duration

    def duration(self) -> float:
        if self.raw_path.exists():
            return max(self.raw_path.stat().st_size, 0) / (SAMPLE_RATE * 2)
        if self.wav_path.exists():
            data = max(self.wav_path.stat().st_size - 44, 0)
            return data / (SAMPLE_RATE * 2)
        return 0.0

    def _wrap_wav(self) -> float:
        if not self.raw_path.exists():
            return 0.0
        with (
            open(self.raw_path, "rb") as raw,
            wave.open(str(self.wav_path), "wb") as wf,
        ):
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            while chunk := raw.read(65536):
                wf.writeframes(chunk)
        return self.duration()
