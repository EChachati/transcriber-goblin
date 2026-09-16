import asyncio

from pycrdt import Doc, Text

from .config import settings
from .db import get_db
from .ysweet import ysweet_manager


def _doc_to_markdown(update: bytes) -> str:
    doc = Doc()
    doc.apply_update(update)
    return str(doc.get("content", type=Text))


async def _mirror_doc(doc_id: str) -> None:
    update = await asyncio.to_thread(ysweet_manager().get_doc_as_update, doc_id)
    if update is None:
        return
    content = await asyncio.to_thread(_doc_to_markdown, update)

    target = settings.mirror_dir / f"{doc_id}.md"
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    print(f"[mirror] {doc_id}: saved {len(content)} chars")


async def mirror_loop() -> None:
    while True:
        try:
            ids = [r["id"] for r in get_db().execute("SELECT id FROM docs").fetchall()]
        except Exception as exc:
            print(f"[mirror] db error: {exc!r}")
            ids = []
        for doc_id in ids:
            try:
                await _mirror_doc(doc_id)
            except Exception as exc:
                print(f"[mirror] {doc_id}: {exc!r}")
        await asyncio.sleep(settings.mirror_interval)
