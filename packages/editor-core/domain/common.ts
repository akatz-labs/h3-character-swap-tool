/** Version-one project records retain historical fields. Validation is owned by
 * the request boundary and the domain validators; transforms only touch named fields. */
export type RecordValue = Record<string, any>;
export interface Environment {
  now(): number;
  uid(prefix: string): string;
}
export const environment: Environment = {
  now: () => Date.now() / 1000,
  uid: (prefix) =>
    prefix + crypto.randomUUID().replaceAll("-", "").slice(0, 12),
};
export const copy = <T>(value: T): T => structuredClone(value);
export const pick = (value: RecordValue, keys: readonly string[]) =>
  Object.fromEntries(keys.filter((k) => k in value).map((k) => [k, value[k]]));
export function requireValue(
  condition: unknown,
  message: string,
): asserts condition {
  if (!condition) throw Error(message);
}
export const object = (v: unknown): v is RecordValue =>
  !!v && typeof v === "object" && !Array.isArray(v);
export const integer = Number.isInteger;
export function text(v: unknown, label: string, limit = 20000, empty = true) {
  requireValue(
    typeof v === "string" && [...v].length <= limit && (empty || v.trim()),
    "Invalid " + label,
  );
}
export function fields(
  value: unknown,
  allowed: readonly string[],
  message: string,
): asserts value is RecordValue {
  requireValue(
    object(value) && Object.keys(value).every((k) => allowed.includes(k)),
    message,
  );
}
export function identifier(v: unknown) {
  requireValue(
    typeof v === "string" && /^[A-Za-z0-9_-]{1,100}$/.test(v),
    "Expected a stable cast, mask or anchor identifier",
  );
}
// Match Python's sorted, ASCII JSON used by retained take signatures. Floating
// integral render_mp values need an explicit marker at their owning call sites.
export class FloatValue {
  constructor(readonly value: number) {}
}
export function canonical(value: any): string {
  if (value instanceof FloatValue)
    return Number.isInteger(value.value)
      ? value.value.toFixed(1)
      : String(value.value);
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (object(value))
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map((k) => canonical(k) + ":" + canonical(value[k]))
        .join(",") +
      "}"
    );
  return JSON.stringify(value).replace(
    /[\u007f-\uffff]/g,
    (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"),
  );
}
// Web Crypto is available in both runtimes. Hashing stays outside synchronous
// editing calculations; signatures below use the small portable SHA implementation.
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
export const digest = (value: unknown) =>
  bytesToHex(sha256(new TextEncoder().encode(canonical(value))));
export const source = (clip: RecordValue): string =>
  clip.render_source || clip.source;
export const HISTORY = [
  0,
  1,
  ...Array.from({ length: 20 }, (_, i) => 18 + i * 17),
];
export const WINDOWS = Array.from({ length: 21 }, (_, i) => 22 + i * 17);
export const RENDER_FIELDS = ["lora_id", "use_mask", "use_source_audio"];
export const PROMPT_FIELDS = ["performance_prompt", "dialogue", "speaker"];
export const EDITABLE = [
  "prompt",
  "seed",
  "connection",
  "history_frames",
  "anchor_mode",
  "anchor",
  "cast_inputs",
  "render_mode",
  "guide_prompt",
  ...PROMPT_FIELDS,
  ...RENDER_FIELDS,
];
export const COLLECTIONS = ["characters", "mask_tracks", "scene_anchors"];
export const MASK_COLORS = [
  "#83d9c4",
  "#db8bce",
  "#e9ac68",
  "#79b8ef",
  "#d8ce73",
  "#b3a0ef",
];
export const MASK_FIELDS = [
  "mask",
  "mask_base",
  "mask_versions",
  "mask_points",
  "mask_applied_points",
  "mask_frames",
  "mask_coverage",
  "mask_empty_frames",
  "mask_approved",
  "mask_text",
  "selection",
  "subject_index",
  "expansion",
];
export const renderShot = (shot: RecordValue, segment: RecordValue) => ({
  ...shot,
  ...pick(
    segment,
    RENDER_FIELDS.filter((k) => segment[k] != null),
  ),
});
export const effectiveHistory = (s: RecordValue): number =>
  s.connection === "continue" ? s.history_frames : 0;
export const anchor = (s: RecordValue, seg: RecordValue) =>
  seg.anchor_mode === "shot"
    ? (s.anchor ?? null)
    : seg.anchor_mode === "custom"
      ? (seg.anchor ?? null)
      : null;
export const promptMode = (s: RecordValue) =>
  s.prompt_mode || (s.performance_prompt != null ? "manual" : "auto");
export const speaker = (s: RecordValue, seg: RecordValue) =>
  seg.speaker || s.speaker || "target";
export function dialogue(
  c: RecordValue,
  s: RecordValue,
  seg: RecordValue,
): string {
  if (seg.dialogue != null) return seg.dialogue;
  if (c.transcript?.segments?.length)
    return c.transcript.segments.filter((part: RecordValue) =>
      part.end > (s.start + seg.start) / 24 && part.start < (s.start + seg.end) / 24
    ).map((part: RecordValue) => part.text).join(" ").trim();
  return (c.transcript?.words || [])
    .filter(
      (w: RecordValue) =>
        (s.start + seg.start) / 24 <= (w.start + w.end) / 2 &&
        (w.start + w.end) / 2 < (s.start + seg.end) / 24,
    )
    .map((w: RecordValue) => w.word)
    .join("")
    .trim();
}
export function settings(s: RecordValue) {
  const result = {
    lora_id: s.lora_id ?? "v1-1000",
    use_anchor: s.use_anchor ?? true,
    anchor_conditioning: s.anchor_conditioning ?? "reference",
    use_mask: s.use_mask ?? true,
    use_source_audio: s.use_source_audio ?? true,
    window_frames: s.window_frames ?? 124,
    history_frames: s.history_frames ?? 18,
  };
  requireValue(
    ["none", "v1-1000", "v2-1000", "viggle-remake-swap"].includes(result.lora_id),
    "Choose V1, V1.5, or no character-swap LoRA",
  );
  requireValue(
    [result.use_anchor, result.use_mask, result.use_source_audio].every(
      (v) => typeof v === "boolean",
    ),
    "Conditioning switches must be true or false",
  );
  requireValue(
    ["reference", "first_frame"].includes(result.anchor_conditioning),
    "Choose appearance reference or first-frame guide",
  );
  requireValue(
    integer(result.window_frames) && integer(result.history_frames),
    "Diffusion window and history must be whole frame counts",
  );
  requireValue(
    WINDOWS.includes(result.window_frames),
    "Window must be 22, 39, 56, ... 362 frames (17n+5).",
  );
  requireValue(
    HISTORY.includes(result.history_frames),
    "History must be 0, 1, or 18, 35, 52, ... (17n+1).",
  );
  requireValue(
    result.history_frames < result.window_frames,
    "History must be smaller than the generation window.",
  );
  return result;
}
export function windowPlan(shot: RecordValue) {
  const config = settings(shot),
    count = shot.end - shot.start,
    windows: RecordValue[] = [];
  requireValue(count >= 1, "Source video has no frames.");
  let covered = 0;
  while (covered < count) {
    const h = windows.length ? config.history_frames : 0,
      start = covered - (h ? 1 : 0),
      needed = Math.min(count - start, config.window_frames);
    const size = WINDOWS.find((n) => n >= needed && n > h)!;
    const end = Math.min(count, start + size);
    windows.push({
      index: windows.length,
      source_start: start,
      source_end_exclusive: end,
      window_frames: size,
      padded_tail_frames: size - (end - start),
      history_frames: h,
      history_start: h ? start - h + 1 : null,
      history_end_exclusive: h ? start + 1 : null,
      discarded_overlap: covered - start,
      delivered_frames: end - covered,
    });
    covered = end;
  }
  return {
    frames: count,
    fps: 24,
    window_frames: config.window_frames,
    history_frames: config.history_frames,
    windows,
    window_choices: WINDOWS,
    history_choices: HISTORY,
  };
}
export function segmentWindow(count: number, h = 0) {
  return (
    Math.ceil((Math.max(22, count + (h ? 1 : 0), h + 1) - 5) / 17) * 17 + 5
  );
}
export function validateMP(v: unknown): asserts v is number {
  requireValue(
    typeof v === "number" && Number.isFinite(v) && v >= 0.1 && v <= 1,
    "Choose a render size between 0.1 and 1.0 MP",
  );
}
export function dimensions(c: RecordValue, s: RecordValue): number[] {
  const mp = s.render_mp ?? 1;
  validateMP(mp);
  if (mp === 1) return c.dimensions;
  const [w, h] = c.dimensions,
    budget = Math.trunc(mp * 1e6),
    iw = Math.sqrt((budget * w) / h),
    ih = Math.sqrt((budget * h) / w);
  let best = [Infinity, 0, 0, 0];
  for (let x = 32; x <= Math.min(16384, Math.floor(budget / 32)); x += 32) {
    const top = Math.min(16384, Math.floor(budget / x / 32) * 32);
    for (const y of new Set([
      Math.max(32, Math.min(top, Math.floor(ih / 32) * 32)),
      Math.max(32, Math.min(top, Math.ceil(ih / 32) * 32)),
    ])) {
      const n = [Math.log(x / iw) ** 2 + Math.log(y / ih) ** 2, -x * y, x, y];
      if (n[0] < best[0] || (n[0] === best[0] && n[1] < best[1])) best = n;
    }
  }
  return best.slice(2);
}
export const hashText = (text: string) =>
  bytesToHex(sha256(new TextEncoder().encode(text)));
