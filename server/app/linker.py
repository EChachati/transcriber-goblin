import re
import unicodedata
from collections import Counter
from pathlib import Path

from .config import settings
from .db import get_db

_WORD_RE = re.compile(r"\b[\wÁ-ú]+\b")
_plural_re = re.compile(r"^(?P<word>.+?)(?P<s>s|es)$")

_STOPWORDS = {
    "el", "la", "los", "las", "de", "del", "a", "al", "y", "o", "u", "en", "con",
    "que", "es", "un", "una", "para", "por", "se", "su", "sus", "lo", "le", "les",
    "como", "mas", "pero", "no", "ni", "esto", "este", "esta", "ese", "esa", "los",
}


def normalize(text: str) -> str:
    """Minúsculas, sin acentos/tildes, y con la 's' final de plural quitada."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = []
    for w in _WORD_RE.findall(text):
        m = _plural_re.match(w)
        if m and len(m.group("word")) > 2:
            w = m.group("word")
        words.append(w)
    return " ".join(words)


def expand_variants(title: str, aliases: list[str]) -> list[str]:
    """Título + aliases + prefijos (primeras 1..N palabras) como variantes normalizadas."""
    out: list[str] = []
    seen: set[str] = set()

    def add(t: str):
        n = normalize(t)
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    for s in [title, *aliases]:
        words = _WORD_RE.findall(s.lower())
        for i in range(1, len(words) + 1):
            add(" ".join(words[:i]))
    return out


def _load_notes(db) -> list[dict]:
    rows = db.execute(
        """
        SELECT d.id, d.title,
               COALESCE((SELECT GROUP_CONCAT(a.alias, '||') FROM aliases a
                         WHERE a.doc_id = d.id), '') AS aliases
        FROM docs d
        """
    ).fetchall()
    notes = []
    for r in rows:
        aliases = [a for a in (r["aliases"] or "").split("||") if a]
        notes.append({"id": r["id"], "title": r["title"], "aliases": aliases})
    return notes


def _tokenize_with_positions(text: str) -> list[dict]:
    """Devuelve tokens con start/end en char-offsets del texto original (case-preservado)."""
    tokens = []
    for m in _WORD_RE.finditer(text):
        tokens.append({"word": m.group(0), "start": m.start(), "end": m.end()})
    return tokens


def _build_span_index(text: str):
    """Mapa normalize(tokens) -> lista de spans (word literal, start, end) en el texto original."""
    index: dict[str, list[dict]] = {}
    for tok in _tokenize_with_positions(text):
        n = normalize(tok["word"])
        index.setdefault(n, []).append(
            {"lit": tok["word"], "start": tok["start"], "end": tok["end"]}
        )
    return index


def _match_variant_in_content(content: str, variant_norm: str):
    """Busca en content (string original) la secuencia de palabras cuya versión
    normalizada = variant_norm. Devuelve (literal_match, start, end) o None."""
    parts = variant_norm.split()
    tokens = _tokenize_with_positions(content)
    keys = [normalize(t["word"]) for t in tokens]
    for i in range(len(tokens) - len(parts) + 1):
        if keys[i : i + len(parts)] == parts:
            span = tokens[i : i + len(parts)]
            return (
                " ".join(t["word"] for t in span),
                span[0]["start"],
                span[-1]["end"],
            )
    return None


def run_linker(db=None, apply_auto: bool = True, target: str = "crdt") -> dict:
    db = db or get_db()
    notes = _load_notes(db)
    results = {"auto": [], "proposals": [], "notes": len(notes)}
    assert target in ("crdt", "mirror")

    read = _read_markdown if target == "crdt" else _read_mirror
    write = _apply_markdown if target == "crdt" else _write_mirror

    # Precompute per-note variants
    notes_data = []
    for n in notes:
        variants = expand_variants(n["title"], n["aliases"])
        exact_variants = {normalize(s) for s in [n["title"], *n["aliases"]] if normalize(s)}
        notes_data.append(
            {"id": n["id"], "title": n["title"], "variants": variants, "exact_variants": exact_variants}
        )

    # Variant -> notes that claim it (for ambiguity)
    variant_owner: dict[str, list[int]] = {}
    for idx, nd in enumerate(notes_data):
        for v in nd["variants"]:
            variant_owner.setdefault(v, []).append(idx)

    for note in notes_data:
        content = read(note["id"])
        if not content.strip():
            continue
        new_content = _link_doc(
            content, note, notes_data, variant_owner, db, apply_auto
        )
        if new_content is not None and new_content != content:
            if apply_auto:
                write(note["id"], new_content)
            results["auto"].append(
                {"doc_id": note["id"], "links": sorted(_extract_wikilinks(new_content) - _extract_wikilinks(content))}
            )

    results["proposals"] = _pending_proposals(db)
    return results


def _link_doc(content, note, notes_data, variant_owner, db, apply_auto):
    used_spans: list[tuple[int, int, str]] = []
    changed = False
    for other in notes_data:
        if other["id"] == note["id"]:
            continue
        for variant in other["variants"]:
            match = _match_variant_in_content(content, variant)
            if match is None:
                continue
            literal, start, end = match
            if any(start < e and s < end for s, e, _ in used_spans):
                continue  # overlaps an already-linked span
            owners = variant_owner.get(variant, [])
            exact = variant in other["exact_variants"]
            unambiguous = len(owners) == 1
            wikilink = f"[[{other['title']}]]"
            if wikilink in content:
                continue
            conf = 1.0 if exact and unambiguous else 0.6 if exact else 0.4
            if conf >= 0.999 and apply_auto:
                content = content[:start] + wikilink + content[end:]
                used_spans.append((start, start + len(wikilink), other["id"]))
                changed = True
                _record_proposal(db, note["id"], other["id"], literal, 1.0, status="applied")
            else:
                _record_proposal(db, note["id"], other["id"], literal, conf, status="pending")
    return content if changed else None


def _write_mirror(doc_id: str, content: str) -> None:
    target: Path = settings.mirror_dir / f"{doc_id}.md"
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def _read_mirror(doc_id: str) -> str:
    target: Path = settings.mirror_dir / f"{doc_id}.md"
    if target.exists():
        return target.read_text(encoding="utf-8")
    return ""


def _apply_markdown(doc_id: str, content: str) -> None:
    """Escribe el contenido al CRDT (fuente canónica) vía y-sweet.

    El mirror loop recogerá después este texto, así los links persisten aunque
    el CRDT se edite desde Obsidian.
    """
    from pycrdt import Doc, Text

    from .ysweet import ysweet_manager

    current = ysweet_manager().get_doc_as_update(doc_id)
    doc = Doc()
    if current:
        doc.apply_update(current)
    text = doc.get("content", type=Text)
    text.clear()
    text.insert(0, content)
    ysweet_manager().update_doc(doc_id, doc.get_update())


def _read_markdown(doc_id: str) -> str:
    """Lee el contenido markdown desde el CRDT (fuente canónica)."""
    from pycrdt import Doc, Text

    from .ysweet import ysweet_manager

    update = ysweet_manager().get_doc_as_update(doc_id)
    if not update:
        return ""
    doc = Doc()
    doc.apply_update(update)
    return str(doc.get("content", type=Text))


def _apply_link(doc_id: str, alias: str, wikilink: str) -> bool:
    """Sustituye una mención (alias) por un wikilink en el CRDT. Devuelve True si cambió algo."""
    content = _read_markdown(doc_id)
    if wikilink in content:
        return False
    token = _match_literal(content, alias)
    if token is None:
        return False
    new_content = content.replace(token, wikilink, 1)
    _apply_markdown(doc_id, new_content)
    return True


def _match_literal(content: str, alias: str) -> str | None:
    """Devuelve la subcadena literal de content que coincide con alias (normalizado,
    tolerante a acentos/plural). None si no hay match."""
    variant = normalize(alias)
    match = _match_variant_in_content(content, variant)
    if match is None:
        return None
    return match[0]


def _record_proposal(db, doc_id, target_id, alias, confidence, status="pending"):
    db.execute(
        """
        INSERT INTO linker_proposals (doc_id, target_id, alias, confidence, reason, status)
        VALUES (?, ?, ?, ?, 'auto-detected', ?)
        ON CONFLICT(doc_id, target_id, alias)
        DO UPDATE SET confidence = excluded.confidence, status = excluded.status
        """,
        (doc_id, target_id, alias, confidence, status),
    )
    db.commit()


def _pending_proposals(db) -> list[dict]:
    rows = db.execute(
        """
        SELECT p.rowid AS id, p.doc_id, p.target_id, p.alias, p.confidence, p.reason, p.status,
               d.title AS target_title
        FROM linker_proposals p JOIN docs d ON d.id = p.target_id
        WHERE p.status = 'pending'
        ORDER BY p.confidence DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _extract_wikilinks(content: str) -> set:
    return set(re.findall(r"\[\[([^\]]+)\]\]", content))


def extract_keywords(text: str, top_n: int = 10) -> list[str]:
    """keyBERT para extraer keywords; fallback TF-IDF/frecuencia simple."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        from keybert import KeyBERT

        kw = KeyBERT(model="all-MiniLM-L6-v2")
        kws = kw.extract_keywords(text, keyphrase_ngram_range=(1, 2), stop_words="spanish", top_n=top_n)
        return [k for k, _ in kws]
    except Exception:
        return _freq_fallback(text, top_n)


def _freq_fallback(text: str, top_n: int) -> list[str]:
    words = [
        w
        for w in _WORD_RE.findall(text.lower())
        if w not in _STOPWORDS and len(w) > 3 and not w.isdigit()
    ]
    return [w for w, _ in Counter(words).most_common(top_n)]
