"""Compara el transcript generado por whisper-rs contra el oro de faster-whisper.

Métricas:
  - WER / CER sobre el texto concatenado y normalizado.
  - Drift de timestamps: cada segmento del oro se casa con el segmento rust de
    centro más cercano; se reporta drift mean/max de start.

Uso:
    python compare.py oro.json rust.json [--verbose]
Salida: JSON con {wer, cer, n_gold, n_rust, drift_start_mean_s, drift_start_max_s}.
"""
import argparse
import json
import re
import sys

try:
    from jiwer import cer, wer
except ImportError:
    sys.exit("falta jiwer: uv run --with jiwer python compare.py ...")

PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)
SPACE = re.compile(r"\s+")


def norm(text: str) -> str:
    return SPACE.sub(" ", PUNCT.sub(" ", text.lower())).strip()


def center(seg) -> float:
    return (seg["start"] + seg["end"]) / 2.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("oro")
    ap.add_argument("rust")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    with open(args.oro, encoding="utf-8") as f:
        gold = json.load(f)
    with open(args.rust, encoding="utf-8") as f:
        rust = json.load(f)

    gold_text = " ".join(s["text"] for s in gold)
    rust_text = " ".join(s["text"] for s in rust)
    w = wer(norm(gold_text), norm(rust_text))
    c = cer(norm(gold_text), norm(rust_text))

    drifts = []
    for g in gold:
        nearest = min(rust, key=lambda r: abs(center(r) - center(g)))
        drifts.append(nearest["start"] - g["start"])
    drift_mean = sum(drifts) / len(drifts) if drifts else 0.0
    drift_max = max((abs(d) for d in drifts), default=0.0)

    result = {
        "wer": round(w, 4),
        "cer": round(c, 4),
        "n_gold": len(gold),
        "n_rust": len(rust),
        "drift_start_mean_s": round(drift_mean, 3),
        "drift_start_max_s": round(drift_max, 3),
        "accept_wer": w <= 0.05,
        "accept_drift": abs(drift_mean) <= 0.5,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.verbose:
        print("\n-- oro --")
        for s in gold:
            print(f"  [{s['start']:7.2f} -> {s['end']:7.2f}] {s['text']}")
        print("\n-- rust --")
        for s in rust:
            print(f"  [{s['start']:7.2f} -> {s['end']:7.2f}] {s['text']}")


if __name__ == "__main__":
    main()