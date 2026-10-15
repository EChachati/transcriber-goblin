"""Interfaz de línea de comandos de goblin-agent."""

from __future__ import annotations

import argparse
from dataclasses import replace

from .config import Config
from .ollama import OllamaClient, OllamaError
from .summarize import run
from .transcript import parse_minutes
from .vault import VaultIndex, refresh_work_vault


def _parse_ranges(values) -> list[tuple[float, float]]:
    """«76.7-94.8» / «76:40-94:50» → [(76.7, 94.8)]."""
    ranges: list[tuple[float, float]] = []
    for raw in values or ():
        try:
            start_raw, end_raw = str(raw).split("-", 1)
            start, end = parse_minutes(start_raw), parse_minutes(end_raw)
        except (ValueError, TypeError) as exc:
            raise SystemExit(
                f"--skip-range inválido: {raw!r} (usa INICIO-FIN, p. ej. 76.7-94.8 "
                "o 76:40-94:50)"
            ) from exc
        if end <= start:
            raise SystemExit(f"--skip-range inválido: {raw!r} (el fin debe ser > inicio)")
        ranges.append((start, end))
    return ranges


def _apply_overrides(cfg: Config, args) -> Config:
    overrides = {}
    if getattr(args, "source_vault", None):
        from pathlib import Path

        overrides["source_vault"] = Path(args.source_vault).expanduser()
    if getattr(args, "work_vault", None):
        from pathlib import Path

        overrides["work_vault"] = Path(args.work_vault).expanduser()
    if getattr(args, "campaign", None):
        overrides["campaign_name"] = args.campaign
    if getattr(args, "model", None):
        overrides["model"] = args.model
    if getattr(args, "ollama_url", None):
        overrides["ollama_url"] = args.ollama_url
    if getattr(args, "language", None):
        overrides["language"] = args.language
    return replace(cfg, **overrides) if overrides else cfg


def cmd_info(args) -> int:
    cfg = _apply_overrides(Config.from_env(), args)
    print(f"Vault origen  : {cfg.source_vault}")
    print(f"Vault trabajo : {cfg.work_vault}")
    print(f"Campaña       : {cfg.campaign_name}")
    print(f"Modelo        : {cfg.model} @ {cfg.ollama_url} (num_ctx={cfg.num_ctx})")
    print(f"Idioma        : {cfg.language}")
    if cfg.work_vault.is_dir():
        index = VaultIndex.build(cfg.work_vault)
        print(f"Notas índice  : {len(index.notes)}")
        print(f"Roster ({len(index.roster)}): {', '.join(index.roster) or '—'}")
        print(f"Sesiones      : {index.session_numbers()}")
        print(f"Siguiente nº  : {index.next_session_number()}")
    else:
        print("(el vault de trabajo no existe aún; ejecuta 'refresh')")
    try:
        models = OllamaClient(cfg.ollama_url).available_models()
        print(f"Ollama        : OK ({', '.join(models) or 'sin modelos'})")
    except OllamaError as exc:
        print(f"Ollama        : ERROR -> {exc}")
    return 0


def cmd_refresh(args) -> int:
    cfg = _apply_overrides(Config.from_env(), args)
    refresh_work_vault(cfg.source_vault, cfg.work_vault)
    return 0


def cmd_summarize(args) -> int:
    cfg = _apply_overrides(Config.from_env(), args)
    result = run(
        cfg,
        args.transcript,
        session_number=args.session,
        template_path=args.template,
        refresh=not args.no_refresh,
        propose_notes=not args.no_propose_notes,
        dry_run=args.dry_run,
        out=args.out,
        dump=args.dump,
        skip_minutes=args.skip_minutes,
        skip_tail_minutes=args.skip_tail_minutes,
        skip_ranges=_parse_ranges(args.skip_range),
    )
    if args.dry_run:
        print(f"[dry] {result.chunks} tramos (no se llamó al modelo).")
    else:
        print(
            f"[ok] sesión nº {result.session_number} · {result.chunks} tramos · "
            f"borrador: {result.draft_path} · notas nuevas: {len(result.new_notes)}"
        )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="goblin-agent",
        description="Resúmenes temáticos de sesiones de rol con Ollama local.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--source-vault", help="vault original (SOLO LECTURA)")
        p.add_argument("--work-vault", help="copia de trabajo donde se escribe")
        p.add_argument("--campaign", help="nombre de la campaña")
        p.add_argument("--model", help="modelo de Ollama (ej. qwen2.5:3b)")
        p.add_argument("--ollama-url", help="URL de Ollama")
        p.add_argument("--language", help="idioma del resumen (es/en/...)")

    p_info = sub.add_parser("info", help="muestra configuración, índice y estado de Ollama")
    add_common(p_info)
    p_info.set_defaults(func=cmd_info)

    p_ref = sub.add_parser("refresh", help="sincroniza el vault de trabajo desde el original")
    add_common(p_ref)
    p_ref.set_defaults(func=cmd_refresh)

    p_sum = sub.add_parser("summarize", help="genera el borrador de resumen de una sesión")
    p_sum.add_argument("transcript", help="ruta a transcript.json o transcript.md")
    p_sum.add_argument("--session", type=int, help="número de sesión (por defecto, el siguiente)")
    p_sum.add_argument("--template", help="ruta a una plantilla markdown")
    p_sum.add_argument("--out", help="ruta del borrador de salida")
    p_sum.add_argument("--no-refresh", action="store_true", help="no sincronizar el vault")
    p_sum.add_argument("--no-propose-notes", action="store_true", help="no proponer notas nuevas")
    p_sum.add_argument("--dry-run", action="store_true", help="analiza sin llamar al modelo")
    p_sum.add_argument("--dump", help="carpeta para volcar pasos intermedios (depuración)")
    p_sum.add_argument(
        "--skip-minutes",
        type=float,
        default=0.0,
        help="minutos iniciales a ignorar (cháchara previa a la partida)",
    )
    p_sum.add_argument(
        "--skip-tail-minutes",
        type=float,
        default=0.0,
        help="minutos finales a ignorar (cierre del directo, post-partida)",
    )
    p_sum.add_argument(
        "--skip-range",
        action="append",
        default=[],
        metavar="INICIO-FIN",
        help=(
            "rango de minutos a ignorar (pausas/charla fuera de juego); repite el "
            "flag para varios. Acepta 76.7-94.8 o 76:40-94:50"
        ),
    )
    add_common(p_sum)
    p_sum.set_defaults(func=cmd_summarize)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
