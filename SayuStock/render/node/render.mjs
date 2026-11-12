// One bounded render per process: node render.mjs request.json output.png.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const stage = (name) => process.stderr.write(`stage=${name}\n`);
stage("node.start");

if (Number(process.versions.node.split(".")[0]) !== 24) {
  throw new Error("SayuStock rendering requires Node.js 24.x");
}

const modules = process.env.SAYUSTOCK_RENDER_MODULES || path.join(import.meta.dirname, "node_modules");
const require = createRequire(path.join(modules, "noop.js"));
for (const name of ["core", "helpers"]) {
  const manifest = JSON.parse(fs.readFileSync(path.join(modules, "@takumi-rs", name, "package.json"), "utf8"));
  if (manifest.version !== "2.14.0") throw new Error(`Expected @takumi-rs/${name} 2.14.0`);
}
const { Renderer } = require("@takumi-rs/core");
const { fromHtml } = require("@takumi-rs/helpers/html");

// One line per failure keeps the caller's log readable.
process.on("uncaughtException", (error) => {
  process.stderr.write(`error=${String(error && error.message || error).replace(/\s+/g, " ")}
`);
  process.exit(2);
});

const [requestPath, outputPath] = process.argv.slice(2);
const request = JSON.parse(fs.readFileSync(requestPath, "utf8"));

const renderer = new Renderer();
stage("fonts");
for (const font of request.fonts || []) {
  await renderer.registerFont({ name: font.name, data: fs.readFileSync(font.path) });
}

stage("layout");
const { node, css } = fromHtml(request.html);
const scale = request.scale || 1;
stage("render");
const image = await renderer.render(node, {
  width: Math.round(request.width * scale),
  devicePixelRatio: scale,
  format: request.format || "png",
  css: [...css, ...(request.css || [])],
  lang: request.lang || "zh-CN",
});
fs.writeFileSync(outputPath, image);
stage("complete");
