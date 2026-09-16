# HANDOFF — Transcriber Goblin

> Documento de continuidad. Última actualización: 2026-08-23.
> Objetivo: que cualquier persona (o sesión futura) pueda retomar el proyecto sin re-descubrir nada.

---

## 1. Qué es Transcriber Goblin

Sistema self-hosted para **convertir reuniones/llamadas en notas colaborativas vivas**:

1. Se transcribe lo que se habla en la PC (llamadas de Discord, Meet, etc.).
2. Las transcripciones llegan como notas a un backend propio con **edición colaborativa realtime** (CRDT).
3. Un agente local (Ollama) genera resúmenes: decisiones, action items, citas.
4. Un linker conecta conceptos entre notas con `[[wikilinks]]` automáticos.
5. Todo se consume desde un plugin de Obsidian y una futura web app con grafo + "memoria" para IA.

Sin monetización, sin nubes de terceros: servidor propio (por ahora local, luego VPS), amigos invitados por códigos.

## 2. Estado por módulo

| # | Módulo | Estado | Notas |
|---|--------|--------|-------|
| 1 | `server/` | ✅ **terminado y verificado e2e** | FastAPI + SQLite + y-sweet (CRDT) |
| 2 | `transcriber/` | ✅ **terminado y verificado e2e** | micro+escritorio → whisper GPU → nota publicada |
| 3 | `plugin-obsidian/` | 🟨 **MVP compilado + protocolo verificado e2e** | falta probar dentro de Obsidian y pulir presencia/adjuntos |
| 4 | `goblin-agent/` | ⬜ pendiente | resúmenes Ollama sobre el espejo `.md` |
| 5 | `linker/` | ⬜ pendiente | keywords → wikilinks |
| 6 | `webapp/` | ⬜ pendiente | editor, backlinks, grafo, memoria IA |

## 3. Arquitectura implementada (partes 1–2)

```
Llamada sonando en la PC
        │  tg-transcriber record
        ├─ parec(micro) ──▶ TU
        └─ parec(monitor) ─▶ REMOTO          ← separación de hablantes gratis
                │ faster-whisper small int8_float16 (RTX 3050 Ti) + VAD Silero
                ▼
        transcript.md ──publica──▶ server FastAPI :8000
                                          │ crea doc + escribe Y.Doc vía HTTP
        Obsidian plugin / webapp ◀──ws── y-sweet :7700 (CRDT)
                                          │
                          SQLite + adjuntos(sha256) + espejo data/mirror/<id>.md
```

Flujo completo demostrado en esta máquina: voz real → WAVs distintos por fuente → 19 segmentos etiquetados TU/REMOTO → nota publicada (`doc=8c734c33aae929b6`) → espejo markdown escrito por el worker del server.

## 4. Cómo levantar todo

```bash
cd ~/Projects/transcriber-goblin

# 1) Infra (contenedor y-sweet; NO tiene entrypoint, el command va completo)
docker compose up -d

# 2) API (desde server/)
uv sync && uv run uvicorn app.main:app --port 8000   # lee .env del repo raíz
# para desarrollo con espejo rápido:
MIRROR_INTERVAL=10 DATA_DIR=/tmp/opencode/tg-e2e uv run uvicorn app.main:app --port 8000

# 3) Tests backend (requieren y-sweet arriba; si no, saltan)
cd ../server && uv run pytest -q            # 6 passed esperado

# 4) Transcriptor
cd ../transcriber && uv sync
uv run tg-transcriber devices               # verifica MIC/MONITOR detectados
source /tmp/opencode/tg-env                 # TG_TOKEN de prueba (usuario goblin-cli)
export TG_API_URL=http://localhost:8000
uv run tg-transcriber record --name "Prueba" --language es   # Ctrl+C termina y publica
```

Sesiones locales: `~/.local/share/tg-transcriber/sessions/<fecha>_<nombre>/`
Espejo canónico: `<DATA_DIR>/mirror/<docId>.md`

## 5. Credenciales y datos (dev)

- `.env` (repo raíz): `YSWEET_PRIVATE_KEY`, `YSWEET_SERVER_TOKEN`, `ADMIN_TOKEN=admin-dev-e2e`, `YSWEET_URL`. Generados con `y-sweet gen-auth --json`.
- Token de usuario de pruebas: `/tmp/opencode/tg-env` → usuarios `goblin-cli` y `plugin-test` (⚠️ `/tmp` se borrea al reiniciar; si falta, crear nuevo invite: `curl -X POST localhost:8000/invites -H "Authorization: Bearer $ADMIN_TOKEN"` y redimirlo).
- DB dev: `/tmp/opencode/tg-e2e/goblin.db`. Docs de prueba: `f3a8bd1e1e0740bb` ("Nota desde el plugin", tiene contenido CRDT + espejo).

## 6. Decisiones técnicas y trampas descubiertas (¡no re-investigar!)

### Backend / y-sweet
- Imagen `ghcr.io/jamsocket/y-sweet:latest` **sin entrypoint**: el comando debe empezar con `y-sweet serve ...`.
- Auth: `gen-auth --json` → `private_key` (arranque del server) + `server_token` (username del connection string `ys://TOKEN@host:7700`).
- Rutas HTTP reales (v0.9.x): `/doc/new`, `/doc/{id}/auth` (**404 si el doc no existe** → recrear token), `/d/{id}/as-update` (GET estado CRDT), `/d/{id}/update` (POST bytes crudos).
- Client token JSON: `{url, baseUrl, docId, token, authorization}`; `baseUrl = ws://host/d/{docId}` (cambiar ws→http para REST). El campo `url` apunta al websocket.
- **Se descartó `y-sweet-sdk` y `ypy-websocket`**: conflicto de versiones con `pycrdt`. Hay cliente HTTP propio minimalista (~60 líneas, httpx) en `server/app/ysweet.py`.
- El espejo usa **polling HTTP as-update** + decode pycrdt: sin websockets, simple y robusto.
- Docs vacíos se GCean sin checkpoint → el endpoint de tokens los vuelve a crear (comportamiento ya manejado).
- FastAPI arranca con `docs_url=None, redoc_url=None`: GET /docs colisionaba con Swagger UI.

### Transcriptor / audio
- `sounddevice`/PortAudio **no expone monitores de PipeWire** → se usa `parec` nativo (PulseAudio-compat).
- Bug resuelto: dos `pw-record --target <nombre>` concurrentes desde Python caen en la MISMA fuente (archivos byte-idénticos). Con `parec -d <fuente>` los targets resuelven independientes. **Usar siempre parec.**
- CUDA para faster-whisper: deps pip `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` (marcador linux) + pre-carga ctypes de `site-packages/nvidia/*/lib/*.so*` antes de importar ctranslate2 (`stt.py::_preload_cuda_libs`). Cambiar LD_LIBRARY_PATH a runtime NO funciona. Verificado `('cuda', 'int8_float16')` en RTX 3050 Ti 4GB.
- `SIGINT` al wrapper `uv run` no llega al proceso python → ejecutar directamente `.venv/bin/tg-transcriber`.

### Entorno / shell (lecciones de esta sesión)
- `pkill -f <patrón>` puede matar el propio shell si el patrón coincide con su cmdline → usar `fuser -k PUERTO/tcp`.
- Procesos de fondo: desprender bien `( cmd > log 2>&1 & )`; pw-play/pw-record heredan stdout y cuelgan el tool si no se redirige.
- Python del sistema 3.14; proyectos fijados con uv a **3.12**.
- Lanzar uvicorn directo (`.venv/bin/uvicorn`) **NO carga `.env`**: era `uv run` quien lo hacía. Exportar a mano (`set -a; source ../.env; set +a`) o el server usa defaults (`dev-admin`, `ys://devkey@...`) y falla auth contra y-sweet.

### Plugin Obsidian / cliente web
- Repo upstream movido: es **jamsocket/y-sweet** (antes drifting-in-space). Rutas reales v0.9: `/doc/new`, `/doc/{id}/auth`, `/d/{id}/as-update`, `/d/{id}/update` y websocket en **`/d/:doc_id/ws/:doc_id2` — el docId va DOS veces**. El campo `url` del client token llega como `…/d/<id>/ws` (corto): normalizar agregando `/<docId>` antes de conectar (lo hace `GoblinApi.websocketUrl()`).
- El paquete npm `obsidian` fija peers exactos de `@codemirror/*` que chocan con las versiones de otros paquetes → `.npmrc` con `legacy-peer-deps=true` en `plugin-obsidian/`.
- En Obsidian las peticiones HTTP van con `requestUrl()` (viaja por Electron main, sin CORS); los websockets no tienen CORS. El middleware CORS añadido al FastAPI es para la futura webapp.
- `@codemirror/*`, `@lezer/*`, `obsidian`, `electron`, `moment` quedan como externals de esbuild: Obsidian los provee en runtime (duplicarlos rompe `instanceof`).
- Verificación e2e del protocolo CRDT sin GUI: `plugin-obsidian/scripts/ws-e2e-test.mjs <ws-url-con-token>` (sync step1/2 + update + relectura). Pasada 2026-08-24 OK: escribe → persiste → espejo lo recoge.

### Hardware del usuario
- ⚠️ Micrófono interno **ALC294 entrega señal saturada en DC** (ruido a plena escala en cualquier formato s16/s32). Es problema de driver/config del portátil, NO del código. Pipeline funciona igual; segmentos TU saldrán sucios hasta revisar `alsamixer` F4 (puerto "Internal Microphone") o pavucontrol. Pendiente de arreglar por el dueño de la laptop.
- PipeWire 1.6.8; monitores no aparecen en `pw-dump` hasta que algo captura de ellos (se crean bajo demanda).

## 7. Problemas abiertos

1. **Micrófono ALC294 saturado** (ver arriba) — configuración del sistema, fuera del código.
2. **Repo aún no inicializado en git** — primer paso al retomar: `git init` + `.gitignore` (`.env`, `data/`, `.venv`, sesiones) + commit inicial.
3. Token de prueba vive en `/tmp` (efímero).
4. Diarización fina entre varios hablantes REMOTOS quedó postergada a propósito (hoy solo TU vs REMOTO); evaluar pyannote cuando haga falta.

## 8. Próximos pasos (orden sugerido)

### Parte 3 — Plugin Obsidian (en curso)
- ✅ Hecho: scaffold TS (esbuild + tsc), settings tab, `GoblinApi` (`requestUrl`), provider websocket propio (~250 líneas, adaptado de y-websocket MIT), vista lista de notas, vista editor CodeMirror6 + `yCollab`, protocolo verificado e2e contra y-sweet real.
- ⬜ Falta: instalar en un vault y probar en la GUI de Obsidian; presencia con nombre/color por usuario en RemoteSelections; adjuntos vía `/attachments`; modal propio para "nueva nota" (hoy `window.prompt`).
- El fork de `~/Projects/Relay` resultó demasiado acoplado (editorContext, merge-hsm): se usa el paquete npm oficial `y-codemirror.next` (mismo origen MIT). Relay sirve solo como referencia de patrón.

### Parte 4 — goblin-agent
- Lee `data/mirror/<id>.md` (o endpoint nuevo `GET /docs/{id}/markdown`), llama Ollama local, agrega sección `## Resumen` escribiendo al CRDT vía `/update`.
- Prompt objetivo: decisiones, action items con responsable, temas abiertos, citas textuales relevantes.

### Parte 5 — linker · ### Parte 6 — webapp
- Linker: extraer keywords (keyBERT local u Ollama) → insertar `[[wikilinks]]` resolviendo títulos existentes vía API.
- Webapp: servir estático desde FastAPI o Caddy; editor CRDT en browser; grafo de backlinks; base de la futura "memoria" para agentes IA.

### Infra (cuando haya VPS)
- Caddy TLS inverso a :7700 (ws) y :8000; regenerar credenciales y-sweet productivas; backups de SQLite + adjuntos; `systemd` units o compose remoto.

## 9. Archivos clave

| Archivo | Qué es |
|---|---|
| `README.md` | visión, roadmap, setup general |
| `docker-compose.yml` + `.env` | infra y-sweet + secretos dev |
| `server/app/main.py` | FastAPI (monta rutas + mirror loop) |
| `server/app/ysweet.py` | cliente HTTP propio hacia y-sweet |
| `server/app/{db,auth,routes_docs,routes_attachments,mirror}.py` | dominio del backend |
| `transcriber/src/tg_transcriber/capture.py` | parec ×2 (TU/REMOTO) |
| `transcriber/src/tg_transcriber/stt.py` | faster-whisper + pre-carga CUDA |
| `transcriber/src/tg_transcriber/session.py` | orquestación grabación→transcripción |
| `transcriber/src/tg_transcriber/publish.py` | render markdown + subida adjuntos/nota |
| `plugin-obsidian/src/{main,api,provider,docsView,docEditor}.ts` | plugin: entry+settings, API REST, provider y-sweet, vistas |
| `plugin-obsidian/scripts/ws-e2e-test.mjs` | prueba del protocolo CRDT sin GUI |
| `~/Projects/Relay` | repo referencia (patrón provider; código demasiado acoplado para copiar) |
