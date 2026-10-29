// Bundles the SPA into dist/ with content-hashed assets. No inline scripts (strict CSP).
import * as esbuild from "esbuild";
import { mkdirSync, readFileSync, writeFileSync, rmSync, readdirSync } from "node:fs";

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
mkdirSync("dist/assets", { recursive: true });

const opts = {
  entryPoints: { app: "src/main.tsx" },
  bundle: true, minify: !watch, sourcemap: watch, format: "esm", target: "es2022",
  outdir: "dist/assets", entryNames: watch ? "[name]" : "[name]-[hash]", loader: { ".css": "css" },
  define: { "process.env.NODE_ENV": watch ? '"development"' : '"production"' }, metafile: true,
};

function writeIndex() {
  const files = readdirSync("dist/assets");
  const js = files.find((f) => f.startsWith("app") && f.endsWith(".js"));
  const css = files.find((f) => f.startsWith("app") && f.endsWith(".css"));
  const html = readFileSync("index.html", "utf8")
    .replace("%JS%", `/assets/${js}`).replace("%CSS%", `/assets/${css}`);
  writeFileSync("dist/index.html", html);
}

if (watch) {
  const ctx = await esbuild.context({ ...opts, plugins: [{ name: "index", setup(b) { b.onEnd(writeIndex); } }] });
  await ctx.watch();
  console.log("watching…");
} else {
  await esbuild.build(opts);
  writeIndex();
  console.log("built dist/");
}
