# Transcriber Goblin 🧹

Colaboración en notas entre amigos + transcripción de llamadas + memoria para IA. 100% self-hosted, sin nubes propietarias.

## Partes

| # | Módulo | Estado | Qué hace |
|---|--------|--------|----------|
| 1 | `server/` | **hecho** | FastAPI: auth por invitación, tokens y-sweet, adjuntos por hash, espejo CRDT→markdown |
| 2 | `transcriber/` | **hecho** | Captura micro+escritorio → faster-whisper GPU + VAD → nota al backend |
| 3 | `plugin-obsidian/` | **en progreso** | Plugin TS: editar notas del vault con CRDT realtime (MVP compilado, protocolo verificado e2e) |
| 4 | `goblin-agent/` | pendiente | Resúmenes con Ollama (decisiones, action items, citas) |
| 5 | `linker/` | pendiente | Keywords → `[[wikilinks]]` automáticos entre notas |
| 6 | `webapp/` | pendiente | Web propia: editor, backlinks, grafo, memoria de IA |

## Arquitectura

```
Llamada (Discord/Meet/etc) sonando en la PC
        │  tg-transcriber record
        ├─ parec(micro) ──▶ TU
        └─ parec(monitor) ─▶ REMOTO
                │ faster-whisper GPU + VAD
                ▼
        transcript.json / .md ──publica──▶ server (FastAPI)
                                              │
        Obsidian plugin / webapp ◀─ws─ y-sweet (CRDT) ◀─tokens─┘
                                                  │
                                    SQLite + adjuntos(sha256) + espejo .md
```

## Parte 1: levantar el backend

Requisitos: Docker, uv.

```bash
# 1. Genera credenciales de y-sweet (una sola vez)
docker run --rm ghcr.io/jamsocket/y-sweet:latest y-sweet gen-auth --json
#    -> copia private_key y server_token a .env

cp .env.example .env
#    edita: YSWEET_PRIVATE_KEY, YSWEET_SERVER_TOKEN, ADMIN_TOKEN
#    para desarrollo local agrega ademas:
#    YSWEET_URL=ys://<server_token>@localhost:7700

# 2. Servidor CRDT con persistencia en volumen
docker compose up -d ysweet

# 3. Tests (los de docs requieren y-sweet arriba)
cd server && uv sync && uv run pytest

# 4. API en :8000
uv run uvicorn app.main:app --reload
```

En VPS (compose completo): `docker compose up -d --build` — la API queda en `:8000` y `YSWEET_PUBLIC_URL` debe apuntar a la URL pública (`wss://sync.tudominio.com`).

Flujo de alta de un amigo:

```bash
# tú (admin): crear invitación
curl -X POST localhost:8000/invites -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"days_valid": 7}'

# tu amigo: canjear código -> recibe su token (mostrar una sola vez)
curl -X POST localhost:8000/auth/redeem -H 'Content-Type: application/json' \
  -d '{"code": "goblin-...", "name": "amigo1"}'

# crear nota y obtener token de conexión y-sweet (para el cliente)
TOKEN=...   # token del amigo
curl -X POST localhost:8000/docs -H "Authorization: Bearer $TOKEN" -d '{"title":"Mi nota"}'
curl -X POST localhost:8000/docs/<id>/token -H "Authorization: Bearer $TOKEN"
```

Convención de documentos: cada nota es un Y.Doc cuyo `Y.Text` vive bajo la clave `content` (markdown plano). El espejo (`data/mirror/<doc_id>.md`) se refresca cada 30s y es la fuente canónica para búsqueda, embeddings y la futura IA.
