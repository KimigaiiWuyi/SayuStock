// Install explicitly, smoke-test, then switch; request handlers never install.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const plugin = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const core = path.resolve(plugin, "../../..");
const source = path.join(plugin, "SayuStock/render/node");
const args = process.argv.slice(2);
const options = new Map();
for (let i = 0; i < args.length; i += 2) {
  if (!["--runtime-dir", "--font", "--npm-cli"].includes(args[i]) || !args[i + 1]) {
    throw new Error("Usage: node tools/install-render-runtime.mjs [--runtime-dir PATH] [--font PATH] [--npm-cli PATH]");
  }
  options.set(args[i], path.resolve(args[i + 1]));
}
if (Number(process.versions.node.split(".")[0]) !== 24) throw new Error("Run this installer with Node.js 24.x");
if (!options.has("--runtime-dir") && !fs.existsSync(path.join(core, "gsuid_core/data_store.py"))) {
  throw new Error("Standalone checkout: pass --runtime-dir <Core>/data/SayuStock/render-runtime and --font <MiSansVF.ttf>");
}
const root = options.get("--runtime-dir") || path.join(core, "data/SayuStock/render-runtime");
const font = options.get("--font") || path.join(core, "gsuid_core/utils/fonts/MiSansVF.ttf");
const nodeDir = path.dirname(process.execPath);
const npmCandidates = options.has("--npm-cli") ? [options.get("--npm-cli")] : [
  path.join(nodeDir, "node_modules/npm/bin/npm-cli.js"),
  path.resolve(nodeDir, "../lib/node_modules/npm/bin/npm-cli.js"),
];
const npm = npmCandidates.find((candidate) => fs.existsSync(candidate));
if (!npm) throw new Error("npm-cli.js was not found beside Node; install Node with npm or pass --npm-cli PATH");
if (!fs.existsSync(font)) throw new Error(`MiSans font not found: ${font}`);

fs.mkdirSync(root, { recursive: true });
const stage = fs.mkdtempSync(path.join(root, "stage-"));
for (const name of ["package.json", "package-lock.json"]) fs.copyFileSync(path.join(source, name), path.join(stage, name));
const env = { ...process.env, npm_config_cache: path.join(root, "npm-cache") };
const install = spawnSync(process.execPath, [npm, "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"], {
  cwd: stage, env, stdio: "inherit", timeout: 300_000, windowsHide: true,
});
if (install.status !== 0) throw new Error(`npm ci failed; active runtime unchanged. Staging directory: ${stage}`);

const request = path.join(stage, "smoke.json");
const output = path.join(stage, "smoke.png");
fs.writeFileSync(request, JSON.stringify({
  html: '<div style="display:flex;width:320px;height:90px;background:#10151d;color:#e6edf3;font-family:MiSans;font-size:24px">早柚股票 123.45%</div>',
  width: 320, scale: 1, fonts: [{ name: "MiSans", path: font }],
}));
const smoke = spawnSync(process.execPath, [path.join(source, "render.mjs"), request, output], {
  env: { ...env, SAYUSTOCK_RENDER_MODULES: path.join(stage, "node_modules") },
  stdio: "inherit", timeout: 45_000, windowsHide: true,
});
if (smoke.status !== 0 || !fs.existsSync(output)) throw new Error(`Render smoke failed; active runtime unchanged: ${stage}`);
const png = fs.readFileSync(output);
if (!png.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) || png.readUInt32BE(16) !== 320) {
  throw new Error(`Render smoke produced an invalid image; active runtime unchanged: ${stage}`);
}
fs.unlinkSync(request);
fs.unlinkSync(output);
fs.writeFileSync(path.join(stage, "runtime.json"), JSON.stringify({
  node: process.versions.node, takumi: "2.14.0", installedAt: new Date().toISOString(),
}, null, 2) + "\n");

const current = path.join(root, "current");
const backup = path.join(root, `previous-${Date.now()}`);
const hadCurrent = fs.existsSync(current);
if (hadCurrent) fs.renameSync(current, backup);
try {
  fs.renameSync(stage, current);
} catch (error) {
  if (hadCurrent) fs.renameSync(backup, current);
  throw error;
}
console.log(`Takumi 2.14.0 installed and verified: ${current}`);
console.log(`Use Node ${process.versions.node} on Core's PATH, or set SAYUSTOCK_RENDER_NODE=${process.execPath}`);
if (hadCurrent) console.log(`Previous runtime retained: ${backup}`);
