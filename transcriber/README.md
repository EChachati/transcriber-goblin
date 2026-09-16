# transcriber

Captura el audio de tu microfono + el del sistema (llamadas de Discord/Meet/cualquier app) y lo convierte en notas publicadas al backend.

## Uso

```bash
cd transcriber
uv sync
uv run tg-transcriber devices     # verifica fuentes MIC (TU) y MONITOR (REMOTO)
uv run tg-transcriber record --name "Sesión D&D" --language es
```

- Graba hasta `Ctrl+C`, luego transcribe con faster-whisper (GPU si hay CUDA, si no CPU).
- Etiqueta cada segmento como `TU` (micro) o `REMOTO` (audio del sistema).
- Sube los WAV como adjuntos y publica la nota (`transcript.md`) al backend.
- Todo queda también en `~/.local/share/tg-transcriber/sessions/<fecha>_<nombre>/`.

Variables: `TG_API_URL` (default `http://localhost:8000`), `TG_TOKEN` (token de usuario del backend), `TG_SESSIONS_DIR`.

## Cómo funciona

1. `capture.py`: dos procesos `parec` (PipeWire/PulseAudio) graban simultáneamente la fuente del micro y el **monitor** del sink por defecto → WAV 16kHz mono.
2. `stt.py`: faster-whisper con VAD (Silero integrado) por stream; GPU vía ctranslate2 con libs nvidia pre-cargadas.
3. `session.py`: mezcla segmentos por tiempo absoluto → `transcript.json`.
4. `publish.py`: sube audios a `/attachments`, crea la nota y escribe su Y.Doc vía HTTP (`/update`).

## Notas de hardware

- El micrófono interno ALC294 de esta laptop entrega señal saturada (offset DC). Verificar en `alsamixer` (F4: dispositivos de captura, seleccionar puerto "Internal Microphone") o con pavucontrol. El pipeline funciona igual; cuando el micro esté sano sus segmentos saldrán limpios.
- En Wayland/GNOME con seguridad estricta puede requerir permisos para capturar el monitor de audio (en Hyprland/Omarchy funciona directo).
