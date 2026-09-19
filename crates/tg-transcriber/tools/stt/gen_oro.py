"""Genera el transcript de referencia ("oro") con faster-whisper, el motor actual.

Uso (desde tools/stt):
    uv run --python 3.12 --with "faster-whisper>=1.1" \
        python gen_oro.py --model small [--language es] [--device cpu] \
        corpus/jfk.wav corpus/jfk.oro.json
"""
import argparse
import json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("out_json")
    ap.add_argument("--model", default="small")
    ap.add_argument("--language", default=None)
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    args = ap.parse_args()

    print(f"[oro] cargando faster-whisper '{args.model}' en {args.device}...", flush=True)
    # Pre-carga de libs CUDA para que el import de ctranslate2 encuentre los .so
    if args.device == "cuda":
        import ctypes
        import glob
        import os
        import sysconfig

        for d in glob.glob(os.path.join(sysconfig.get_paths()["purelib"], "nvidia", "*", "lib")):
            for so in glob.glob(os.path.join(d, "*.so*")):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device=args.device, compute_type="int8_float16" if args.device == "cuda" else "int8")
    kwargs: dict = {
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
        "condition_on_previous_text": False,
    }
    if args.language:
        kwargs["language"] = args.language

    segments, info = model.transcribe(args.wav, **kwargs)
    out = [
        {"start": round_s(s.start), "end": round_s(s.end), "text": s.text.strip()}
        for s in segments
        if s.text.strip()
    ]
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[oro] language={info.language} prob={info.language_probability:.3f} segments={len(out)} -> {args.out_json}")


def round_s(x: float) -> float:
    return round(float(x), 3)


if __name__ == "__main__":
    main()