import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { plan } from "../dist/domain/index.js";
const cases = JSON.parse(
  await readFile(new URL("./planner-golden.json", import.meta.url), "utf8"),
);
for (const c of cases)
  test(c.name, () => {
    let id = 0;
    const env = {
      now: () => 1700000000,
      uid: (p) => p + String(++id).padStart(12, "0"),
    };
    const original = JSON.stringify(c.input);
    if(c.input.operation==='render-input'){
      assert.throws(()=>plan(c.input,env),/updated editor/);
      assert.equal(JSON.stringify(c.input),original);return;
    }
    if (c.error) assert.throws(() => plan(c.input, env), { message: c.error });
    else assert.deepEqual(plan(c.input, env), c.result);
    assert.equal(
      JSON.stringify(c.input),
      original,
      "planner must not mutate input",
    );
  });
