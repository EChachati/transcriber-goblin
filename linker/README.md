# linker

Sistema de keywords → hiperenlaces (`[[wikilinks]]`) entre notas.

## Qué hace

- Extrae keywords de cada nota (keyBERT; fallback TF-IDF/frecuencia si no está instalado).
- Detecta menciones de **títulos** y **aliases** de otras notas en el espejo markdown.
- Si el match es **100% seguro** (alias/título exacto + sin ambigüedad con otra nota) → escribe el `[[wikilink]]` automáticamente en el **CRDT** (fuente canónica).
- Si no es seguro (match incompleto o ambiguo) → guarda una **propuesta** pendiente.
- Los links persisten en y-sweet: el mirror loop los refleja en el espejo `.md`, y no se pierden aunque edites desde Obsidian.

## Aliases

Cada nota puede tener múltiples aliases. Sirven para resolver variaciones:

- Nota "Geralt de Rivia" con alias "Geralt" → el texto "Geralt" en cualquier nota enlaza a "Geralt de Rivia".
- El linker también genera automáticamente **prefijos** (primeras 1..N palabras del título/alias) como variantes candidatas, con tolerance a **acentos**, **mayúsculas** y **plural** (`Notas` → `nota`).

## Endpoints (FastAPI)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/linker/run` | Ejecuta el linker sobre los docs del usuario (aplica automáticos + genera propuestas) |
| `GET`  | `/linker/{doc_id}/keywords?top_n=10` | keywords del espejo de un doc |
| `GET`  | `/linker/{doc_id}/aliases` | lista aliases de un doc |
| `POST` | `/linker/{doc_id}/aliases` | añade alias manual (`{"alias": "..."}`) |
| `DELETE` | `/linker/{doc_id}/aliases` | quita alias (`{"alias": "..."}`) |
| `GET`  | `/linker/proposals?status=pending` | listado de propuestas (por defecto `pending`) |
| `POST` | `/linker/proposals/{id}` | aceptar (`{"action":"apply"}`) o descartar (`{"action":"dismiss"}`) una propuesta |
| `GET`  | `/linker/graph` | nodos (docs) y edges (wikilinks) para el grafo |

Todas requieren `Authorization: Bearer <token>` y respetan permisos `doc_access`.
`POST /linker/run` acepta `?target=crdt|mirror` (por defecto `crdt`; `mirror` útil para tests/scripts sin y-sweet). Las propuestas se devuelven con su `id` (rowid) para poder aplicarlas o descartarlas.

## Dependencia opcional

`keybert` es opcional; se instala con:

```bash
cd server && uv sync --extra linker
```

`extract_keywords` hace fallback a frecuencia simple si keybert no está disponible.

## Notas de implementación

- La lógica vive en `server/app/linker.py` (normalización, matching sobre el espejo). 
- Los endpoints en `server/app/routes_linker.py`.
- Tablas SQL: `aliases` (doc_id, alias, source) y `linker_proposals` (doc_id, target_id, alias, confidence, status).
- El matching trabaja sobre el espejo `.md` (fuente canónica), no sobre el CRDT.
- Tests: `server/tests/test_linker.py` (no requieren y-sweet; insertan docs directamente en SQLite).
