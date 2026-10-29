"""Orquestación del resumen: map-reduce jerárquico, referencias y notas nuevas."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .chunking import Chunk, chunk_segments
from .config import Config
from .ollama import OllamaClient
from .template import DEFAULT_TEMPLATE, load_template, parse_template
from .transcript import (
    apply_speaker_names,
    drop_leading_minutes,
    drop_minutes_range,
    drop_trailing_minutes,
    load_transcript,
)
from .vault import (
    DRAFTS_DIR,
    NEW_NOTES_DIR,
    VaultIndex,
    assert_safe_paths,
    normalize,
    refresh_work_vault,
    safe_write,
    sanitize_filename,
)

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Quita un bloque ```markdown ... ``` si el modelo lo añadió alrededor."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def looks_verbatim(chunk_text: str, output: str) -> bool:
    """Heurística: ¿el modelo copió frases enteras de la transcripción?"""
    norm_out = normalize(output)
    source_lines: list[str] = []
    for raw in chunk_text.splitlines():
        stripped = re.sub(r"^\[[0-9:]+\]\s*[^\s:]+:\s*", "", raw.strip())
        if len(stripped) >= 25:
            source_lines.append(normalize(stripped))
    if not source_lines:
        return False
    hits = sum(1 for line in source_lines if line in norm_out)
    return hits / len(source_lines) >= 0.5


def drop_verbatim_lines(chunk_text: str, output: str) -> str:
    """Quita de un resumen las líneas que son copia literal del tramo.

    El modelo (sobre todo el pequeño) a veces pega líneas enteras de la
    transcripción en medio de las viñetas: basura de mesa que acaba en el
    borrador. Se quitan aquí, sin tocar las paráfrasis ni la línea «CLAVE:».
    """
    full = normalize(chunk_text)
    kept: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("CLAVE:"):
            kept.append(line)
            continue
        content = re.sub(r"^[-*]\s*", "", stripped)
        content = re.sub(r"^\[[0-9:]{1,8}\]\s*", "", content)
        content = _NOTE_SPEAKER_RE.sub("", content)
        norm = normalize(content).strip(" .,;:!?…")
        if len(norm) >= 30 and norm in full:
            continue  # copia literal del tramo: fuera
        kept.append(line)
    return "\n".join(kept).strip()


def strip_timestamps(text: str) -> str:
    """Quita las marcas «[mm:ss]» que el paso map usa para ordenar.

    Acepta también rangos («[196:01-196:06]») y negrita («**[39:03]**»), que el
    modelo escribe cuando un resumen abarca varios segundos.
    """
    return re.sub(
        r"\**\[[0-9]{1,3}:[0-9]{2}(?:\s*-\s*[0-9]{1,3}:[0-9]{2})?\]\**[ \t]*",
        "",
        text,
    ).strip()


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
def _map_system(cfg: Config) -> str:
    return (
        "Eres un asistente experto que resume tramos de sesiones de rol "
        "(Dungeons & Dragons). Escribes SIEMPRE en "
        f"{cfg.language}. RESUMES, nunca transcribes literalmente. Eres fiel a lo "
        "que ocurre: no inventas hechos ni nombres que no aparezcan."
    )


# Pistas de que una línea contiene trama (frente a charla de mesa).
_PLOT_HINTS = re.compile(
    r"\b(?:muer[ea]|muer\w*|muri\w+|mat[ao]\w*|asesin\w+|ritual|cuerpo|revel\w+|"
    r"confies\w+|secreto|misi[oó]n|encarg\w+|libro|maldici\w+|embosc\w+|trampa|"
    r"pacto|profec\w+|aviso|advert\w+|rescatar|salvar|peligro|traici\w+|"
    r"misteri\w+|aparece|apareci\w+|desapare\w+|hija|hijo|herman[oa]|padre|madre|"
    r"amig[oa]|ángel|angel|celestial|aasimar|demonio|naturaleza|huesos|"
    r"en realidad|por fin|al final)\b",
    re.IGNORECASE,
)
# Nombre propio a mitad de frase («… y te dice Sulfur …»): señal de entidad.
_MID_SENTENCE_NAME_RE = re.compile(r"[a-záéíóúñ,;:!?]\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{3,}")


def salient_lines(
    chunk_text: str,
    entity_names: Sequence[str] = (),
    limit: int = 6,
) -> list[str]:
    """Líneas del tramo con posible carga de trama (nombres propios o sucesos).

    El modelo tiende a resumir la charla de mesa y a saltarse lo que importa
    (muertes, revelaciones, misiones). Estas líneas se le muestran como pistas
    para que no se le escapen; siguen siendo entrada, no salida.
    """
    name_words = {
        word
        for name in entity_names
        for word in normalize(name).split()
        if len(word) >= 4
    }
    scored: list[tuple[int, int, str]] = []
    for position, raw in enumerate(chunk_text.splitlines()):
        line = raw.strip()
        if len(line) < 30:
            continue
        body = re.sub(r"^\[\d{1,3}:\d{2}\]\s*", "", line)
        body = re.sub(
            r"^(?:S\d{1,3}|[A-ZÁÉÍÓÚÑ]{2,}|TU|TÚ|REMOTE|REMOTO)\s*:\s*",
            "",
            body,
            flags=re.I,
        )
        if len(body) < 30:
            continue
        norm = normalize(body)
        score = 0
        if name_words and any(word in norm for word in name_words):
            score += 2
        if _PLOT_HINTS.search(body) or _MID_SENTENCE_NAME_RE.search(body):
            score += 1
        if score:
            scored.append((score, position, line))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen = scored[:limit]
    chosen.sort(key=lambda item: item[1])
    return [item[2] for item in chosen]


def speaker_labels_in(text: str) -> set[str]:
    """Etiquetas de hablante presentes en un tramo («[12:03] S2: …»)."""
    return set(re.findall(r"(?m)^\[\d{1,3}:\d{2}\]\s*([^:\n]+?):", text))


def describe_speakers(text: str) -> str:
    """Explica al modelo qué significan las etiquetas QUIÉN del tramo.

    Distingue el formato clásico (TU/REMOTO) del de diarización (S1, S2…). En
    el caso diarizado, cada S# es UNA persona distinta: sirve para saber quién
    habla, no son nombres de personaje.
    """
    labels = {lab.strip().upper() for lab in speaker_labels_in(text)}
    if any(re.fullmatch(r"S\d{1,3}", lab) for lab in labels):
        return (
            "El tramo usa el formato [mm:ss] QUIÉN: texto. Las etiquetas «S1», "
            "«S2», «S3»… son HABLANTES DISTINTOS (cada una es una persona "
            "diferente, detectada por voz). Esas etiquetas NO son nombres de "
            "personaje: no las conviertas en nombres propios. Úsalas para saber "
            "quién dice o hace cada cosa; al redactar escribe el nombre del "
            "personaje/jugador si lo conoces (p. ej. por el roster), y si no, "
            "refiérete a él de forma genérica («un jugador», «el DM»)."
        )
    if labels & {"TU", "TÚ", "REMOTO", "REMOTE"}:
        return (
            "El tramo de transcripción usa el formato [mm:ss] QUIÉN: texto. 'TU' es "
            "el jugador que grabó; 'REMOTO' es el resto de participantes. Esas "
            "etiquetas NO son nombres ni personajes: no las conviertas en nombres "
            "propios; escribe «el grupo» o el nombre del jugador si lo conoces."
        )
    return (
        "El tramo usa el formato [mm:ss] QUIÉN: texto. Esas etiquetas NO son "
        "nombres ni personajes: no las conviertas en nombres propios; escribe «el "
        "grupo» o el nombre del jugador si lo conoces."
    )


def speaker_note(text: str) -> str:
    """Frase breve (prompt de secciones) sobre cómo tratar las etiquetas QUIÉN.

    El cuerpo recibe notas ya limpias (``clean_notes``), así que normalmente no
    verá etiquetas y caerá en el texto genérico; se adapta igual que
    ``describe_speakers`` por si llegan notas sin limpiar (o retocadas a mano).
    """
    labels = {lab.strip().upper() for lab in speaker_labels_in(text)}
    if any(re.fullmatch(r"S\d{1,3}", lab) for lab in labels):
        return (
            "«S1», «S2», «S3»… son hablantes distintos (una persona cada uno), NO "
            "nombres de personaje: escribe el nombre del jugador si lo conoces o "
            "«un jugador»."
        )
    if labels & {"TU", "TÚ", "REMOTO", "REMOTE"}:
        return (
            "«REMOTO»/«REMOTE»/«TU» y «Remoto» no son nombres de personaje (son "
            "jugadores): escribe «el grupo» o el nombre del jugador."
        )
    return (
        "Las etiquetas de hablante («S1», «S2»…, «REMOTO»/«REMOTE», «TU») no son "
        "nombres de personaje (son jugadores): escribe el nombre del jugador si lo "
        "conoces o «el grupo»/«un jugador»."
    )


def build_map_prompt(
    cfg: Config,
    chunk: Chunk,
    refs: list[tuple[str, str]],
    roster: list[str],
    salient: Sequence[str] | None = None,
) -> str:
    parts: list[str] = [
        f"Campaña: {cfg.campaign_name}.",
        "Tarea: resume el tramo de transcripción que viene al final. Escribe de 4 "
        "a 10 viñetas; cada viñeta es UNA línea que empieza por «- [mm:ss] » (la "
        "marca del primer evento que menciona). Cubre TODO el tramo, de principio "
        "a fin, sin saltarte escenas: qué ocurre, quién lo hace o lo dice, y las "
        "decisiones y datos importantes (nombres propios, misiones, muertes, "
        "revelaciones, lugares). NO copies frases de la transcripción palabra por "
        "palabra: reescríbelo con tus propias palabras. No inventes nombres ni "
        "hechos: usa solo los que aparecen en el tramo. No escribas etiquetas de "
        "diálogo («REMOTO:», «S1:», «Nombre:») al principio de las viñetas; empieza "
        "contando qué ocurre. No añadas conclusiones ni secciones finales: "
        "responde SOLO con las viñetas.",
    ]
    if roster:
        parts.append("Personajes jugadores (roster): " + ", ".join(roster) + ".")
    if refs:
        parts.append("Contexto del vault (úsalo solo como referencia; no lo resumas):")
        for stem, note in refs:
            parts.append(f"### {stem}\n{note}")
    parts.append(
        describe_speakers(chunk.text)
    )
    parts.append(
        "Ignora la charla fuera de juego (la grabación, el stream, planes reales, "
        "temas del canal) salvo que cambie la historia de la partida."
    )
    if salient:
        parts.append(
            "Líneas del tramo que suelen contener lo importante (resume su "
            "contenido con tus palabras dentro de las viñetas; NO las copies "
            "literalmente y no las ignores si son parte de la historia):\n"
            + "\n".join(salient)
        )
    parts.append("TRAMO A RESUMIR:\n" + chunk.text)
    parts.append(
        "Resume en viñetas el tramo completo (de 4 a 10). Termina con una última "
        "línea aparte: «CLAVE: » y UNA frase con el hecho más importante del tramo "
        "(muerte, revelación, misión, encuentro, giro); si no hay ninguno, escribe "
        "«CLAVE: —»:"
    )
    return "\n\n".join(parts)


def _final_system(cfg: Config) -> str:
    return (
        "Eres un asistente que redacta las notas de una campaña de rol "
        f"(Dungeons & Dragons). Escribes en {cfg.language}. Eres fiel a los hechos."
    )


def theme_count_for(condensed: str) -> int:
    """Nº de secciones de tema según el tamaño de las notas (3 a 6)."""
    return max(3, min(6, 2 + len(condensed) // 2500))


# Palabras de origen («REMOTE:», «[REMOTE]:», «TU:») que ensucian las notas del map.
# También cubre las etiquetas de la diarización («S1:», «S12:»).
_NOTE_SPEAKER_RE = re.compile(
    r"\[?\b(?:S\d{1,3}|REMOTE|REMOTO|TU|TÚ)\]?\s*:\s*", re.IGNORECASE
)
# Etiqueta de un hablante diarizado (S1, S2…) al principio de una línea y SIN
# dos puntos («S2 decide …»): se sustituye por un sujeto neutro singular.
_SINGLE_LINE_RE = re.compile(
    r"(?m)^([ \t]*(?:[-*]\s+)?(?:\*\*)?)(?:\[)?S\d{1,3}(?:\])?(?:\*\*)?:?"
)
# Etiqueta de origen al principio de una línea y SIN dos puntos («REMOTE decide …»
# o «Remoto calma …»): el modelo la convierte en personaje si no la tocamos.
# OJO: sin re.IGNORECASE para no tocar el «tu» en minúscula del español.
_LINE_SPEAKER_RE = re.compile(
    r"(?m)^([ \t]*(?:[-*]\s+)?(?:\*\*)?)(?:\[)?"
    r"(?:REMOTE|REMOTO|TU|TÚ|Remoto|Remote)"
    r"(?:\])?(?:\*\*)?:?"
)
# Mismo problema en mitad de una frase («a REMOTE dos veces»). No se toca el
# «remoto» en minúscula: en español es un adjetivo normal («ataque remoto»).
_BARE_SPEAKER_RE = re.compile(r"\b(?:REMOTE|REMOTO|Remoto|Remote)\b")
# Igual para un S# suelto («se lo dice a S2»).
_BARE_SINGLE_RE = re.compile(r"\bS\d{1,3}\b")
# «el grupo» que empieza frase → «El grupo».
_SUBJECT_START_RE = re.compile(r"(?m)(^|[.!?][ \t]+)el grupo\b")
# Sujeto neutro con el que sustituirla («REMOTO» es el resto de jugadores).
_GROUP_SUBJECT = {"es": "El grupo", "en": "The group"}
# Sujeto neutro para un hablante diarizado anónimo (una sola persona).
_SINGLE_SUBJECT = {"es": "Uno de los jugadores", "en": "One of the players"}
# «uno de los jugadores» que empieza frase → «Uno de los jugadores».
_SINGLE_START_RE = re.compile(r"(?m)(^|[.!?][ \t]+)uno de los jugadores\b")
# Contracciones del español alrededor del sujeto («a el grupo» → «al grupo»).
_CONTRACTIONS = {
    "es": [((r"\ba el grupo\b"), "al grupo"), (r"\bde el grupo\b", "del grupo")]
}


def _group_subject(language: str) -> str:
    return _GROUP_SUBJECT.get((language or "es").strip().lower()[:2], "The group")


def _single_subject(language: str) -> str:
    return _SINGLE_SUBJECT.get(
        (language or "es").strip().lower()[:2], "One of the players"
    )


def clean_notes(notes: list[str], language: str = "es") -> list[str]:
    """Normaliza las notas del map: quita prefijos de origen y espacios de sobra.

    «REMOTE: texto» se elimina (el texto ya se entiende solo); la etiqueta sin dos
    puntos se sustituye por un sujeto neutro («El grupo decide activar el rage»),
    porque «Remoto» no es un personaje y arruina las secciones finales. Con la
    diarización, «S2: texto» se limpia igual y un «S2» suelto pasa a «Uno de los
    jugadores» (etiqueta anónima, no un nombre propio).
    """
    subject = _group_subject(language)
    single = _single_subject(language)
    key = (language or "es").strip().lower()[:2]
    cleaned: list[str] = []
    for note in notes:
        text = _NOTE_SPEAKER_RE.sub("", note)
        text = _SINGLE_LINE_RE.sub(lambda m: m.group(1) + single, text)
        text = _LINE_SPEAKER_RE.sub(lambda m: m.group(1) + subject, text)
        text = _BARE_SINGLE_RE.sub(single.lower(), text)
        text = _BARE_SPEAKER_RE.sub(subject.lower(), text)
        for pattern, replacement in _CONTRACTIONS.get(key, []):
            text = re.sub(pattern, replacement, text)
        text = _SUBJECT_START_RE.sub(lambda m: m.group(1) + subject, text)
        text = _SINGLE_START_RE.sub(lambda m: m.group(1) + single, text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            cleaned.append(text)
    return cleaned


def build_sections_prompt(
    cfg: Config,
    batch: list[str],
    used_titles: list[str],
    key_facts: list[str] | None = None,
) -> str:
    """Prompt de UNA sección del cuerpo a partir de un lote de notas de hoy.

    Cada llamada ve solo notas de la sesión de hoy; el recordatorio de la sesión
    anterior se genera en otra llamada y se ensambla después. Así el modelo no
    mezcla sesiones y ningún lote desborda el contexto (no se pierden tramos).
    """
    parts = [
        f"Campaña: {cfg.campaign_name}.",
        "Eres el cronista de la partida. Resume las notas de abajo como UNA sección "
        "de la crónica.\n"
        "Formato EXACTO:\n"
        "- Primera línea: «# Titulo» (título temático de 2 a 6 palabras).\n"
        "- Después, de 4 a 8 viñetas; cada una empieza por «- » y es UNA sola frase "
        "de máximo 30 palabras.\n"
        "Nada de sub-viñetas, ni negritas de sección, ni texto antes del título.\n"
        "Contenido: di qué ocurre y quién lo hace, con nombres propios. Reparte las "
        "viñetas entre TODAS las notas del lote: no te dejes los hechos importantes "
        "(muertes, revelaciones, misiones, encuentros) por contar detalles menores. "
        "Omite la charla de mesa (dados, reglas, risas, inventarios, la grabación o "
        "el stream) salvo que cambie la historia; no menciones tiradas, "
        "modificadores ni dudas de reglas, solo el hecho en el mundo. "
        + speaker_note("\n".join(batch))
        + " No copies frases "
        "textuales y no inventes hechos que no estén en las notas. "
        f"Escribe en {cfg.language}.",
    ]
    if used_titles:
        parts.append(
            "No repitas estos títulos ya usados: " + "; ".join(used_titles) + "."
        )
    if key_facts:
        parts.append(
            "HECHOS CLAVE que DEBEN aparecer en esta sección (redáctalos con tus "
            "palabras, pero no los omitas ni los sustituyas por detalles menores):\n"
            + "\n".join(f"- {fact}" for fact in key_facts)
        )
    parts.append("### NOTAS\n" + "\n".join(batch) + "\n### FIN")
    cue = 'Empieza ya por "# ":'
    if key_facts:
        cue = (
            "Recuerda: la sección DEBE incluir estos hechos clave: "
            + "; ".join(key_facts)
            + ".\n"
            + cue
        )
    parts.append(cue)
    return "\n\n".join(parts)


def build_closing_prompt(cfg: Config, body: str, fixed_sections: list[str]) -> str:
    """Prompt de las secciones de cierre a partir del cuerpo ya generado."""
    closing = ", ".join(f"«{h}»" for h in fixed_sections)
    return "\n\n".join(
        [
            "A partir del resumen de la sesión de abajo, escribe SOLO las secciones "
            f"{closing} de la nota. Para cada una: una línea «# Titulo» y debajo 2 a 4 "
            "viñetas que empiecen por «- ». No repitas información entre secciones y "
            f"no inventes hechos. Escribe en {cfg.language}.",
            "### RESUMEN\n" + body + "\n### FIN",
            'Empieza directamente por "# ":',
        ]
    )


def build_recap_prompt(cfg: Config, previous_text: str | None) -> str:
    """Prompt para el recordatorio «Anteriormente en …» (solo la sesión anterior)."""
    if not previous_text:
        return ""
    return "\n\n".join(
        [
            "Resume la siguiente sesión de rol en EXACTAMENTE 2 a 5 viñetas "
            f"concisas, en {cfg.language}. Cada viñeta empieza por «- ». "
            "No escribas encabezados, títulos ni explicaciones.",
            previous_text,
        ]
    )


def extract_bullets(text: str, limit: int = 5) -> list[str]:
    """Extrae viñetas «- »/«* » de una respuesta, cortando en el primer encabezado."""
    bullets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            break
        if stripped.startswith("- ") or stripped.startswith("* "):
            content = stripped[2:].strip()
            # Viñeta que en realidad es un título («- **Apartado**»): la saltamos.
            if _TITLE_BULLET_RE.match(content):
                continue
            bullets.append("- " + content)
        if len(bullets) >= limit:
            break
    return bullets


_HEADING_RE = re.compile(r"^\s*#{1,6}\s*#*\s*(.*)$", re.MULTILINE)
_TOPIC_PREFIX_RE = re.compile(
    r"^(?:tema|secci[oó]n|parte)\s*\d+\s*[:.\-–]\s*", re.IGNORECASE
)
_TITLE_BULLET_RE = re.compile(r"^\*\*[^*]+\*\*[.:]?$")


def normalize_headings(text: str) -> str:
    """Deja los encabezados como «# Título» y limpia prefijos tipo «Tema 1:»."""
    lines: list[str] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if not match:
            lines.append(line.rstrip())
            continue
        title = _TOPIC_PREFIX_RE.sub("", match.group(1).strip()).strip(" #*:-–")
        if title:
            lines.append(f"# {title}")
    return "\n".join(lines).strip()


def build_propose_prompt(cfg: Config, final_note: str, existing: list[str]) -> str:
    names = ", ".join(sorted(existing)[:400])
    return (
        f"Campaña: {cfg.campaign_name}.\n\n"
        "Estas son las notas que YA existen en el vault: "
        f"{names}.\n\n"
        "A partir del siguiente resumen de sesión, detecta entidades NUEVAS que "
        "merezcan su propia nota y NO estén en la lista: personajes (NPC), "
        "lugares, objetos, grupos u organizaciones.\n"
        "NO propongas etiquetas de hablante («S1», «S2»…), colectivos genéricos "
        "(«el grupo», «los jugadores», «el equipo») ni el DM: no son entidades "
        "del vault.\n\n"
        f"Resumen de la sesión:\n{final_note}\n\n"
        "Responde SOLO con un array JSON, sin texto alrededor, con esta forma:\n"
        '[{"tipo": "NPC|Lugar|Objeto|Grupo", "nombre": "...", '
        '"zona": "Vallaki|Krezk|Pueblo de Barovia|... o vacío", '
        '"descripcion": "1-3 frases"}]'
    )


# --------------------------------------------------------------------------- #
# Pasos
# --------------------------------------------------------------------------- #
def section_count_for(total_chars: int, cfg: Config) -> int:
    """Nº de secciones del cuerpo: ~3-8, nunca menos que el tope de lote."""
    if total_chars <= 0:
        return 1
    by_budget = math.ceil(total_chars / max(1, cfg.body_batch_chars))
    target = max(3, min(8, 2 + total_chars // 12000))
    by_content = 1 + total_chars // 1500  # una sección necesita un mínimo de texto
    return max(1, min(max(target, by_budget), by_content))


def split_notes(notes: list[str], count: int) -> list[list[str]]:
    """Reparte notas contiguas en `count` lotes de tamaño (en chars) parecido."""
    if count <= 1 or len(notes) <= 1:
        return [notes]
    target = sum(len(n) for n in notes) / count
    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for note in notes:
        current.append(note)
        size += len(note)
        if size >= target and len(groups) < count - 1:
            groups.append(current)
            current = []
            size = 0
    if current:
        groups.append(current)
    return groups


def section_titles(text: str) -> list[str]:
    """Títulos («# …») que aparecen en una sección."""
    return [
        m.group(1).strip() for m in _HEADING_RE.finditer(text) if m.group(1).strip()
    ]


def ensure_heading(text: str, fallback: str) -> str:
    """Garantiza que una sección empiece por un encabezado «# »."""
    cleaned = strip_code_fences(text)
    first = next((line for line in cleaned.splitlines() if line.strip()), "")
    if _HEADING_RE.match(first):
        return cleaned
    return f"# {fallback}\n{cleaned}".strip()


def _bullet_tokens(text: str) -> set[str]:
    """Palabras de una viñeta (≥3 caracteres) para comparar similitud."""
    return {w for w in re.findall(r"[a-z0-9áéíóúñü]+", text.lower()) if len(w) >= 3}


def _near_duplicate(a: set[str], b: set[str]) -> bool:
    """¿Dos viñetas dicen lo mismo con otras palabras? (paráfrasis del modelo)."""
    if not a or not b:
        return False
    shared = len(a & b)
    if not shared:
        return False
    shortest, longest = sorted((len(a), len(b)))
    if shortest / longest < 0.6:  # frases de muy distinta longitud
        return False
    return shared / shortest >= 0.7


def dedupe_bullets(text: str) -> str:
    """Quita viñetas idénticas o casi idénticas repetidas entre secciones."""
    seen: set[str] = set()
    token_sets: list[set[str]] = []
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            content = stripped[2:]
            key = normalize(content).casefold()
            tokens = _bullet_tokens(content)
            if key and key in seen:
                continue
            if any(_near_duplicate(tokens, other) for other in token_sets):
                continue
            if key:
                seen.add(key)
            if tokens:
                token_sets.append(tokens)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def cap_bullets(text: str, max_bullets: int = 8) -> str:
    """Recorta una sección a sus encabezados y las primeras N viñetas."""
    lines: list[str] = []
    bullets = 0
    for line in text.splitlines():
        if line.strip().startswith("- "):
            bullets += 1
            if bullets > max_bullets:
                continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


# Viñetas de mecánica (tiradas, ventajas, iniciativas) que no son historia.
_MECHANIC_RE = re.compile(
    r"\bsac(?:a|an|ó|o|á|e|en)\s+(?:un\s+|una\s+)?\d{1,2}\b"
    r"|\bsaque\s+\d{1,2}\b"
    r"|\bmenos\s+(?:uno|\d{1,2})\b"
    r"|\b(?:bonus|malus)\s*action\b"
    r"|\b(?:des|con)\s*ventaja\b"
    r"|\bnat(?:ural)?\s*\d{1,2}\b"
    r"|\bd\d{1,2}\b"
    r"|\bpo\s*\d{1,2}\b"
    r"|\biniciativa\b"
    r"|\brage\b"
    r"|\bcure wounds\b"
    r"|\bpuntos? de (?:inspiraci[oó]n|golpe|expiraci[oó]n)\b"
    r"|\bestabiliza\w+"
    r"|\bpuntos? de vida\b",
    re.IGNORECASE,
)
# Viñetas de fuera de juego (directo, pausa, Discord) que se colaron.
_OFF_GAME_RE = re.compile(
    r"\b(?:stream|transmisi[oó]n|mutead\w*|c[aá]mara|grabaci[oó]n|discord|"
    r"cameo|producci[oó]n|suscri\w*|brexit|pausa|chat de voz|el video|"
    r"horario|fecha de corte)\b",
    re.IGNORECASE,
)


def drop_noise_bullets(text: str) -> str:
    """Quita viñetas de dados/reglas y de charla fuera de juego (determinista).

    El modelo pequeño hace caso omiso de «omite dados y cháchara» a medias; aquí
    se filtra por patrones después de generar, sin volver a llamar al modelo.
    También descarta viñetas-migaja de 1 o 2 palabras («Pobre.», «Buena suerte.»).
    """
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            if (
                _MECHANIC_RE.search(stripped)
                or _OFF_GAME_RE.search(stripped)
                or len(stripped[2:].split()) < 3
            ):
                continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- #
# Hechos clave («CLAVE:» del paso map): el modelo pequeño los descarta a menudo,
# así que se garantizan de forma determinista.
# --------------------------------------------------------------------------- #
_CLAVE_RE = re.compile(r"CLAVE:\s*([^\]\n]+)")
_FACT_PLACEHOLDERS = {"—", "-", "...", "nada", "ninguno", "sin nada"}
_FACT_STOPWORDS = {
    # español (en minúsculas ya normalizado: sin acentos)
    "para",
    "como",
    "pero",
    "este",
    "esta",
    "esto",
    "esos",
    "esas",
    "muy",
    "desde",
    "hacia",
    "entre",
    "sobre",
    "cuando",
    "porque",
    "entonces",
    "todos",
    "todas",
    "tienen",
    "tiene",
    "habia",
    "sino",
    "otro",
    "otra",
    "otros",
    "otras",
    "mismo",
    "misma",
    "donde",
    "durante",
    "antes",
    "despues",
    "aunque",
    "tambien",
    "vuelve",
    "empieza",
    "sigue",
    "dice",
    # inglés
    "the",
    "and",
    "that",
    "with",
    "from",
    "this",
    "have",
    "were",
    "they",
    "their",
    "into",
    "then",
    "them",
    "when",
    "what",
    "which",
}


def extract_key_facts(notes: list[str]) -> list[str]:
    """Hechos «CLAVE:» de un lote de notas (sin los marcadores vacíos)."""
    facts: list[str] = []
    seen: set[str] = set()
    for note in notes:
        match = _CLAVE_RE.search(note)
        if not match:
            continue
        fact = strip_timestamps(match.group(1)).strip()
        fact = re.sub(r"^[-*•]\s*", "", fact).strip(" -—")
        if not fact or normalize(fact) in _FACT_PLACEHOLDERS:
            continue
        if normalize(fact) in seen:
            continue
        seen.add(normalize(fact))
        facts.append(fact)
    return facts


def _fact_words(text: str) -> set[str]:
    """Palabras con contenido (≥4 caracteres, sin palabras de relleno)."""
    return {
        word
        for word in re.findall(r"[a-z0-9]{4,}", normalize(text))
        if word not in _FACT_STOPWORDS
    }


def _fact_covered(fact: str, section_words: set[str]) -> bool:
    words = _fact_words(fact)
    if len(words) < 3:  # demasiado corto para comparar con fiabilidad
        return True
    if not section_words:
        return False
    return len(words & section_words) / len(words) >= 0.6


def ensure_key_facts(section: str, facts: list[str]) -> str:
    """Garantiza que los «CLAVE:» del lote estén reflejados en la sección.

    Si el modelo se saltó uno, se añade su viñeta a mano (sin volver a llamarlo).
    """
    if not facts:
        return section
    section_words = _fact_words(section)
    missing = [f for f in facts if not _fact_covered(f, section_words)]
    if not missing:
        return section
    lines = [line.rstrip() for line in section.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    for fact in missing:
        lines.append("- " + fact[:300].rstrip())
    return "\n".join(lines).strip()


def _note_core(note: str) -> str | None:
    """Viñeta más representativa de una nota (la de más carga de trama).

    Devuelve ``None`` si la nota no tiene ninguna viñeta con pinta de hecho de
    juego (así no se fuerza en el cuerpo la charla de mesa de un tramo malo).
    """
    best: tuple[int, str] | None = None
    for line in note.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("- ", "* ")):
            stripped = stripped[2:].strip()
        content = strip_timestamps(stripped).strip().replace("**", "")
        content = re.sub(r"\[?CLAVE:.*$", "", content).strip()
        content = re.sub(r"^\[[^\]\n]{1,30}\]\s*:\s*", "", content).strip()
        content = re.sub(r"^([A-ZÁÉÍÓÚÑ][\w'’\-]{1,20}):\s*", "", content).strip()
        if len(content.split()) < 5:
            continue
        if _MECHANIC_RE.search(content) or _OFF_GAME_RE.search(content):
            continue
        score = len(_PLOT_HINTS.findall(content)) + len(
            _MID_SENTENCE_NAME_RE.findall(content)
        )
        # El diálogo literal («Me volteo y le digo…») es peor viñeta que un resumen.
        if re.match(r"(?i)^(?:yo|me|mi|nos|te|t[uú]|nosotros|aquí|ahora)\b", content):
            score -= 1
        if score > 0 and (best is None or score > best[0]):
            best = (score, content)
    return best[1] if best else None


def ensure_note_coverage(
    section: str,
    notes: list[str],
    max_added: int = 4,
) -> str:
    """Garantiza que cada nota con trama aporte al menos una viñeta al cuerpo.

    El modelo pequeño tiende a resumir solo una parte del lote y a olvidar
    tramos enteros; aquí, si una nota con hechos no aparece en la sección, se
    añade a mano su viñeta más representativa (sin volver a llamar al modelo).
    """
    if not notes:
        return section
    section_words = _fact_words(section)
    additions: list[str] = []
    for note in notes:
        core = _note_core(note)
        if not core or _fact_covered(core, section_words):
            continue
        if any(_fact_covered(core, _fact_words(added)) for added in additions):
            continue
        additions.append(core)
        if len(additions) >= max_added:
            break
    if not additions:
        return section
    lines = [line.rstrip() for line in section.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend("- " + core[:300].rstrip() for core in additions)
    return "\n".join(lines).strip()


_BOLD_SPAN_RE = re.compile(r"\*\*([^*]+)\*\*")


def polish_section(text: str) -> str:
    """Pule una sección generada: sin marcas de tiempo ni negritas en viñetas."""
    text = strip_timestamps(text)
    lines: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("- "):
            line = _BOLD_SPAN_RE.sub(lambda m: m.group(1), line)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def merge_sections(text: str) -> str:
    """Fusiona secciones con el mismo título y descarta las que quedan vacías."""
    parsed: list[tuple[str, list[str]]] = []
    title: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match and match.group(1).strip():
            if title is not None:
                parsed.append((title, lines))
            title = _TOPIC_PREFIX_RE.sub("", match.group(1).strip()).strip(" #*:-–")
            lines = []
        elif title is not None:
            lines.append(line.rstrip())
    if title is not None:
        parsed.append((title, lines))

    index_by_key: dict[str, int] = {}
    merged: list[tuple[str, list[str]]] = []
    for name, body_lines in parsed:
        bullets = [line for line in body_lines if line.strip().startswith("- ")]
        if not bullets:
            continue  # sección sin viñetas → fuera
        key = normalize(name)
        if key in index_by_key:
            merged[index_by_key[key]][1].extend(bullets)
        else:
            index_by_key[key] = len(merged)
            merged.append((name, bullets))
    return "\n\n".join(f"# {name}\n" + "\n".join(bullets) for name, bullets in merged)


def _generate_body(
    cfg: Config, client: OllamaClient, notes: list[str], log, dump_dir: Path | None
) -> str:
    """Cuerpo de la nota: una sección por lote de notas de hoy, concatenadas."""
    notes = clean_notes(notes, language=cfg.language)
    count = section_count_for(sum(len(n) for n in notes), cfg)
    batches = split_notes(notes, count)
    sections: list[str] = []
    used_titles: list[str] = []
    for i, batch in enumerate(batches, 1):
        facts = extract_key_facts(batch)
        prompt = build_sections_prompt(cfg, batch, used_titles, key_facts=facts)
        raw = client.generate(prompt, system=_final_system(cfg))
        section = ensure_note_coverage(
            ensure_key_facts(
                polish_section(
                    drop_noise_bullets(
                        cap_bullets(normalize_headings(ensure_heading(raw, f"Parte {i}")))
                    )
                ),
                facts,
            ),
            batch,
        )
        if section:
            sections.append(section)
            used_titles.extend(section_titles(section))
        if dump_dir:
            (dump_dir / f"body_section_{i:02d}.txt").write_text(
                section + "\n", encoding="utf-8"
            )
        log(
            f"[body] sección {i}/{len(batches)} "
            f"({len(batch)} notas, {sum(len(b) for b in batch)} chars"
            + (f", {len(facts)} claves" if facts else "")
            + ")"
        )
    return dedupe_bullets(merge_sections("\n\n".join(sections)))


def _generate_closing(
    cfg: Config,
    client: OllamaClient,
    body: str,
    fixed_sections: list[str],
    log,
    dump_dir: Path | None,
) -> str:
    """Secciones de cierre (a partir del cuerpo ya escrito)."""
    if not fixed_sections or not body:
        return ""
    prompt = build_closing_prompt(cfg, body, fixed_sections)
    raw = client.generate(prompt, system=_final_system(cfg))
    closing = polish_section(
        drop_noise_bullets(normalize_headings(ensure_heading(raw, fixed_sections[0])))
    )
    if dump_dir:
        (dump_dir / "closing.txt").write_text(closing + "\n", encoding="utf-8")
    log(f"[body] secciones de cierre: {', '.join(fixed_sections)}")
    return closing


def _extract_json_array(text: str) -> list[dict]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict) and item.get("nombre")]


def _folder_for(tipo: str) -> str:
    return {
        "npc": "NPCs",
        "lugar": "Lugares",
        "objeto": "Objetos",
        "grupo": "Grupos",
    }.get((tipo or "").strip().lower(), "NPCs")


def _render_proposal(entry: dict) -> str:
    lines = [
        f"# {entry.get('nombre', 'Sin nombre')}",
        "",
        f"- Tipo: {entry.get('tipo', 'NPC')}",
    ]
    if entry.get("zona"):
        lines.append(f"- Zona: {entry['zona']}")
    lines += [
        "- _Borrador generado por goblin-agent. Revisar antes de integrar en el vault._",
        "",
        entry.get("descripcion", "").strip(),
        "",
    ]
    return "\n".join(lines)


def _name_key(name: str) -> str:
    """Clave de comparación de nombres: sin acentos, sin artículo inicial."""
    key = normalize(name).strip()
    for article in ("la ", "el ", "los ", "las ", "un ", "una ", "the "):
        if key.startswith(article):
            key = key[len(article) :]
            break
    return key


# Nombres genéricos que el modelo propone a veces como «entidad nueva»: son
# etiquetas de hablantes o colectivos, no notas del vault.
_PROPOSE_DENY = {
    "grupo",
    "party",
    "remote",
    "remoto",
    "jugadores",
    "jugador",
    "equipo",
    "team",
    "dm",
    "dungeon master",
    "master",
    "narrador",
}
# Etiquetas de la diarización («S1», «S2»…) propuestas como nombre propio.
_PROPOSE_DENY_RE = re.compile(r"s\d{1,3}")


def is_denied_proposal(name: str) -> bool:
    """¿El nombre propuesto es una etiqueta de hablante o un colectivo genérico?

    Evita crear notas como «S1»/«S12» (diarización) o «El grupo»: el modelo a
    veces los confunde con entidades del vault.
    """
    key = _name_key(name)
    return not key or key in _PROPOSE_DENY or bool(_PROPOSE_DENY_RE.fullmatch(key))


def _write_new_notes(
    cfg: Config, client: OllamaClient, final_note: str, index: VaultIndex, log
) -> list[Path]:
    existing = set(index.notes)
    existing_keys = {_name_key(n) for n in existing}
    raw = client.generate(
        build_propose_prompt(cfg, final_note, list(existing)),
        system=(
            "Eres un asistente que detecta entidades nuevas en notas de rol y "
            "responde en JSON."
        ),
    )
    entries = _extract_json_array(raw)
    written: list[Path] = []
    for entry in entries:
        name = str(entry.get("nombre", "")).strip()
        key = _name_key(name)
        if not name or key in existing_keys or is_denied_proposal(name):
            continue
        folder = _folder_for(str(entry.get("tipo", "")))
        zona = str(entry.get("zona", "")).strip()
        base = (
            cfg.work_vault
            / NEW_NOTES_DIR
            / folder
            / (sanitize_filename(zona) if zona else "")
        )
        path = base / f"{sanitize_filename(name)}.md"
        if path.exists():
            continue
        written.append(safe_write(path, _render_proposal(entry), cfg.source_vault))
    return written


# --------------------------------------------------------------------------- #
# Entrada principal
# --------------------------------------------------------------------------- #
@dataclass
class SummaryResult:
    session_number: int
    draft_path: Path | None
    chunks: int
    partials: int
    new_notes: list[Path]


def run(
    cfg: Config,
    transcript_path: str | Path,
    *,
    session_number: int | None = None,
    template_path: str | Path | None = None,
    refresh: bool = True,
    propose_notes: bool = True,
    dry_run: bool = False,
    out: str | Path | None = None,
    dump: str | Path | None = None,
    skip_minutes: float = 0.0,
    skip_tail_minutes: float = 0.0,
    skip_ranges: Sequence[tuple[float, float]] | None = None,
    speaker_names: dict[str, str] | None = None,
    log=print,
) -> SummaryResult:
    assert_safe_paths(cfg.source_vault, cfg.work_vault)

    if refresh:
        refresh_work_vault(cfg.source_vault, cfg.work_vault, log=log)

    transcript = load_transcript(transcript_path)
    if speaker_names:
        transcript = apply_speaker_names(transcript, speaker_names)
        log(
            "[agent] renombro hablantes: "
            + ", ".join(f"{k}→{v}" for k, v in sorted(speaker_names.items()))
        )
    if skip_minutes and skip_minutes > 0:
        trimmed = drop_leading_minutes(transcript, skip_minutes)
        if len(trimmed.segments) != len(transcript.segments):
            log(f"[agent] omito los primeros {skip_minutes:g} min de la grabación")
            transcript = trimmed
    for start, end in skip_ranges or ():
        trimmed = drop_minutes_range(transcript, start, end)
        if len(trimmed.segments) != len(transcript.segments):
            log(f"[agent] omito el rango {start:g}-{end:g} min (charla fuera de juego)")
            transcript = trimmed
    if skip_tail_minutes and skip_tail_minutes > 0:
        trimmed = drop_trailing_minutes(transcript, skip_tail_minutes)
        if len(trimmed.segments) != len(transcript.segments):
            log(f"[agent] omito los últimos {skip_tail_minutes:g} min (post-partida)")
            transcript = trimmed
    if not transcript.segments:
        raise ValueError("La transcripción no tiene segmentos tras el recorte.")

    index = VaultIndex.build(cfg.work_vault)
    number = session_number or index.next_session_number()
    log(
        f"[agent] sesión nº {number} · {len(transcript.segments)} segmentos · "
        f"{len(index.notes)} notas en el índice · roster: {', '.join(index.roster) or '—'}"
    )

    chunks = chunk_segments(
        transcript.segments, cfg.chunk_chars, cfg.chunk_overlap, cfg.lookahead
    )
    log(f"[agent] troceo híbrido -> {len(chunks)} tramos")

    if dry_run:
        for ch in chunks[:3]:
            refs = index.references_for(
                ch.text, cfg.max_ref_notes, cfg.max_ref_chars, cfg.max_ref_total
            )
            log(
                f"[dry] tramo {ch.index + 1}: {ch.text.count(chr(10)) + 1} líneas, "
                f"referencias: {', '.join(s for s, _ in refs) or '—'}"
            )
        return SummaryResult(number, None, len(chunks), 0, [])

    client = OllamaClient(
        base_url=cfg.ollama_url,
        model=cfg.model,
        num_ctx=cfg.num_ctx,
        num_predict=cfg.num_predict,
        temperature=cfg.temperature,
    )

    dump_dir = Path(dump).expanduser() if dump else None
    if dump_dir:
        dump_dir.mkdir(parents=True, exist_ok=True)

    # --- map ------------------------------------------------------------- #
    partials: list[str] = []
    verbatim_hits = 0
    for ch in chunks:
        refs = index.references_for(
            ch.text, cfg.max_ref_notes, cfg.max_ref_chars, cfg.max_ref_total
        )
        prompt = build_map_prompt(
            cfg,
            ch,
            refs,
            index.roster,
            salient=salient_lines(ch.text, [s for s, _ in refs]),
        )
        result = strip_code_fences(client.generate(prompt, system=_map_system(cfg)))
        if looks_verbatim(ch.text, result):
            verbatim_hits += 1
            log(f"[map] tramo {ch.index + 1}: copia literal detectada; reintentando")
            retry_prompt = (
                "IMPORTANTE: está PROHIBIDO copiar frases de la transcripción. "
                "Reescribe todo con tus propias palabras, resumiendo.\n\n" + prompt
            )
            result = strip_code_fences(
                client.generate(retry_prompt, system=_map_system(cfg))
            )
        partials.append(result)
        if dump_dir:
            (dump_dir / f"map_{ch.index + 1:02d}.txt").write_text(
                f"### refs: {', '.join(s for s, _ in refs)}\n\n{result}\n",
                encoding="utf-8",
            )
        log(
            f"[map] tramo {ch.index + 1}/{len(chunks)} "
            f"(refs: {', '.join(s for s, _ in refs) or '—'})"
        )
    if verbatim_hits:
        log(f"[map] {verbatim_hits}/{len(chunks)} tramos requirieron reintento")

    # --- notas de hoy ----------------------------------------------------- #
    partials_clean = [strip_timestamps(p).strip() for p in partials if p.strip()]

    template = (
        load_template(template_path) or index.vault_template() or DEFAULT_TEMPLATE
    )
    template = template.replace("{{campana}}", cfg.campaign_name)
    recap_heading, fixed_sections = parse_template(template)
    if not recap_heading:
        recap_heading = f"# Anteriormente en {cfg.campaign_name}"
    recap_text = index.previous_session_text(number, max_chars=1400)
    if dump_dir:
        (dump_dir / "notes.txt").write_text(
            "\n\n".join(partials_clean) + "\n", encoding="utf-8"
        )

    # --- nota final: cuerpo (solo hoy) + cierre + recap (solo anterior) --- #
    body = _generate_body(cfg, client, partials_clean, log, dump_dir)
    if dump_dir:
        (dump_dir / "body.txt").write_text(body + "\n", encoding="utf-8")
    closing = _generate_closing(cfg, client, body, fixed_sections, log, dump_dir)

    recap_prompt = build_recap_prompt(cfg, recap_text)
    recap_bullets: list[str] = []
    if recap_prompt:
        if dump_dir:
            (dump_dir / "recap_prompt.txt").write_text(recap_prompt, encoding="utf-8")
        recap_raw = strip_code_fences(
            client.generate(recap_prompt, system=_final_system(cfg))
        )
        recap_bullets = extract_bullets(recap_raw, limit=5)
        if dump_dir:
            (dump_dir / "recap_raw.txt").write_text(recap_raw + "\n", encoding="utf-8")

    parts: list[str] = []
    if recap_bullets:
        parts.append(recap_heading + "\n\n" + "\n".join(recap_bullets))
    if body:
        parts.append(body)
    if closing:
        parts.append(closing)
    final_note = "\n\n".join(parts).rstrip() + "\n"
    if dump_dir:
        (dump_dir / "final_note.txt").write_text(final_note, encoding="utf-8")

    draft_path = (
        Path(out).expanduser()
        if out
        else cfg.work_vault / DRAFTS_DIR / f"Sesión {number}.md"
    )
    safe_write(draft_path, final_note, cfg.source_vault)
    log(f"[ok] borrador escrito: {draft_path}")
    if dump_dir:
        (dump_dir / "final.md").write_text(final_note, encoding="utf-8")

    new_notes: list[Path] = []
    if propose_notes:
        try:
            new_notes = _write_new_notes(cfg, client, final_note, index, log)
        except Exception as exc:  # la propuesta de notas nunca debe romper el resumen
            log(f"[warn] no se pudieron proponer notas nuevas: {exc!r}")
        for path in new_notes:
            log(f"[ok] nota nueva propuesta: {path}")

    return SummaryResult(number, draft_path, len(chunks), len(partials), new_notes)
