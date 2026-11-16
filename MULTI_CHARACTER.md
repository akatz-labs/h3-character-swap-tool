# Multi-character segments

Feature work starts from `ab1b58b`, preserving the existing shot workspace,
source-audio switch and audio-aware prompt rebuilding. Development uses the
same checkout and media store; a branch does not require another media copy.

## Interaction contract

- Keep the generation sidebar, playback controls, segment editor, exact segment
  prompts, dialogue/transcription, continuation history, queue controls, take
  selection/downloads and whole-shot takes.
- The initial setup tab contains the shot cast and a separate scene-anchor
  library. Each character has a name, reference image and optional description.
  Do not require a permanent source-actor comparison on character cards.
- Scrub and download source frames, edit externally, then upload scene anchors.
  A scene anchor can contain one or several characters. Its source timestamp
  records provenance, not a timed guide constraint.
- Character masks are independent, versioned tracks over the entire shot.
  Segment boundaries do not truncate tracks. Selecting a character switches
  correction points, expansion and tracking history without changing other
  characters' state. Empty frames mean the character is absent.
- Each segment explicitly selects characters, mask tracks and scene anchors.
  Selecting a mask also selects its owning character; scene anchors remain
  independent. Selections do not depend on percentage visibility.
- Segment selection changes the displayed selections, prompt, dialogue, seed,
  connection and history. Resolution and LoRA retain their existing shot scope.
- A transition segment can select A and B with both masks and only B's scene
  anchor. A third, unrelated character is not sent merely because it exists in
  the shot library.
- Split copies the source segment's selections into both children. Merge takes
  the ordered union of selections and requires review. Boundary edits preserve
  per-segment choices and flag changed ranges for review. Preserve previous
  layouts and their takes.
- Prompt rebuilding is explicit. Changing references must flag potentially
  stale picture tokens, not silently rewrite an edited prompt.

## Data contract

`shot.cast_setup` is the standard, versioned library with `characters`,
`mask_tracks` and `scene_anchors`. Mask editor state belongs to a track, linked
by `character_id`. Assets remain existing store identifiers.

`segment.cast_inputs` holds ordered ID lists for those same three collections.
An empty list is an explicit choice, not an instruction to include the whole
library. A cast-enabled shot with missing segment selections requires review.
Existing shots and saved layouts are converted by a one-time database migration.
New shots start with one character and its mask selected; Add character expands
the same workflow. There is no separate setup mode or migration button.

The reference plan orders selected character images, generated history boundary
when present, then selected scene anchors. The same plan must drive both prompt
tokens and graph connections. The adapter must check actual supported reference
counts; library capacity is not a claim of model capacity.

Mask tracks keep independent expansion. The render adapter must expand each
selected mask separately before combining them, and avoid applying expansion a
second time to the union. A combined edit mask does not provide a hard
region-to-character identity binding.

Snapshots freeze only selected inputs plus existing render settings and exact
predecessor takes. Unselected library changes should not stale a take. Selected
input changes must invalidate that segment and dependent continuations without
deleting results. Job attachment must target the exact mask track and revision.

## Implementation status

The feature branch now connects the libraries, editor and segment executor:

- A one-time database migration reuses existing references, masks, anchors,
  segments and takes, including saved layouts. Exact effective prompts stay
  unchanged. Matching take signatures are translated into the new schema while
  retaining their original signature; stale takes remain stale. Existing cast
  setups are not rewritten. Clip revision manifests retain the pre-migration data.
- Every newly created shot has one character card and its mask selected by
  default. Additional characters and independent segment choices use the same UI.
- Revision-guarded APIs add/edit characters and upload references and scene
  anchors. Anchor uploads retain source-frame provenance and replacement history.
- The mask tab switches independent correction points, expansion, auto-selection
  settings, playback and saved masks. **Mark absent** clears an inclusive frame
  range on only the selected character while preserving the previous mask.
- The segment editor selects characters, masks and scene anchors independently.
  Update prompt uses the same picture numbering as the render graph. Users may
  explicitly confirm their manual prompt instead. Audio-off prompts omit audio
  guidance.
- Generation freezes selected inputs, checks the native nine-image reference
  limit (including the generated history boundary), expands each selected mask
  independently, and sends their union without a second expansion. Continuation
  still uses the exact generated predecessor frames.
- Mask jobs are deduplicated and attached per character track. Empty tracked
  frames are accepted; the automatic missing-person recovery does not run on
  cast tracks, where another visible person may be a different character.
- Generation controls, dialogue/transcription, segment editing, prompt editing,
  history, seeds, model settings and take selection remain available. Playback
  and segment selection remain visible from either setup tab.

CPU/API/graph tests cover selection, picture ordering, independent mask expansion,
job attachment, absence/history, split/merge/boundary edits, revision conflicts and
legacy compatibility. Browser checks use a disposable store with worker execution
stopped and exercise uploads, switching, prompt updates and queued job snapshots.

This integrates model inputs; it does not establish multi-character visual quality.
The model still receives a union edit mask, not separate hard identity bindings.
Descriptions, selected scene anchors and manual performance prompts provide that
association. Scene anchors in reference mode do not pin source timestamps. The
first-frame guide mode accepts one scene anchor for a fresh segment.
