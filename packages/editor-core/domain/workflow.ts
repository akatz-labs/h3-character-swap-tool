import preset from "../workflow.json";
import pika from "../recipes/pika-swap-relay-v2.json";
import {
  copy,
  type RecordValue as R,
  type Environment,
  requireValue,
} from "./common.js";
import { current } from "./segments.js";
export const WORKFLOW = Object.freeze(preset);
export const MODEL_PRESETS = Object.freeze([
  { ...preset, label: "Viggle Remake Swap", description: "MiniMax H3 · Viggle Remake Swap" },
  { ...pika, label: "Pika + Character Swap V1.5", description: "MiniMax H3 · Pika + Character Swap V1.5" },
]);
export function isCurrentWorkflow(s: R) {
  return current(s).every(
    (seg, index) =>
      seg.render_mode === WORKFLOW.render_mode &&
      MODEL_PRESETS.some(p => p.lora_id === seg.lora_id) &&
      seg.connection === (index ? "continue" : "start") &&
      seg.history_frames ===
        (index && seg.start < WORKFLOW.history_frames
          ? 1
          : WORKFLOW.history_frames),
  );
}
/** Explicit revision-guarded cutover. Historical settings and media remain intact. */
export function adoptWorkflow(s: R, env: Environment) {
  if (isCurrentWorkflow(s)) return;
  const saved = current(s);
  s.segment_layout_history ??= [];
  s.segment_layout_history.push({
    id: env.uid("segments-"),
    created: env.now(),
    reason: "workflow-upgrade",
    segments: copy(s.segments ?? saved),
    selected_render: s.selected_render ?? null,
  });
  s.segments = saved.map((seg, index) => ({
    ...seg,
    render_mode: WORKFLOW.render_mode,
    lora_id: MODEL_PRESETS.some(p => p.lora_id === seg.lora_id) ? seg.lora_id : WORKFLOW.lora_id,
    connection: index ? "continue" : "start",
    history_frames:
      index && seg.start < WORKFLOW.history_frames
        ? 1
        : WORKFLOW.history_frames,
  }));
}
export function requireWorkflow(s: R) {
  requireValue(
    isCurrentWorkflow(s),
    "Open this shot in the updated editor to apply the current workflow. Existing settings will be retained in layout history.",
  );
}
