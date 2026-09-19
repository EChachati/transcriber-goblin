"""Baseline de rendimiento del linker actual (Python), fase 0 del rework a Rust.

Mide run_linker(target="mirror") sobre N docs sintéticos con M menciones cada uno.
No requiere y-sweet ni server levantado.

Uso (desde la raíz del repo):
    PYTHONPATH=server uv run --python 3.12 python crates/tg-server/tools/linker_bench.py
"""
import os
import secrets
import tempfile
import time
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="tg-bench-"))

from app.config import settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.linker import run_linker  # noqa: E402

TITLE = "Nota iterativa {i} sobre despliegue servidor y migracion de datos"


def reset() -> None:
    db = get_db()
    db.execute("DELETE FROM mirror_bench") if False else None
    # limpiar tablas usadas (esquema no tiene FKs ON DELETE CASCADE)
    for t in ("linker_proposals", "doc_access", "docs", "aliases"):
        db.execute(f"DELETE FROM {t}")
    db.commit()
    for f in settings.mirror_dir.glob("*.md"):
        f.unlink()


def seed(n: int, refs_each: int) -> None:
    db = get_db()
    settings.mirror_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        did = secrets.token_hex(8)
        title = TITLE.format(i=i)
        db.execute(
            "INSERT INTO docs (id, title, owner_id) VALUES (?, ?, 'bench')",
            (did, title),
        )
        db.execute(
            "INSERT INTO doc_access (doc_id, user_id, role) VALUES (?, 'bench', 'owner')",
            (did,),
        )
        others = [TITLE.format(i=j) for j in range(max(0, i - refs_each), i)]
        body = [f"# {title}", ""]
        for t in others:
            body.append(f"Se hablo de {t} con el equipo y se decidio migrar {title}.")
        (settings.mirror_dir / f"{did}.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    db.commit()


def bench(n: int, refs_each: int) -> None:
    reset()
    seed(n, refs_each)
    db = get_db()
    t0 = time.perf_counter()
    res = run_linker(db, apply_auto=True, target="mirror")
    dt = (time.perf_counter() - t0) * 1000
    print(
        f"N={n:4d} refs={refs_each}  {dt:8.1f} ms   "
        f"auto={len(res['auto'])} propuestas={len(res['proposals'])}"
    )


if __name__ == "__main__":
    print("baseline linker (Python, mirror target)")
    bench(20, 10)
    bench(40, 10)
    bench(80, 10)
    bench(160, 10)