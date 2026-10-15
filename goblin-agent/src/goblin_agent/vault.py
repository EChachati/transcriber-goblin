"""Acceso al vault de Obsidian, con la regla sagrada: NUNCA tocar el original.

Todo el trabajo (leer referencias y escribir borradores) ocurre sobre una copia
de trabajo. ``assert_safe_paths`` y ``safe_write`` abortan si por error se
intentara escribir dentro del vault original.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Carpetas del vault que sirven como memoria de referencia.
REFERENCE_FOLDERS = ["NPCs", "Lugares", "Objetos", "Grupos", "Misiones", "Glosario"]
ROSTER_FOLDER = "Jugadores"
SESSIONS_FOLDER = "Sesiones"

# Estructura propia del agente dentro del vault de trabajo.
CONFIG_DIR = "_goblin"
DRAFTS_DIR = "_goblin/borradores"
NEW_NOTES_DIR = "_goblin/borradores/nuevas-notas"
TEMPLATE_NAME = "plantilla.md"

_SESSION_RE = re.compile(r"sesi[oó]n\s+(\d+)", re.IGNORECASE)
_STOPWORDS = {
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "y",
    "von",
    "van",
    "der",
    "un",
    "una",
    "san",
    "familia",
    "casa",
    "gran",
}


# --------------------------------------------------------------------------- #
# Utilidades de texto
# --------------------------------------------------------------------------- #
def normalize(text: str) -> str:
    """Minúsculas sin acentos, para comparar nombres de forma robusta."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def strip_noise(text: str) -> str:
    """Elimina imágenes base64 / data-URIs y ruido que no aporta al resumen."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if stripped.startswith("data:") or "base64," in stripped:
            continue
        # Líneas larguísimas sin espacios = blob de datos (imagen embebida).
        if len(stripped) > 1200 and " " not in stripped[40:200]:
            continue
        out.append(line)
    return "\n".join(out)


def _variants(stem: str) -> list[str]:
    """Variantes de un nombre de nota para detectar menciones (nombre completo + tokens)."""
    variants = [stem]
    for token in re.split(r"[\s,]+", stem):
        token = token.strip()
        if len(token) >= 4 and token.lower() not in _STOPWORDS:
            variants.append(token)
    return variants


def sanitize_filename(name: str) -> str:
    name = name.strip().replace("/", "-").replace("\\", "-")
    name = re.sub(r"[^\w\s\.\-áéíóúÁÉÍÓÚñÑ]", "", name)
    return re.sub(r"\s+", " ", name).strip() or "sin-titulo"


# --------------------------------------------------------------------------- #
# Guardrails y copia de trabajo
# --------------------------------------------------------------------------- #
def assert_safe_paths(source: Path, work: Path) -> None:
    """Comprueba que el vault de trabajo NO esté anidado con el original."""
    s = source.resolve()
    w = work.resolve()
    if s == w:
        raise ValueError(f"El vault de trabajo y el original son el mismo: {s}")
    if s in w.parents:
        raise ValueError(
            f"El vault de trabajo ({w}) está dentro del original ({s}). Abortando."
        )
    if w in s.parents:
        raise ValueError(
            f"El original ({s}) está dentro del vault de trabajo ({w}). Abortando."
        )


def safe_write(path: Path, text: str, source: Path) -> Path:
    """Escribe ``text`` en ``path`` salvo que esté dentro del vault original."""
    p = path.resolve()
    s = source.resolve()
    if p == s or s in p.parents:
        raise ValueError(f"Escritura bloqueada dentro del vault original: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def refresh_work_vault(source: Path, work: Path, log=print) -> None:
    """Sincroniza el contenido del original en la copia, excluyendo ``_goblin``."""
    assert_safe_paths(source, work)
    if not source.is_dir():
        raise FileNotFoundError(f"Vault origen no encontrado: {source}")
    work.mkdir(parents=True, exist_ok=True)
    (work / ".obsidian").mkdir(exist_ok=True)  # para poder abrirlo como vault

    if shutil.which("rsync"):
        log(f"[vault] rsync '{source}' -> '{work}' (excluye _goblin/ y .obsidian/)")
        subprocess.run(
            [
                "rsync",
                "-a",
                "--delete",
                "--exclude",
                "_goblin/",
                "--exclude",
                ".obsidian/",
                f"{source}/",
                f"{work}/",
            ],
            check=True,
        )
        return

    log("[vault] rsync no disponible; copiando con shutil")
    for entry in source.iterdir():
        if entry.name in {CONFIG_DIR, ".obsidian"}:
            continue
        dest = work / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, dest)


# --------------------------------------------------------------------------- #
# Índice del vault
# --------------------------------------------------------------------------- #
@dataclass
class VaultIndex:
    root: Path
    roster: list[str] = field(default_factory=list)
    notes: dict[str, Path] = field(default_factory=dict)
    variants: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, work: Path) -> "VaultIndex":
        idx = cls(root=work)
        for folder in REFERENCE_FOLDERS + [ROSTER_FOLDER, "Reglas"]:
            base = work / folder
            if not base.is_dir():
                continue
            for md in sorted(base.rglob("*.md")):
                idx.notes[md.stem] = md
                if folder == ROSTER_FOLDER:
                    idx.roster.append(md.stem)
        idx.roster = sorted(set(idx.roster))
        idx._build_variants()
        return idx

    def _build_variants(self) -> None:
        for stem in self.notes:
            for variant in _variants(stem):
                self.variants.setdefault(normalize(variant), []).append(stem)

    # -- menciones / referencias ------------------------------------------- #
    def find_mentions(self, text: str, limit: int) -> list[str]:
        norm = normalize(text)
        stems: list[str] = []
        matched: set[str] = set()
        # Variantes largas primero: preferimos nombres específicos.
        for variant in sorted(self.variants, key=len, reverse=True):
            if variant in matched:
                continue
            if re.search(rf"\b{re.escape(variant)}\b", norm):
                matched.add(variant)
                for stem in self.variants[variant]:
                    if stem not in stems:
                        stems.append(stem)
            if len(stems) >= limit:
                break
        return stems[:limit]

    def read_note(self, stem: str, max_chars: int) -> str:
        path = self.notes.get(stem)
        if not path:
            return ""
        text = strip_noise(path.read_text(encoding="utf-8")).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n[...recortado...]"
        return text

    def references_for(
        self, text: str, max_notes: int, max_chars: int, max_total: int
    ) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        used = 0
        for stem in self.find_mentions(text, max_notes):
            note = self.read_note(stem, max_chars)
            if not note:
                continue
            if out and used + len(note) > max_total:
                break
            out.append((stem, note))
            used += len(note)
        return out

    # -- sesiones ---------------------------------------------------------- #
    def session_numbers(self) -> list[int]:
        base = self.root / SESSIONS_FOLDER
        numbers: list[int] = []
        if base.is_dir():
            for md in base.glob("*.md"):
                m = _SESSION_RE.search(md.stem)
                if m:
                    numbers.append(int(m.group(1)))
        return sorted(set(numbers))

    def next_session_number(self) -> int:
        numbers = self.session_numbers()
        return max(numbers) + 1 if numbers else 1

    def session_file(self, number: int) -> Path | None:
        base = self.root / SESSIONS_FOLDER
        if not base.is_dir():
            return None
        for md in base.glob("*.md"):
            m = _SESSION_RE.search(md.stem)
            if m and int(m.group(1)) == number:
                return md
        return None

    def previous_session_text(self, number: int, max_chars: int) -> str | None:
        path = self.session_file(number - 1)
        if not path:
            return None
        text = strip_noise(path.read_text(encoding="utf-8")).strip()
        return text[:max_chars]

    def style_examples(
        self, number: int, count: int, max_chars: int
    ) -> list[tuple[str, str]]:
        all_numbers = self.session_numbers()
        # Evitamos la sesión inmediatamente anterior: ya se pasa como recordatorio.
        numbers = [n for n in all_numbers if n < number and n != number - 1]
        if not numbers:
            numbers = [n for n in all_numbers if n < number]
        numbers = sorted(numbers, reverse=True)[:count]
        examples: list[tuple[str, str]] = []
        for n in numbers:
            path = self.session_file(n)
            if path:
                text = strip_noise(path.read_text(encoding="utf-8")).strip()
                examples.append((path.stem, text[:max_chars]))
        return examples

    # -- plantilla --------------------------------------------------------- #
    def vault_template(self) -> str | None:
        path = self.root / CONFIG_DIR / TEMPLATE_NAME
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return None
