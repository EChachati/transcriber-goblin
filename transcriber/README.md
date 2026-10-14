# transcriber

Captura el audio de tu micrófono + el del sistema (llamadas de Discord/Meet/cualquier app), lo transcribe y guarda la nota localmente.

## Uso

```bash
cd transcriber
uv sync
cp .env.example .env  # opcional: editar con tus configs
uv run tg-transcriber devices     # verifica fuentes MIC (TU) y MONITOR (REMOTO)
uv run tg-transcriber record --name "Sesión D&D" --language es
```

Para escribir directamente en Obsidian:

```bash
TG_OBSIDIAN_VAULT=~/Vaults/MiVault TG_OBSIDIAN_FOLDER=Transcripciones \
  uv run tg-transcriber record --name "Reunión"
```

O usando flags: `--obsidian-vault`, `--obsidian-folder`, `--obsidian-no-overwrite`.

Para **publicar al backend/API** (edición colaborativa con y-sweet):

```bash
TG_TOKEN=tu-token uv run tg-transcriber record --name "Reunión" --publish
```

- Graba hasta `Ctrl+C`, luego transcribe con faster-whisper (GPU si hay CUDA, si no CPU).
- Etiqueta cada segmento como `TU` (micro) o `REMOTO` (audio del sistema).
- Guarda `transcript.md`, `transcript.json` y los WAVs en `~/.local/share/tg-transcriber/sessions/<fecha>_<nombre>/`.
- Con `--publish` sube los audios y publica la nota al backend para edición colaborativa.
- Opcionalmente, si configuras `TG_OBSIDIAN_VAULT`, también se escribe un `.md` directamente en ese vault de Obsidian.

Variables de entorno (ver `.env.example`):
- `TG_API_URL` (default `http://localhost:8000`) - solo con `--publish`
- `TG_TOKEN` (token de usuario del backend) - solo con `--publish`
- `TG_SESSIONS_DIR` (default `~/.local/share/tg-transcriber/sessions`)
- `TG_OBSIDIAN_VAULT` (ruta al vault de Obsidian)
- `TG_OBSIDIAN_FOLDER` (carpeta dentro del vault, p.ej. `Transcripciones`)

## Cómo funciona

1. `capture.py`: dos procesos `parec` (PipeWire/PulseAudio) graban simultáneamente la fuente del micro y el **monitor** del sink por defecto → WAV 16kHz mono.
2. `stt.py`: faster-whisper con VAD (Silero integrado) por stream; GPU vía ctranslate2 con libs nvidia pre-cargadas.
3. `session.py`: mezcla segmentos por tiempo absoluto → `transcript.json`.
4. `publish.py`: (opcional con `--publish`) sube audios a `/attachments`, crea la nota y escribe su Y.Doc vía HTTP (`/update`). También permite escribir el `.md` directamente al vault de Obsidian.

## Notas de hardware

- El micrófono interno ALC294 de esta laptop entrega señal saturada (offset DC). Verificar en `alsamixer` (F4: dispositivos de captura, seleccionar puerto "Internal Microphone") o con pavucontrol. El pipeline funciona igual; cuando el micro esté sano sus segmentos saldrán limpios.
- En Wayland/GNOME con seguridad estricta puede requerir permisos para capturar el monitor de audio (en Hyprland/Omarchy funciona directo).
