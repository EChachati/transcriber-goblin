#!/usr/bin/env bash
# === Fase 0 STT: correr ambos motores sobre un WAV y comparar ===
# uso: ./run.sh <audio.wav> <nombre> [language] [model-ruta]
#     ./run.sh /tmp/opencode/tg-stt/es_reunion16k.wav es-reunion es
set -euo pipefail

STT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$STT_DIR/../../../.." && pwd)"
WAV="$1"
NAME="$2"
LANG="${3:-}"
MODEL="${4:-$REPO/crates/tg-transcriber/models/ggml-small-q5_1.bin}"

mkdir -p "$STT_DIR/corpus/$NAME"

echo "== oro (faster-whisper, cpu) =="
(cd "$STT_DIR" && uv run --python 3.12 --with "faster-whisper>=1.1" \
    python gen_oro.py --model small --language "$LANG" --device cpu \
    "$WAV" "corpus/$NAME.oro.json")

echo "== rust (whisper-rs, cpu) =="
LANG_ARGS=(); [ -n "$LANG" ] && LANG_ARGS=(--language "$LANG")
"$REPO/target/debug/tg-transcriber" --model "$MODEL" "${LANG_ARGS[@]}" --threads 4 \
    "$WAV" > "$STT_DIR/corpus/$NAME.rust.json"

echo "== comparación =="
(cd "$STT_DIR" && uv run --python 3.12 --with jiwer \
    python compare.py --verbose "corpus/$NAME.oro.json" "corpus/$NAME.rust.json")