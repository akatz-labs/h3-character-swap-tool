import {
  type RecordValue as R,
  type Environment,
  copy,
  pick,
  requireValue,
  fields,
  integer,
  text,
  MASK_FIELDS,
  MASK_COLORS,
  source,
  renderShot,
  effectiveHistory,
  anchor,
  settings,
  validateMP,
  EDITABLE,
} from "./common.js";
import { WORKFLOW, adoptWorkflow, requireWorkflow } from "./workflow.js";
import * as cast from "./cast.js";
import * as guides from "./guides.js";
import * as segments from "./segments.js";
export function edit(
  c: R,
  s: R,
  command: R,
  characters: R[],
  env: Environment,
) {
  const { method, route } = command,
    b = command.body ?? {},
    parts = route.split("/");
  if (route === "segments/workflow") adoptWorkflow(s, env);
  else if (parts.length === 3 && parts[0] === "segments" && parts[2] === "take")
    segments.selectTake(c, s, parts[1], b.take_id, env);
  else if (route === "segments/layout")
    segments.applyLayout(c, s, b.operation, env);
  else if (route === "shot-settings") shotSettings(c, s, b.changes);
  else if (route === "guide-add") {
    guides.frame(c, s, b.frame);
    s.guides ??= [];
    s.guides.push({
      id: b.id,
      frame: s.start + b.frame,
      source: source(c),
      source_asset: b.asset,
      dimensions: c.dimensions,
      prompt: "",
      references: [],
      quality: "medium",
      helpers: [pick(guides.PRESETS[0], ["id", "text"])],
      enabled: true,
      approved: null,
      versions: [],
      created: env.now(),
    });
  } else if (route === "guide-upload") {
    const g = guides.marker(s, b.guideId);
    if (b.kind === "reference") {
      requireValue(
        g.references.length < 8,
        "Use at most 15 additional references",
      );
      s.guide_references ??= [];
      s.guide_references.push({
        asset: b.asset,
        name: b.name.slice(0, 160),
        kind: "Uploaded image",
      });
      if (b.select_reference) g.references.push(b.asset);
    } else if (b.kind === "source") {
      g.source_uploads ??= [];
      g.source_uploads.push(b.asset);
    } else if (b.kind === "version")
      g.versions.push({
        id: env.uid("version-"),
        asset: b.asset,
        created: env.now(),
        model: "Uploaded edit",
        prompt: "",
        references: [],
        quality: null,
        note: "",
      });
    else throw Error("Upload a source, reference or edited version");
  } else if (parts[0] === "guides") {
    const g = guides.marker(s, parts[1]),
      changes = b.changes || {};
    if (method === "DELETE" && parts.length === 2)
      Object.assign(g, { archived: true, enabled: false });
    else if (method === "PATCH" && parts.length === 2) {
      guides.validateDraft(c, s, g, changes, characters);
      Object.assign(g, changes);
    } else if (
      method === "POST" &&
      parts.length === 3 &&
      ["save", "approve"].includes(parts[2])
    ) {
      if (parts[2] === "save") {
        fields(
          changes,
          ["prompt", "quality", "references", "helpers", "edit_source_asset"],
          "Unknown guide setting",
        );
        guides.validateDraft(c, s, g, changes, characters);
      }
      const version = b.version_id ?? null;
      if (version != null) {
        requireValue(
          guides.current(c, s, g),
          "Source changed; create a new guide",
        );
        requireValue(
          g.versions.some((v: R) => v.id === version),
          "Choose a saved version",
        );
      }
      if (parts[2] === "save") Object.assign(g, changes);
      g.approved = version;
    } else throw Error("Unsupported guide command");
    guides.syncReference(c, s, g);
  } else if (parts[0] === "segments") {
    if (method === "PATCH" && parts.length === 2)
      segments.edit(c, s, parts[1], b.changes);
    else if (method === "POST" && parts.length === 3 && parts[2] === "prompt") {
      const items = segments.current(s),
        seg = items.find((v) => v.id === parts[1]);
      requireValue(seg, "Segment no longer exists");
      Object.assign(seg, {
        performance_prompt: segments.buildPrompt(c, s, seg),
        prompt_mode: "auto",
        needs_review: false,
      });
      if (s.cast_setup)
        seg.cast_prompt_signature = cast.promptSignature(c, s, seg);
      segments.validate(s, items);
      s.segments = items;
    } else throw Error("Unsupported segment command");
  } else if (parts[0] === "cast" && parts[1] === "characters") {
    const library = s.cast_setup.characters;
    let target: R | undefined;
    if ("segment_id" in b) {
      target = s.segments.find((seg: R) => seg.id === b.segment_id);
      requireValue(target, "Segment no longer exists");
    }
    const member = (id: string) => {
      const entry = characters.find((c) => c.id === id);
      requireValue(entry, "Character not found in this project");
      return entry;
    };
    const add = (entry: R) => {
      let existing = library.find(
        (c: R) => c.project_character_id === entry.id,
      );
      if (!existing) {
        existing = shotRecord(entry, env.uid("cast-"));
        library.push(existing);
      }
      return existing;
    };
    if (method === "POST" && parts.length === 2) {
      const ids = b.character_ids;
      requireValue(
        Array.isArray(ids) &&
          ids.length &&
          ids.every((k) => typeof k === "string") &&
          new Set(ids).size === ids.length,
        "Choose project characters",
      );
      requireValue(
        !b.create_mask || s.segment_masks_version === 1,
        "Create masks separately in the hosted editor",
      );
      for (const id of ids) {
        const entry = add(member(id));
        for (const seg of target ? [target] : s.segments)
          if (!seg.cast_inputs.characters.includes(entry.id))
            seg.cast_inputs.characters.push(entry.id);
      }
    } else if (["PATCH", "DELETE"].includes(method) && parts.length === 3) {
      const curr = library.find((c: R) => c.id === parts[2]);
      requireValue(curr, "Character no longer exists");
      if (method === "PATCH") {
        const replacement = member(b.project_character_id);
        if (target) {
          const selected = target.cast_inputs;
          requireValue(
            selected.characters.includes(curr.id),
            "Character not assigned to this segment",
          );
          const entry = add(replacement);
          requireValue(
            entry.id === curr.id || !selected.characters.includes(entry.id),
            "Character already assigned",
          );
          const assignments = cast.characterMasks(selected);
          if (curr.id !== entry.id) {
            assignments[entry.id] = assignments[curr.id] || [];
            delete assignments[curr.id];
          }
          selected.characters = selected.characters.map((id: string) =>
            id === curr.id ? entry.id : id,
          );
          cast.applyCharacterMasks(selected, assignments);
        } else Object.assign(curr, shotRecord(replacement, curr.id));
      } else {
        s.cast_setup.characters = library.filter((c: R) => c !== curr);
        for (const t of s.cast_setup.mask_tracks)
          if (t.character_id === curr.id) t.character_id = null;
        for (const seg of s.segments) {
          const selected = seg.cast_inputs;
          if (!selected.characters.includes(curr.id)) continue;
          const assignments = cast.characterMasks(selected);
          selected.characters = selected.characters.filter(
            (id: string) => id !== curr.id,
          );
          delete assignments[curr.id];
          cast.applyCharacterMasks(selected, assignments);
          if (s.segment_masks_version === 1)
            seg.use_mask = !!selected.mask_tracks.length;
        }
      }
    } else throw Error("Unsupported character assignment command");
    c.result = null;
  } else if (route === "mask-settings") {
    const edits = b.changes;
    fields(
      edits,
      ["mask_points", "expansion", "selection", "subject_index", "mask_text"],
      "Unknown mask setting; saved mask selection requires processing",
    );
    const t = cast.track(s, command.trackId),
      v = {
        expansion: 64,
        selection: "largest",
        subject_index: 0,
        mask_points: [],
        mask_applied_points: [],
        mask: null,
        ...copy(t.state),
      };
    cast.preserve(v, env);
    if ("selection" in edits)
      requireValue(
        ["largest", "leftmost", "rightmost", "index"].includes(edits.selection),
        "Invalid person selection",
      );
    if ("subject_index" in edits)
      requireValue(
        integer(edits.subject_index) &&
          edits.subject_index >= 0 &&
          edits.subject_index <= 100,
        "Invalid person index",
      );
    if ("mask_text" in edits) text(edits.mask_text, "mask description");
    if (
      ["selection", "subject_index", "mask_text"].some(
        (k) => k in edits && v[k] !== edits[k],
      )
    )
      v.mask = null;
    Object.assign(v, edits, { mask_approved: false });
    t.state = pick(v, MASK_FIELDS);
    c.result = null;
  } else if (parts[0] === "cast" && parts[1] === "masks") {
    const tracks = s.cast_setup.mask_tracks;
    if (method === "POST" && parts.length === 2) {
      requireValue(
        s.segment_masks_version === 1,
        "Upgrade this shot to segment masks before editing",
      );
      const seg = s.segments.find((v: R) => v.id === b.segment_id);
      requireValue(seg, "Choose a segment before adding a mask");
      const local = tracks.filter((t: R) => t.segment_id === seg.id),
        names = new Set(
          local.map((t: R, i: number) => t.name ?? `Mask ${i + 1}`),
        );
      let n = 1;
      while (names.has(`Mask ${n}`)) n++;
      const r = {
        id: env.uid("mask-"),
        segment_id: seg.id,
        character_id: null,
        name: `Mask ${n}`,
        color: MASK_COLORS[(n - 1) % MASK_COLORS.length],
        state: {
          mask: null,
          expansion: 64,
          selection: "largest",
          subject_index: 0,
          mask_points: b.points || [],
          mask_applied_points: [],
        },
      };
      tracks.push(r);
      // Editing a mask does not opt a character into using it.
      seg.use_mask = !!seg.cast_inputs.mask_tracks.length;
      c.result = null;
    } else if (["PATCH", "DELETE"].includes(method) && parts.length === 3) {
      const t = cast.track(s, parts[2]);
      if (method === "PATCH") {
        text(b.name, "mask name", 120, false);
        t.name = b.name.trim();
      } else {
        s.mask_track_history ??= [];
        s.mask_track_history.push(copy(t));
        s.cast_setup.mask_tracks = tracks.filter((v: R) => v !== t);
        for (const seg of s.segments) {
          const sel = seg.cast_inputs;
          if (!sel.mask_tracks.includes(t.id)) continue;
          const assignments = cast.characterMasks(sel);
          cast.applyCharacterMasks(
            sel,
            Object.fromEntries(
              Object.entries(assignments).map(([id, masks]) => [
                id,
                masks.filter((k) => k !== t.id),
              ]),
            ),
          );
          seg.use_mask = !!sel.mask_tracks.length;
        }
        c.result = null;
      }
    } else throw Error("Unsupported mask command");
  } else throw Error("Unsupported document command");
  if (s.cast_setup) {
    cast.validateCatalog(s, s.cast_setup);
    segments.validate(s, segments.current(s));
  }
  segments.refreshPrompts(c);
  c.revision++;
  return c;
}
function shotRecord(c: R, id: string) {
  return {
    ...pick(c, ["name", "description", "reference"]),
    id,
    project_character_id: c.id,
    project_character_revision: c.revision,
  };
}
function shotSettings(c: R, s: R, changes: unknown) {
  fields(
    changes,
    [
      "dialogue",
      "speaker",
      "prompt",
      "anchor_prompt",
      "anchor_instructions",
      "reference",
      "character",
      "anchor_conditioning",
      "seed",
      "render_mp",
      "window_frames",
      "history_frames",
      "lora_id",
      "use_anchor",
      "use_mask",
      "use_source_audio",
      "mode",
    ],
    "Unsupported hosted shot setting",
  );
  settings({ ...s, ...changes });
  if ("render_mp" in changes) validateMP(changes.render_mp);
  for (const k of [
    "dialogue",
    "prompt",
    "anchor_prompt",
    "anchor_instructions",
    "character",
  ])
    if (k in changes) text(changes[k], k);
  if ("seed" in changes)
    requireValue(
      integer(changes.seed) && changes.seed >= 0 && changes.seed < 2 ** 63,
      "Invalid seed",
    );
  for (const [k, options] of [
    ["speaker", ["target", "other", "offscreen", "none"]],
    ["mode", ["generate", "preserve"]],
  ] as const)
    if (k in changes)
      requireValue(
        (options as readonly string[]).includes(changes[k]),
        "Invalid " + k,
      );
  for (const k of ["use_anchor", "use_mask", "use_source_audio"])
    if (k in changes)
      requireValue(typeof changes[k] === "boolean", "Invalid " + k);
  if (
    ["reference", "character"].some(
      (k) => k in changes && changes[k] !== (k in s ? s[k] : c[k]),
    )
  ) {
    s.anchor_approved = false;
    c.result = null;
  }
  for (const k of ["dialogue", "prompt"])
    if (k in changes && changes[k] !== s[k]) s[k + "_edited"] = true;
  if ("mode" in changes && changes.mode !== s.mode) c.result = null;
  Object.assign(s, changes);
}
export function project(document: R, command: R, env: Environment) {
  const b = command.body;
  if (command.route === "import-prepared") {
    for (const item of b.imports) {
      if (document.clips.some((c: R) => c.id === item.clipId)) continue;
      const p = item.ingestion,
        v = p.normalized,
        src = "asset-" + v.id,
        poster = "asset-" + p.thumbnail.id;
      const c: R = {
        id: item.clipId,
        batch_id: document.batch.id,
        revision: 1,
        name: item.name,
        source: src,
        render_source: src,
        original: "asset-" + p.original.id,
        poster,
        width: v.width,
        height: v.height,
        dimensions: [v.width, v.height],
        frames: v.frames,
        fps: v.fps,
        duration: v.durationSeconds,
        audio: v.audio,
        reference: null,
        character: "",
        shots: [],
        cuts_approved: true,
        transcript: null,
        result: null,
        platform_ingestion: p,
        platform_playback: {
          state: "ready",
          asset: "asset-" + p.preview.id,
          frames: v.frames,
          fps: 24,
          width: p.preview.width,
          height: p.preview.height,
        },
      };
      const s: R = {
        id: env.uid("shot-"),
        start: 0,
        end: v.frames,
        mode: "generate",
        source_frame: poster,
        speaker: c.audio ? "target" : "none",
        dialogue: "",
        seed: 194729,
        render_mp: 0.5,
        lora_id: WORKFLOW.lora_id,
        use_anchor: true,
        use_mask: true,
        expansion: 64,
        selection: "largest",
        subject_index: 0,
        mask: null,
        mask_approved: false,
        anchor: null,
        anchor_approved: false,
        anchors: [],
        renders: [],
        selected_render: null,
      };
      s.prompt = segments.mediaPrompt(c, s);
      s.anchor_conditioning = "reference";
      const seg = segments.fresh(s, 0, v.frames, 0, env);
      s.cast_setup = {
        version: 1,
        characters: [],
        mask_tracks: [],
        scene_anchors: [],
      };
      seg.cast_inputs = { characters: [], mask_tracks: [], scene_anchors: [] };
      s.segments = [seg];
      seg.performance_prompt = segments.buildPrompt(c, s, seg);
      seg.cast_prompt_signature = cast.promptSignature(c, s, seg);
      seg.cast_inputs.mask_assignments = {};
      seg.use_mask = false;
      s.segment_masks_version = 1;
      s.mask_track_history = [
        {
          id: "mask-legacy",
          character_id: null,
          name: "Mask 1",
          color: MASK_COLORS[0],
          state: {
            mask: null,
            expansion: 64,
            selection: "largest",
            subject_index: 0,
            mask_points: [],
            mask_applied_points: [],
          },
        },
      ];
      s.use_mask = false;
      s.use_anchor = false;
      c.shots = [s];
      document.clips.push(c);
    }
  } else if (command.route === "character") {
    const key = b.id;
    let row: R, data: R;
    if (command.method === "POST") {
      data = { name: b.name, description: "", reference: null };
      cast.validateCharacter(data);
      row = {
        id: key,
        batch_id: document.batch.id,
        revision: 1,
        created: env.now(),
        data,
      };
      document.characters.push(row);
    } else {
      row = document.characters.find((c: R) => c.id === key);
      requireValue(row, "Character not found");
      requireValue(
        row.revision === b.revision,
        "Character changed; reload before saving",
      );
      fields(
        b.changes,
        ["name", "description", "reference"],
        "Unknown character field",
      );
      data = { ...row.data, ...b.changes };
      cast.validateCharacter(data);
      if (JSON.stringify(data) === JSON.stringify(row.data)) return document;
      row.data = data;
      row.revision++;
      for (const c of document.clips) {
        let changed = false;
        for (const s of c.shots)
          for (const ch of s.cast_setup?.characters || [])
            if (ch.project_character_id === key) {
              Object.assign(ch, data, {
                project_character_revision: row.revision,
              });
              changed = true;
            }
        if (changed) {
          segments.refreshPrompts(c);
          c.result = null;
          c.revision++;
        }
      }
    }
    document.characterVersions.push({
      character_id: key,
      revision: row.revision,
      created: env.now(),
      data: copy(data),
    });
  } else throw Error("Unsupported project command");
  return document;
}
export function renderPlan(c: R, s: R, b: R) {
  requireWorkflow(s);
  const items = segments.current(s),
    expected = b.expected_render;
  if (expected) {
    const defaults: R = {
      render_mp: 0.5,
      window_frames: 124,
      history_frames: 18,
      anchor_conditioning: "reference",
      use_source_audio: true,
    };
    requireValue(
      Object.entries(expected).every(
        ([k, v]) =>
          "" + JSON.stringify(s[k] ?? defaults[k]) === JSON.stringify(v),
      ),
      "Generation settings changed; review the saved settings",
    );
  }
  const selection = b.options?.segments || {};
  requireValue(
    (selection.mode ?? "segment") === "segment" || items.length <= 1,
    "The connected preview renders one selected segment at a time",
  );
  if (Object.keys(selection).length)
    requireValue(
      selection.plan_revision === segments.planRevision(s),
      "Segment settings changed; reload the saved plan",
    );
  const seg = Object.keys(selection).length
    ? items.find((v) => v.id === selection.segment_id)
    : items[0];
  requireValue(seg, "Select a current segment");
  const chosen = guides.selected(c, s, seg, true, false);
  const config = renderShot(s, seg);
  const selectedMasks = cast.generationInputs(c, s, seg).mask_tracks;
  requireValue(
    selectedMasks.every((m: R) => m.mask),
    "Track selected masks before generating",
  );
  requireValue(!cast.pendingMasks(c, s, seg).length, "Apply pending corrections on selected masks before generating");
  const history = segments
    .predecessors(items, items.indexOf(seg))
    .map(([parent, frames]) => ({
      segment_id: parent.id,
      frames,
      take_id: segments.continuationTake(c, s, parent)?.id ?? null,
      asset: segments.reusable(c, s, parent)
        ? (segments.continuationTake(c, s, parent)?.master ?? null)
        : null,
      signature: segments.signature(c, s, parent),
    }));
  const references = s.cast_setup
    ? cast.referencePlan(c, s, seg)
    : [{ asset: "reference" in s ? s.reference : c.reference }].filter(r => r.asset);
  requireValue(
    references.length <= 9 &&
      references.every((r: R) => r.asset),
    "Use up to 9 valid character references",
  );
  const reference = references[0]?.asset ?? null;
  requireValue(
    seg.end - seg.start >= 5 && seg.end - seg.start <= 362,
    "Character swap supports 5–362 frames per segment",
  );
  requireValue(
    seg.end - seg.start <= 720,
    "The installed cloud operation accepts segments up to 30 seconds",
  );
  const start = s.start + seg.start,
    result: R = {
      source: source(c),
      reference,
      prompt: segments.prompt(c, s, seg),
      clipId: c.id,
      shotId: s.id,
      sourceRevision: c.revision,
      segment: Object.fromEntries(
        Object.entries(seg).filter(([k]) => k !== "takes"),
      ),
      signature: guides.signature(c, s, seg),
      history,
      dimensions: c.dimensions,
    };
  result.guided = {
    references: references.map((r: R) => r.asset),
    ...(selectedMasks.length ? { masks: selectedMasks } : {}),
    guides: chosen,
    parameters: {
      historyFrames: effectiveHistory(seg),
      guideFrames: JSON.stringify(
        chosen.map((g) => g.frame - start),
      ).replaceAll(",", ", "),
      seed: seg.seed,
      megapixels: s.render_mp ?? 0.5,
      sourceAudio: config.use_source_audio ?? true,
      offloading: false,
    },
  };
  result.sourceRange = {
    startFrame: start,
    frameCount: seg.end - seg.start,
    fps: 24,
    prefixFrames: effectiveHistory(seg),
  };
  return result;
}
