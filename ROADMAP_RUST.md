# ROADMAP — Rework a Rust

> Documento de continuidad del rework. Complementa a `HANDOFF.md`.
> Objetivo: portar Transcriber Goblin a Rust manteniendo el silencio de la API y el formato de datos.
> Última actualización: 2026-09-19.

---

## 1. Contexto y restricciones

- **Motivación**: rendimiento (linker O(N·M), latencia del mirror por polling 30s), distribución (binario único, sin uv/venv), aprendizaje Rust.
- **Hosting**: internet desde el día 1 — el server corre **siempre encendido en un VPS** detrás de **Caddy (TLS)**. NO es red local.
- **Usuarios**: los amigos usan **Windows** pero **solo consumen vía plugin Obsidian** (no instalan nada; es TS y ya es cross-platform). El que transcribe es el dueño, desde su **Linux** (RTX 3050 Ti, mic ALC294 saturado).
- Windows SOLO es objetivo del transcriber si algún día el flujo cambia (trait `AudioSource` listo, backend WASAPI diferido).

## 2. Decisiones tomadas

| Decisión | Elección | Por qué |
|---|---|---|
| CRDT en el server | **`yrs` + `yrs-axum` embebido (arquitectura B)** | Elimina el contenedor y-sweet → binario único; mirror en tiempo real; linker sin clobbering. `yrs` es la misma implementación que `pycrdt`. |
| Persistencia CRDT | **Binlog de updates en SQLite** + replay al boot; `yrs::Doc` en memoria por nota | Simple, cross-platform, reemplaza el store de Jamsocket. |
| Websocket sync | `y-sync` / `yrs-axum` (y-sweet-compatible) | El plugin y `ws-e2e-test.mjs` ya hablan ese protocolo. |
| DB | **`rusqlite` + `Mutex`** | Mismo modelo que el `db.py` actual; sin dependencias nuevas. Migrar a `sqlx` solo si el tráfico lo pide. |
| Keywords linker | **Determinista (TF-IDF, sin ML)** | keyBERT era de todos modos opcional; el fallback de frecuencia se mejora con IDF. Contrato JSON idéntico. |
| STT | **`whisper-rs`** (whisper.cpp, CUDA en Linux) con **`TG_STT_ENGINE=rust|python`** | Mejor distribución y aprendizaje; escape hatch hasta validar calidad con corpus oro. |
| Captura audio | Linux: `parec` como subproceso (igual que hoy). **`trait AudioSource`** para el futuro | `parec` resuelve el bug de PipeWire documentado en `HANDOFF.md`; no reabrir ese frente. |
| Deploy | Binario **musl static** detrás de Caddy; compose con solo el servicio `api` (o systemd) | TLS lo termina Caddy (`wss://sync.tudominio.com` → `:8000`); el server no habla TLS. |

## 3. Arquitectura objetivo

```
Workspace cargo
  Cargo.toml
  crates/
    tg-commons/      # modelos/domain, normalize(), render_markdown, auth (hash/hmac)
    tg-linker/       # algoritmo puro de linking + keywords TF-IDF (sin dep del server)
    tg-server/       # binario axum: rutas, auth, sqlite, binlog CRDT, ws y-sync, mirror por suscripción
    tg-transcriber/  # binario clap: capture (parec ×2), stt (whisper-rs), publish (yrs + reqwest)

Transcriber (tu Linux)                        VPS (siempre encendido)
  tg-transcriber record ──https──▶ Caddy ──▶ tg-server :8000 (axum)
                                          │     ├─ yrs::Doc en memoria (CRDT) + ws /d/{id}/ws/{id}
                                          │     ├─ SQLite: users/invites/docs/doc_access/attachments/aliases/linker_proposals
                                          │     ├─ SQLite binlog: updates CRDT (append-only) + replay al boot
                                          │     └─ mirror: UpdateSubscription de yrs ─▶ data/mirror/<id>.md (real-time)
Amigos (Windows): Obsidian plugin ──wss──▶ Caddy ──▶ tg-server
```

## 4. Contrato de compatibilidad (invariantes que NO cambian)

- Rutas y JSON de la API: `/docs`, `/docs/{id}/token`, `/attachments`, `/linker/*`, `/invites`, `/auth/redeem`, `/me`, `/health`.
- Esquema SQLite (`db.py` SCHEMA) incluyendo `aliases` y `linker_proposals`.
- Formato del espejo: `data/mirror/<doc_id>.md` (markdown plano, clave `content` del Y.Text).
- `transcript.json` / `transcript.md` del transcriber (incl. `attachment://<sha>`).
- Envs: `ADMIN_TOKEN`, `DATA_DIR`, `MIRROR_INTERVAL`, `TG_API_URL`, `TG_TOKEN`. Se eliminan `YSWEET_*` (ya no hace falta y-sweet) salvo compat temporal.
- Superficie y-sweet-compatible que usa el plugin hoy: HTTP `POST /doc/new`, `POST /doc/{id}/auth`, `GET/POST /d/{id}/as-update`, `/d/{id}/update`; ws `/d/{id}/ws/{id}` (y-sync + awareness, docId repetido).
- Los 6 tests de pytest actuales + `plugin-obsidian/scripts/ws-e2e-test.mjs` actúan como **harness de oro** contra el server Rust.

## 5. Fases

### Fase 0 — Investigación (resultados 2026-09-19)

Resultado: **spike STT validado, corpus + harness en el repo, baselines medidos, `trait AudioSource` definido.**

- [x] Spike STT: `whisper-rs` 0.16 (whisper.cpp/vendored ggml) en **CPU** (modelo `ggml-small-q5_1`, 190 MB). Binario `crates/tg-transcriber`.
  - ⚠️ **CUDA diferido**: no hay CUDA toolkit (nvcc) en la máquina y `sudo` requiere contraseña → la feature `cuda` de whisper-rs necesita nvcc en build. Hoy corre CPU; añadir CUDA cuando se instale el toolkit (pacman) o vía runfile sin root en `~/cuda`.
- [x] Corpus oro (`crates/tg-transcriber/tools/stt/corpus/`): **jfk** (en, clásico) y **es-reunion** (es-ES, TTS edge-tts de un texto de reunión, mp3→wav 16k). Se añadirán grabaciones reales (TU con mic ALC294 y REMOTO) cuando existan — la e2e previa (`doc=8c734c33aae929b6`) ya no existe en disco.
- [x] Harness (`tools/stt/compare.py`, dev-only): **WER/CER** (jiwer) sobre texto normalizado + **drift de timestamps** por emparejamiento por centro. `tools/stt/run.sh <wav> <name> [lang]` corre oro+rust+comparación en un paso.
  - Resultados: **jfk** WER 0.0, drift 0.0 · **es-reunion** WER 0.0, CER 0.0, drift medio 0.02 s (rust parte en más segmentos: 5 vs 4, pero texto idéntico). Criterio de aceptación (≤5 % WER, ≤0.5 s drift) **superado** en el corpus actual.
- [x] Baseline del linker (`crates/tg-server/tools/linker_bench.py`, `target="mirror"`, sin y-sweet): confirma **O(N²)** (~4.2–4.8× al duplicar N):

  | N notes | tiempo |
  |---|---|
  | 20 | 1.2 s |
  | 40 | 5.8 s |
  | 80 | 25.6 s |
  | 160 | 105.1 s |

- [x] Latencia del mirror: propiedad de diseño = **polling con `MIRROR_INTERVAL` (30 s)**; peor caso ~30 s. Desaparece en B con `UpdateSubscription`.
- [x] `trait AudioSource` (draft) en `crates/tg-transcriber/src/audio.rs` (backend `parec` previsto en fase 3; WASAPI después).
- [x] Workspace cargo creado: root `Cargo.toml` + crates `tg-commons`, `tg-linker`, `tg-server`, `tg-transcriber` (espiga STT incluida).
- [x] Decisiones cerradas (sección 2) y este documento.

### Fase 1 — tg-linker (crate puro)

**Estado 2026-09-19: núcleo del linker portado y validado.** `crates/tg-linker` (solo deps: `serde`, `unicode-normalization`).

- [x] Port de `normalize` (NFKD, plural `s`/`es`), `expand_variants` (prefijos), matching por variantes, propuestas y `extract_keywords`.
  - **Keywords deterministas**: `keywords_tfidf(text, corpus, n)` (TF-IDF 1–2 gramos, bump de encabezados `#`, suavizado) en `src/keywords.rs`; `keywords_frequency` para paridad con el fallback.
  - **Diseño**: el contenido se tokeniza **una vez por doc** (el Python lo re-escaneaba por cada variante; ahí vivía el coste). Los auto-links se devuelven como **ediciones** `{start, end, replacement}` (offsets de byte) en vez de mutar el string → la Fase 2 las aplica por diffs sobre `yrs::Doc`.
- [x] Tests portados desde `server/tests/test_linker.py`: normalize, prefijos, auto (inambiguo), overlap, ambigüedad → propuestas, "ya enlazado" → skip, y TF-IDF/frecuencia. **8/8 verdes**.
- [x] Benchmark (`examples/bench.rs`, mismos datos sintéticos que `linker_bench.py`) — núcleo puro, sin I/O:

  | N notes | Python (fase 0) | Rust | factor |
  |---|---|---|---|
  | 20 | 1209 ms | 5.5 ms | ~220× |
  | 40 | 5804 ms | 21.8 ms | ~266× |
  | 80 | 25582 ms | 82.7 ms | ~309× |
  | 160 | 105135 ms | 322 ms | **~327×** |

  Nota: sigue siendo O(N²) algorítmico (es proporcional a notas × variantes × tokens), pero con factor gravísimo menor y la relinking en Fase 2 será incremental (solo el doc editado) vía `UpdateSubscription`.
- [ ] Aplicación de links **por diffs sobre el `yrs::Doc` en memoria** — reemplaza `_apply_markdown` (`clear()+insert`) que clobberea ediciones concurrentes. *Se hace en Fase 2 al integrar el CRDT.*

### Fase 2 — tg-server (axum, arquitectura B)
- [x] Scaffold axum: `Settings::from_env` (`DATA_DIR/ADMIN_TOKEN/TG_HOST/TG_PORT/TG_PUBLIC_URL/MIRROR_INTERVAL`), `rusqlite`+`Mutex` (schema idéntico al Python), auth (sha256 + hmac del token de doc), CORS, `tower-http`.
- [x] CRDT embebido: `yrs::Doc` por nota en memoria, snapshot `.bin` por doc, mirror write-through (`data/mirror/<id>.md`), canal `tokio::sync::broadcast` para peers WS.
- [x] Superficie y-sweet-compatible para el plugin: `POST /doc/new`, `POST /doc/{id}/auth` (client token HMAC), `GET /d/{id}/as-update`, `POST /d/{id}/update`, WS `GET /d/{id}/ws/{ws_id}?token=`.
- [x] Peer WS con y-protocols a mano (codificación yjs, no la `write_buf` de yrs en SyncStep1): sync Step1↔Step2 en ambos sentidos + updates rebroadcast; awareness ignorado por ahora (TODO presencia).
- [x] Rutas completas: `health`, `/me`, compartición/invites/redeem, `/docs` CRUD + token, `/attachments` (multipart, dedupe por sha256), `/linker` (graph, keywords TF-IDF, aliases CRUD, proposals apply/dismiss, run `crdt|mirror`).
- [x] Proposals: ciclo completo probado (descubrimiento por variantes de título/contenido, `apply` con fallback mirror→CRDT que siembra el doc, `dismiss`, y rechazo por acceso).
- [x] Validación: test de integración único (`tests/api.rs`) cubriendo toda la API + roundtrip WS real (Step1→Step2, escribir, re-verificar por `as-update`), rechazo con token inválido y ciclo de proposals. `ws-e2e-test.mjs` pendiente de run (este entorno no tiene `node`); cubierto por el equivalente Rust en `tests/api.rs`.
- [ ] Pending TODO menor: awareness/presencia en el peer WS (hoy se ignora el tag 1).
- [ ] Deploy: build musl static, Dockerfile multistage (solo `api`) o systemd en el VPS, detrás de Caddy.

### Fase 3 — tg-transcriber (Linux)
- Capture: `parec` micro+monitor → WAV 16 kHz mono s16le (igual al `capture.py`).
- STT: `whisper-rs` (CUDA) con `TG_STT_ENGINE=rust|python` hasta pasar la Fase 0.
- Publish: `yrs` + reqwest por HTTPS; mismo `transcript.md`, mismo marcado `attachment://<sha>`.

### Fase 4 — Diferible (en este orden)
1. **Resolver `attachment://<sha>` en el plugin**: mapear al server `/attachments/<sha>` vía `requestUrl` (sin CORS) para abrir audios fuera de sesión. Requiere trabajo en `plugin-obsidian`.
2. **Windows para transcriber**: backend WASAPI en `trait AudioSource` + STT Vulkan (features de `whisper-rs`) + CI matrix (ubuntu+windows). Solo si cambia el caso de uso.
3. **goblin-agent en Rust**: cliente Ollama (reqwest/JSON) + map-reduce sobre el espejo.
4. **Webapp**: servir estáticos desde `tg-server` (`axum ServeDir`); el editor habla y-sync con el server.

## 6. Riesgos y decisiones abiertas

1. **Calidad whisper-rs vs. faster-whisper**: no es idéntica en segmentación/timestamps (rust parte en más segmentos), pero ya valida **texto idéntico en CPU** sobre el corpus de Fase 0 (WER 0.0). Mitigación: corpus oro + `TG_STT_ENGINE` fallback. Pendiente: validar con grabaciones reales (mic ALC294) y re-evaluar en GPU cuando exista CUDA toolkit (nvcc) para la feature `cuda` de whisper-rs.
2. **Vender el CRDT (B)**: reimplementar la superficie y-sweet compatible es el precio de eliminar el contenedor. Riesgo acotado por `ws-e2e-test.mjs` como criterio de aceptación.
3. **Binlog vs. checkpoint**: crecer ilimitado; añadir compactación (checkpoint del `yrs::Doc` al SQLite/metadatos) cuando haya volumen.
4. **Adjuntos multi-máquina**: hoy son globales por hash y descargables con cualquier token; decidir si se asocian a docs antes de dar acceso a terceros.

## 7. Referencias en el código actual

| Pieza actual | Contra parte en Rust |
|---|---|
| `server/app/{auth,db,ysweet,mirror}.py` | `tg-server`: auth, rusqlite, cliente/ws → `yrs`/`y-sync` |
| `server/app/routes_{docs,attachments,linker}.py` | `tg-server` (axum) |
| `server/app/linker.py` | `tg-linker` |
| `transcriber/src/tg_transcriber/{capture,session,stt,publish,cli}.py` | `tg-transcriber` |
| `transcriber` deps (pycrdt, httpx, faster-whisper) | `yrs`, `reqwest`, `whisper-rs` |