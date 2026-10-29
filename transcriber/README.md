# transcriber

Captura el audio de tu micrófono + el del sistema (llamadas de Discord/Meet/cualquier app), lo transcribe (y opcionalmente **diariza** los hablantes) y guarda la nota localmente.

## Uso

```bash
cd transcriber
uv sync
cp .env.example .env  # opcional: editar con tus configs
uv run tg-transcriber devices     # verifica fuentes MIC (TU) y MONITOR (REMOTO)
uv run tg-transcriber record --name "Sesión D&D" --language es
```

### Diarización (¿quién habla?)

Con `transcript.json` «clásico» solo se distingue `TU` (tu micro) de `REMOTO`
(el resto del audio). Si necesitas saber **qué persona remota** habla y cuándo,
añade `--diarize`: se usa [pyannote.audio](https://github.com/pyannote/pyannote-audio)
para separar las voces y cada segmento queda etiquetado como `S1`, `S2`, `S3`…
(`S1` es quien habla primero). Requiere:

1. Aceptar las condiciones de `pyannote/speaker-diarization-community-1` en HuggingFace.
2. Tener un token de HuggingFace en `transcriber/.env` (o en el entorno):

   ```bash
   echo 'HF_TOKEN=hf_...' >> .env
   ```

```bash
# Grabar y diarizar; se estima el nº de hablantes automáticamente
uv run tg-transcriber record --name "Sesión D&D" --diarize

# Si conoces a cuántos jugadores hay que separar:
uv run tg-transcriber record --name "Sesión D&D" --diarize --speakers 5
uv run tg-transcriber record --name "Sesión D&D" --diarize --min-speakers 4 --max-speakers 7
```

La primera ejecución deduce `cuda` (si hay GPU) y cae a CPU si falla. El paso de
diarización tarda bastante más que la transcripción.

### Transcribir ficheros ya grabados (VODs, mp4/mkv)

El subcomando `file` procesa un audio/vídeo existente sin grabar: extrae el audio
con `ffmpeg`, transcribe y **diariza por defecto**, y escribe `transcript.json`
(más `transcript.md`). Pensado para subir VODs de partidas largas a posteriori:

```bash
uv run tg-transcriber file ~/Downloads/S11.mp4 --model small --language es \
  --min-speakers 4 --max-speakers 8 --out ~/s11-transcript
```

- `--no-diarize` para desactivarla; `--speakers`/`--min-speakers`/`--max-speakers`
  ajustan el número de voces.
- Escribe en `--out` (o en `~/.local/share/tg-transcriber/sessions/…` si no lo das).

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
- Etiqueta cada segmento como `TU` (micro) o `REMOTO` (audio del sistema); con `--diarize`, como `S1`…`Sn`.
- Guarda `transcript.md`, `transcript.json` y los WAVs en `~/.local/share/tg-transcriber/sessions/<fecha>_<nombre>/`.
- Con `--publish` sube los audios y publica la nota al backend para edición colaborativa.
- Opcionalmente, si configuras `TG_OBSIDIAN_VAULT`, también se escribe un `.md` directamente en ese vault de Obsidian.

Variables de entorno (ver `.env.example`):
- `TG_API_URL` (default `http://localhost:8000`) - solo con `--publish`
- `TG_TOKEN` (token de usuario del backend) - solo con `--publish`
- `TG_SESSIONS_DIR` (default `~/.local/share/tg-transcriber/sessions`)
- `TG_OBSIDIAN_VAULT` (ruta al vault de Obsidian)
- `TG_OBSIDIAN_FOLDER` (carpeta dentro del vault, p.ej. `Transcripciones`)
- `HF_TOKEN` (token de HuggingFace) - solo con `--diarize` (o `file`)
- `TG_DIARIZE_DEVICE` (fuerza `cuda`/`cpu` para pyannote; si no, autodetecta)

## Cómo funciona

1. `capture.py`: dos procesos `parec` (PipeWire/PulseAudio) graban simultáneamente la fuente del micro y el **monitor** del sink por defecto → WAV 16kHz mono.
2. `stt.py`: faster-whisper con VAD (Silero integrado) por stream; GPU vía ctranslate2 con libs nvidia pre-cargadas. Con diarización pide también los *timestamps por palabra*.
3. `session.py`: mezcla segmentos por tiempo absoluto → `transcript.json`. Si hay diarización, `media.py` mezcla micro+escritorio y `diarize.py` (pyannote) asigna a cada segmento su hablante `S1`…`Sn`, partiendo los segmentos que abarcan varios turnos.
4. `file_job.py`: pipeline offline para ficheros (`file`): extrae audio, transcribe, diariza y escribe `transcript.json`.
5. `publish.py`: (opcional con `--publish`) sube audios a `/attachments`, crea la nota y escribe su Y.Doc vía HTTP (`/update`). También permite escribir el `.md` directamente al vault de Obsidian.

## Notas de hardware

- El micrófono interno ALC294 de esta laptop entrega señal saturada (offset DC). Verificar en `alsamixer` (F4: dispositivos de captura, seleccionar puerto "Internal Microphone") o con pavucontrol. El pipeline funciona igual; cuando el micro esté sano sus segmentos saldrán limpios.
- En Wayland/GNOME con seguridad estricta puede requerir permisos para capturar el monitor de audio (en Hyprland/Omarchy funciona directo).
