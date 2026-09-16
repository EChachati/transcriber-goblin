import pytest

from tg_transcriber.publish import render_markdown
from tg_transcriber.stt import Segment


def _seg(start, end, source, text):
    return Segment(start=start, end=end, source=source, text=text)


EPOCH = 1755000000.0


def test_render_orders_and_formats():
    segs = [
        _seg(65.2, 68.0, "REMOTO", "hola equipo"),
        _seg(3.1, 5.5, "TU", "empezamos?"),
    ]
    md = render_markdown("Sesion X", EPOCH, sorted(segs, key=lambda s: s.start))
    assert "**[00:03] TU:** empezamos?" in md
    assert "**[01:05] REMOTO:** hola equipo" in md
    idx_tu = md.index("empezamos?")
    idx_remoto = md.index("hola equipo")
    assert idx_tu < idx_remoto


def test_render_empty():
    md = render_markdown("Vacia", EPOCH, [])
    assert "Sin voz detectada" in md
    assert "# Vacia" in md


def test_render_with_attachments():
    segs = [_seg(0.0, 1.0, "TU", "test")]
    md = render_markdown(
        "Con audio", EPOCH, segs, {"TU": "attachment://" + "a" * 64}
    )
    assert "## Audio" in md
    assert "attachment://" + "a" * 64 in md
