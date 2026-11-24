import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { plan, hasTrackedMask } from "../dist/domain/index.js";
const cases = JSON.parse(
  await readFile(new URL("./planner-golden.json", import.meta.url), "utf8"),
);
const base = cases.find((c) => c.name === "split").input;

let id = 0;
function edit(clip, command) {
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
test("layout requires confirmation even when a segment mask is disabled", () => {
  const clip = tracked();
  clip.shots[0].segments[0].use_mask = false;
  const op = { operation: "split", frame: 24 };
  assert.throws(() => edit(clip, layout(op)), /Review the masks/);
  const shot = edit(clip, layout({...op, remove_mask_ids: ["mask-tracked"]})).shots[0];
  assert.equal(shot.cast_setup.mask_tracks.length, 0);
  for (const seg of shot.segments) {
    assert.deepEqual(seg.cast_inputs.characters, ["actor"]);
    assert.deepEqual(seg.cast_inputs.mask_tracks, []);
    assert.equal(seg.use_mask, false);
  }
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

test("frame-only previews stay outside the render union until the segment is tracked", () => {
  const versions = [{asset: "p", label: "Frame update"}, {asset: "t", label: "Tracked segment"}, {asset:"c", label:"Edited frame 2"}];
  for (const [mask, expected] of [["p", false], ["t", true], ["c", true]]) {
    assert.equal(hasTrackedMask({mask, mask_versions: versions}), expected);
  }
});

test("segment edits enforce one second at both sides and absorb short partition tails", () => {
  const clip=structuredClone(base.clip), shot=clip.shots[0];
  shot.end=shot.start+107;
  shot.segments[0].end=107;
  const apply=(op)=>edit(clip,layout(op)).shots[0].segments;
  const ranges=(rows)=>rows.map(({start,end})=>[start,end]);
  shot.segments=apply({operation:'partition',frames:48});
  assert.deepEqual(ranges(shot.segments),[[0,48],[48,107]]);
  for (const frame of [23,84])
    assert.throws(()=>apply({operation:'boundary',index:1,frame}),/at least 1 second/);
  for (const frame of [24,83])
    assert.equal(apply({operation:'boundary',index:1,frame})[0].end,frame);
  assert.throws(()=>apply({operation:'split',frame:25}),/at least 1 second/);
  assert.deepEqual(ranges(apply({operation:'split',frame:24})),[[0,24],[24,48],[48,107]]);
});
