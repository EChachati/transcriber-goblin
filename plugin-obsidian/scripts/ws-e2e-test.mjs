/**
 * Prueba e2e mínima del protocolo que usa el plugin:
 * conecta al websocket de y-sweet, sincroniza, escribe y re-verifica.
 *
 * Uso: node scripts/ws-e2e-test.mjs "<ws-url-con-token>" [texto-a-escribir]
 */
import * as Y from "yjs";
import * as syncProtocol from "y-protocols/sync";
import * as encoding from "lib0/encoding";
import * as decoding from "lib0/decoding";

const [wsUrl, textToWrite] = process.argv.slice(2);
if (!wsUrl) {
	console.error("falta la url");
	process.exit(1);
}

const doc = new Y.Doc();
const ws = new WebSocket(wsUrl);
ws.binaryType = "arraybuffer";

function sendSync(builder) {
	const encoder = encoding.createEncoder();
	encoding.writeVarUint(encoder, 0); // messageSync
	builder(encoder);
	ws.send(encoding.toUint8Array(encoder));
}

doc.on("update", (update) => {
	sendSync((enc) => syncProtocol.writeUpdate(enc, update));
});

ws.onopen = () => {
	sendSync((enc) => syncProtocol.writeSyncStep1(enc, doc));
};

ws.onmessage = (event) => {
	const buf = new Uint8Array(event.data);
	const decoder = decoding.createDecoder(buf);
	const messageType = decoding.readVarUint(decoder);
	if (messageType !== 0) return;
	const syncType = decoding.readVarUint(decoder);
	if (syncType === syncProtocol.messageYjsSyncStep1) {
		// el server pide nuestro estado
		sendSync((enc) => syncProtocol.readSyncStep1(decoder, enc, doc));
	} else if (syncType === syncProtocol.messageYjsSyncStep2) {
		syncProtocol.readSyncStep2(decoder, doc, null);
		console.log("[e2e] contenido remoto tras sync:", JSON.stringify(doc.getText("content").toString().slice(0, 120)));
		if (textToWrite && !doc.getText("content").toString().includes(textToWrite)) {
			doc.getText("content").insert(0, `${textToWrite}\n`);
			console.log("[e2e] insertado localmente:", JSON.stringify(textToWrite));
		}
	}
};

setTimeout(() => {
	console.log("[e2e] contenido final:", JSON.stringify(doc.getText("content").toString().slice(0, 160)));
	ws.close();
	process.exit(0);
}, 3000);
