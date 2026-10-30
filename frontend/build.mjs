// Bundles the SPA. No inline scripts (strict CSP).
//   node build.mjs                      → dist/        server edition (served by the Python API)
//   node build.mjs --edition=browser    → dist-pages/  browser edition (static, for GitHub Pages)
import * as esbuild from "esbuild";
import { mkdirSync, readFileSync, writeFileSync, rmSync, readdirSync } from "node:fs";

const watch = process.argv.includes("--watch");
const edition = (process.argv.find((a) => a.startsWith("--edition=")) || "--edition=server").split("=")[1];
const browser = edition === "browser";
const out = browser ? "dist-pages" : "dist";
rmSync(out, { recursive: true, force: true });
mkdirSync(`${out}/assets`, { recursive: true });

const opts = {
  entryPoints: { app: "src/main.tsx" },
  bundle: true, minify: !watch, sourcemap: watch, format: "esm", target: "es2022",
  outdir: `${out}/assets`, entryNames: watch ? "[name]" : "[name]-[hash]", loader: { ".css": "css" },
  define: { "process.env.NODE_ENV": watch ? '"development"' : '"production"', __EDITION__: JSON.stringify(browser ? "browser" : "server") },
  metafile: true,
};

// Browser edition: a strict Content-Security-Policy in the page itself. connect-src 'none' means the app
// cannot send anything (footage, frames, projects) to any server — the privacy promise is enforced by the browser.
const PAGES_CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; " +
  "connect-src 'none'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; worker-src 'self' blob:; frame-src 'self' blob:";

function writeIndex() {
  const files = readdirSync(`${out}/assets`);
  const js = files.find((f) => f.startsWith("app") && f.endsWith(".js"));
  const css = files.find((f) => f.startsWith("app") && f.endsWith(".css"));
  const prefix = browser ? "./assets/" : "/assets/";
  let html = readFileSync("index.html", "utf8").replace("%JS%", prefix + js).replace("%CSS%", prefix + css);
  if (browser) {
    html = html.replace('<meta name="referrer" content="no-referrer">',
      `<meta name="referrer" content="no-referrer">\n  <meta http-equiv="Content-Security-Policy" content="${PAGES_CSP}">\n  <meta name="description" content="CineVision Studio — private cinematic previsualisation that runs entirely in your browser.">`);
    writeFileSync(`${out}/.nojekyll`, "");
    writeFileSync(`${out}/404.html`, html);
  }
  writeFileSync(`${out}/index.html`, html);
}

if (watch) {
  const ctx = await esbuild.context({ ...opts, plugins: [{ name: "index", setup(b) { b.onEnd(writeIndex); } }] });
  await ctx.watch();
  console.log(`watching (${edition})…`);
} else {
  await esbuild.build(opts);
  writeIndex();
  console.log(`built ${out}/ (${edition} edition)`);
}
