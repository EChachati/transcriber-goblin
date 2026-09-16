export interface TgSettings {
	apiUrl: string;
	personalToken: string;
	userName: string;
}

export const DEFAULT_SETTINGS: TgSettings = {
	apiUrl: "http://localhost:8000",
	personalToken: "",
	userName: "",
};
