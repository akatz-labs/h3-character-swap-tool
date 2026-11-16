import {
  type RecordValue as R,
  type Environment,
  environment,
  copy,
  pick,
  requireValue,
  fields,
  integer,
  HISTORY,
  EDITABLE,
  PROMPT_FIELDS,
  RENDER_FIELDS,
  COLLECTIONS,
  source,
  renderShot,
  effectiveHistory,
  anchor,
  promptMode,
  speaker,
  dialogue,
  digest,
  settings,
  windowPlan,
  segmentWindow,
  dimensions,
} from "./common.js";
import * as cast from "./cast.js";
import * as guides from "./guides.js";
export const usesGuides = (s: R) =>
  ["guides", "swap_pika"].includes(s.render_mode);
export function fresh(
  s: R,
  start: number,
  end: number,
  index: number,
  env: Environment = environment,
  key?: string,
): R {
  return {
    id: key || env.uid("segment-"),
    start,
    end,
    connection: index ? "continue" : "start",
    history_frames: s.history_frames ?? 18,
    prompt: "",
    performance_prompt: null,
    dialogue: null,
    speaker: null,
    cast_inputs: s.cast_setup
      ? {
          characters: [],
          mask_tracks: [],
          scene_anchors: [],
          mask_assignments: {},
        }
      : null,
    render_mode: "swap",
    guide_prompt: "",
    seed: ((s.seed ?? 194729) + index) % 2 ** 63,
    anchor_mode: !index && (s.use_anchor ?? true) ? "shot" : "none",
    anchor: null,
    anchors: [],
    takes: [],
    selected_take: null,
    needs_review: false,
    prompt_mode: "auto",
  };
}
export function current(s: R): R[] {
  if (s.segments != null)
    return copy(s.segments).map((v: R) => {
      for (const k of ["cast_inputs", ...PROMPT_FIELDS])
        if (!(k in v)) v[k] = null;
      v.render_mode ??= "swap";
      v.guide_prompt ??= "";
      return v;
    });
  return windowPlan(s).windows.map((w, i) =>
    fresh(
      s,
      w.source_start + w.discarded_overlap,
      w.source_end_exclusive,
      i,
      environment,
      `segment-${s.id}-${w.source_start + w.discarded_overlap}-${w.source_end_exclusive}`,
    ),
  );
}
export function validate(s: R, items: any): R[] {
  requireValue(
    Array.isArray(items) && items.length >= 1 && items.length <= 1000,
    "Choose between 1 and 1000 segments",
  );
  let covered = 0,
    chain = 0;
  const ids = new Set();
  for (const [i, item] of items.entries()) {
    fields(
      item,
      [
        ...EDITABLE,
        "id",
        "start",
        "end",
        "anchors",
        "takes",
        "selected_take",
        "needs_review",
        "cast_prompt_signature",
        "prompt_mode",
      ],
      "Unknown segment setting",
    );
    requireValue(
      RENDER_FIELDS.every((k) => !(k in item) || item[k] != null),
      "Choose a valid segment render setting",
    );
    settings(renderShot(s, item));
    requireValue(
      item.render_mode == null ||
        ["swap", "guides", "swap_pika"].includes(item.render_mode),
      "Choose Character swap or Guided edit",
    );
    requireValue(
      item.guide_prompt == null ||
        (typeof item.guide_prompt === "string" &&
          [...item.guide_prompt].length <= 12000),
      "Guided render prompt must be at most 12,000 characters",
    );
    requireValue(
      typeof item.id === "string" &&
        /^[A-Za-z0-9_-]{1,100}$/.test(item.id) &&
        !ids.has(item.id),
      "Segment identifiers must be unique",
    );
    ids.add(item.id);
    requireValue(
      integer(item.start) &&
        integer(item.end) &&
        item.start === covered &&
        item.end > item.start,
      "Segments must cover the shot in order, without gaps or overlaps",
    );
    requireValue(
      ["start", "continue"].includes(item.connection) &&
        (i || item.connection === "start"),
      "The first segment starts fresh; later segments can continue or start fresh",
    );
    if (item.connection === "start") chain = item.start;
    requireValue(
      integer(item.history_frames) && HISTORY.includes(item.history_frames),
      "Choose a supported continuation history",
    );
    requireValue(
      effectiveHistory(item) <= item.start - chain,
      "History extends before this continuous sequence; reduce history or move the boundary",
    );
    requireValue(
      ["shot", "custom", "none"].includes(item.anchor_mode),
      "Choose the shot anchor, a segment reference, or no anchor",
    );
    requireValue(
      typeof item.prompt === "string" && [...item.prompt].length <= 20000,
      "Segment directions must be text of at most 20,000 characters",
    );
    for (const k of ["performance_prompt", "dialogue"])
      requireValue(
        item[k] == null ||
          (typeof item[k] === "string" && [...item[k]].length <= 20000),
        `Segment ${k} must be text of at most 20,000 characters`,
      );
    requireValue(
      item.prompt_mode == null || ["auto", "manual"].includes(item.prompt_mode),
      "Choose automatic or manual prompt text",
    );
    requireValue(
      item.speaker == null ||
        ["target", "other", "offscreen", "none"].includes(item.speaker),
      "Choose a supported segment speaker",
    );
    requireValue(
      integer(item.seed) && item.seed >= 0 && item.seed < 2 ** 63,
      "Enter a whole-number seed of 0 or greater",
    );
    requireValue(
      item.anchor_mode !== "custom" ||
        (item.anchors || []).some((a: R) => a.asset === item.anchor),
      "Choose an imported or generated reference for this segment",
    );
    if (s.cast_setup) {
      cast.validateSelection(s.cast_setup, item.cast_inputs);
      if (s.segment_masks_version === 1)
        requireValue(
          s.cast_setup.mask_tracks.every(
            (t: R) =>
              !item.cast_inputs.mask_tracks.includes(t.id) ||
              t.segment_id === item.id,
          ),
          "Select masks belonging to this segment",
        );
    }
    covered = item.end;
  }
  requireValue(
    covered === s.end - s.start,
    "Segments must cover the entire shot",
  );
  return items;
}
export const FIRST =
  "<Picture 2> is the exact first-frame scene and pose anchor.";
export const REFERENCE =
  "<Picture 2> is an appearance reference for the replacement character in this scene, not a timed guide frame. Follow <Video 1> for pose, visibility, background geometry and camera movement; do not force the reference composition onto the video.";
export const referencePrompt = (text: string, s: R) =>
  (s.anchor_conditioning ?? "reference") === "reference"
    ? text.replaceAll(FIRST, REFERENCE)
    : text.replaceAll(REFERENCE, FIRST);
export function mediaPrompt(c: R, s: R) {
  const speech: R = {
    target: "The selected character is speaking.",
    offscreen: "Speech is offscreen; do not make the selected character speak.",
    other:
      "Another person is speaking; preserve the selected character’s actual mouth performance.",
    none: "No dialogue; preserve the visible source performance.",
  };
  const performance =
    (s.use_source_audio ?? true)
      ? `${speech[s.speaker ?? "target"]} Current shot dialogue: "${s.dialogue ?? ""}". <Audio 1> and source video determine exact timing. Do not repeat earlier dialogue or invent words, gestures, people or camera cuts.`
      : "Preserve the visible source performance. <Video 1> determines exact timing. Do not invent mouth movements, gestures, people or camera cuts.";
  return referencePrompt(
    `Replace only the selected person in <Video 1> with the single character in <Picture 1>: ${(s.character ?? c.character) || "the reference character"}. ${FIRST} Keep the reference identity and costume throughout. Match source posture, gaze, blinks, facial emotion, mouth movement, hand contacts and camera movement. ${performance} Retain the scene outside the person region. Output the raw decoded generation.`,
    s,
  );
}
function referenceContext(s: R, seg: R) {
  const h = effectiveHistory(seg),
    ref = anchor(s, seg);
  let text = h
    ? "<Picture 2> is the generated boundary from the preceding segment. Continue its motion and appearance."
    : ref
      ? referencePrompt(FIRST, s)
      : "";
  if (h && ref)
    text +=
      " <Picture 3> is an additional scene and performance reference for this segment; preserve continuity with the generated boundary.";
  return text;
}
export function buildPrompt(c: R, s: R, seg: R): string {
  if (s.cast_setup) return cast.buildPrompt(c, s, seg);
  return [
    mediaPrompt(c, {
      ...s,
      dialogue: dialogue(c, s, seg),
      speaker: speaker(s, seg),
    })
      .replaceAll(referencePrompt(FIRST, s), referenceContext(s, seg))
      .replaceAll("Current shot dialogue:", "Current segment dialogue:"),
    seg.prompt.trim(),
  ]
    .filter(Boolean)
    .join("\n\n");
}
export function prompt(c: R, s: R, seg: R): string {
  if (seg.render_mode === "guides")
    return seg.guide_prompt || guides.PROPAGATION;
  if (s.cast_setup)
    return promptMode(seg) === "auto"
      ? buildPrompt(c, s, seg)
      : (seg.performance_prompt ?? s.prompt ?? "");
  if (seg.performance_prompt != null)
    return referencePrompt(seg.performance_prompt, s);
  const shared = (s.prompt || "")
    .replaceAll(`Current shot dialogue: "${s.dialogue ?? ""}".`, "")
    .replaceAll(FIRST, "")
    .replaceAll(REFERENCE, "")
    .trim();
  return [
    shared,
    referenceContext(s, seg),
    `Current segment dialogue: "${dialogue(c, s, seg)}". Follow source audio timing. Earlier reference dialogue is context only; do not repeat it.`,
    seg.prompt.trim(),
  ]
    .filter(Boolean)
    .join("\n\n");
}
export function refreshPrompts(c: R) {
  for (const s of c.shots || [])
    if (s.cast_setup)
      for (const seg of s.segments) {
        if (seg.render_mode === "guides" || promptMode(seg) !== "auto")
          continue;
        seg.performance_prompt = buildPrompt(c, s, seg);
        seg.cast_prompt_signature = cast.promptSignature(c, s, seg);
        seg.needs_review = false;
      }
}
export function signature(c: R, s: R, seg: R): string {
  if (usesGuides(seg)) return guides.signature(c, s, seg);
  s = renderShot(s, seg);
  const gen: R = settings(s);
  if (gen.use_source_audio) delete gen.use_source_audio;
  if (s.cast_setup)
    return digest({
      inputs: cast.generationInputs(c, s, seg, false),
      timed_guides: guides.selected(c, s, seg),
      generation: gen,
      render_mp: s.render_mp ?? 1,
      seed: seg.seed,
      prompt: prompt(c, s, seg),
    });
  return digest({
    source: source(c),
    reference: "reference" in s ? s.reference : (c.reference ?? null),
    shot_start: s.start,
    segment: pick(seg, [
      "id",
      "start",
      "end",
      "connection",
      "history_frames",
      "seed",
    ]),
    prompt: prompt(c, s, seg),
    anchor: anchor(s, seg),
    generation: gen,
    render_mp: s.render_mp ?? 1,
    mask: (s.use_mask ?? true) ? (s.mask ?? null) : null,
    expansion: s.expansion ?? 64,
  });
}
export const take = (s: R, id?: string) =>
  s.takes?.find((t: R) => t.id === (id || s.selected_take));
/** Resolve once, in timeline order: a stale ancestor invalidates its descendants.
 * Saved takes remain in Outputs; only a matching chain can supply continuation. */
export function currentTakes(c: R, s: R, items = current(s)): Map<string, R> {
  const result = new Map<string, R>();
  for (const [i, seg] of items.entries()) {
    const parents = predecessors(items, i);
    if (parents.some(([p]) => !result.has(p.id))) continue;
    const deps = parents.map(([p, frames]) => ({segment_id: p.id, take_id: result.get(p.id)!.id, frames}));
    const sig = signature(c, s, seg);
    const candidates = [...(seg.takes ?? [])].reverse();
    const selected = take(seg);
    if (selected) candidates.unshift(selected);
    const chosen = candidates.find((t: R) => {
      const inputs = t.guide_inputs_snapshot || t.cast_inputs_snapshot;
      return t.master && t.start === seg.start && t.end === seg.end &&
        JSON.stringify(t.dimensions) === JSON.stringify(dimensions(c, s)) &&
        (!inputs || (inputs.source === source(c) && inputs.shot_start === s.start)) &&
        t.signature === sig && digest(t.dependencies || []) === digest(deps);
    });
    if (chosen) result.set(seg.id, chosen);
  }
  return result;
}
export function continuationTake(c: R, s: R, seg: R) {
  return currentTakes(c, s).get(seg.id);
}
export function reusable(c: R, s: R, seg: R) {
  return currentTakes(c, s).has(seg.id);
}
export function predecessors(items: R[], i: number): [R, number][] {
  let needed = effectiveHistory(items[i]);
  const result: [R, number][] = [];
  for (const item of items.slice(0, i).reverse()) {
    if (!needed) break;
    const n = Math.min(needed, item.end - item.start);
    result.push([item, n]);
    needed -= n;
    if (item.connection === "start") break;
  }
  requireValue(
    !needed,
    "Not enough preceding frames in this continuous sequence",
  );
  return result.reverse();
}
export const planRevision = (s: R) => digest(current(s));
export function view(c: R, s: R) {
  const items = validate(s, current(s));
  const valid = currentTakes(c, s, items);
  const states = items.map((seg, i) => {
    const chosen = valid.get(seg.id),
      parents = predecessors(items, i),
      deps = parents.map(([p, n]) => ({
        segment_id: p.id,
        take_id: valid.get(p.id)?.id ?? null,
        frames: n,
      }));
    const ready = !!chosen;
    return {
      ...(s.cast_setup
        ? {
            pictures: cast.referencePlan(c, s, seg, false),
            prompt_mode: promptMode(seg),
            prompt_needs_update:
              seg.render_mode !== "guides" &&
              promptMode(seg) === "auto" &&
              seg.cast_prompt_signature !== cast.promptSignature(c, s, seg),
          }
        : {}),
      id: seg.id,
      current: ready,
      status: ready ? "ready" : seg.takes?.length ? "stale" : "empty",
      reusable: ready,
      blocked: parents.some(([p]) => !valid.has(p.id)),
      dialogue: dialogue(c, s, seg),
      prompt: prompt(c, s, seg),
      speaker: speaker(s, seg),
      dependencies: deps,
      window_frames: segmentWindow(seg.end - seg.start, effectiveHistory(seg)),
    };
  });
  return {
    segments: items,
    states,
    revision: c.revision,
    plan_revision: planRevision(s),
    history_choices: HISTORY,
  };
}
export function layout(s: R, op: R, env: Environment) {
  const old = current(s),
    end = s.end - s.start;
  let bounds = [0, ...old.map((s) => s.end)];
  if (op.operation === "partition") {
    requireValue(
      integer(op.frames) && op.frames >= 1,
      "Choose a positive whole-frame segment length",
    );
    bounds = [];
    for (let n = 0; n < end; n += op.frames) {
      requireValue(bounds.length < 1000, "Choose between 1 and 1000 segments");
      bounds.push(n);
    }
    bounds.push(end);
  } else if (op.operation === "split") {
    requireValue(
      integer(op.frame) &&
        op.frame > 0 &&
        op.frame < end &&
        !bounds.includes(op.frame),
      "Place the playhead inside a segment to split it",
    );
    bounds.push(op.frame);
    bounds.sort((a, b) => a - b);
  } else if (["merge", "boundary"].includes(op.operation)) {
    requireValue(
      integer(op.index) && op.index >= 1 && op.index < old.length,
      "Choose a boundary between segments",
    );
    if (op.operation === "merge") bounds.splice(op.index, 1);
    else {
      requireValue(
        integer(op.frame) &&
          op.frame > bounds[op.index - 1] &&
          op.frame < bounds[op.index + 1],
        "The boundary must stay between its neighboring boundaries",
      );
      bounds[op.index] = op.frame;
    }
  } else throw Error("Unknown segment layout operation");
  const result: R[] = [];
  for (let i = 0; i < bounds.length - 1; i++) {
    const a = bounds[i],
      b = bounds[i + 1],
      exact = old.find((s) => s.start === a && s.end === b);
    if (exact) {
      result.push(copy(exact));
      continue;
    }
    let overlaps = old.filter((s) => s.end > a && s.start < b);
    if (op.operation === "boundary") overlaps = [old[i]];
    const seg = fresh(s, a, b, i, env);
    if (overlaps.length) {
      const f = overlaps[0];
      Object.assign(
        seg,
        {
          render_mode: f.render_mode || "swap",
          guide_prompt: f.guide_prompt || "",
        },
        copy(pick(f, ["seed", "history_frames", "prompt", ...RENDER_FIELDS])),
      );
      if (a === f.start)
        Object.assign(
          seg,
          copy(pick(f, ["connection", "anchor_mode", "anchor", "anchors"])),
        );

      seg.prompt = [
        ...new Set(overlaps.map((v) => v.prompt).filter((v) => v.trim())),
      ].join("\n\n");
      for (const k of ["performance_prompt", "dialogue"]) {
        const values = [
          ...new Set(overlaps.map((v) => v[k]).filter((v) => v != null)),
        ];
        if (values.length) seg[k] = values.join("\n\n");
      }
      seg.speaker = f.speaker ?? null;
      seg.prompt_mode = overlaps.some((v) => promptMode(v) === "manual")
        ? "manual"
        : "auto";
      seg.needs_review =
        !!seg.prompt || PROMPT_FIELDS.some((k) => seg[k] != null);
    }
    if (s.cast_setup) {
      const sources =
        ["merge", "boundary"].includes(op.operation) ||
        (overlaps.length && a === overlaps[0].start)
          ? overlaps
          : [];
      seg.cast_inputs = Object.fromEntries(
        COLLECTIONS.map((k) => [
          k,
          [...new Set(sources.flatMap((v) => v.cast_inputs[k]))],
        ]),
      );
      if (sources.some((item) => "mask_assignments" in item.cast_inputs)) {
        const assignments: Record<string, string[]> = {};
        for (const item of sources)
          for (const [id, masks] of Object.entries(
            cast.characterMasks(item.cast_inputs),
          ))
            assignments[id] = [
              ...new Set([...(assignments[id] || []), ...masks]),
            ];
        cast.applyCharacterMasks(seg.cast_inputs, assignments);
      }
      seg.cast_prompt_signature = null;
      seg.needs_review = true;
    }
    result.push(seg);
  }
  let chain = 0;
  for (const [i, seg] of result.entries()) {
    if (!i) seg.connection = "start";
    if (seg.connection === "start") chain = seg.start;
    else if (seg.history_frames > seg.start - chain)
      seg.history_frames = Math.max(
        ...HISTORY.filter((n) => n <= seg.start - chain),
      );
  }
  return validate({ ...s, segment_masks_version: 0 }, result);
}
export function applyLayout(c: R, s: R, op: R, env: Environment) {
  const items = layout(s, op, env);
  if (digest(items) === digest(current(s))) return;
  s.segment_layout_history ??= [];
  s.segment_layout_history.push({
    id: env.uid("segments-"),
    created: env.now(),
    segments: current(s),
    selected_render: s.selected_render ?? null,
  });
  if (s.segment_masks_version === 1) cast.rebindLayout(s, items, env);
  s.segments = items;
  s.selected_render = null;
  c.result = null;
}
export function edit(c: R, s: R, id: string, changes: unknown) {
  fields(changes, EDITABLE, "Unknown segment field");
  const items = current(s),
    seg = items.find((v) => v.id === id);
  requireValue(seg, "Segment no longer exists");
  if (
    ("dialogue" in changes || "speaker" in changes) &&
    !("performance_prompt" in changes) &&
    (!s.cast_setup || promptMode(seg) === "manual")
  )
    seg.performance_prompt = prompt(c, s, seg);
  Object.assign(seg, changes, { needs_review: false });
  if (s.segment_masks_version === 1)
    seg.use_mask = !!seg.cast_inputs.mask_tracks.length;
  if ("performance_prompt" in changes) {
    seg.prompt_mode = "manual";
    if (s.cast_setup)
      seg.cast_prompt_signature = cast.promptSignature(c, s, seg);
  }
  validate(s, items);
  s.segments = items;
}
export function selectTake(
  c: R,
  s: R,
  id: string,
  takeId: string,
  env: Environment,
) {
  const items = current(s),
    seg = items.find((v) => v.id === id);
  requireValue(seg, "Segment no longer exists");
  const chosen = take(seg, takeId);
  requireValue(chosen, "Take does not belong to this segment");
  Object.assign(seg, copy(pick(chosen.settings || {}, EDITABLE)), {
    render_mode: chosen.settings?.render_mode || "swap",
    guide_prompt: chosen.settings?.guide_prompt || "",
    cast_prompt_signature: chosen.settings?.cast_prompt_signature ?? null,
    prompt_mode: "manual",
    needs_review: false,
  });
  for (const k of PROMPT_FIELDS) seg[k] = chosen.settings?.[k] ?? null;
  if (s.cast_setup && seg.cast_inputs == null)
    seg.cast_inputs = cast.legacySelection(s, seg);
  if (s.segment_masks_version === 1) cast.restoreTake(s, seg, chosen, env);
  validate(s, items);
  seg.selected_take = chosen.id;
  s.segments = items;
  s.selected_render = null;
  c.result = null;
}
