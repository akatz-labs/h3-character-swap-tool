import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { plan } from "../dist/domain/index.js";
const fixtures = JSON.parse(
  readFileSync(new URL("./planner-golden.json", import.meta.url)),
);
const saved = fixtures.find((c) => c.name === "edit performance_prompt");
const env = { now: () => 1700000000, uid: (p) => p + "000000000001" };

test("failed edit cannot poison the next account or request", () => {
  const bad = structuredClone(saved.input);
  bad.command.body.changes.seed = -1;
  const before = structuredClone(bad);
  assert.throws(() => plan(bad, env));
  assert.deepEqual(bad, before);
  assert.deepEqual(plan(saved.input, env), saved.result);
  const other = structuredClone(saved.input);
  other.clip.id = "another-account-clip";
  const result = plan(other, env);
  assert.equal(result.id, "another-account-clip");
  assert.deepEqual(plan(saved.input, env), saved.result);
});
test("changing an overridden shot reference back to its clip reference invalidates output", () => {
  const input = structuredClone(saved.input);
  input.clip.reference = "clip-reference";
  input.clip.result = "old-output";
  input.clip.shots[0].reference = "shot-reference";
  input.clip.shots[0].anchor_approved = true;
  input.command = {
    method: "PATCH",
    route: "shot-settings",
    body: { changes: { reference: "clip-reference" } },
  };
  const result = plan(input, env);
  assert.equal(result.result, null);
  assert.equal(result.shots[0].anchor_approved, false);
  assert.equal(input.clip.result, "old-output");
});
test("unchanged inherited reference retains the existing output", () => {
  const input = structuredClone(saved.input);
  input.clip.reference = "clip-reference";
  input.clip.result = "old-output";
  delete input.clip.shots[0].reference;
  input.clip.shots[0].anchor_approved = true;
  input.command = {
    method: "PATCH",
    route: "shot-settings",
    body: { changes: { reference: "clip-reference" } },
  };
  const result = plan(input, env);
  assert.equal(result.result, "old-output");
  assert.equal(result.shots[0].anchor_approved, true);
});
