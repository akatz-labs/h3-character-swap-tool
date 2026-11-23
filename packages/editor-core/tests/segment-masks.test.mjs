import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { plan, maskCovers } from "../dist/domain/index.js";
const cases = JSON.parse(
  await readFile(new URL("./planner-golden.json", import.meta.url), "utf8"),
);
const base = cases.find((c) => c.name === "split").input;

function edit(clip, command) {
  let id = 0;
  const env = { now: () => 1700000000, uid: (p) => p + String(++id).padStart(12, "0") };
  return plan({ ...structuredClone(base), clip, command }, env);
}
// One 48-frame segment with a character and a mask tracked over the whole shot.
function tracked() {
  const clip = structuredClone(base.clip),
    shot = clip.shots[0];
  shot.cast_setup.mask_tracks = [
    {
      id: "mask-tracked",
      segment_id: "segment-1",
      character_id: null,
      name: "Fox",
      state: { mask: "mask-video", mask_frames: [0, 48], expansion: 64, mask_points: [], mask_applied_points: [] },
    },
  ];
  Object.assign(shot.segments[0], { use_mask: true });
  shot.segments[0].cast_inputs.mask_tracks = ["mask-tracked"];
  return clip;
}
const layout = (operation) => ({ method: "POST", route: "segments/layout", body: { operation } });
const masksOf = (clip, seg) =>
  clip.shots[0].cast_setup.mask_tracks.filter((t) => seg.cast_inputs.mask_tracks.includes(t.id));

test("both halves of a split keep the characters and the tracked mask", () => {
  const clip = edit(tracked(), layout({ operation: "split", frame: 6 })),
    [left, right] = clip.shots[0].segments;
  for (const seg of [left, right]) {
    assert.deepEqual(seg.cast_inputs.characters, ["actor"]);
    assert.deepEqual(seg.cast_inputs.mask_assignments, {});
    assert.equal(seg.use_mask, true);
    const [mask] = masksOf(clip, seg);
    assert.equal(mask.segment_id, seg.id);
    assert.equal(mask.state.mask, "mask-video");
    assert.deepEqual(mask.state.mask_frames, [0, 48]);
    assert.ok(maskCovers(mask, seg));
  }
  assert.notEqual(masksOf(clip, left)[0].id, masksOf(clip, right)[0].id);
});

test("extending a segment past its mask's tracked frames needs tracking", () => {
  const split = edit(tracked(), layout({ operation: "split", frame: 24 })),
    shot = split.shots[0];
  // The left half was tracked again on its own frames.
  masksOf(split, shot.segments[0])[0].state.mask_frames = [0, 24];
  const moved = edit(split, layout({ operation: "boundary", index: 1, frame: 30 })),
    [left, right] = moved.shots[0].segments;
  assert.ok(!maskCovers(masksOf(moved, left)[0], left));
  assert.ok(maskCovers(masksOf(moved, right)[0], right));
});

test("removing a character keeps the segment's masks", () => {
  const clip = edit(tracked(), { method: "DELETE", route: "cast/characters/actor", body: {} }),
    seg = clip.shots[0].segments[0];
  assert.deepEqual(seg.cast_inputs.characters, []);
  assert.deepEqual(seg.cast_inputs.mask_tracks, ["mask-tracked"]);
  assert.equal(seg.use_mask, true);
});

test("deleting a mask removes it from the render", () => {
  const clip = edit(tracked(), { method: "DELETE", route: "cast/masks/mask-tracked", body: {} }),
    seg = clip.shots[0].segments[0];
  assert.deepEqual(seg.cast_inputs.mask_tracks, []);
  assert.equal(seg.use_mask, false);
});

test("a reset mask forgets its tracked frames", () => {
  const clip = edit(tracked(), {
    method: "POST",
    route: "mask-settings",
    body: { changes: { selection: "leftmost" } },
    trackId: "mask-tracked",
  });
  const state = clip.shots[0].cast_setup.mask_tracks[0].state;
  assert.equal(state.mask, null);
  assert.equal("mask_frames" in state, false);
});

test("tracked frame ranges must lie inside the shot", () => {
  for (const range of [[10, 5], [0, 49], [-1, 4], [0]]) {
    const clip = tracked();
    clip.shots[0].cast_setup.mask_tracks[0].state.mask_frames = range;
    assert.throws(
      () => edit(clip, { method: "PATCH", route: "cast/masks/mask-tracked", body: { name: "Renamed" } }),
      /Invalid tracked mask frame range/,
    );
  }
});
