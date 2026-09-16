import { ItemView, WorkspaceLeaf } from "obsidian";
import type TranscriberGoblinPlugin from "./main";
import type { DocInfo } from "./api";

export const DOCS_VIEW_TYPE = "goblin-docs-list";

export class DocsListView extends ItemView {
	private docs: DocInfo[] = [];
	private loading = false;
	private error: string | null = null;

	constructor(
		leaf: WorkspaceLeaf,
		private plugin: TranscriberGoblinPlugin,
	) {
		super(leaf);
		this.navigation = false;
	}

	getViewType(): string {
		return DOCS_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Notas Goblin";
	}

	getIcon(): string {
		return "ghost";
	}

	async onOpen() {
		const content = this.contentEl;
		content.empty();
		content.addClass("tg-docs-view");

		const header = content.createDiv("tg-header");
		header.createSpan({ text: "Notas Goblin" });
		const actions = header.createDiv("tg-actions");
		actions
			.createEl("button", { attr: { "aria-label": "Refrescar" }, text: "↻" })
			.addEventListener("click", () => this.refresh());
		actions
			.createEl("button", { attr: { "aria-label": "Nueva nota" }, text: "+" })
			.addEventListener("click", () => this.createNew());

		this.listEl = content.createDiv("tg-list");
		await this.refresh();
	}

	private listEl: HTMLElement;

	private async refresh() {
		if (this.loading) return;
		if (!this.plugin.settings.personalToken) {
			this.renderMessage(
				"Configura la URL del backend y tu token en los ajustes del plugin.",
			);
			return;
		}
		this.loading = true;
		this.error = null;
		this.renderLoading();
		try {
			this.docs = await this.plugin.api.listDocs();
		} catch (err) {
			this.error = err instanceof Error ? err.message : String(err);
		} finally {
			this.loading = false;
			this.renderList();
		}
	}

	private async createNew() {
		let title: string | null;
		do {
			title = await window.prompt("Título de la nota");
			if (title === null) return;
			title = title.trim();
		} while (!title);
		try {
			const doc = await this.plugin.api.createDoc(title);
			await this.refresh();
			await this.plugin.openDoc(doc.id, doc.title);
		} catch (err) {
			new Notification("Goblin", { body: `No se pudo crear la nota: ${err}` });
		}
	}

	private renderMessage(message: string) {
		this.listEl.empty();
		this.listEl.createDiv({
			cls: "tg-empty",
			text: message,
		});
	}

	private renderLoading() {
		this.listEl.empty();
		this.listEl.createDiv({ cls: "tg-empty", text: "Cargando…" });
	}

	private renderList() {
		this.listEl.empty();
		if (this.error) {
			this.listEl.createDiv({
				cls: "tg-empty tg-error",
				text: `Error: ${this.error}`,
			});
			return;
		}
		if (this.docs.length === 0) {
			this.listEl.createDiv({
				cls: "tg-empty",
				text: "Sin notas todavía. Crea una con «+».",
			});
			return;
		}
		for (const doc of this.docs) {
			const row = this.listEl.createDiv("tg-doc-row");
			row.createSpan({ cls: "tg-doc-title", text: doc.title || doc.id });
			row.createSpan({ cls: "tg-doc-role", text: doc.role });
			row.addEventListener("click", () =>
				this.plugin.openDoc(doc.id, doc.title),
			);
		}
	}

	async onClose() {}
}
