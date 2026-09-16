import { requestUrl, type RequestUrlParam } from "obsidian";
import type { TgSettings } from "./settings";

export interface DocInfo {
	id: string;
	title: string;
	created_at: string;
	role: string;
}

/** Token de conexión que devuelve POST /docs/{id}/token (formato y-sweet). */
export interface ClientToken {
	/** Websocket del documento (sin query). */
	url: string;
	baseUrl?: string;
	docId: string;
	token: string;
	authorization?: "full" | "read-only";
}

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string,
	) {
		super(message);
	}
}

export class GoblinApi {
	constructor(private settings: TgSettings) {}

	private async req(
		method: string,
		path: string,
		body?: unknown,
	): Promise<unknown> {
		const base = this.settings.apiUrl.replace(/\/+$/, "");
		const params: RequestUrlParam = {
			url: base + path,
			method,
			headers: {
				Authorization: `Bearer ${this.settings.personalToken}`,
			},
			contentType: "application/json",
		};
		if (body !== undefined) params.body = JSON.stringify(body);

		const res = await requestUrl(params);
		if (res.status >= 400) throw new ApiError(res.status, res.text || res.status.toString());
		return res.json;
	}

	async me(): Promise<{ id: string; name: string }> {
		return (await this.req("GET", "/me")) as { id: string; name: string };
	}

	async listDocs(): Promise<DocInfo[]> {
		return (await this.req("GET", "/docs")) as DocInfo[];
	}

	async createDoc(title: string): Promise<{ id: string; title: string }> {
		return (await this.req("POST", "/docs", { title })) as {
			id: string;
			title: string;
		};
	}

	async getDocToken(docId: string): Promise<ClientToken> {
		return (await this.req("POST", `/docs/${docId}/token`)) as ClientToken;
	}

	/**
	 * URL websocket completa con el token como query param.
	 *
	 * Ojo: el campo `url` del client token llega como `…/d/<id>/ws`, pero las
	 * rutas reales de y-sweet son `/d/:doc_id/ws/:doc_id2` (el docId va dos
	 * veces). Se normaliza aquí para no depender de cómo lo genere el server.
	 */
	websocketUrl(token: ClientToken): string {
		let url = token.url?.replace(/\/+$/, "") ?? "";
		if (!url) {
			url = `${(token.baseUrl ?? "").replace(/\/+$/, "")}/ws/${token.docId}`;
		} else if (/\/ws$/.test(url)) {
			url = `${url}/${token.docId}`;
		}
		if (token.token) {
			const sep = url.includes("?") ? "&" : "?";
			url += `${sep}token=${encodeURIComponent(token.token)}`;
		}
		return url;
	}
}
