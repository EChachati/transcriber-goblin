# goblin-agent

Convierte la transcripción de una sesión de rol (D&D) en una **nota de Obsidian**
organizada por temas y escenas, con un recordatorio de la sesión anterior y
propuestas de notas nuevas para el vault.

Está pensado para sesiones largas (de hasta 8 horas) y usa un modelo **local**
con [Ollama](https://ollama.com), así que no envía nada a internet.

---

## TL;DR (los 3 comandos que importan)

```bash
# 1) Ver que todo está listo (vault, modelo, Ollama)
goblin-agent info

# 2) Copiar el vault original a la copia de trabajo (segura)
goblin-agent refresh

# 3) Generar el borrador de la sesión a partir de la transcripción
goblin-agent summarize /ruta/a/transcript.json
```

El resultado aparece en tu copia de trabajo:
`<vault-de-trabajo>/_goblin/borradores/Sesión N.md`

---

## ¿Qué hace exactamente?

1. **Trocea** la transcripción en tramos, cortando en los **silencios** naturales
   (no en mitad de una conversación) para no perder contexto.
2. **Resume cada tramo** (*map*), dándole al modelo las fichas del vault que se
   mencionan (NPCs, lugares, objetos…) para que use los nombres correctos.
   Además le pasa unas **pistas de trama** sacadas de la propia transcripción
   (líneas con nombres propios, muertes, revelaciones o misiones) para que no se
   le escapen los hechos importantes. Cada tramo termina marcando su **hecho más
   importante** (`CLAVE: …`).
3. **Agrupa** los resúmenes en lotes que quepan en el contexto del modelo y pide
   **una sección** por lote (las concatena). Así **toda** la sesión aparece en la
   nota, por larga que sea (4–8 h), sin que ningún tramo se pierda.
4. **Garantiza los hechos y limpia el texto** sin volver a llamar al modelo: si un
   `CLAVE:` no apareció en su sección, se añade su viñeta a mano; si un tramo con
   hechos se quedó fuera, se añade su viñeta más representativa; y se filtran por
   patrones las viñetas de dados/reglas, de charla del directo y las repeticiones
   casi idénticas.
5. **Escribe la nota final** en pasos separados:
   - el **cuerpo** (solo la sesión de hoy, en secciones con título),
   - las **secciones de cierre** de la plantilla, y
   - el **recuerdo** («Anteriormente en …») usando **solo** la sesión previa.
   Se separan a propósito: si el modelo ve las dos sesiones a la vez, tiende a
   mezclarlas.
6. **Propone notas nuevas** (NPC, lugar, objeto o grupo) para las entidades que
   aún no existen en el vault. Quedan como borradores, tú decides si las creas.

---

## Requisitos

- **Python 3.12**
- **[uv](https://docs.astral.sh/uv/)** (gestor de entorno; también sirve `pip`)
- **[Ollama](https://ollama.com)** funcionando en local
- Un vault de Obsidian (o cualquier carpeta de notas en Markdown)

---

## Instalación

Desde la carpeta `goblin-agent/`:

```bash
# Crea el entorno e instala el paquete (incluye el comando goblin-agent)
uv sync

# (Opcional) herramientas de desarrollo, para lanzar los tests
uv sync --group dev
```

Descarga un modelo en Ollama (por defecto se usa `qwen2.5:7b`; ver «Modelos» más abajo):

```bash
ollama pull qwen2.5:7b
```

Comprueba que todo responde:

```bash
.venv/bin/goblin-agent info
```

Durante el desarrollo puedes ejecutarlo así (sin instalar nada):
`uv run goblin-agent ...`

---

## Configuración

Copia el ejemplo y edítalo:

```bash
cp .env.example .env
```

Variables disponibles (todas son opcionales; estos son los valores por defecto):

| Variable | Para qué sirve | Defecto |
| --- | --- | --- |
| `TG_GOBLIN_SOURCE_VAULT` | Vault **original** (solo lectura) | `~/Documents/CurseOfStrahd` |
| `TG_GOBLIN_WORK_VAULT` | Copia de trabajo (aquí se escribe) | `~/Documents/TranscriberGoblin/CurseOfStrahd-Goblin` |
| `TG_GOBLIN_CAMPAIGN` | Nombre de la campaña | `Curse of Strahd` |
| `TG_GOBLIN_MODEL` | Modelo de Ollama | `qwen2.5:7b` |
| `TG_GOBLIN_OLLAMA_URL` | URL de Ollama | `http://localhost:11434` |
| `TG_GOBLIN_LANGUAGE` | Idioma del resumen | `es` |
| `TG_GOBLIN_NUM_CTX` | Contexto del modelo (tokens) | `8192` |
| `TG_GOBLIN_NUM_PREDICT` | Máx. tokens por respuesta | `3072` |
| `TG_GOBLIN_TEMPERATURE` | Creatividad (0 = fiel) | `0.3` |
| `TG_GOBLIN_CHUNK_CHARS` | Tamaño objetivo de cada tramo | `4000` |
| `TG_GOBLIN_CHUNK_OVERLAP` | Segmentos de solape entre tramos | `2` |
| `TG_GOBLIN_LOOKAHEAD` | Margen para buscar el silencio | `6` |
| `TG_GOBLIN_MAX_REF_NOTES` | Fichas de referencia por tramo | `6` |
| `TG_GOBLIN_MAX_REF_CHARS` | Caracteres por ficha | `1200` |
| `TG_GOBLIN_MAX_REF_TOTAL` | Caracteres totales de fichas | `5000` |
| `TG_GOBLIN_BODY_BATCH_CHARS` | Tope de caracteres por lote del cuerpo | `6000` |

> El orden de prioridad es: **flags de la CLI** → `.env` → valores por defecto.

---

## Uso detallado

### `goblin-agent info`

Muestra la configuración efectiva, cuántas notas hay en el índice, el roster de
jugadores, el número de la próxima sesión y si Ollama responde.

### `goblin-agent refresh`

Sincroniza la **copia de trabajo** con el vault original (`rsync`, o copia
manual si no está disponible). **Nunca** toca el original.

### `goblin-agent summarize TRANSCRIPCION`

Genera el borrador. La transcripción puede ser un `transcript.json` (el que
produce `tg-transcriber`) o un Markdown con líneas `**[mm:ss] QUIEN:** texto`.

El campo `source` puede ser `TU`/`REMOTO` (grabación clásica) o `S1`, `S2`… si la
transcripción viene **diarizada** (varias voces remotas distinguidas por
`tg-transcriber --diarize` o `tg-transcriber file`). En ambos casos las etiquetas
son *hablantes*, no nombres de personaje: el agente no las convierte en notas ni
en personajes. Si sabes quién es cada `S#`, mapéalos a su nombre real con
`--speaker-names` para que el resumen use los nombres correctos.

Opciones útiles:

| Flag | Qué hace |
| --- | --- |
| `--session N` | Fuerza el número de sesión (si no, usa el siguiente). |
| `--template RUTA` | Usa otra plantilla de estructura. |
| `--out RUTA` | Escribe el borrador en otra ruta. |
| `--no-refresh` | No sincroniza el vault antes de empezar (más rápido). |
| `--skip-minutes N` | Ignora los primeros N minutos (cháchara previa a la partida). |
| `--skip-tail-minutes N` | Ignora los últimos N minutos (cierre del directo, post-partida). |
| `--skip-range INICIO-FIN` | Ignora un tramo intermedio (pausas, charla fuera de juego). Se repite el flag para varios. Acepta `76.7-94.8` o `76:40-94:50`. |
| `--speaker-names S1=Nombre,...` | Renombra los hablantes diarizados antes de resumir (`S1=DM,S2=Alicia`). |
| `--no-propose-notes` | No propone notas nuevas. |
| `--dry-run` | Solo analiza y dice cuántos tramos salen (no llama al modelo). |
| `--dump CARPETA` | Vuelca todos los pasos intermedios (para depurar). |
| `--model`, `--language`, `--campaign` | Sobrescriben la configuración. |

Ejemplos:

```bash
# Sesión concreta, sin sincronizar el vault, y guardando los pasos intermedios
goblin-agent summarize transcript.json --session 29 --no-refresh --dump /tmp/dump

# Probar otro modelo y otro idioma
goblin-agent summarize transcript.json --model qwen2.5:7b --language en

# Ver solo cuántos tramos saldrían (no llama a Ollama)
goblin-agent summarize transcript.json --dry-run

# Saltar los primeros 20 min (setup del directo) antes de resumir
goblin-agent summarize transcript.json --skip-minutes 20 --session 11 --no-refresh

# Una pausa a mitad de partida y la despedida del directo al final
goblin-agent summarize transcript.json \
  --skip-minutes 35 --skip-range 76:40-94:50 --skip-tail-minutes 7

# Transcripción diarizada (S1..Sn): decirle quién es cada voz
goblin-agent summarize transcript.json \
  --speaker-names 'S1=DM,S2=Arcaris,S3=Kaelen' --no-refresh
```

---

## La plantilla (editable)

La nota respeta una plantilla. El agente busca:

- el **primer encabezado** que contenga «Anteriormente» o «Recap» → encabezado
  del recuerdo;
- los demás encabezados (salvo los marcadores `# Tema` / `# Sección`) → son las
  **secciones de cierre** que siempre se añaden al final y en ese orden.

Plantilla por defecto:

```markdown
# Anteriormente en {{campana}}

# Tema

# Próximos pasos

# Cabos sueltos
```

`{{campana}}` se sustituye por el nombre de la campaña. Si quieres tu propia
estructura, coloca `plantilla.md` en `<vault-de-trabajo>/_goblin/` o pásala con
`--template`.

---

## ¿Dónde queda todo?

```
<vault-de-trabajo>/
├── _goblin/
│   ├── plantilla.md                     # (opcional) tu plantilla
│   └── borradores/
│       ├── Sesión 29.md                 # el borrador final
│       └── nuevas-notas/
│           ├── NPCs/Vallaki/…
│           ├── Lugares/…
│           ├── Objetos/…
│           └── Grupos/…
```

Las notas nuevas se agrupan por tipo y por zona. **Nada** se escribe en el vault
original.

---

## Seguridad: el vault original es sagrado

El agente trabaja siempre sobre una **copia**. Antes de escribir comprueba que
la ruta destino no esté dentro del vault original; si lo estuviera, **aborta**.
No hay forma de que `goblin-agent` modifique tus notas buenas por accidente.

---

## Modelos

El modelo por defecto es **`qwen2.5:7b`**: cabe en 4 GB de VRAM con *offload* a
RAM, tarda ~40–60 s por tramo (una sesión de 3 h son unos 25–35 min) y captura
los hechos clave («el cuerpo de Bruna está acoplado con el de Enza», la muerte de
Ari, la revelación de la Aasimar):

```bash
ollama pull qwen2.5:7b
goblin-agent summarize transcript.json
```

Si necesitas velocidad por encima de todo, `qwen2.5:3b` cabe sobrado y es más
rápido, pero **se le escapan hechos importantes** y a veces copia la transcripción
en vez de resumirla; con él cuentas con un borrador flojo (sirve para ver la
estructura, no para quedártelo). Para usarlo:

```bash
ollama pull qwen2.5:3b
goblin-agent summarize transcript.json --model qwen2.5:3b
```

---

## Pruébalo con el ejemplo incluido

El repo trae una transcripción de ejemplo (`examples/transcript-ejemplo.json`) para
que puedas probar sin depender de una grabación real:

```bash
# 1) Comprueba que el entorno y Ollama están listos
uv run goblin-agent info

# 2) Sincroniza la copia de trabajo desde tu vault original
uv run goblin-agent refresh

# 3) Genera el borrador del ejemplo
uv run goblin-agent summarize examples/transcript-ejemplo.json --no-refresh
```

El resultado queda en:

```
~/Documents/TranscriberGoblin/CurseOfStrahd-Goblin/_goblin/borradores/Sesión N.md
```

> `--no-refresh` evita volver a copiar el vault (más rápido). El paso 2 solo
> hace falta la primera vez o cuando añadas notas nuevas al original.

Si quieres ver **qué hizo por dentro** (tramos, resúmenes intermedios, prompts):

```bash
uv run goblin-agent summarize examples/transcript-ejemplo.json \
  --no-refresh --dump /tmp/goblin-dump
```

---

## Tests

No necesitan Ollama (solo prueban la parte determinista):

```bash
uv run --group dev pytest -q
```

---

## Limitaciones conocidas

- La calidad del texto depende del modelo. Un modelo pequeño puede inventar
  algún detalle: **revisa siempre el borrador** antes de darlo por bueno.
- El «Anteriormente» resume la sesión previa tal cual está en el vault; conviene
  que esa nota esté revisada.
- Solo trabaja en local: no hay backend ni sincronización.
- Las grabaciones largas traen **charla fuera de juego** (setup del directo,
  pausas, Discord, despedidas). El agente la filtra por patrones, pero lo más
  fiable es recortarla tú con `--skip-minutes`, `--skip-range` y
  `--skip-tail-minutes`. Para encontrar los minutos, busca en la transcripción
  cuándo se anuncia la pausa y cuándo se despide el directo (o mira los
  `[mm:ss]` de las líneas que no son de partida) y usa esos rangos.

---

## Cómo encaja con tg-transcriber

`tg-transcriber` graba y transcribe la partida y deja un `transcript.json`.
`goblin-agent` lo lee, y a partir de ahí todo ocurre en tu copia de trabajo del
vault de Obsidian:

```
audio  ──►  tg-transcriber  ──►  transcript.json  ──►  goblin-agent  ──►  Sesión N.md
```
