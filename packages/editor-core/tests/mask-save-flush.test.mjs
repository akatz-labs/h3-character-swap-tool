import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const text = await readFile(
  new URL(process.env.MASK_EDITOR_SOURCE || "../static/mask-editor.js", import.meta.url),
  "utf8",
);
const point = (id) => ({ id, frame: 20, x: 0.5, y: 0.5, label: 1 });

// Hosted saves take a full project round trip, so the autosave timer and a
// segment switch can both be waiting on the same in-flight save.
test("overlapping mask flushes send each draft once against the current base", async () => {
  const server = { points: [point("a")], expansion: 64 },
    sent = [];
  const env = {
    S: { clip: "c", shot: "s" },
    shot: () => ({ segment_masks_version: 1 }),
    sameSettings: (a, b) => JSON.stringify(a) === JSON.stringify(b),
    maskKeepDraft: () => {},
    updateMaskEditorStatus: () => {},
    $: () => null,
    clearTimeout,
    setTimeout,
    maskPatch: async (changes, scope, expected) => {
      const key = Object.keys(changes)[0],
        saved = key === "mask_points" ? server.points : server.expansion;
      if (JSON.stringify(saved) !== JSON.stringify(expected[key]))
        throw Error("Saved settings changed elsewhere. Your unsaved edits have been preserved.");
      sent.push(changes);
      await new Promise((r) => setTimeout(r, 30));
      if (key === "mask_points") server.points = changes.mask_points;
      else server.expansion = changes.expansion;
    },
  };
  vm.createContext(env);
  vm.runInContext(
    "let maskEditState=null;const maskCopy=(v)=>JSON.parse(JSON.stringify(v));" +
      text.slice(text.indexOf("function maskPointDraft"), text.indexOf("// Create the server track")) +
      ";globalThis.flush=saveMaskEdits;globalThis.setEditor=(e)=>{maskEditState=e;};",
    env,
  );
  const e = {
    key: "c:s",
    trackId: "track",
    points: [point("a"), point("b")],
    basePoints: [point("a")],
    padding: 64,
    basePadding: 64,
  };
  env.setEditor(e);
  const first = env.flush(e);
  await new Promise((r) => setTimeout(r, 5));
  // A new point and a padding change arrive while the first save is in flight,
  // then the timer and a guarded action flush at the same time.
  e.points.push(point("c"));
  e.padding = 80;
  await Promise.all([first, env.flush(e), env.flush(e), env.flush()]);
  const ids = (points) => JSON.stringify(points.map((p) => p.id));
  assert.equal(ids(server.points), '["a","b","c"]');
  assert.equal(server.expansion, 80);
  assert.equal(ids(e.basePoints), '["a","b","c"]');
  assert.equal(sent.filter((c) => c.mask_points).length, 2);
  assert.equal(sent.filter((c) => "expansion" in c).length, 1);
  assert.equal(e.pointFlush, null);
  assert.equal(e.paddingFlush, null);
  // A flush that finds nothing to save must not block the next one.
  e.points.push(point("d"));
  await env.flush(e);
  assert.equal(ids(server.points), '["a","b","c","d"]');
});
