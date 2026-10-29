"""Tests deterministas de las etiquetas de hablante (diarización S1..Sn).

Cubren el soporte añadido para transcripciones diarizadas: lectura de nombres
(``S1=DM``), limpieza de notas, descripción de hablantes en los prompts y el
rechazo de etiquetas/colectivos como entidades nuevas del vault. No llaman a
Ollama.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from goblin_agent.chunking import Chunk
from goblin_agent.config import Config
from goblin_agent.summarize import (
    build_map_prompt,
    build_propose_prompt,
    build_sections_prompt,
    clean_notes,
    describe_speakers,
    is_denied_proposal,
    salient_lines,
    speaker_labels_in,
    speaker_note,
)
from goblin_agent.transcript import (
    apply_speaker_names,
    load_transcript,
    parse_speaker_names,
)


def _cfg(**over) -> Config:
    base: dict = {
        "source_vault": Path("/tmp/origen"),
        "work_vault": Path("/tmp/trabajo"),
        "campaign_name": "Curse of Strahd",
        "ollama_url": "http://localhost:11434",
        "model": "qwen2.5:3b",
        "num_ctx": 8192,
        "num_predict": 3072,
        "temperature": 0.3,
        "language": "es",
        "chunk_chars": 4000,
        "chunk_overlap": 2,
        "lookahead": 6,
        "max_ref_notes": 6,
        "max_ref_chars": 1200,
        "max_ref_total": 5000,
        "body_batch_chars": 9000,
    }
    base.update(over)
    return Config(**base)


# --------------------------------------------------------------------------- #
# Lectura de transcripciones diarizadas
# --------------------------------------------------------------------------- #
def test_md_line_acepta_minutos_de_tres_digitos(tmp_path):
    # Una sesión de 3 h 55 m supera los 99 min: el bug era el ancho de minutos.
    p = tmp_path / "t.md"
    p.write_text(
        "**[12:03] S1:** hola\n"
        "**[120:05] S2:** ya pasaron las dos horas\n"
        "**[235:59] S12:** final del VOD\n",
        encoding="utf-8",
    )
    t = load_transcript(p)
    assert [round(s.start) for s in t.segments] == [
        12 * 60 + 3,
        120 * 60 + 5,
        235 * 60 + 59,
    ]
    assert [s.source for s in t.segments] == ["S1", "S2", "S12"]


def test_parse_speaker_names():
    assert parse_speaker_names(None) == {}
    assert parse_speaker_names("") == {}
    assert parse_speaker_names("S1=DM,S2=Alicia") == {"S1": "DM", "S2": "Alicia"}
    # Tolerante con espacios, separadores y etiquetas en minúscula.
    assert parse_speaker_names(" s1 = Dm ; S2=Alicia ,, ") == {
        "S1": "Dm",
        "S2": "Alicia",
    }
    with pytest.raises(ValueError):
        parse_speaker_names("S1DM")


def test_apply_speaker_names_renombra_solo_lo_mapeado(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps(
            {
                "session": "s",
                "segments": [
                    {"start": 0, "end": 1, "source": "S1", "text": "uno"},
                    {"start": 1, "end": 2, "source": "s2", "text": "dos"},
                    {"start": 2, "end": 3, "source": "S3", "text": "tres"},
                ],
            }
        ),
        encoding="utf-8",
    )
    t = load_transcript(p)
    out = apply_speaker_names(t, {"S1": "DM", "S2": "Alicia"})
    assert [s.source for s in out.segments] == ["DM", "Alicia", "S3"]
    # El original no se muta.
    assert [s.source for s in t.segments] == ["S1", "s2", "S3"]
    # Sin mapa, devuelve el mismo objeto.
    assert apply_speaker_names(t, {}) is t


# --------------------------------------------------------------------------- #
# Detección y descripción de etiquetas
# --------------------------------------------------------------------------- #
def test_speaker_labels_in():
    text = "[12:03] S2: hola\n[13:00] S10: adiós\n[14:00] DM: ojo"
    assert speaker_labels_in(text) == {"S2", "S10", "DM"}


def test_describe_speakers_diarizado():
    out = describe_speakers("[12:03] S1: hola\n[12:10] S2: qué tal")
    assert "HABLANTES DISTINTOS" in out
    assert "S1" in out and "no las conviertas en nombres propios" in out


def test_describe_speakers_clasico():
    out = describe_speakers("[12:03] TU: hola\n[12:10] REMOTO: qué tal")
    assert "'TU' es" in out
    assert "el resto de participantes" in out


def test_describe_speakers_generico():
    out = describe_speakers("[12:03] DM: hola")
    assert "NO son" in out
    assert "el grupo" in out


def test_speaker_note_adaptativa():
    assert "hablantes distintos" in speaker_note("[1:00] S3: hola")
    assert "«REMOTO»" in speaker_note("[1:00] REMOTO: hola")
    # Sin etiquetas (notas ya limpias) se avisa igualmente de que S# no son nombres.
    generic = speaker_note("- El grupo entra en la casa")
    assert "S1" in generic and "no son nombres de personaje" in generic


# --------------------------------------------------------------------------- #
# Limpieza de notas del map
# --------------------------------------------------------------------------- #
def test_clean_notes_quita_prefijo_s():
    # «S2: texto» → se quita el prefijo (y el sujeto inicial se capitaliza).
    assert clean_notes(["S2: el grupo entra en la casa"]) == [
        "El grupo entra en la casa"
    ]
    assert clean_notes(["S12: uno  dos\n\n\nS3: tres"]) == ["uno dos\n\ntres"]


def test_clean_notes_etiqueta_s_sin_dos_puntos_usasujeto_neutro():
    assert clean_notes(["- S3 decide activar el rage"]) == [
        "- Uno de los jugadores decide activar el rage"
    ]
    # En minúscula, a mitad de frase.
    assert clean_notes(["Lex dice a S4 dos veces"]) == [
        "Lex dice a uno de los jugadores dos veces"
    ]
    # Y con mayúscula tras un punto.
    assert clean_notes(["Oferta. S2 propone 300 GP"]) == [
        "Oferta. Uno de los jugadores propone 300 GP"
    ]


def test_clean_notes_sujeto_singular_en_ingles():
    assert clean_notes(["- S2 decide"], language="en") == [
        "- One of the players decide"
    ]


def test_clean_notes_no_toca_una_s_minuscula_normal():
    # «s» con dígitos en una palabra no es una etiqueta S#.
    assert clean_notes(["Compro 20 s3cretos del mercado"]) == [
        "Compro 20 s3cretos del mercado"
    ]


def test_salient_lines_quita_etiqueta_s():
    chunk = "[71:20] S3: jajaja qué gracioso el chiste de siempre, no puede ser\n[71:35] S2: ves que viene Sulfur y te aparta y te dice el cuerpo de Bruna está con el de Enza\n[71:50] S1: ok"
    lines = salient_lines(chunk, ["Sulfur", "Bruna"])
    assert len(lines) == 1
    assert "Sulfur" in lines[0] and "Enza" in lines[0]


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
def test_build_map_prompt_explica_hablantes_diarizados():
    cfg = _cfg()
    chunk = Chunk(
        index=0, start=0.0, end=10.0, text="[00:01] S1: hola\n[00:05] S2: hey"
    )
    prompt = build_map_prompt(cfg, chunk, refs=[], roster=["Lex"])
    assert "HABLANTES DISTINTOS" in prompt
    # Y sigue pidiendo la clave del tramo.
    assert "CLAVE:" in prompt


def test_build_sections_prompt_avisa_de_etiquetas_s():
    cfg = _cfg()
    con_s = build_sections_prompt(cfg, ["[00:10] S2: el grupo entra en la casa"], [])
    assert "hablantes distintos" in con_s
    # Notas ya limpias: aun así menciona las etiquetas S#.
    limpio = build_sections_prompt(cfg, ["- El grupo entra en la casa"], [])
    assert "S1" in limpio
    # El aviso clásico sigue ahí para notas con TU/REMOTO.
    clasico = build_sections_prompt(cfg, ["[00:10] REMOTO: entramos"], [])
    assert "«REMOTO»" in clasico


def test_build_propose_prompt_prohibe_etiquetas_y_colectivos():
    prompt = build_propose_prompt(_cfg(), "resumen", ["Ari"])
    assert "NO propongas etiquetas de hablante" in prompt


# --------------------------------------------------------------------------- #
# Entidades nuevas: rechazo de etiquetas/colectivos
# --------------------------------------------------------------------------- #
def test_is_denied_proposal():
    for name in ("S1", "s12", "S999", "El grupo", "REMOTO", "DM", "los jugadores"):
        assert is_denied_proposal(name), name
    for name in ("Ari", "Mujer de Negro", "Lucian Petrovich"):
        assert not is_denied_proposal(name), name
