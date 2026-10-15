"""Tests deterministas del goblin-agent (sin llamar a Ollama).

Cubren las piezas que no dependen del modelo: troceo, índice del vault,
guardrails de escritura, plantilla y limpieza de la salida del LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from goblin_agent.chunking import Chunk, chunk_segments, fmt_ts
from goblin_agent.config import Config
from goblin_agent.summarize import (
    build_map_prompt,
    build_sections_prompt,
    cap_bullets,
    clean_notes,
    dedupe_bullets,
    drop_noise_bullets,
    ensure_heading,
    ensure_key_facts,
    ensure_note_coverage,
    extract_bullets,
    extract_key_facts,
    looks_verbatim,
    merge_sections,
    normalize_headings,
    polish_section,
    salient_lines,
    section_count_for,
    section_titles,
    split_notes,
    strip_code_fences,
    strip_timestamps,
    theme_count_for,
)
from goblin_agent.template import parse_template
from goblin_agent.transcript import (
    Segment,
    Transcript,
    drop_leading_minutes,
    drop_minutes_range,
    drop_trailing_minutes,
    load_transcript,
    parse_minutes,
)
from goblin_agent.vault import (
    VaultIndex,
    assert_safe_paths,
    normalize,
    safe_write,
    sanitize_filename,
    strip_noise,
)


# --------------------------------------------------------------------------- #
# Troceo
# --------------------------------------------------------------------------- #
def _segments(n: int, text: str = "hola que tal amigos") -> list[Segment]:
    return [
        Segment(
            start=float(i * 10), end=float(i * 10 + 5), source="TU", text=f"{text} {i}"
        )
        for i in range(n)
    ]


def test_chunk_empty():
    assert chunk_segments([]) == []


def test_chunk_covers_all_segments():
    segs = _segments(50)
    chunks = chunk_segments(segs, target_chars=200, overlap=2, lookahead=3)
    assert chunks
    for chunk in chunks:
        assert chunk.text.strip()
    # El primer tramo empieza en el primer segmento y el último acaba en el último.
    assert chunks[0].start == segs[0].start
    assert chunks[-1].end == segs[-1].end
    # Índices consecutivos desde 0.
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunk_forward_progress_terminates():
    # Aunque haya solapamiento, el troceo debe avanzar y terminar.
    chunks = chunk_segments(_segments(30), target_chars=80, overlap=5, lookahead=2)
    assert 1 < len(chunks) < 30


def test_fmt_ts():
    assert fmt_ts(0) == "00:00"
    assert fmt_ts(65) == "01:05"
    assert fmt_ts(3725) == "62:05"


# --------------------------------------------------------------------------- #
# Guardrails de rutas / escritura
# --------------------------------------------------------------------------- #
def test_assert_safe_paths_same(tmp_path):
    with pytest.raises(ValueError):
        assert_safe_paths(tmp_path, tmp_path)


def test_assert_safe_paths_nested(tmp_path):
    src = tmp_path / "original"
    work = tmp_path / "original" / "copia"
    with pytest.raises(ValueError):
        assert_safe_paths(src, work)


def test_assert_safe_paths_ok(tmp_path):
    source = tmp_path / "original"
    work = tmp_path / "trabajo"
    source.mkdir()
    assert_safe_paths(source, work)  # no debe lanzar


def test_safe_write_refuses_source(tmp_path):
    source = tmp_path / "original"
    source.mkdir()
    with pytest.raises(ValueError):
        safe_write(source / "nota.md", "no", source)


def test_safe_write_creates_parents(tmp_path):
    work = tmp_path / "work"
    path = work / "sub" / "nota.md"
    written = safe_write(path, "contenido", tmp_path / "source")
    assert written.read_text(encoding="utf-8") == "contenido"


# --------------------------------------------------------------------------- #
# Índice del vault
# --------------------------------------------------------------------------- #
def _make_vault(root):
    (root / "NPCs" / "Vallaki").mkdir(parents=True)
    (root / "Lugares").mkdir()
    (root / "Jugadores").mkdir()
    (root / "Sesiones").mkdir()
    (root / "NPCs" / "Vallaki" / "Lady Wachter.md").write_text(
        "Lady Wachter", encoding="utf-8"
    )
    (root / "Lugares" / "Templo de Ámbar.md").write_text("Templo", encoding="utf-8")
    (root / "Jugadores" / "Mujer de Negro.md").write_text("PC", encoding="utf-8")
    (root / "Sesiones" / "Sesión 28.md").write_text("resumen previo", encoding="utf-8")
    (root / "Sesiones" / "Sesión 29.md").write_text("resumen actual", encoding="utf-8")


def test_vault_index(tmp_path):
    _make_vault(tmp_path)
    idx = VaultIndex.build(tmp_path)
    assert "Lady Wachter" in idx.notes
    assert "Templo de Ámbar" in idx.notes
    assert idx.roster == ["Mujer de Negro"]
    assert idx.session_numbers() == [28, 29]
    assert idx.next_session_number() == 30


def test_vault_find_mentions_accent_insensitive(tmp_path):
    _make_vault(tmp_path)
    idx = VaultIndex.build(tmp_path)
    found = idx.find_mentions("fuimos al templo de ambar con lady wachter", limit=5)
    assert "Templo de Ámbar" in found
    assert "Lady Wachter" in found


def test_vault_previous_session_text(tmp_path):
    _make_vault(tmp_path)
    idx = VaultIndex.build(tmp_path)
    assert idx.previous_session_text(29, max_chars=100) == "resumen previo"
    assert idx.previous_session_text(1, max_chars=100) is None


# --------------------------------------------------------------------------- #
# Plantilla
# --------------------------------------------------------------------------- #
def test_parse_template():
    tpl = "# Anteriormente en {{campana}}\n\n# Tema\n\n# Próximos pasos\n\n# Cabos sueltos\n"
    recap, fixed = parse_template(tpl.replace("{{campana}}", "Curse of Strahd"))
    assert recap == "# Anteriormente en Curse of Strahd"
    assert fixed == ["# Próximos pasos", "# Cabos sueltos"]


def test_parse_template_defaults_when_no_fixed():
    _, fixed = parse_template("# Anteriormente en X\n\n# Tema\n")
    assert fixed == ["# Próximos pasos", "# Cabos sueltos"]


# --------------------------------------------------------------------------- #
# Limpieza de la salida del modelo
# --------------------------------------------------------------------------- #
def test_strip_code_fences():
    assert strip_code_fences("```markdown\n# Hola\n- a\n```") == "# Hola\n- a"
    assert strip_code_fences("# Hola\n- a") == "# Hola\n- a"


def test_normalize_headings():
    raw = "### # Tema 1: Ruta a Berez\n- uno\n## Sección 2: Combate\n- dos\n### Próximos pasos\n- tres"
    out = normalize_headings(raw)
    assert out.splitlines()[0] == "# Ruta a Berez"
    assert "# Combate" in out
    assert "# Próximos pasos" in out


def test_extract_bullets_caps_and_stops_at_heading():
    raw = "- uno\n- dos\n- tres\n# Título\n- cuatro"
    assert extract_bullets(raw, limit=2) == ["- uno", "- dos"]
    assert extract_bullets(raw, limit=10) == ["- uno", "- dos", "- tres"]


def test_strip_timestamps():
    assert strip_timestamps("[01:23] hola [02:00] mundo") == "hola mundo"
    assert strip_timestamps("sin marcas") == "sin marcas"
    # Rangos (el modelo los escribe cuando abarca varios segundos) y negrita.
    assert strip_timestamps("[196:01-196:06] hola") == "hola"
    assert strip_timestamps("- **[39:03-39:10]** hola") == "- hola"


def test_looks_verbatim():
    transcript = (
        "[00:01] TU: Strahd descendio del cielo y mato a Ireena frente al grupo entero"
    )
    assert looks_verbatim(
        transcript, "Strahd descendio del cielo y mato a Ireena frente al grupo entero"
    )
    assert not looks_verbatim(transcript, "- Resumen breve de la escena con Ireena.")


def test_theme_count_for():
    assert theme_count_for("x" * 100) == 3
    assert theme_count_for("x" * 12000) == 6


# --------------------------------------------------------------------------- #
# Cuerpo por lotes (secciones)
# --------------------------------------------------------------------------- #
def _cfg(**over) -> Config:
    base: dict = dict(
        source_vault=Path("/tmp/origen"),
        work_vault=Path("/tmp/trabajo"),
        campaign_name="Curse of Strahd",
        ollama_url="http://localhost:11434",
        model="qwen2.5:3b",
        num_ctx=8192,
        num_predict=3072,
        temperature=0.3,
        language="es",
        chunk_chars=4000,
        chunk_overlap=2,
        lookahead=6,
        max_ref_notes=6,
        max_ref_chars=1200,
        max_ref_total=5000,
        body_batch_chars=9000,
    )
    base.update(over)
    return Config(**base)


def test_section_count_respeta_el_tope_de_lote():
    cfg = Config.from_env()
    # un lote nunca supera body_batch_chars
    for total in (500, 4000, 20000, 47000, 96000):
        count = section_count_for(total, cfg)
        assert count >= 1
        assert total / count <= cfg.body_batch_chars or count == 1
    # sesión corta: ~3 secciones; sesión larga de 8 h: bastantes más
    assert section_count_for(12000, cfg) == 3
    assert section_count_for(96000, cfg) >= 6


def test_split_notes_balancea_y_mantiene_orden():
    notes = ["a" * 1000, "b" * 1000, "c" * 1000, "d" * 1000]
    groups = split_notes(notes, 2)
    assert [n for g in groups for n in g] == notes  # orden intacto
    assert all(sum(len(n) for n in g) <= 3000 for g in groups)
    assert split_notes(notes, 1) == [notes]


def test_section_titles_y_ensure_heading():
    text = "# La taberna\n- uno\n## Otra escena\n- dos"
    assert section_titles(text) == ["La taberna", "Otra escena"]
    assert ensure_heading("# Ya tiene", "Parte 1") == "# Ya tiene"
    assert ensure_heading("- solo viñetas", "Parte 1").startswith("# Parte 1")


def test_dedupe_bullets_quita_repetidas():
    text = "# A\n- uno\n- dos\n# B\n- UNO\n- tres"
    out = dedupe_bullets(text)
    assert out.count("- ") == 3  # "- UNO" se descarta por duplicar "- uno"


def test_dedupe_bullets_quita_parrafasis():
    text = "\n".join(
        [
            "# A",
            "- Mujer de Negro declara a los presentes que su verdadera naturaleza "
            "es la de un ser celestial y poderoso.",
            "# B",
            "- Mujer de Negro declara ante todos su verdadera naturaleza, "
            "afirmando ser un ser celestial y poderoso.",
            "- Lex entra en la iglesia de Vallaki a hablar con el padre.",
        ]
    )
    out = dedupe_bullets(text)
    assert out.count("- ") == 2  # la paráfrasis de la sección B se cae


def test_extract_key_facts():
    notes = [
        "- [39:01] El grupo entra en la iglesia.\n"
        "CLAVE: Los huesos de Sant Andral han desaparecido de la iglesia",
        "- [40:02] Charla menor.\nCLAVE: —",
        "- [41:00] Tramo sin clave.",
    ]
    assert extract_key_facts(notes) == [
        "Los huesos de Sant Andral han desaparecido de la iglesia"
    ]


def test_extract_key_facts_acepta_clave_en_linea():
    notes = [
        "- [63:09] El grupo se reúne. [CLAVE: Arcaris revela su pasado y su hija]"
    ]
    assert extract_key_facts(notes) == ["Arcaris revela su pasado y su hija"]


def test_salient_lines_prioriza_nombres_y_trama():
    chunk = "\n".join(
        [
            "[71:20] REMOTE: jajaja qué gracioso el chiste de siempre, no puede ser",
            "[71:35] REMOTE: tú ves que viene Sulfur y te aparta y te dice maestro un "
            "momento, el cuerpo de Bruna está acoplado con el de Enza",
            "[71:50] REMOTE: ok",
            "[72:05] TU: y ahora qué hacemos con el barco pequeño de madera viejo",
        ]
    )
    lines = salient_lines(chunk, ["Sulfur", "Bruna"])
    assert len(lines) == 1
    assert "Sulfur" in lines[0]
    assert "Enza" in lines[0]


def test_ensure_key_facts_anade_lo_que_falta():
    section = "# T\n- Bruna llora por la muerte de Enza mientras el grupo espera."
    # Cubierto → la sección no cambia.
    assert ensure_key_facts(section, ["Bruna llora por la muerte de Enza"]) == section
    assert ensure_key_facts(section, []) == section
    # No cubierto → se añade su viñeta al final.
    missing = "Los huesos de Sant Andral han desaparecido de la iglesia"
    out = ensure_key_facts(section, [missing])
    assert out.splitlines()[-1] == "- " + missing


def test_ensure_note_coverage_fuerza_una_vineta_por_nota():
    notes = [
        "- El grupo llega a la iglesia y habla con el padre Lucian Petrovich.",
        "- Sulfur revela que el cuerpo de Bruna está acoplado con el de Enza.",
        "- jajaja qué risa, ok, sí, claro.",
    ]
    section = "# T\n- El grupo llega a la iglesia y habla con el padre Lucian Petrovich."
    out = ensure_note_coverage(section, notes)
    # La primera nota ya está cubierta; la de Sulfur se añade; la de risas no.
    assert "Sulfur revela" in out
    assert "jajaja" not in out
    assert out.count("- ") == 2
    # Sin notas nuevas, la sección no cambia.
    assert ensure_note_coverage(section, [notes[0]]) == section
    assert ensure_note_coverage(section, []) == section


def test_polish_section_quita_marcas_y_negritas():
    text = (
        "# T\n"
        "- **[39:03-39:10]** REMOTE se acerca al altar\n"
        "- **Kaelen observa:** con la espada desenvainada"
    )
    out = polish_section(text)
    assert "**" not in out
    assert "[39:03-39:10]" not in out
    assert "- REMOTE se acerca al altar" in out
    assert "- Kaelen observa: con la espada desenvainada" in out


def test_prompts_piden_clave_y_hechos_obligatorios():
    cfg = _cfg()
    chunk = Chunk(index=0, start=0.0, end=10.0, text="[00:01] TU: hola")
    map_prompt = build_map_prompt(cfg, chunk, refs=[], roster=["Lex"])
    assert "CLAVE:" in map_prompt
    with_facts = build_sections_prompt(cfg, ["nota"], [], key_facts=["Ari muere"])
    assert "HECHOS CLAVE" in with_facts
    assert "- Ari muere" in with_facts
    assert "HECHOS CLAVE" not in build_sections_prompt(cfg, ["nota"], [])
    with_salient = build_map_prompt(
        cfg,
        chunk,
        refs=[],
        roster=["Lex"],
        salient=["[00:01] REMOTE: viene Sulfur y te aparta"],
    )
    assert "viene Sulfur" in with_salient


def test_clean_notes_quita_origen():
    notes = ["REMOTE: uno  dos\n\n\nREMOTO: tres", "TU: cuatro"]
    out = clean_notes(notes)
    assert out == ["uno dos\ntres".replace("\n", "\n\n"), "cuatro"]


def test_clean_notes_etiqueta_sin_dos_puntos_da_sujeto():
    # «REMOTO» no es personaje: sin dos puntos se sustituye por un sujeto neutro.
    out = clean_notes(["- REMOTE decide activar el rage", "- Remoto calma nervios"])
    assert out == ["- El grupo decide activar el rage", "- El grupo calma nervios"]
    assert clean_notes(["TU propone huir"], language="en") == ["The group propone huir"]
    # En mitad de frase, con contracción española y mayúscula tras punto.
    assert clean_notes(["Lex dice a REMOTE dos veces"]) == [
        "Lex dice al grupo dos veces"
    ]
    assert clean_notes(["Oferta. REMOTE propone 300 GP"]) == [
        "Oferta. El grupo propone 300 GP"
    ]
    # El «remoto» en minúscula es un adjetivo normal: no se toca.
    assert clean_notes(["Ataque remoto del conjuro"]) == ["Ataque remoto del conjuro"]


def test_cap_bullets_recorta():
    text = "# T\n" + "\n".join(f"- b{i}" for i in range(12))
    out = cap_bullets(text, max_bullets=5)
    assert out.count("- ") == 5
    assert out.startswith("# T")


def test_merge_sections_fusiona_y_quita_vacias():
    text = "# A\n- uno\n\n# B\n- dos\n\n# A\n- tres\n\n# Vacia\n(sin viñetas)"
    out = merge_sections(text)
    assert out.count("# ") == 2  # "Vacia" fuera; "A" fusionada
    assert out.count("- ") == 3
    assert out.startswith("# A\n- uno\n- tres")


# --------------------------------------------------------------------------- #
# Recorte inicial de la grabación
# --------------------------------------------------------------------------- #
def test_extract_bullets_ignora_titulos():
    text = "\n".join(
        [
            "- **Investigación del tigre**",
            "- El grupo entierra al tigre.",
            "- **Infiltración en la Casa Vallakovich**",
            "- Lyssandra roba los planos.",
        ]
    )
    out = extract_bullets(text, limit=5)
    assert out == [
        "- El grupo entierra al tigre.",
        "- Lyssandra roba los planos.",
    ]


def test_drop_leading_minutes():
    t = Transcript(
        session="s",
        started_at=None,
        segments=[
            Segment(0, 10, "A", "charla previa"),
            Segment(1200, 1210, "A", "empieza la partida"),
        ],
    )
    out = drop_leading_minutes(t, 20)  # 20 min = 1200 s
    assert [s.text for s in out.segments] == ["empieza la partida"]
    assert drop_leading_minutes(t, 0) is t


def _t(segments):
    return Transcript(session="s", started_at=None, segments=segments)


def test_drop_trailing_minutes():
    t = _t(
        [
            Segment(0, 10, "A", "partida"),
            Segment(1200, 1250, "A", "última escena"),
            Segment(1380, 1410, "A", "post-partida"),
        ]
    )  # duración = 1410 s = 23,5 min
    out = drop_trailing_minutes(t, 2)  # corta a partir de 21,5 min = 1290 s
    assert [s.text for s in out.segments] == ["partida", "última escena"]
    assert drop_trailing_minutes(t, 0) is t


def test_drop_minutes_range():
    t = _t(
        [
            Segment(0, 10, "A", "antes"),
            Segment(4610, 4700, "A", "pausa"),
            Segment(4500, 4620, "A", "cruza el borde"),
            Segment(5700, 5800, "A", "después"),
        ]
    )
    out = drop_minutes_range(t, 76.7, 94.8)  # 4602 s .. 5688 s
    assert [s.text for s in out.segments] == ["antes", "cruza el borde", "después"]
    assert drop_minutes_range(t, 10, 5) is t  # rango invertido → sin cambios


def test_parse_minutes():
    assert parse_minutes("90") == 90
    assert parse_minutes("94.5") == 94.5
    assert parse_minutes("94:50") == pytest.approx(94 + 50 / 60)
    assert parse_minutes("1:34:50") == pytest.approx(94 + 50 / 60)


def test_drop_noise_bullets_quita_dados_y_fuera_de_juego():
    text = "\n".join(
        [
            "# T",
            "- Arcaris saca 22 y gana la iniciativa.",
            "- Todos tienen menos uno en la arcanía.",
            "- Kaelen identifica un hechizo de bola de fuego.",
            "- Frank lleva 3 años muteado en el stream.",
            "- 20 sucios. Todos lanzan iniciativa contra el sol brillante.",
            "- Pobre.",
            "- El grupo entra en la iglesia.",
        ]
    )
    out = drop_noise_bullets(text)
    assert out == (
        "# T\n- Kaelen identifica un hechizo de bola de fuego.\n- El grupo entra en "
        "la iglesia."
    )


# --------------------------------------------------------------------------- #
# Utilidades de vault
# --------------------------------------------------------------------------- #
def test_normalize_accents():
    assert normalize("Ámbar") == "ambar"
    assert normalize("  Lady   WACHTER ") == "lady wachter"


def test_sanitize_filename():
    assert sanitize_filename("Baba/Lysaga") == "Baba-Lysaga"
    assert sanitize_filename("   ") == "sin-titulo"


def test_strip_noise():
    raw = "texto\nbase64,AAAA\nnormal\ndata:image/png;base64,zzz\n" + "A" * 1300
    out = strip_noise(raw)
    assert "base64" not in out
    assert "texto" in out and "normal" in out
    assert "A" * 1300 not in out


# --------------------------------------------------------------------------- #
# Transcripciones
# --------------------------------------------------------------------------- #
def test_load_transcript_json(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps(
            {
                "session": "test",
                "segments": [
                    {"start": 0, "end": 1, "source": "TU", "text": "hola"},
                    {"start": 1, "end": 2, "source": "REMOTO", "text": "  "},
                    {"start": 2, "source": "REMOTO", "text": "adios"},
                ],
            }
        ),
        encoding="utf-8",
    )
    t = load_transcript(p)
    assert t.session == "test"
    assert len(t.segments) == 2  # el segmento vacío se descarta
    assert t.segments[1].text == "adios"
    assert t.segments[1].end == 2  # end por defecto = start


def test_load_transcript_markdown(tmp_path):
    p = tmp_path / "t.md"
    p.write_text("**[00:05] TU:** hola\n**[01:10] REMOTO:** adios\n", encoding="utf-8")
    t = load_transcript(p)
    assert [s.start for s in t.segments] == [5, 70]
    assert t.segments[1].source == "REMOTO"
