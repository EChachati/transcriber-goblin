import { ItemView, Notice, WorkspaceLeaf } from "obsidian";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { syntaxHighlighting, defaultHighlightStyle, indentOnInput, bracketMatching } from "@codemirror/language";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import * as Y from "yjs";
import { yCollab } from "y-codemirror.next";
import * as awarenessProtocol from "y-protocols/awareness";

import type TranscriberGoblinPlugin from "./main";
import { YSweetProvider, type ProviderStatus } from "./provider";

export const DOC_VIEW_TYPE = "goblin-doc-editor";

export interface DocViewState {
	docId: string;
	title?: string;
}

export class DocEditorView extends ItemView {
	private state: DocViewState | null = null;

	private ydoc: Y.Doc | null = null;
	private provider: YSweetProvider | null = null;
	private editor: EditorView | null = null;
	private statusEl!: HTMLElement;
	private cmContainer!: HTMLElement;

	constructor(
		leaf: WorkspaceLeaf,
		private plugin: TranscriberGoblinPlugin,
	) {
		super(leaf);
	}

	getViewType(): string {
		return DOC_VIEW_TYPE;
	}

	getDisplayText(): string {
		return this.state?.title || this.state?.docId || "Nota Goblin";
	}

	getIcon(): string {
		return "file-pen-line";
	}

	setState(state: Record<string, unknown>, _result: any): Promise<void> {
		const prevId = this.state?.docId;
		this.state = state as unknown as DocViewState;
		if (prevId !== this.state.docId && this.cmContainer) {
			void this.loadDoc();
		}
		return Promise.resolve();
	}

	getState(): Record<string, unknown> {
		return { ...(this.state ?? { docId: "" }) };
	}

	async onOpen() {
		this.contentEl.empty();
		this.contentEl.addClass("tg-editor-view");

		const header = this.contentEl.createDiv("tg-header");
		header.createSpan({ cls: "tg-title", text: this.getDisplayText() });
		this.statusEl = header.createSpan({ cls: "tg-status", text: "…" });

		this.cmContainer = this.contentEl.createDiv("tg-cm-container");
		if (this.state) await this.loadDoc();
	}

	private async loadDoc() {
		this.teardown();
		if (!this.state?.docId) return;

		const { docId, title } = this.state;
		this.contentEl
			.querySelector(".tg-title")
			?.setText(title || docId);
		this.setStatus("connecting");

		let wsUrl: string;
		try {
			const token = await this.plugin.api.getDocToken(docId);
			wsUrl = this.plugin.api.websocketUrl(token);
		} catch (err) {
			new Notice(`Goblin: no se pudo obtener token (${err instanceof Error ? err.message : err})`);
			this.setStatus("disconnected");
			return;
		}

		this.ydoc = new Y.Doc();
		this.provider = new YSweetProvider(wsUrl, this.ydoc);
		this.provider.on("status", (status: ProviderStatus) => {
			this.setStatus(status);
		});
		this.provider.on("synced", () => this.setStatus(this.provider!.status));

		const ytext = this.ydoc.getText("content");
		const userName =
			this.plugin.settings.userName ||
			this.ydoc.clientID.toString().slice(-6);

		const awareness = this.provider.awareness;
		awareness.setLocalStateField("user", { name: userName });

		const undoManager = new Y.UndoManager(ytext);

		this.editor = new EditorView({
			state: EditorState.create({
				doc: ytext.toString(),
				extensions: [
					lineNumbers(),
					history(),
					indentOnInput(),
					bracketMatching(),
					highlightActiveLine(),
					highlightSelectionMatches(),
					keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap]),
					markdown({ base: markdownLanguage }),
					syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
					EditorView.lineWrapping,
					yCollab(ytext, awareness, { undoManager }),
				],
			}),
			parent: this.cmContainer,
		});
	}

	private setStatus(status: ProviderStatus) {
		const labels: Record<ProviderStatus, string> = {
			connected: "● conectado",
			connecting: "○ conectando",
			disconnected: "○ desconectado",
		};
		this.statusEl.setText(labels[status] ?? status);
		this.statusEl.toggleClass("tg-online", status === "connected");
	}

	private teardown() {
		this.editor?.destroy();
		this.editor = null;
		this.provider?.destroy();
		this.provider = null;
		this.ydoc?.destroy();
		this.ydoc = null;
	}

	async onClose() {
		// retirar el estado local de awareness antes de cerrar el socket
		if (this.provider && this.ydoc) {
			awarenessProtocol.removeAwarenessStates(
				this.provider.awareness,
				[this.ydoc.clientID],
				"view closed",
			);
		}
		this.teardown();
	}
}
