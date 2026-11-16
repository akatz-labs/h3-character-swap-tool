import { build } from "esbuild";
import { execFileSync } from "node:child_process";
const external = [
  "react",
  "react-dom",
  "react-dom/*",
  "@akatz/app-kit",
  "@akatz/app-kit/*",
  "zod",
];
await build({
  entryPoints: ["web/src/index.ts"],
  bundle: true,
  format: "esm",
  platform: "browser",
  target: "es2022",
  outfile: "dist/index.js",
  external,
  loader: { ".svg": "dataurl", ".png": "dataurl", ".webp": "dataurl" },
  sourcemap: true,
});
await build({
  entryPoints: ["domain/index.ts"],
  bundle: true,
  format: "esm",
  platform: "neutral",
  target: "es2022",
  outfile: "dist/domain/index.js",
  external: ["zod"],
  sourcemap: true,
});
execFileSync(
  process.execPath,
  [
    "../../node_modules/typescript/bin/tsc",
    "-p",
    "tsconfig.json",
    "--noEmit",
    "false",
    "--declaration",
    "--emitDeclarationOnly",
    "--outDir",
    "dist/types",
  ],
  { stdio: "inherit" },
);
