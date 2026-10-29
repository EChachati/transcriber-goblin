import argparse
import datetime as dt
import os
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from .capture import list_sources, resolve_targets
from .file_job import process_file
from .publish import publish_note, render_markdown, upload_attachment, write_to_obsidian
from .session import Session

# Cargar .env si existe
load_dotenv(Path.cwd() / ".env")
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

DEFAULT_API_URL = os.environ.get("TG_API_URL", "http://localhost:8000")
DEFAULT_SESSIONS_DIR = Path(
    os.environ.get("TG_SESSIONS_DIR", "~/.local/share/tg-transcriber/sessions")
).expanduser()


def cmd_devices(_args) -> int:
    mic, monitor = resolve_targets()
    print("Fuentes PipeWire (pactl):")
    for name in list_sources():
        tags = []
        if name == mic:
            tags.append("MIC (TU)")
        if name == monitor:
            tags.append("MONITOR (REMOTO)")
        suffix = f"  <-- {'+'.join(tags)}" if tags else ""
        print(f"  {name}{suffix}")
    return 0


def _speaker_args(parser) -> None:
    parser.add_argument(
        "--diarize",
        action="store_true",
        help="separa los hablantes (S1, S2...) con pyannote (requiere HF_TOKEN)",
    )
    parser.add_argument(
        "--speakers", type=int, default=None, help="nº exacto de hablantes"
    )
    parser.add_argument(
        "--min-speakers", type=int, default=None, help="mínimo de hablantes"
    )
    parser.add_argument(
        "--max-speakers", type=int, default=None, help="máximo de hablantes"
    )


def _emit_outputs(args, name, markdown, out_dir) -> None:
    """Escribe transcript.md, publica en el backend y/o escribe en Obsidian."""
    md_path = out_dir / "transcript.md"
    md_path.write_text(markdown, encoding="utf-8")
    print(f"[ok] transcript local: {md_path}")

    if getattr(args, "publish", False) and getattr(args, "_token", None):
        doc_id = publish_note(args.api_url, args._token, name, markdown)
        print(f"[ok] nota publicada en el backend: doc={doc_id}")

    obs_vault = args.obsidian_vault or os.environ.get("TG_OBSIDIAN_VAULT")
    if obs_vault:
        try:
            obs_path = write_to_obsidian(
                vault_path=obs_vault,
                markdown=markdown,
                title=name,
                folder=args.obsidian_folder or os.environ.get("TG_OBSIDIAN_FOLDER"),
                overwrite=not args.obsidian_no_overwrite,
            )
            print(f"[ok] escrito en Obsidian: {obs_path}")
        except Exception as exc:
            print(f"[warn] no se pudo escribir en Obsidian: {exc!r}")


def cmd_record(args) -> int:
    token = None
    if args.publish:
        token = (
            args.token
            or os.environ.get("TG_TOKEN")
            or input("Token de usuario TG: ").strip()
        )
    args._token = token
    name = args.name or f"Sesion {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:40]
    out_dir = (
        Path(args.out).expanduser() / f"{stamp}_{slug}"
        if args.out
        else DEFAULT_SESSIONS_DIR / f"{stamp}_{slug}"
    )

    session = Session(
        name=name,
        out_dir=out_dir,
        model_size=args.model,
        language=args.language,
        diarize=args.diarize,
        num_speakers=args.speakers,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
    )
    session.start()
    print(f"[rec] grabando '{name}' — Ctrl+C para terminar y transcribir")
    stop = {"requested": False}

    def _request_stop(signum, frame):
        stop["requested"] = True

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    try:
        while not stop["requested"]:
            time.sleep(0.5)
            durations = []
            for rec in (session.mic_rec, session.desk_rec):
                if rec is not None:
                    durations.append(f"{rec.label}:{rec.duration():.0f}s")
            sys.stdout.write(f"\r[rec] {' '.join(durations):<40} ")
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    print("\n[rec] deteniendo...")

    segments = session.stop_and_transcribe()
    print(f"[stt] {len(segments)} segmentos")

    attachments: dict[str, str] = {}
    if args.publish and not args.no_upload_audio and token:
        for rec in (session.mic_rec, session.desk_rec):
            if rec is not None and rec.duration() > 0:
                try:
                    attachments[rec.label] = upload_attachment(
                        args.api_url, token, rec.wav_path, rec.wav_path.name
                    )
                except Exception as exc:
                    print(f"[pub] no se pudo subir audio {rec.label}: {exc!r}")

    markdown = render_markdown(name, session.started_at, segments, attachments)
    _emit_outputs(args, name, markdown, out_dir)
    return 0


def cmd_file(args) -> int:
    token = None
    if args.publish:
        token = (
            args.token
            or os.environ.get("TG_TOKEN")
            or input("Token de usuario TG: ").strip()
        )
    args._token = token

    input_path = Path(args.input).expanduser()
    name = args.name or input_path.stem
    if args.out:
        out_dir = Path(args.out).expanduser()
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:40]
        out_dir = DEFAULT_SESSIONS_DIR / f"{stamp}_{slug}"

    result = process_file(
        input_path,
        out_dir,
        session_name=name,
        model_size=args.model,
        language=args.language,
        diarize=args.diarize,
        num_speakers=args.speakers,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
    )

    attachments: dict[str, str] = {}
    if args.publish and not args.no_upload_audio and token:
        try:
            attachments["audio"] = upload_attachment(
                args.api_url, token, result.audio_path, result.audio_path.name
            )
        except Exception as exc:
            print(f"[pub] no se pudo subir audio: {exc!r}")

    date = (
        input_path.stat().st_mtime
        if input_path.exists()
        else dt.datetime.now().timestamp()
    )
    markdown = render_markdown(name, date, result.segments, attachments)
    _emit_outputs(args, name, markdown, out_dir)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tg-transcriber")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    sub = parser.add_subparsers(dest="command", required=True)

    p_dev = sub.add_parser("devices", help="lista dispositivos de entrada")
    p_dev.set_defaults(func=cmd_devices)

    p_rec = sub.add_parser("record", help="graba micro+escritorio y publica la nota")
    p_rec.add_argument("--name", help="título de la sesión/nota")
    p_rec.add_argument("--model", default="small", help="faster-whisper model size")
    p_rec.add_argument("--language", default=None, help="código ISO (es/en/...) o auto")
    p_rec.add_argument("--token", default=None, help="token de usuario del backend")
    p_rec.add_argument("--out", default=None, help="directorio base de sesiones")
    p_rec.add_argument(
        "--publish", action="store_true", help="publicar nota en el backend/API"
    )
    p_rec.add_argument(
        "--no-upload-audio",
        action="store_true",
        help="no subir audios (solo con --publish)",
    )
    _speaker_args(p_rec)
    p_rec.add_argument(
        "--obsidian-vault",
        default=None,
        help="ruta al vault de Obsidian (TG_OBSIDIAN_VAULT)",
    )
    p_rec.add_argument(
        "--obsidian-folder",
        default=None,
        help="carpeta dentro del vault (TG_OBSIDIAN_FOLDER)",
    )
    p_rec.add_argument(
        "--obsidian-no-overwrite",
        action="store_true",
        help="no sobrescribir si el archivo ya existe",
    )
    p_rec.set_defaults(func=cmd_record)

    p_file = sub.add_parser(
        "file", help="transcribe (y diariza) un fichero de audio/vídeo existente"
    )
    p_file.add_argument("input", help="ruta al mp4/mkv/wav/... de entrada")
    p_file.add_argument("--name", help="título de la sesión/nota")
    p_file.add_argument("--model", default="small", help="faster-whisper model size")
    p_file.add_argument(
        "--language", default=None, help="código ISO (es/en/...) o auto"
    )
    p_file.add_argument("--token", default=None, help="token de usuario del backend")
    p_file.add_argument("--out", default=None, help="directorio de salida")
    p_file.add_argument(
        "--publish", action="store_true", help="publicar nota en el backend/API"
    )
    p_file.add_argument(
        "--no-upload-audio", action="store_true", help="no subir el audio"
    )
    p_file.add_argument(
        "--no-diarize",
        dest="diarize",
        action="store_false",
        help="desactivar la diarización",
    )
    p_file.add_argument(
        "--diarize",
        dest="diarize",
        action="store_true",
        help="separar hablantes S1, S2...",
    )
    p_file.set_defaults(diarize=True)
    # --speakers/--min-speakers/--max-speakers sin flag duplicado --diarize
    p_file.add_argument(
        "--speakers", type=int, default=None, help="nº exacto de hablantes"
    )
    p_file.add_argument(
        "--min-speakers", type=int, default=None, help="mínimo de hablantes"
    )
    p_file.add_argument(
        "--max-speakers", type=int, default=None, help="máximo de hablantes"
    )
    p_file.add_argument(
        "--obsidian-vault",
        default=None,
        help="ruta al vault de Obsidian (TG_OBSIDIAN_VAULT)",
    )
    p_file.add_argument(
        "--obsidian-folder",
        default=None,
        help="carpeta dentro del vault (TG_OBSIDIAN_FOLDER)",
    )
    p_file.add_argument(
        "--obsidian-no-overwrite",
        action="store_true",
        help="no sobrescribir si el archivo ya existe",
    )
    p_file.set_defaults(func=cmd_file)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
