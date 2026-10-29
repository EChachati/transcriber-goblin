# HANDOFF — Transcriber Goblin

> Documento de continuidad. Última actualización: 2026-10-15.
> Objetivo: que cualquier persona (o sesión futura) pueda retomar el proyecto sin re-descubrir nada.

> **Estado actual (2026-10-15):** el grueso del trabajo pendiente está hecho.
> `transcriber` gana **diarización** de hablantes (pyannote → etiquetas `S1`…`Sn`)
> y un modo offline (`tg-transcriber file`) para transcribir VODs ya grabados;
> `goblin-agent` ya **resume transcripciones diarizadas** (limpieza de etiquetas,
> prompts adaptativos, `--speaker-names`). El **VOD completo ya se ejecutó y
> verificó** (S11, 3 h 55 m → borrador real; ver §11). Queda decidir sobre
> `drop_verbatim_lines`. Ver §10 (Registro de avance).

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
| 2 | `transcriber/` | ✅ **terminado y verificado e2e** | micro+escritorio → whisper GPU → nota publicada; + diarización (pyannote) y modo `file` para VODs |
| 3 | `plugin-obsidian/` | 🟨 **MVP compilado + protocolo verificado e2e** | falta probar dentro de Obsidian y pulir presencia/adjuntos |
| 4 | `goblin-agent/` | 🟨 **en progreso (e2e ok)** | resúmenes Ollama sobre el espejo `.md`; soporta transcripciones diarizadas (`S1`…`Sn`) |
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

### Diarización (pyannote → `S1`…`Sn`)
- Modelo: `pyannote/speaker-diarization-community-1` (open source) vía `pyannote.audio` 4.0.7. Requiere `HF_TOKEN` (aceptar condiciones en HuggingFace). El token se busca en: argumento → env (`HF_TOKEN`/`HUGGING_FACE_HUB_TOKEN`/`HUGGINGFACE_TOKEN`) → `transcriber/.env` → `.env` raíz.
- ⚠️ **`transcriber/.env` NO se sube** (tiene el `HF_TOKEN`): está ignorado por `.gitignore` (`.env`), pero conviene no `git add -A` a ciegas.
- Se usa la diarización **exclusiva** (`exclusive_speaker_diarization`) para alinear mejor con el STT; los turnos se limpian (`drop_short_turns` < 0.20 s, `merge_adjacent` < 0.25 s) y se renombran `S1..Sn` por orden de primera aparición.
- `assign_speakers` cruza segmentos de whisper con los turnos: si un segmento abarca varios turnos se parte. Con `word_timestamps=True` se reparte palabra a palabra; sin él, de forma proporcional a la duración.
- GPU: pyannote usa CUDA si hay; si `.to(cuda)` falla cae a CPU automáticamente (`TG_DIARIZE_DEVICE` fuerza el dispositivo). En la RTX 3050 Ti (4 GB) hay que **descargar los modelos de Ollama** (`keep_alive: 0`) antes de diarizar o compiten por la VRAM.
- Rendimiento medido (12 min de audio, small): STT 20 s + diarización 40 s. La diarización de 4 h se estima en ~13 min.
- **Sobre-segmenta**: en un tramo con ~5-6 voces reales llegó a detectar 7 (`S1..S7`); parte de los turnos son falsos cambios. Es esperable sin `--speakers` exacto.

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
2. **Repo ya inicializado en git** (hecho); `.gitignore` cubre `.env`, `data/`, `.venv`, sesiones.
3. Token de prueba vive en `/tmp` (efímero).
4. ~~Diarización fina entre varios hablantes REMOTOS postergada~~ → **hecha** (pyannote). El nº de hablantes varía por sesión (S11 dio 8 de forma correcta: 5 jugadores + DM + un saludo + 2 cameos); se puede fijar `--speakers` cuando se conozca el número exacto.
5. `summarize.py::drop_verbatim_lines` está definido pero **no se usa** (se añadió para quitar de las viñetas líneas copiadas literalmente del tramo; el `borrador` del tramo E2E aún mostraba alguna). Loose end a decidir: engancharlo tras el *map* o borrarlo.

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
| `transcriber/src/tg_transcriber/stt.py` | faster-whisper (+ timestamps por palabra) + pre-carga CUDA |
| `transcriber/src/tg_transcriber/media.py` | ffmpeg → WAV, `mix_wavs` (micro+escritorio), `wav_duration` |
| `transcriber/src/tg_transcriber/diarize.py` | pyannote → `SpeakerTurn`, `S1..Sn`, `assign_speakers`/`split_segment` |
| `transcriber/src/tg_transcriber/session.py` | orquestación grabación→transcripción (y diarización de la mezcla) |
| `transcriber/src/tg_transcriber/file_job.py` | pipeline offline `tg-transcriber file` (VOD → transcript.json) |
| `transcriber/src/tg_transcriber/publish.py` | render markdown + subida adjuntos/nota |
| `goblin-agent/src/goblin_agent/{cli,summarize,transcript,vault}.py` | CLI, map-reduce + limpieza, lectura de transcripts (`S#`, `--speaker-names`), índice del vault |
| `plugin-obsidian/src/{main,api,provider,docsView,docEditor}.ts` | plugin: entry+settings, API REST, provider y-sweet, vistas |
| `plugin-obsidian/scripts/ws-e2e-test.mjs` | prueba del protocolo CRDT sin GUI |
| `~/Projects/Relay` | repo referencia (patrón provider; código demasiado acoplado para copiar) |

## 10. Registro de avance (rama `diarizacion`, 2026-10-15)

Trabajo de esta iteración (sin commit, a petición del usuario):

### transcriber
- **`media.py`** (nuevo): `extract_audio_to_wav` (ffmpeg), `mix_wavs` (suma WAVs mono con saturación y relleno) y `wav_duration`.
- **`diarize.py`** (nuevo): `SpeakerTurn`, `_huggingface_token`, `relabel_by_first_appearance` (`S1..Sn`), `merge_adjacent`, `drop_short_turns`, `Diarizer` (pyannote community-1, cuda→cpu, `num/min/max_speakers`, usa `exclusive_speaker_diarization`), `assign_speakers`, `split_segment` (reparto proporcional o por palabras), `_split_by_words`, `nearest_turn`.
- **`stt.py`**: `Segment.words` opcional + `to_dict()` (sin palabras); `transcribe_file(..., word_timestamps=False)`.
- **`session.py`**: flags `diarize/num/min/max_speakers`; transcribe con `word_timestamps` si diariza; `_apply_diarization` (mezcla mic+escritorio → diariza → reetiqueta `S1..Sn`); serializa con `to_dict`.
- **`file_job.py`** (nuevo): `process_file` offline (extrae audio → transcribe → diariza → `transcript.json`).
- **`cli.py`**: refactor `_speaker_args`/`_emit_outputs`; flags `--diarize --speakers --min-speakers --max-speakers` en `record`; nuevo subcomando `file`.
- **`pyproject.toml`**: `pyannote.audio` añadido (instalado: pyannote.audio 4.0.7, torch 2.14.1+cu130).
- **Tests nuevos**: `test_diarize.py` (12, deterministas) y `test_media.py` (5). **20 passed** en total.

### goblin-agent
- **`transcript.py`**: regex `_MD_LINE` con minutos `\d{1,3}` (corrige el bug de >99 min; el VOD dura 3 h 55 m); `parse_speaker_names` y `apply_speaker_names` (`S1=Nombre`).
- **`summarize.py`**: regexes extendidas con `S\d{1,3}` (`_NOTE_SPEAKER_RE`, `_SINGLE_LINE_RE`, `_BARE_SINGLE_RE`); `clean_notes` con sujeto neutro «Uno de los jugadores»; `salient_lines` quita la etiqueta `S#`; `describe_speakers` + `build_map_prompt` adaptativos; **`speaker_note`** y `build_sections_prompt` adaptativos (ya no mencionan solo REMOTO/REMOTE/TU); **`is_denied_proposal`** + `_PROPOSE_DENY_RE` (rechaza `S1..Sn` y colectivos como entidad nueva) y `build_propose_prompt` avisa de no proponerlos.
- **`cli.py`/`summarize.run`**: nuevo flag `--speaker-names 'S1=DM,S2=Alicia'` (usa `parse_speaker_names`/`apply_speaker_names`).
- **Tests nuevos**: `tests/test_speaker_labels.py` (18, deterministas). **64 passed** en total.

### Verificación
- **Tests**: transcriber **20 passed**; goblin-agent **64 passed**.
- **E2E tramo 5-15 min**: `tg-transcriber file` sobre 12 min de `S11.mp4` (`--min-speakers 4 --max-speakers 8`) → 185 segmentos, 7 hablantes `S1..S7`; `goblin-agent summarize` (qwen2.5:7b) → borrador sin etiquetas `S#` en el cuerpo (se limpian en `clean_notes`). Ficheros en `/tmp/opencode/S11/e2e_med/` y `/tmp/opencode/S11/e2e_summary/`.
- **VOD completo**: ver §11.

### Pendiente / a decidir
- Decidir qué hacer con `drop_verbatim_lines` (enganchar o borrar).
- **NO se ha hecho commit** de esta iteración.

## 11. Ejecución del VOD completo (S11)

✅ **Ejecutado y verificado** (2026-10-15). Fuente: `~/Downloads/S11.mp4` (234.9 min ≈ 3 h 55 m).

### Transcripción + diarización

```bash
cd transcriber
TG_OBSIDIAN_VAULT= uv run tg-transcriber file ~/Downloads/S11.mp4 \
  --out ~/s11-full --model small --language es \
  --min-speakers 4 --max-speakers 8 --name "S11"
```

- Log: `/tmp/opencode/S11/full_run.log`. Salida: `~/s11-full/transcript.json` + `~/s11-full/transcript.md`.
- Audio extraído en 15.3 s (WAV borrado después para liberar 451 MB).
- STT (`small`, cuda int8_float16): **2829 segmentos base en 622.4 s** (~10.4 min).
- Diarización (pyannote community-1, cuda): **3981 turnos en 835.2 s** (~13.9 min) → **8 hablantes `S1..S8`** tras `relabel_by_first_appearance`.
- Tras `split_segment`: **3674 segmentos** finales. Reparto por hablante (campo `source`):
  `S2:731 · S5:722 · S1:525 · S6:460 · S8:442 · S3:413 · S4:276 · S7:105`.
- Nota: los **8 hablantes son correctos** en esta sesión: a los 5 jugadores + DM se sumaron una persona que pasó a saludar y dos cameos de personajes que no eran de la party. No es sobre-segmentación (confirmado por el usuario).

### Resumen (goblin-agent)

```bash
cd goblin-agent
uv run goblin-agent summarize ~/s11-full/transcript.json --session 11 \
  --no-refresh --skip-minutes 35 --skip-range 76:40-94:50 --skip-tail-minutes 6.5 \
  --dump /tmp/opencode/S11/full_summary/dump
```

- Log: `/tmp/opencode/S11/full_summary.log`. Modelo por defecto `qwen2.5:7b`.
- Tras los recortes: **2526 segmentos**, 205 notas en el índice, roster del vault reconocido.
- **30 tramos** (map) → **4 secciones** de cuerpo → cierre (`# Próximos pasos`, `# Cabos sueltos`).
- Borrador escrito en `…/CurseOfStrahd-Goblin/_goblin/borradores/Sesión 11.md` (copia previa en `/tmp/opencode/S11/Sesion11_borrador_previo.md`).
- 2 notas nuevas propuestas: `NPCs/Aria la mujer de negro.md`, `Objetos/Libro prohibido.md`.
- **Verificado**: ninguna etiqueta `S#` se filtró al borrador ni al `final.md` (`grep -E '\bS[0-9]{1,3}\b'` sin coincidencias); las líneas se limpian o se generalizan a «Uno de los jugadores».
- Calidad del borrador aceptable; se cuelan algunas líneas casi literales (p. ej. «Sé quién nos mandaron para un ángel…») → revisar `drop_verbatim_lines`.
