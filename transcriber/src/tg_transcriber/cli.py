import argparse
import datetime as dt
import os
import signal
import sys
import time
from pathlib import Path

from .capture import list_sources, resolve_targets
from .publish import publish_note, render_markdown, upload_attachment
from .session import Session

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


def cmd_record(args) -> int:
    token = args.token or os.environ.get("TG_TOKEN") or input("Token de usuario TG: ").strip()
    name = args.name or f"Sesion {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:40]
    out_dir = Path(args.out).expanduser() / f"{stamp}_{slug}" if args.out else DEFAULT_SESSIONS_DIR / f"{stamp}_{slug}"

    session = Session(
        name=name,
        out_dir=out_dir,
        model_size=args.model,
        language=args.language,
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
    if not args.no_upload_audio and not args.no_publish:
        for rec in (session.mic_rec, session.desk_rec):
            if rec is not None and rec.duration() > 0:
                try:
                    attachments[rec.label] = upload_attachment(
                        args.api_url, token, rec.wav_path, rec.wav_path.name
                    )
                except Exception as exc:
                    print(f"[pub] no se pudo subir audio {rec.label}: {exc!r}")

    markdown = render_markdown(name, session.started_at, segments, attachments)
    md_path = out_dir / "transcript.md"
    md_path.write_text(markdown, encoding="utf-8")
    print(f"[ok] transcript local: {md_path}")

    if not args.no_publish:
        doc_id = publish_note(args.api_url, token, name, markdown)
        print(f"[ok] nota publicada en el backend: doc={doc_id}")
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
    p_rec.add_argument("--no-publish", action="store_true")
    p_rec.add_argument("--no-upload-audio", action="store_true")
    p_rec.set_defaults(func=cmd_record)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
