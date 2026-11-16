import {
  type RecordValue as R,
  type Environment,
  copy,
  pick,
  requireValue,
  object,
  integer,
  text,
  fields,
  identifier,
  COLLECTIONS,
  MASK_COLORS,
  MASK_FIELDS,
  source,
  renderShot,
  effectiveHistory,
  dialogue,
  speaker,
  digest,
  hashText,
  anchor,
} from "./common.js";
/** UI associations only: generation still uses one union of edit masks.
 * Arrays retain an older combined region (or a merged segment) without dropping inputs.
 */
export function characterMasks(selection: R): Record<string, string[]> {
  const result: Record<string, string[]> = {};
  for (const [id, value] of Object.entries(selection.mask_assignments || {}))
    if (selection.characters.includes(id)) result[id] = Array.isArray(value) ? [...value] : [value as string];
  const used = new Set(Object.values(result).flat());
  const retained = selection.mask_tracks.filter((id: string) => !used.has(id));
  const first = selection.characters[0];
  if (first && retained.length) result[first] = [...(result[first] || []), ...retained];
  return result;
}
export function applyCharacterMasks(selection: R, assignments: Record<string, string[]>) {
  selection.mask_assignments = Object.fromEntries(Object.entries(assignments).filter(([id, masks]) => selection.characters.includes(id) && masks.length));
  selection.mask_tracks = [...new Set(Object.values(selection.mask_assignments).flat())];
  return selection;
}
export function validateSelection(library: R, selection: R) {
  fields(
    selection,
    [...COLLECTIONS, "mask_assignments"],
    "Select characters, mask tracks and scene anchors explicitly",
  );
  for (const k of COLLECTIONS) {
    const keys = selection[k];
    requireValue(Array.isArray(keys), `Selected ${k} must be a list`);
    keys.forEach(identifier);
    requireValue(new Set(keys).size === keys.length, `Duplicate selected ${k}`);
    requireValue(
      keys.every((id) => library[k].some((r: R) => r.id === id)),
      `Selected ${k} no longer exist`,
    );
  }
  if (selection.mask_assignments != null) {
    const a = selection.mask_assignments;
    requireValue(object(a), "Invalid mask assignments");
    requireValue(
      Object.keys(a).every((k) => selection.characters.includes(k)),
      "Assign only selected characters",
    );
    requireValue(
      Object.values(a).flat().every(
        (k) => typeof k === "string" && selection.mask_tracks.includes(k),
      ),
      "Assign only selected masks",
    );

  } else
    for (const t of library.mask_tracks)
      requireValue(
        !selection.mask_tracks.includes(t.id) ||
          t.character_id == null ||
          selection.characters.includes(t.character_id),
        "A selected mask requires its character reference",
      );
  return selection;
}
export function validatePoints(points: any, frames: number) {
  requireValue(
    Array.isArray(points) && points.length <= 256,
    "Use at most 256 correction points per shot",
  );
  const ids = new Set();
  for (const p of points) {
    fields(p, ["id", "frame", "x", "y", "label"], "Invalid correction point");
    requireValue(Object.keys(p).length === 5, "Invalid correction point");
    requireValue(
      typeof p.id === "string" &&
        p.id.length >= 1 &&
        p.id.length <= 80 &&
        !ids.has(p.id),
      "Point IDs must be unique",
    );
    ids.add(p.id);
    requireValue(
      integer(p.frame) && p.frame >= 0 && p.frame < frames,
      "Point frame is outside this shot",
    );
    requireValue(
      integer(p.label) && [0, 1].includes(p.label),
      "Point must include or exclude",
    );
    for (const a of ["x", "y"])
      requireValue(
        typeof p[a] === "number" &&
          Number.isFinite(p[a]) &&
          p[a] >= 0 &&
          p[a] <= 1,
        "Point coordinates must be between 0 and 1",
      );
  }
}
export function asset(v: any, optional = false) {
  requireValue(
    (optional && v == null) || (typeof v === "string" && v.length > 0),
    "Expected a saved asset identifier",
  );
}
export function validateCharacter(c: R) {
  text(c.name, "character name", 120, false);
  text(c.description, "character description");
  asset(c.reference, true);
}
export function validateCatalog(shot: R, value: R) {
  fields(value, ["version", ...COLLECTIONS], "Unsupported shot cast format");
  requireValue(
    value.version === 1 && Object.keys(value).length === 4,
    "Unsupported shot cast format",
  );
  const ids = new Set();
  for (const k of COLLECTIONS) {
    requireValue(Array.isArray(value[k]), `${k} must be a list`);
    for (const r of value[k]) {
      requireValue(object(r), "Invalid cast entry");
      identifier(r.id);
      requireValue(
        !ids.has(r.id),
        "Cast, mask and anchor identifiers must be unique",
      );
      ids.add(r.id);
    }
  }
  for (const c of value.characters) {
    fields(
      c,
      [
        "id",
        "name",
        "reference",
        "description",
        "project_character_id",
        "project_character_revision",
      ],
      "Character cards contain a name, reference and description",
    );
    requireValue(
      ["id", "name", "reference", "description"].every((k) => k in c),
      "Character cards contain a name, reference and description",
    );
    validateCharacter(c);
    if ("project_character_id" in c) {
      identifier(c.project_character_id);
      requireValue(
        integer(c.project_character_revision) &&
          c.project_character_revision >= 1,
        "Invalid project character revision",
      );
    }
  }
  const owners = new Set(),
    frames = shot.end - shot.start;
  for (const t of value.mask_tracks) {
    requireValue(
      shot.segment_masks_version !== 1 || "segment_id" in t,
      "A mask must belong to a segment",
    );
    fields(
      t,
      [
        "id",
        "character_id",
        "state",
        "name",
        "color",
        "segment_id",
        "legacy_id",
      ],
      "Invalid character mask track",
    );
    requireValue(
      ["id", "character_id", "state"].every((k) => k in t),
      "Invalid character mask track",
    );
    if ("name" in t) text(t.name, "mask name", 120, false);
    if ("color" in t)
      requireValue(MASK_COLORS.includes(t.color), "Unknown mask color");
    if (t.character_id != null) {
      identifier(t.character_id);
      requireValue(
        value.characters.some((c: R) => c.id === t.character_id),
        "Mask track refers to an unknown character",
      );
      requireValue(
        !owners.has(t.character_id),
        "Each character has one independent mask track",
      );
      owners.add(t.character_id);
    }
    fields(t.state, MASK_FIELDS, "Invalid mask editor state");
    asset(t.state.mask, true);
    asset(t.state.mask_base, true);
    for (const k of ["mask_points", "mask_applied_points"])
      validatePoints(t.state[k] || [], frames);
    if ("segment_id" in t) {
      const seg = shot.segments.find((s: R) => s.id === t.segment_id);
      requireValue(seg, "Mask belongs to an older segment layout");
      requireValue(
        ["mask_points", "mask_applied_points"].every((k) =>
          (t.state[k] || []).every(
            (p: R) => p.frame >= seg.start && p.frame < seg.end,
          ),
        ),
        "Mask points must be inside their segment",
      );
    }
    const exp = t.state.expansion ?? 64;
    requireValue(
      integer(exp) && exp >= 0 && exp <= 256,
      "Mask expansion must be a whole number from 0 to 256",
    );
  }
  for (const a of value.scene_anchors) {
    fields(
      a,
      ["id", "name", "asset", "source_frame", "provenance"],
      "Unknown scene anchor field",
    );
    text(a.name, "scene anchor name", 120, false);
    asset(a.asset);
    requireValue(
      a.source_frame == null ||
        (integer(a.source_frame) &&
          a.source_frame >= 0 &&
          a.source_frame < frames),
      "Anchor source frame is outside this shot",
    );
    requireValue(object(a.provenance ?? {}), "Invalid anchor provenance");
  }
  return value;
}
export function resolve(clip: R, shot: R, segment: R) {
  const lib = validateCatalog(
      shot,
      shot.cast_setup || legacyCatalog(clip, shot),
    ),
    sel = validateSelection(
      lib,
      segment.cast_inputs ?? legacySelection(shot, segment),
    );
  const result: R = {};
  for (const k of COLLECTIONS)
    result[k] = sel[k].map((id: string) =>
      copy(lib[k].find((r: R) => r.id === id)),
    );
  // Associations never become character targeting in the current workflow.
  for (const t of result.mask_tracks) t.character_id = null;
  return result;
}
export function referencePlan(c: R, s: R, seg: R, strict = true) {
  const chosen = resolve(c, s, seg);
  return chosen.characters.map((v: R, i: number) => {
    if (strict)
      requireValue(v.reference, `Add a reference image for ${v.name}`);
    return { role: "character", id: v.id, asset: v.reference, picture: i + 1 };
  });
}
/** Pending point edits affect the intended pixels, even before SAM is run. */
export function pendingMasks(c: R, s: R, seg: R): string[] {
  s = renderShot(s, seg);
  if (!(s.use_mask ?? true)) return [];
  const points = (rows: R[]) => rows.map(p => [p.frame, p.x, p.y, p.label]).sort((a,b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  return resolve(c, s, seg).mask_tracks.filter((t: R) =>
    digest(points(t.state.mask_points || [])) !== digest(points(t.state.mask_applied_points || []))
  ).map((t: R) => t.id);
}
export function generationInputs(c: R, s: R, seg: R, strict = true) {
  s = renderShot(s, seg);
  const resolved = resolve(c, s, seg);
  return {
    version: 1,
    characters: resolved.characters.map((c: R) =>
      pick(c, ["id", "name", "reference", "description"]),
    ),
    mask_tracks:
      (s.use_mask ?? true)
        ? resolved.mask_tracks.map((t: R) => ({
            id: t.id,
            character_id: t.character_id,
            mask: t.state.mask ?? null,
            expansion: t.state.expansion ?? 64,
          }))
        : [],
    scene_anchors: [],
    pictures: referencePlan(c, s, seg, strict),
    source: source(c),
    shot_start: s.start,
    target_range: [seg.start, seg.end],
    history_frames: effectiveHistory(seg),
  };
}
export function promptSignature(c: R, s: R, seg: R) {
  s = renderShot(s, seg);
  return digest({
    ...pick(generationInputs(c, s, seg, false), ["characters", "pictures"]),
    prompt_recipe: "character-video-v2",
    audio: s.use_source_audio ?? true,
  });
}
export function buildPrompt(c: R, s: R, seg: R): string {
  s = renderShot(s, seg);
  const chosen = resolve(c, s, seg),
    pictures = referencePlan(c, s, seg, false);
  const labels = pictures.map((p: R) => {
    const ch = chosen.characters.find((v: R) => v.id === p.id),
      d = ch.description.trim().replace(/\.+$/, "");
    return `${ch.name} from <Picture ${p.picture}>` + (d ? ` (${d})` : "");
  });
  const words = labels.length
    ? [
        `Replace ${labels.length === 1 ? "the person" : "the selected people"} in <Video 1> with ${labels.join(" and ")}.`,
      ]
    : [];
  words.push(
    "Keep the source motion, expressions, timing, camera and background. Use the character references for identity and clothing only.",
  );
  if (pictures.length > 1)
    words.push(
      "Keep each replacement matched to its source person; leave other people unchanged.",
    );
  const audio = s.use_source_audio ?? true;
  if (audio) words.push("Follow <Audio 1> for speech timing.");
  const d = audio ? dialogue(c, s, seg) : (seg.dialogue || "").trim();
  if (d) {
    const speech: R = {
      target: "The selected character is speaking.",
      other: "Another person is speaking.",
      offscreen: "Speech is offscreen; do not make visible characters speak.",
      none: "No dialogue; preserve the visible source performance.",
    };
    words.push(`${speech[speaker(s, seg)]} Dialogue: "${d}".`);
  }
  return [words.join(" "), (seg.prompt || "").trim()].join("\n\n").trim();
}
export function track(s: R, id: string): R {
  const t = s.cast_setup?.mask_tracks.find((t: R) => t.id === id);
  requireValue(t, "Choose an existing character mask");
  return t;
}
export function preserve(state: R, env: Environment) {
  if (!state.mask) return;
  state.mask_base ??= state.mask;
  state.mask_versions ??= [];
  if (!state.mask_versions.some((v: R) => v.asset === state.mask))
    state.mask_versions.push({
      asset: state.mask,
      label: state.mask_versions.length ? "Saved mask" : "Original mask",
      points: state.mask_applied_points || [],
      created: env.now(),
    });
}
export function cloneTrack(t: R, seg: R, env: Environment) {
  const r = copy(t);
  Object.assign(r, {
    id: env.uid("mask-"),
    segment_id: seg.id,
    character_id: null,
    legacy_id: t.legacy_id ?? t.id,
  });
  for (const k of ["mask_points", "mask_applied_points"])
    r.state[k] = (r.state[k] || []).filter(
      (p: R) => p.frame >= seg.start && p.frame < seg.end,
    );
  return r;
}
export function rebindLayout(s: R, items: R[], env: Environment) {
  const tracks = s.cast_setup.mask_tracks,
    retained = tracks.filter((t: R) =>
      items.some((seg) => seg.id === t.segment_id),
    );
  for (const seg of items) {
    const assignments = characterMasks(seg.cast_inputs), remap: Record<string, string> = {};
    seg.cast_inputs.mask_tracks = seg.cast_inputs.mask_tracks.map(
      (id: string) => {
        let t = tracks.find((v: R) => v.id === id);
        if (t.segment_id !== seg.id) {
          t = cloneTrack(t, seg, env);
          retained.push(t);
        }
        remap[id] = t.id;
        return t.id;
      },
    );
    applyCharacterMasks(seg.cast_inputs, Object.fromEntries(Object.entries(assignments).map(([id, masks]) => [id, masks.map(k => remap[k])])));
    seg.use_mask = !!seg.cast_inputs.mask_tracks.length;
  }
  s.mask_track_history ??= [];
  s.mask_track_history.push(
    ...copy(tracks.filter((t: R) => !retained.includes(t))),
  );
  s.cast_setup.mask_tracks = retained;
}
export function restoreTake(s: R, seg: R, take: R, env: Environment) {
  const selected: string[] = [], remap: Record<string, string> = {}, assignments = characterMasks(seg.cast_inputs),
    tracks = s.cast_setup.mask_tracks;
  for (const e of take.cast_inputs_snapshot?.mask_tracks || []) {
    let t = tracks.find(
      (t: R) => t.segment_id === seg.id && [t.id, t.legacy_id].includes(e.id),
    );
    if (!t) {
      t = {
        id: env.uid("mask-"),
        segment_id: seg.id,
        legacy_id: e.id,
        character_id: null,
        name: `Mask ${selected.length + 1}`,
        state: {},
      };
      tracks.push(t);
    }
    preserve(t.state, env);
    Object.assign(t.state, {
      mask: e.mask,
      expansion: e.expansion,
      mask_points: [],
      mask_applied_points: [],
      mask_approved: false,
    });
    t.state.selection ??= "largest";
    t.state.subject_index ??= 0;
    delete t.state.mask_coverage;
    delete t.state.mask_empty_frames;
    preserve(t.state, env);
    selected.push(t.id);
    remap[e.id] = t.id;
  }
  seg.cast_inputs.mask_tracks = selected;
  seg.cast_inputs.mask_assignments = Object.fromEntries(Object.entries(assignments).map(([id, masks]) => [id, masks.map(k => remap[k]).filter(Boolean)]));
  seg.cast_inputs.mask_assignments = characterMasks(seg.cast_inputs);
  seg.use_mask = !!selected.length;
}

import { current as currentSegments } from "./segments.js";
export const anchorId = (asset: string) =>
  "anchor-" + hashText(asset).slice(0, 24);
export function legacySelection(s: R, seg: R) {
  const a = anchor(s, seg);
  return {
    characters: ["character-legacy"],
    mask_tracks: ["mask-legacy"],
    scene_anchors: a ? [anchorId(a)] : [],
  };
}
export function legacyCatalog(c: R, s: R) {
  const character = {
    id: "character-legacy",
    name: "Character 1",
    reference: "reference" in s ? s.reference : (c.reference ?? null),
    description: ("character" in s ? s.character : c.character) || "",
  };
  const state = copy(pick(s, MASK_FIELDS));
  state.expansion ??= 64;
  const track = { id: "mask-legacy", character_id: "character-legacy", state };
  const records: R[] = [...(s.anchors || [])];
  for (const layout of [
    currentSegments(s),
    ...(s.segment_layout_history || []).map((v: R) => v.segments),
  ])
    for (const seg of layout) {
      records.push(...(seg.anchors || []));
      for (const t of seg.takes || []) {
        records.push(...(t.settings?.anchors || []));
        if (t.settings?.anchor) records.push({ asset: t.settings.anchor });
      }
      if (seg.anchor) records.push({ asset: seg.anchor });
    }
  if (s.anchor) records.push({ asset: s.anchor });
  const anchors = new Map();
  for (const r of records) {
    if (!r.asset || anchors.has(r.asset)) continue;
    let frame = integer(r.source_frame) ? r.source_frame - s.start : null;
    if (frame != null && (frame < 0 || frame >= s.end - s.start)) frame = null;
    anchors.set(r.asset, {
      id: anchorId(r.asset),
      name: `Scene anchor ${anchors.size + 1}`,
      asset: r.asset,
      source_frame: frame,
      provenance: copy(r),
    });
  }
  return {
    version: 1,
    characters: [character],
    mask_tracks: [track],
    scene_anchors: [...anchors.values()],
  };
}
export function upgrade(c: R, s: R) {
  const result = copy(s);
  result.cast_setup = validateCatalog(s, legacyCatalog(c, s));
  result.segments = currentSegments(s);
  for (const seg of result.segments) seg.cast_inputs = legacySelection(s, seg);
  return result;
}
