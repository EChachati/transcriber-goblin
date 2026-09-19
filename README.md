# Transcriber Goblin 🧹

Colaboración en notas entre amigos + transcripción de llamadas + memoria para IA. 100% self-hosted, sin nubes propietarias.

> **Rama `rust-rework`**: el stack Python (FastAPI + contenedor y-sweet) se está reescribiendo en Rust en un workspace cargo único. El estado de este README refleja la rama; la versión legacy vive en `server/` y `transcriber/`.

## Partes

| # | Módulo | Estado | Qué hace |
|---|--------|--------|----------|
| 1 | `tg-server` (`crates/tg-server`) | **Portada a Rust (Fase 2 cerrada)** | axum: auth por invitación, tokens de doc (HMAC), CRDT **embebido** (`yrs`, sin contenedor), adjuntos por sha256, espejo CRDT→markdown, linker, superficie y-sweet-compatible |
| 2 | `tg-transcriber` (`crates/tg-transcriber`) | pendiente (Fase 3) | Captura micro+escritorio (parec) → whisper-rs + VAD → nota al backend (port de `transcriber/`) |
| 3 | `plugin-obsidian/` | **en progreso** | Plugin TS: editar notas del vault con CRDT realtime (MVP compilado, protocolo verificado e2e) |
| 4 | `goblin-agent/` | pendiente | Resúmenes con Ollama (decisiones, action items, citas) |
| 5 | `tg-linker` (`crates/tg-linker`) | **Portado a Rust (Fase 0+1)** | Keywords → `[[wikilinks]]` automáticos entre notas (~327× más rápido que el port Python) |
| 6 | `webapp/` | pendiente | Web propia: editor, backlinks, grafo, memoria de IA |

## Arquitectura

```
Llamada (Discord/Meet/etc) sonando en la PC
        │  tg-transcriber record
        ├─ parec(micro) ──▶ TU
        └─ parec(monitor) ─▶ REMOTO
                │ whisper-rs + VAD
                ▼
        transcript.json / .md ──publica──▶ tg-server (axum + yrs embebido)
                                              │
        Obsidian plugin / webapp ◀─ws─ sync yjs ◀─tokens─┘
                                                  │
                                    SQLite + adjuntos(sha256) + CRDT .bin + espejo .md
```

## Levantar el backend (tg-server)

Requisitos: Rust toolchain (`cargo`). No hace falta Docker ni y-sweet: el CRDT va embebido.

```bash
# build + run en desarrollo
cargo run -p tg-server &
# por defecto escucha en :8791 con DATA_DIR=./data y ADMIN_TOKEN=dev-admin;
# configurables via env: ADMIN_TOKEN, DATA_DIR, TG_HOST, TG_PORT, TG_PUBLIC_URL, MIRROR_INTERVAL
```

Tests:

```bash
cargo test -p tg-server        # suite de integración completa (toda la API + sync WS real)
cargo test -p tg-linker        # unit del linker
```

Flujo de alta de un amigo (misma API que el legacy):

```bash
# tú (admin): crear invitación
curl -X POST localhost:8791/invites -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"days_valid": 7}'

# tu amigo: canjear código -> recibe su token (mostrar una sola vez)
curl -X POST localhost:8791/auth/redeem -H 'Content-Type: application/json' \
  -d '{"code": "goblin-...", "name": "amigo1"}'

# crear nota y obtener token de conexión (para el cliente)
TOKEN=...   # token del amigo
curl -X POST localhost:8791/docs -H "Authorization: Bearer $TOKEN" -d '{"title":"Mi nota"}'
curl -X POST localhost:8791/docs/<id>/token -H "Authorization: Bearer $TOKEN"
```

### Compatibilidad y-sweet (para el plugin Obsidian)

`tg-server` sirve la misma superficie que y-sweet con el contenedor fuera del camino:

Convención de documentos: cada nota es un `Y.Doc` cuyo `Y.Text` vive bajo la clave `content` (markdown plano). El espejo (`data/mirror/<doc_id>.md`) se refresca cada `MIRROR_INTERVAL` segundos (por defecto 30s) y la snapshot CRDT queda en `data/crdt/<doc_id>.bin`.

| Endpoint | Equivalente y-sweet | Uso |
|---|---|---|
| `POST /doc/new` | `y-sweet` | crear doc (admin), responde `docId` |
| `POST /doc/:id/auth` | `client_token` | devuelve `token` (HMAC del `doc_id`) y `url` `ws://host/d/:id/ws` |
| `GET/PUT /d/:id/as-update` | `as-update` | estado completo del doc (bearer del doc) |
| `POST /d/:id/update` | `update` | aplicar update crudo |
| `GET /d/:id/ws/:ws_id?token=` | ws de y-sweet | sync y-protocols (Step1↔Step2 + updates en directo) |

El peer WS está implementado a mano siguiendo la codificación **yjs** (sincroniza con el plugin a través del proxy `ws://host/d/:id/ws/:uid?token=`).

Nota: en este entorno no hay `node`, así que la validación del roundtrip WS del plugin se replica en Rust dentro de `tg-server/tests/api.rs`.

## Roadmap

El detalle vivo está en `ROADMAP_RUST.md` (Fases 0–4). Fases 0+1 (scaffold+linker) y 2 (server) cerradas; restan Fase 3 (transcriber), Fase 4 (deploy, resolución de `attachment://`, goblin-agent, webapp).