/**
 * Provider websocket para y-sweet, adaptado de y-websocket / y-sweet-sdk.
 *
 * https://raw.githubusercontent.com/yjs/y-websocket/master/src/y-websocket.js
 * (MIT © Kevin Jahns y colaboradores)
 */

import * as Y from "yjs";
import * as time from "lib0/time";
import * as encoding from "lib0/encoding";
import * as decoding from "lib0/decoding";
import * as syncProtocol from "y-protocols/sync";
import * as authProtocol from "y-protocols/auth";
import * as awarenessProtocol from "y-protocols/awareness";
import { Observable } from "lib0/observable";
import * as math from "lib0/math";

export const messageSync = 0;
export const messageAwareness = 1;
export const messageAuth = 2;
export const messageQueryAwareness = 3;

const MESSAGE_RECONNECT_TIMEOUT = 30000;
const RECONNECT_BASE_DELAY_MS = 300;
const RECONNECT_MAX_DELAY_MS = 30000;

export type ProviderStatus = "connected" | "connecting" | "disconnected";

type HandlerFunction = (
	encoder: encoding.Encoder,
	decoder: decoding.Decoder,
	provider: YSweetProvider,
	emitSynced: boolean,
	messageType: number,
) => void;

const messageHandlers: Array<HandlerFunction> = [];

messageHandlers[messageSync] = (encoder, decoder, provider, emitSynced) => {
	const syncMessageType = decoding.readVarUint(decoder);
	encoding.writeVarUint(encoder, messageSync);
	switch (syncMessageType) {
		case syncProtocol.messageYjsSyncStep1:
			syncProtocol.readSyncStep1(decoder, encoder, provider.doc);
			break;
		case syncProtocol.messageYjsSyncStep2:
			syncProtocol.readSyncStep2(decoder, provider.doc, provider);
			break;
		case syncProtocol.messageYjsUpdate:
			syncProtocol.readUpdate(decoder, provider.doc, provider);
			break;
		default:
			throw new Error("Unknown sync message type");
	}
	if (emitSynced && syncMessageType === syncProtocol.messageYjsSyncStep2 && !provider.synced) {
		provider.synced = true;
	}
};

messageHandlers[messageQueryAwareness] = (encoder, _decoder, provider) => {
	encoding.writeVarUint(encoder, messageAwareness);
	encoding.writeVarUint8Array(
		encoder,
		awarenessProtocol.encodeAwarenessUpdate(
			provider.awareness,
			Array.from(provider.awareness.getStates().keys()),
		),
	);
};

messageHandlers[messageAwareness] = (_encoder, decoder, provider) => {
	awarenessProtocol.applyAwarenessUpdate(
		provider.awareness,
		decoding.readVarUint8Array(decoder),
		provider,
	);
};

messageHandlers[messageAuth] = (_encoder, decoder, provider) => {
	authProtocol.readAuthMessage(decoder, provider.doc, (_ydoc, reason) => {
		console.warn("[GoblinProvider] permiso denegado:", reason);
		provider.emit("permission-denied", [reason]);
	});
};

const readMessage = (
	provider: YSweetProvider,
	buf: Uint8Array,
	emitSynced: boolean,
): encoding.Encoder => {
	const decoder = decoding.createDecoder(buf);
	const encoder = encoding.createEncoder();
	const messageType = decoding.readVarUint(decoder);
	const handler = provider.messageHandlers[messageType];
	if (handler) {
		handler(encoder, decoder, provider, emitSynced, messageType);
	} else {
		console.error("[GoblinProvider] mensaje desconocido:", messageType);
	}
	return encoder;
};

const setupWS = (provider: YSweetProvider) => {
	if (!provider.shouldConnect || provider.ws !== null) return;

	const websocket = new WebSocket(provider.url);
	websocket.binaryType = "arraybuffer";
	provider.ws = websocket;
	provider.wsconnecting = true;
	provider.wsconnected = false;
	provider.synced = false;

	websocket.onmessage = (event) => {
		if (provider.ws !== websocket) return;
		provider.wsLastMessageReceived = time.getUnixTime();
		const encoder = readMessage(provider, new Uint8Array(event.data), true);
		if (encoding.length(encoder) > 1) {
			websocket.send(encoding.toUint8Array(encoder));
		}
	};
	websocket.onerror = (event) => {
		if (provider.ws !== websocket) return;
		provider.emit("connection-error", [event, provider]);
	};
	websocket.onclose = () => {
		if (provider.ws !== websocket) return;
		provider.emit("connection-close", [provider]);
		provider.ws = null;
		provider.wsconnecting = false;
		if (provider.wsconnected) {
			provider.wsconnected = false;
			provider.synced = false;
			awarenessProtocol.removeAwarenessStates(
				provider.awareness,
				Array.from(provider.awareness.getStates().keys()).filter(
					(client) => client !== provider.doc.clientID,
				),
				provider,
			);
			provider.emit("status", ["disconnected"]);
		}
		provider.wsUnsuccessfulReconnects++;
		provider.emit("status", ["disconnected"]);
		if (provider.shouldConnect) scheduleReconnect(provider);
	};
	websocket.onopen = () => {
		if (provider.ws !== websocket) return;
		provider.wsLastMessageReceived = time.getUnixTime();
		provider.wsconnecting = false;
		provider.wsconnected = true;
		provider.wsUnsuccessfulReconnects = 0;
		provider.emit("status", ["connected"]);
		// sync step 1 al conectar
		const encoder = encoding.createEncoder();
		encoding.writeVarUint(encoder, messageSync);
		syncProtocol.writeSyncStep1(encoder, provider.doc);
		websocket.send(encoding.toUint8Array(encoder));
		// flush de updates bufferizados mientras estaba desconectado
		for (const pending of provider.pendingMessages.splice(0)) {
			websocket.send(pending);
		}
		// broadcast del estado local de awareness
		if (provider.awareness.getLocalState() !== null) {
			const encAwareness = encoding.createEncoder();
			encoding.writeVarUint(encAwareness, messageAwareness);
			encoding.writeVarUint8Array(
				encAwareness,
				awarenessProtocol.encodeAwarenessUpdate(provider.awareness, [
					provider.doc.clientID,
				]),
			);
			websocket.send(encoding.toUint8Array(encAwareness));
		}
	};
	provider.emit("status", ["connecting"]);
};

function reconnectDelay(provider: YSweetProvider): number {
	const exponent = math.max(0, provider.wsUnsuccessfulReconnects - 1);
	const capped = math.min(
		RECONNECT_BASE_DELAY_MS * math.pow(2, exponent),
		RECONNECT_MAX_DELAY_MS,
	);
	return math.floor(Math.random() * capped);
}

function scheduleReconnect(provider: YSweetProvider): void {
	if (!provider.shouldConnect || provider.reconnectTimeout !== null) return;
	const delay = reconnectDelay(provider);
	provider.reconnectTimeout = window.setTimeout(() => {
		provider.reconnectTimeout = null;
		setupWS(provider);
	}, delay);
}

const broadcastMessage = (provider: YSweetProvider, buf: Uint8Array): void => {
	const ws = provider.ws;
	if (provider.wsconnected && ws && ws.readyState === ws.OPEN) {
		ws.send(buf);
	} else {
		provider.pendingMessages.push(buf);
	}
};

export class YSweetProvider extends Observable<string> {
	url: string;
	doc: Y.Doc;
	awareness: awarenessProtocol.Awareness;
	wsconnected = false;
	wsconnecting = false;
	wsUnsuccessfulReconnects = 0;
	messageHandlers: Array<HandlerFunction>;
	pendingMessages: Uint8Array[] = [];
	private syncedState = false;
	ws: WebSocket | null = null;
	wsLastMessageReceived = 0;
	shouldConnect: boolean;
	reconnectTimeout: number | null = null;
	private checkInterval: number;

	constructor(
		serverUrl: string,
		doc: Y.Doc,
		{
			connect = true,
			awareness = new awarenessProtocol.Awareness(doc),
		}: {
			connect?: boolean;
			awareness?: awarenessProtocol.Awareness;
		} = {},
	) {
		super();
		this.url = serverUrl;
		this.doc = doc;
		this.awareness = awareness;
		this.shouldConnect = connect;
		this.messageHandlers = messageHandlers.slice();

		const updateHandler = (update: Uint8Array, origin: unknown) => {
			if (origin === this) return;
			const encoder = encoding.createEncoder();
			encoding.writeVarUint(encoder, messageSync);
			syncProtocol.writeUpdate(encoder, update);
			broadcastMessage(this, encoding.toUint8Array(encoder));
		};
		this.doc.on("update", updateHandler);

		const awarenessUpdateHandler = ({
			added,
			updated,
			removed,
		}: {
			added: number[];
			updated: number[];
			removed: number[];
		}) => {
			const changedClients = added.concat(updated).concat(removed);
			const encoder = encoding.createEncoder();
			encoding.writeVarUint(encoder, messageAwareness);
			encoding.writeVarUint8Array(
				encoder,
				awarenessProtocol.encodeAwarenessUpdate(awareness, changedClients),
			);
			broadcastMessage(this, encoding.toUint8Array(encoder));
		};
		awareness.on("update", awarenessUpdateHandler);

		this.checkInterval = window.setInterval(() => {
			if (
				this.wsconnected &&
				MESSAGE_RECONNECT_TIMEOUT < time.getUnixTime() - this.wsLastMessageReceived
			) {
				// sin mensajes por demasiado tiempo: reconectar
				this.ws?.close();
			}
		}, MESSAGE_RECONNECT_TIMEOUT / 10);

		if (connect) setupWS(this);
	}

	get synced() {
		return this.syncedState;
	}

	set synced(state) {
		if (this.syncedState !== state) {
			this.syncedState = state;
			this.emit("synced", [state]);
			this.emit("sync", [state]);
		}
	}

	get status(): ProviderStatus {
		if (this.ws?.readyState === WebSocket.OPEN) return "connected";
		if (this.ws?.readyState === WebSocket.CONNECTING) return "connecting";
		return "disconnected";
	}

	connect() {
		this.shouldConnect = true;
		if (this.reconnectTimeout !== null) return;
		if (!this.wsconnected && this.ws === null) {
			setupWS(this);
		}
	}

	disconnect() {
		this.shouldConnect = false;
		this.wsconnected = false;
		this.wsconnecting = false;
		this.synced = false;
		if (this.reconnectTimeout !== null) {
			window.clearTimeout(this.reconnectTimeout);
			this.reconnectTimeout = null;
		}
		if (this.ws !== null) {
			this.ws.close();
			this.ws = null;
		}
	}

	destroy() {
		window.clearInterval(this.checkInterval);
		if (this.reconnectTimeout !== null) {
			window.clearTimeout(this.reconnectTimeout);
			this.reconnectTimeout = null;
		}
		this.disconnect();
		this.awareness.destroy();
		this._observers.clear();
		super.destroy();
	}
}
