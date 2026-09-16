import esbuild from "esbuild";
import process from "process";

const prod = process.argv[2] === "build";
const watch = !prod || process.argv.includes("--watch");

// Obsidian provee CodeMirror 6 y moment en runtime: quedan como externals
// para no duplicar instancias (los instanceof entre copias fallan).
const external = [
	"obsidian",
	"electron",
	"@codemirror/autocomplete",
	"@codemirror/collab",
	"@codemirror/commands",
	"@codemirror/language",
	"@codemirror/lint",
	"@codemirror/search",
	"@codemirror/state",
	"@codemirror/view",
	"@lezer/common",
	"@lezer/highlight",
	"@lezer/lr",
];

const context = await esbuild.context({
	entryPoints: ["src/main.ts"],
	bundle: true,
	external,
	format: "cjs",
	target: "es2018",
	minify: prod,
	sourcemap: prod ? false : "inline",
	logLevel: "info",
	treeShaking: true,
	outfile: "main.js",
});

if (watch) {
	await context.watch();
} else {
	await context.rebuild();
	process.exit(0);
}
