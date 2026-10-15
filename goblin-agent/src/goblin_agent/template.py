"""Plantilla del resumen (editable a mano).

La plantilla define la estructura de la nota final. El agente respeta los
encabezados y añade tantas secciones de tema como haga falta.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_TEMPLATE = """\
<!-- Plantilla del resumen de sesión. Edítala a tu gusto.
     El agente respeta estos encabezados y añade una sección por cada tema
     importante (renombra "Tema" y duplica la sección las veces necesarias).
     Usa [[Nombre]] para enlazar notas del vault. -->

# Anteriormente en {{campana}}

# Tema

# Próximos pasos

# Cabos sueltos
"""


def load_template(path: str | Path | None = None) -> str:
    """Devuelve la plantilla del archivo indicado, o la plantilla por defecto."""
    if path:
        p = Path(path).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return DEFAULT_TEMPLATE


# Títulos que, en la plantilla, representan "aquí van las secciones de tema".
_TOPIC_TITLES = {
    "tema",
    "temas",
    "tema de la sesión",
    "tema de la sesion",
    "sección",
    "seccion",
    "secciones",
}


def parse_template(template: str) -> tuple[str | None, list[str]]:
    """Separa la plantilla en (encabezado del recap, secciones fijas).

    * El encabezado del recap es el primer encabezado que contenga "anterior"
      o "recap" (p. ej. ``# Anteriormente en Curse of Strahd``).
    * Las secciones fijas son el resto de encabezados, excluyendo los
      marcadores de tema (``# Tema``, ``# Sección``…), que el agente sustituye
      por tantas secciones temáticas como haga falta.
    """
    recap_heading: str | None = None
    fixed: list[str] = []
    for line in template.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        title = stripped.lstrip("#").strip().lower()
        if recap_heading is None and ("anterior" in title or "recap" in title):
            recap_heading = stripped
            continue
        if title in _TOPIC_TITLES:
            continue
        fixed.append(stripped)
    if not fixed:
        fixed = ["# Próximos pasos", "# Cabos sueltos"]
    return recap_heading, fixed
