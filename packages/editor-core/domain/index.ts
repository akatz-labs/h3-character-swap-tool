import { z } from "zod";
import {
  copy,
  environment,
  type Environment,
  type RecordValue,
  requireValue,
  windowPlan,
} from "./common.js";
import * as segments from "./segments.js";
import * as guides from "./guides.js";
import * as commands from "./commands.js";
const record = z.record(z.string(), z.json());
const command = z.object({
  method: z.string(),
  route: z.string(),
  body: record.optional(),
  trackId: z.string().optional(),
  guideId: z.string().optional(),
});
const request = z.discriminatedUnion("operation", [
  z.object({
    operation: z.literal("project"),
    document: record,
    command: command.extend({
      route: z.enum(["character", "import-prepared"]),
      body: record,
    }),
  }),
  z.object({
    operation: z.enum([
      "segments",
      "render-plan",
      "workspace",
      "guides",
      "edit",
      "guide-frame",
      "guide-plan",
      "render-input",
    ]),
    clip: record,
    shotId: z.string(),
    characters: z.array(record).optional(),
    command: command.optional(),
    settings: record.optional(),
  }),
]);
export type PlannerRequest = z.input<typeof request>;
/** Pure, portable request boundary. Copy-on-write protects caller state on errors.
 * No subprocess, credentials, filesystem, network, or mutable project cache. */
export function plan(input: unknown, env: Environment = environment): unknown {
  const r: RecordValue = copy(request.parse(input));
  if (r.operation === "project")
    return commands.project(r.document, r.command, env);
  const c = r.clip,
    s = c.shots?.find((s: RecordValue) => s.id === r.shotId);
  requireValue(s, "Shot not found");
  switch (r.operation) {
    case "segments":
      return segments.view(c, s);
    case "render-plan":
      return windowPlan({ ...s, ...r.settings });
    case "workspace":
      return { segments: segments.view(c, s), renderPlan: windowPlan(s) };
    case "guides":
      return guides.view(c, s, r.characters || []);
    case "guide-frame":
      return guides.frame(c, s, r.command.body.frame);
    case "guide-plan":
      return guides.plan(c, s, r.command, r.characters || []);
    case "render-input":
      return commands.renderPlan(c, s, r.command.body);
    case "edit":
      return commands.edit(c, s, r.command, r.characters || [], env);
  }
}
export {WORKFLOW,MODEL_PRESETS,adoptWorkflow,isCurrentWorkflow} from "./workflow.js";
export { windowPlan } from "./common.js";
export {
  view as segmentView,
  layout,
  planRevision,
  validate as validateSegments,
} from "./segments.js";
export type { Environment } from "./common.js";

export {characterMasks,applyCharacterMasks,validateSelection as validateCastSelection} from "./cast.js";

export { activateGeneratedGuide } from "./guides.js";
