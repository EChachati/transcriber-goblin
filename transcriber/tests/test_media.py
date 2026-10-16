"""Tests de utilidades de audio (media.py): mezcla de WAVs y duración."""

from __future__ import annotations

import wave

from tg_transcriber.media import mix_wavs, wav_duration


def _write_wav(path, samples, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(int(s).to_bytes(2, "little", signed=True) for s in samples))
    return path


def _read_wav(path):
    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
    import array

    a = array.array("h")
    a.frombytes(frames)
    return list(a)


def test_mix_wavs_sums(tmp_path):
    a = _write_wav(tmp_path / "a.wav", [100, 200, 300])
    b = _write_wav(tmp_path / "b.wav", [1, 2, 3])
    out = mix_wavs([a, b], tmp_path / "mix.wav")
    assert _read_wav(out) == [101, 202, 303]


def test_mix_wavs_pads_short(tmp_path):
    a = _write_wav(tmp_path / "a.wav", [1000, 1000, 1000, 1000])
    b = _write_wav(tmp_path / "b.wav", [10, 10])
    out = mix_wavs([a, b], tmp_path / "mix.wav")
    assert _read_wav(out) == [1010, 1010, 1000, 1000]


def test_mix_wavs_saturates(tmp_path):
    a = _write_wav(tmp_path / "a.wav", [32000])
    b = _write_wav(tmp_path / "b.wav", [32000])
    out = mix_wavs([a, b], tmp_path / "mix.wav")
    assert _read_wav(out) == [32767]


def test_mix_wavs_single(tmp_path):
    a = _write_wav(tmp_path / "a.wav", [5, 6, 7])
    out = mix_wavs([a], tmp_path / "mix.wav")
    assert _read_wav(out) == [5, 6, 7]


def test_wav_duration(tmp_path):
    a = _write_wav(tmp_path / "a.wav", [0] * 16000)
    assert abs(wav_duration(a) - 1.0) < 1e-6
