# plugin-obsidian

Plugin de Obsidian (TypeScript) que conecta un vault con el backend de Transcriber Goblin.

## Qué hace hoy (MVP parte 3)

- Ajustes: URL del backend, token personal y nombre para cursores.
- Vista lateral "Notas Goblin": lista tus docs (`GET /docs`), crea notas nuevas (`POST /docs`).
- Editor colaborativo: cada nota abre en una pestaña con CodeMirror6 + CRDT realtime vía y-sweet.
- Presencia básica por awareness (nombre compartido en el estado).

## Instalación en un vault

```bash
cd plugin-obsidian && npm install && npm run build
# copiar a tu vault:
mkdir -p <vault>/.obsidian/plugins/transcriber-goblin
cp main.js manifest.json styles.css <vault>/.obsidian/plugins/transcriber-goblin/
```

Luego en Obsidian: Ajustes → Plugins comunitarios → activar «Transcriber Goblin»
(activar primero «Modo restringido → desactivado» si aplica) y completar token/URL.

## Desarrollo

```bash
npm run dev   # esbuild --watch, genera main.js inline sourcemap
npm run build # tsc --noEmit + bundle minificado
```

## Notas técnicas

- El Y.Doc vive en el server; la clave `content` es un `Y.Text` markdown plano.
- `POST /docs/{id}/token` devuelve un client token y-sweet; el plugin conecta a
  `token.url?token=...` (websocket binario, protocolo y-sync + awareness).
- Las peticiones HTTP usan `requestUrl` (no `fetch`) para saltar CORS del renderer.
- CodeMirror (`@codemirror/*`, `@lezer/*`) queda como *external*: Obsidian lo provee.
- Provider propio minimalista en `src/provider.ts` (~250 líneas), adaptado de
  y-websocket (MIT). El fork de `~/Projects/Relay` fue la referencia pero está muy
  acoplado a su infraestructura (eventos CBOR, subdocs, métricas).
