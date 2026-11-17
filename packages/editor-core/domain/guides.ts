import {
  type RecordValue as R,
  copy,
  source,
  requireValue,
  fields,
  integer,
  digest,
  dimensions,
  renderShot,
} from "./common.js";
import { PRESETS } from "./guide-presets.js";
import { generationInputs, pendingMasks, upgrade } from "./cast.js";
import { prompt } from "./segments.js";
export { PRESETS };
export const PROPAGATION =
  "Apply the changes shown in the edited guide frames throughout the source video. Preserve the source motion, timing, camera movement and unedited content.";
export function marker(s: R, id: string): R {
  const g = (s.guides || []).find((g: R) => g.id === id && !g.archived);
  requireValue(g, "Guide no longer exists");
  return g;
}
export function current(c: R, s: R, g: R) {
  return (
    g.source === source(c) &&
    s.start <= g.frame &&
    g.frame < s.end &&
    JSON.stringify(g.dimensions) === JSON.stringify(c.dimensions)
  );
}
export function selected(c: R, s: R, seg: R, strict = false, required = true) {
  const result: R[] = [];
  for (const g of [...(s.guides || [])].sort((a, b) => a.frame - b.frame)) {
    if (
      g.archived ||
      !g.enabled ||
      g.frame < s.start + seg.start ||
      g.frame >= s.start + seg.end
    )
      continue;
    const v = g.versions.find((v: R) => v.id === g.approved);
    if (v) {
      if (strict)
        requireValue(
          current(c, s, g),
          "An approved guide belongs to another source or canvas; disable it and add a new guide",
        );
      result.push({
        id: g.id,
        frame: g.frame,
        version_id: v.id,
        asset: v.asset,
        source: g.source,
        dimensions: g.dimensions,
      });
    }
  }
  if (strict && required)
    requireValue(
      result.length,
      "Approve and enable at least one guide in this segment",
    );
  return result;
}
export function signature(c: R, s: R, seg: R) {
  const swap = seg.render_mode === "swap_pika";
  const pending = swap ? pendingMasks(c, s, seg) : [];
  return digest({
    ...(pending.length ? { pending_masks: pending } : {}),
    mode: swap ? (seg.lora_id === "viggle-remake-swap" ? "viggle-remake-swap-v1" : "swap-pika-v1.5-manual-sigmas-v5") : "ripple-v1",
    source: source(c),
    shot_start: s.start,
    range: [seg.start, seg.end],
    history_frames: seg.connection === "continue" ? seg.history_frames : 0,
    guides: selected(c, s, seg),
    dimensions: dimensions(c, s),
    seed: seg.seed,
    prompt: prompt(c, s, seg),
    ...(swap
      ? {
          references: generationInputs(c, s, seg, false),
          source_audio: renderShot(s, seg).use_source_audio ?? true,
        }
      : {}),
  });
}
export function references(c: R, s: R, characters: R[] = []) {
  const result: R[] = [];
  for (const item of [...characters, ...(s.cast_setup?.characters || [])])
    if (item.reference)
      result.push({
        asset: item.reference,
        name: item.name,
        kind: "Character",
      });
  if (!s.cast_setup && (s.reference || c.reference))
    result.push({
      asset: s.reference || c.reference,
      name: "Character reference",
      kind: "Character",
    });
  result.push(...(s.guide_references || []));
  for (const g of s.guides || []) {
    const v = g.versions.find((v: R) => v.id === g.approved);
    if (v && current(c, s, g))
      result.push({
        asset: v.asset,
        name: `Guide · frame ${g.frame - s.start}`,
        kind: "Approved guide",
      });
  }
  return [...new Map(result.map((r) => [r.asset, r])).values()];
}
export function validateDraft(
  c: R,
  s: R,
  g: R,
  changes: unknown,
  characters: R[] = [],
) {
  fields(
    changes,
    [
      "prompt",
      "references",
      "quality",
      "enabled",
      "helpers",
      "edit_source_asset",
    ],
    "Unknown guide setting",
  );
  if ("edit_source_asset" in changes)
    requireValue(
      typeof changes.edit_source_asset === "string" &&
        [
          g.source_asset,
          ...(g.source_uploads || []),
          ...g.versions.map((v: R) => v.asset),
        ].includes(changes.edit_source_asset),
      "Choose the original frame, an uploaded source or a saved version of this guide",
    );
  if ("prompt" in changes)
    requireValue(
      typeof changes.prompt === "string" && [...changes.prompt].length <= 12000,
      "Guide instructions must be text of at most 12,000 characters",
    );
  if ("quality" in changes)
    requireValue(
      ["low", "medium", "high"].includes(changes.quality),
      "Choose low, medium or high quality",
    );
  if ("enabled" in changes)
    requireValue(
      typeof changes.enabled === "boolean",
      "Enabled must be true or false",
    );
  if ("helpers" in changes) {
    const hs = changes.helpers;
    requireValue(
      Array.isArray(hs) && hs.length <= 3,
      "Choose up to three prompt helpers",
    );
    const seen = new Set();
    for (const h of hs) {
      fields(
        h,
        ["id", "text"],
        "A prompt helper needs its preset and editable text",
      );
      requireValue(
        Object.keys(h).length === 2,
        "A prompt helper needs its preset and editable text",
      );
      requireValue(
        typeof h.id === "string" &&
          PRESETS.some((p) => p.id === h.id) &&
          !seen.has(h.id),
        "Choose each available prompt helper at most once",
      );
      requireValue(
        typeof h.text === "string" &&
          h.text.trim() &&
          [...h.text].length <= 6000,
        "Helper text must contain 1–6,000 characters",
      );
      seen.add(h.id);
    }
  }
  if ("references" in changes) {
    const ids = changes.references,
      available = new Set(references(c, s, characters).map((r) => r.asset));
    requireValue(
      Array.isArray(ids) &&
        ids.length <= 8 &&
        ids.every((k) => typeof k === "string" && available.has(k)) &&
        new Set(ids).size === ids.length,
      "Choose up to 8 reference images from this project",
    );
  }
}
export function view(c: R, s: R, characters: R[]) {
  return {
    revision: c.revision,
    guides: (s.guides || [])
      .filter((g: R) => !g.archived)
      .map((g: R) => ({
        ...g,
        helpers: g.helpers || [],
        current: current(c, s, g),
      })),
    references: references(c, s, characters),
    helper_presets: copy(PRESETS),
    prompt_prefix: "",
  };
}
export function frame(c: R, s: R, n: number) {
  requireValue(
    integer(n) && n >= 0 && n < s.end - s.start,
    "Choose a source frame inside this shot",
  );
  const old = (s.guides || []).find(
    (g: R) => !g.archived && g.frame === n + s.start && current(c, s, g),
  );
  if (old) return { existing: old.id };
  const d = c.dimensions;
  requireValue(
    d.length === 2 &&
      d.every((n: number) => integer(n) && n >= 1 && n <= 8192) &&
      d[0] * d[1] <= 40000000,
    "Unsupported guide canvas",
  );
  const [w, h] = d;
  const spatial = c.canvas_aligned
    ? `scale=${w}:${h}:flags=lanczos`
    : `scale=${w}:${h}:force_original_aspect_ratio=decrease,pad=${w}:${h}:(ow-iw)/2:(oh-ih)/2`;
  return {
    source: source(c),
    filter: `select=eq(n\\,${s.start + n}),${spatial},setsar=1`,
  };
}
export function plan(c: R, s: R, command: R, characters: R[]) {
  const g = copy(marker(s, command.guideId)),
    changes = command.body.changes || {};
  fields(
    changes,
    ["prompt", "quality", "references", "helpers", "edit_source_asset"],
    "Unknown generation setting",
  );
  validateDraft(c, s, g, changes, characters);
  Object.assign(g, changes);
  g.helpers ??= [];
  g.edit_source_asset = g.edit_source_asset || g.source_asset;
  requireValue(current(c, s, g), "Source changed; add a new guide");
  validateDraft(
    c,
    s,
    g,
    Object.fromEntries(
      ["prompt", "quality", "references", "helpers", "edit_source_asset"].map(
        (k) => [k, g[k]],
      ),
    ),
    characters,
  );
  const instruction = g.helpers.length
    ? [
        ...(g.prompt ? [g.prompt] : []),
        ...g.helpers.map(
          (h: R) => PRESETS.find((p) => p.id === h.id)!.label + ":\n" + h.text,
        ),
      ].join("\n\n")
    : g.prompt;
  requireValue(
    instruction.trim() && [...instruction].length <= 32000,
    "Describe the edit in at most 32,000 characters",
  );
  const labels = new Map(
    references(c, s, characters).map((r) => [r.asset, r.name]),
  );
  g.reference_labels = g.references.map((k: string) => labels.get(k));
  return {
    guideId: g.id,
    clipId: c.id,
    shotId: s.id,
    sourceRevision: c.revision,
    source: g.edit_source_asset,
    originalSource: g.source_asset,
    references: g.references,
    parameters: { instruction, quality: g.quality, size: "auto" },
    snapshot: g,
  };
}
export function syncReference(c: R, s: R, g: R) {
  if (!s.cast_setup) {
    const v = g.versions.find((v: R) => v.id === g.approved);
    if (!v || !g.enabled || g.archived || !current(c, s, g)) return;
    const up = upgrade(c, s);
    for (const seg of up.segments)
      if (seg.performance_prompt == null && (s.prompt || "").trim()) {
        seg.performance_prompt = prompt(c, s, seg);
        seg.prompt_mode = "manual";
      }
    Object.assign(s, up);
  }
  const prior = new Set(
    s.cast_setup.scene_anchors
      .filter((a: R) => a.provenance?.guide_id === g.id)
      .map((a: R) => a.id),
  );
  for (const seg of s.segments)
    seg.cast_inputs.scene_anchors = seg.cast_inputs.scene_anchors.filter(
      (id: string) => !prior.has(id),
    );
}

/** Activate a completed edit unless the user changed its selection or source meanwhile. */
export function activateGeneratedGuide(c: R, s: R, g: R, submitted: R, versionId: string) {
  if (!g.enabled || g.archived || !current(c, s, g) ||
      g.source_asset !== submitted.source_asset ||
      (g.approved ?? null) !== (submitted.approved ?? null)) return;
  g.approved = versionId;
  g.auto_saved_version = versionId;
  syncReference(c, s, g);
}
