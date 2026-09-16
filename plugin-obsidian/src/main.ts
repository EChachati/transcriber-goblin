import {
	Notice,
	Plugin,
	PluginSettingTab,
	App,
	Setting,
	WorkspaceLeaf,
} from "obsidian";
import { GoblinApi } from "./api";
import { DEFAULT_SETTINGS, type TgSettings } from "./settings";
import { DOCS_VIEW_TYPE, DocsListView } from "./docsView";
import { DOC_VIEW_TYPE, DocEditorView } from "./docEditor";

export default class TranscriberGoblinPlugin extends Plugin {
	settings: TgSettings;
	api: GoblinApi;

	async onload() {
		await this.loadSettings();
		this.api = new GoblinApi(this.settings);

		this.registerView(DOCS_VIEW_TYPE, (leaf) => new DocsListView(leaf, this));
		this.registerView(DOC_VIEW_TYPE, (leaf) => new DocEditorView(leaf, this));

		this.addRibbonIcon("ghost", "Notas Goblin", () =>
			this.activateDocsView(),
		);

		this.addCommand({
			id: "open-docs-list",
			name: "Abrir lista de notas",
			callback: () => this.activateDocsView(),
		});

		this.addCommand({
			id: "verify-connection",
			name: "Verificar conexión con el backend",
			callback: () => this.verifyConnection(),
		});

		this.addSettingTab(new TgSettingTab(this.app, this));
	}

	onunload() {}

	async loadSettings() {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
	}

	async saveSettings() {
		await this.saveData(this.settings);
		this.api = new GoblinApi(this.settings);
	}

	async activateDocsView(): Promise<WorkspaceLeaf> {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(DOCS_VIEW_TYPE)[0];
		if (!leaf) {
			leaf = workspace.getRightLeaf(false)!;
			await leaf.setViewState({
				type: DOCS_VIEW_TYPE,
				active: true,
			});
		}
		workspace.revealLeaf(leaf);
		return leaf;
	}

	async openDoc(docId: string, title?: string): Promise<void> {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(DOC_VIEW_TYPE).find(
			(l) => (l.view as DocEditorView).getState()?.docId === docId,
		);
		if (!leaf) {
			leaf = workspace.getLeaf("tab");
		}
		await leaf.setViewState({
			type: DOC_VIEW_TYPE,
			active: true,
			state: { docId, title },
		});
		workspace.revealLeaf(leaf);
	}

	async verifyConnection() {
		if (!this.settings.personalToken) {
			new Notice("Goblin: primero configura tu token en los ajustes.");
			return;
		}
		try {
			const me = await this.api.me();
			new Notice(`Goblin: conectado como «${me.name}» ✓`);
		} catch (err) {
			new Notice(
				`Goblin: fallo de conexión (${err instanceof Error ? err.message : err})`,
			);
		}
	}
}

class TgSettingTab extends PluginSettingTab {
	constructor(
		app: App,
		private plugin: TranscriberGoblinPlugin,
	) {
		super(app, plugin);
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();
		containerEl.createEl("h2", { text: "Transcriber Goblin" });

		new Setting(containerEl)
			.setName("URL del backend")
			.setDesc("API de Transcriber Goblin (sin / al final).")
			.addText((text) =>
				text
					.setPlaceholder("http://localhost:8000")
					.setValue(this.plugin.settings.apiUrl)
					.onChange(async (value) => {
						this.plugin.settings.apiUrl = value.trim();
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName("Token personal")
			.setDesc(
				"El token que recibiste al canjear tu invitación (se muestra una sola vez).",
			)
			.addText((text) => {
				text.inputEl.type = "password";
				text
					.setPlaceholder("goblin-…")
					.setValue(this.plugin.settings.personalToken)
					.onChange(async (value) => {
						this.plugin.settings.personalToken = value.trim();
						await this.plugin.saveSettings();
					});
			});

		new Setting(containerEl)
			.setName("Tu nombre")
			.setDesc("Nombre que verán los demás cursores colaborativos.")
			.addText((text) =>
				text
					.setPlaceholder("spooky")
					.setValue(this.plugin.settings.userName)
					.onChange(async (value) => {
						this.plugin.settings.userName = value.trim();
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl).addButton((btn) =>
			btn
				.setButtonText("Probar conexión")
				.setCta()
				.onClick(() => this.plugin.verifyConnection()),
		);
	}
}
