# H3 UI component reference

Shared implementations live in `packages/editor-core`. Local setup controls live in
`web/src/LocalSettings.tsx`; the hosted account shell is owned by its private consumer.
Start here before changing a view. The live reference sheet is `/static/components.html`.
It renders the production factories from [static/ui-components.js](../packages/editor-core/static/ui-components.js), not copies.
Approved design: `docs/design/shot-outputs-20261109` (card A, viewer, failure, assembly).

| Component / implementation | Reuse | Contract |
| --- | --- | --- |
| `ToastViewport`, `notify`, `reportError` — [notifications.ts](../packages/editor-core/web/src/notifications.ts), [ToastViewport.tsx](../packages/editor-core/web/src/ToastViewport.tsx) | React and legacy editor notifications | Top-center top-layer toast; no layout displacement; AbortError cancellation is silent, real errors are dismissible, informational notices expire. Keep form validation inline. |
| Guide workspace — [guides.js](../packages/editor-core/static/guides.js), [fixed-editor.css](../packages/editor-core/web/src/fixed-editor.css) | Local and hosted timeline guides | Comparison panels, horizontal candidates, open two-column references, stable inspector scroll, explicit Use this version action and In use badge. [Approved concept](design/guide-modal-20261114/design.md). |
| `WorkbenchHeader` — [web/src/WorkbenchHeader.tsx](../web/src/WorkbenchHeader.tsx) | Local header | Official avatar/brand, GitHub, Support and Settings; no Platform account requests. |
| `LocalSettings` — [web/src/LocalSettings.tsx](../web/src/LocalSettings.tsx) | Local execution modal | App-kit Dialog/SelectMenu/ResourcesPage/WorkerSetup; worker and offloading preferences stay outside project state. Fixed responsive dialog dimensions across tabs; title, tabs and footer stay visible while the content pane scrolls. |
| `LocalGuideSettings` — [web/src/LocalGuideSettings.tsx](../web/src/LocalGuideSettings.tsx) | Local settings → Guide generation | Write-only key entry; configured status, replacement and confirmed deletion. No saved credential or browser persistence. |
| `LocalTranscriptionSettings` — [web/src/LocalTranscriptionSettings.tsx](../web/src/LocalTranscriptionSettings.tsx) | Local settings → Transcription | Shared SelectMenu; Auto prefers installed Whisper, explicit Local/OpenAI overrides persist in the backend. Shows availability without exposing credentials. |
| `EditorAdapter` — [adapter.ts](../packages/editor-core/web/src/adapter.ts) | Both shells | Transport, assets, health, job notifications and execution preferences; no per-field configuration matrix. |
| `H3UI.button`, `select`, `status`, `empty` — [static/ui-components.js](../packages/editor-core/static/ui-components.js) | Output toolbar, assembly actions, empty and progress states | Escaped text, labeled controls, no requests or mutation |
| `generationPriceButton` — [generation-pricing.js](../packages/editor-core/static/generation-pricing.js) | Hosted mask, segment and guide actions | Optional adapter pricing capability; compact credit badge, pending/error disables submission, stale responses ignored. Local execution has no price UI or requests. |
| `H3UI.shotRow` | Flat project shot list | Stable shot and parent IDs; never reconstruct or delete source ranges |
| `H3UI.mediaCard` | Output grid and assembly take choices | Body opens preview; separate download and choice hit targets; no nested interactive elements |
| `H3UI.viewer` | Completed output, final and failed-attempt viewers | Left media / right scrollable inspector; trusted child markup only |
| `H3UI.parameters`, `failure` | All output inspectors | Read-only frozen parameters; escaped copyable errors; recovery suggestions are text |
| `WorkspaceTabs` — [web/src/WorkspaceTabs.tsx](../packages/editor-core/web/src/WorkspaceTabs.tsx) | Shot Edit/Outputs navigation | Accessible current-page state; caller owns navigation |
| `ProjectSidebarFooter` — [web/src/ProjectSidebarFooter.tsx](../packages/editor-core/web/src/ProjectSidebarFooter.tsx) | Shots and Assets sidebars | One project queue entry with immediate submitting/accepted count and activity spinner, package version, centered Credits button using the shared Dialog for model acknowledgments, and maker credit; no duplicated footer markup in the React shell |
| Shared sidebar geometry — [web/src/project-sidebar.css](../packages/editor-core/web/src/project-sidebar.css) | `.h3-project-sidebar`, navigation header, divider and footer | Same padding, typography and footer anchor across both views; validate bounding boxes when switching tabs |
| `ProjectNavigation` — [web/src/ProjectNavigation.tsx](../packages/editor-core/web/src/ProjectNavigation.tsx) | Editor and Assets project headers | Shots / Assets; shared project chooser, per-project pencil and rename/delete dialog, with confirmation before permanent deletion |
| `Icon`, `Dialog`, `SelectMenu`, `WorkerStatusIcon` — `@akatz/app-kit/react` | React shell, worker picker, Assets | Use pinned app-kit implementations; do not fork icon paths or generic shell controls |
| `dialog`, `videoPreview` — `static/app.js`, `static/media-previews.js` | Legacy modal lifecycle and cached playback | Existing cancellation, focus and cleanup; never instantiate a second media loader |
| `static/ui-components.css`, `static/select.css`, `web/src/theme.css` | Shared visual tokens, card/inspector surfaces, native selects | Keep slate/mint branding; focus, pointer, disabled and narrow states |

## Inspector interaction primitives

`bindInspectorAccordion` in [segments.js](../packages/editor-core/static/segments.js)
backs both Prompt & dialogue and Render settings. Each heading contains one native
button with `aria-expanded` and `aria-controls`; its named region is hidden when
closed. Enter/Space toggle and Tab follows normal form order. Opening reveals the
panel in the inspector scroll container without moving keyboard focus, and respects
reduced motion. Semantics follow the [WAI-ARIA accordion pattern](https://www.w3.org/WAI/ARIA/apg/patterns/accordion/);
scrolling into view is an editor usability behavior, not an APG requirement.

`.h3-ui-busy` is the shared circular activity indicator; `.action-busy::after`
uses non-shrinking square geometry for transient button operations. Do not allow
flex layout to compress their width. Interactive chrome and headings disable text
selection; inputs, prompts, transcripts and diagnostic text remain copyable.

## Domain-specific compositions

[static/outputs.js](../packages/editor-core/static/outputs.js) owns output records, filtering, frozen provenance and modal composition.
[static/final-assembly.js](../packages/editor-core/static/final-assembly.js) owns take-selection drafts and continuity feedback. Its cards use
`H3UI.mediaCard` with separate thumbnail preview and labelled Use take / Selected
buttons. Each segment keeps a bounded, thin-scrollbar list; selection redraws retain
scroll and keyboard focus. `openTakePreview` in `outputs.js` opens a child native
dialog above assembly, preserving its mounted draft. Close, Escape, backdrop and
Back to selection dismiss only the preview and restore focus. Selection is
independent of saved generation settings. Backend assembly
validates the immutable request. Timeline guide editing and mask editing remain specialized.
Do not generalize their media interaction state into the component factories.

## Rules

- Check this table and the live sheet first. Extend a shared component for a shared behavior;
  keep single-view orchestration local. Add new reusable components here and to the sheet.
- Factories escape text/attribute inputs. Only `viewer` accepts trusted composed HTML.
- Cards have no Use this take, retry-offload or collect-result action. Failed cards open
  the same inspector pattern. Job management lives in the shared sidebar Project queue.
- No batch-action navigation. Project shots are independent editing units.
- Validate keyboard focus, touch downloads, wheel scrolling, narrow viewports and open
  select menus. Content remains selectable; control chrome does not select text.
- Snapshot parameters belong to the take/job, never the current editable shot.
- Finals are immutable output records; creating one must not restore a take's parameters.

The sheet is developer documentation, not a duplicate application workflow.

## Existing specialized views

- [WorkbenchHeader.tsx](../web/src/WorkbenchHeader.tsx): local header links and the Settings entry point.
- [ProjectAssets.tsx](../packages/editor-core/web/src/ProjectAssets.tsx): project asset management; keep asset editing here rather than duplicating it in output inspectors.
- [cast-ui.js](../packages/editor-core/static/cast-ui.js): character picker, segment assignments and mask controls.
- [guides.js](../packages/editor-core/static/guides.js): timeline guide markers, source/edit comparison and guide version controls.
- [segments.js](../packages/editor-core/static/segments.js): segment timeline, saved prompt controls and generation readiness.
- [legacy-lifecycle.ts](../packages/editor-core/web/src/legacy-lifecycle.ts): scoped editor cleanup and navigation guards.

These are domain components with their own state and persistence. Reuse their existing entry
points; keep generic button/card/inspector presentation in H3UI and shell controls in app-kit.

Character asset cards retain their existing edit/usage behavior in `ProjectAssets.tsx`; their grid uses 220px minimum columns and 210px image areas rather than a fixed three-column layout. Search is bounded to 440px. Output video cards retain their distinct playback/download contract.


`segmentCastHtml` / `bindSegmentCast` in `static/cast-ui.js` compose two independent controls:
character reference cards and the segment Edit region mask list. The latter reuses the existing
named mask toolbar and per-segment `cast_inputs.mask_tracks`. Each mask record has one
`segment_id`; an empty segment offers an implicit Mask 1, instantiated by its first point.
New masks are selected automatically. The **Track** button processes only that segment.
Do not put mask assignments back into character cards. Removing/replacing a reference must retain
mask records; unchecking a mask removes it from generation inputs but retains its points and tracking.
There is no separate Use masks toggle. `use_mask` is derived from the selected mask IDs.

Character references use the shared click/drop upload panel in `ProjectAssets.tsx`, aligned with the details panel. Browser normalization retains alpha and bounds images to 4096 px. Mask points and editable segment/shot fields retain local drafts while serialized autosaves run; navigation flushes those writes without duplicate discard dialogs.

Guide drafts use `packages/editor-core/web/src/guide-drafts.ts`: browser storage is
scoped to the adapter identity, project, clip, shot and guide. Prompt, quality,
references, helpers and source choice survive closing/reloading; version approval
remains explicit. Generating persists those inputs in either backend before
submission. Both hosted and configured local OpenAI providers enable the same
Generate guide action. No credential is stored in browser draft data.

Guide inspector: `static/guides.js` and `web/src/fixed-editor.css` own the scrolling parameters, pinned generation action, header draft status and immediate loading shell. `static/generation-pricing.js` keeps unchanged prices visible during background refresh; changed inputs and failed price reads still gate submission.

Guide modal refinements (2026-11-14): `static/guides.js` owns the revision-aware,
five-minute/eight-shot LRU; the loading view uses the same comparison figures,
history, inspector and footer geometry as the loaded view. Image previews keep
stable asset URLs so the browser HTTP image cache is reused. Queue cards and
completed-output routing share `queueJobHtml`/`queueOutput` in `static/app.js` and
`openOutput` in `static/outputs.js`; errors use a selectable disclosure with Copy
error. `ToastViewport` dismisses every notification after at most ten seconds.
`shot-autosave.js` publishes save status to the shared shot header; failed saves
retain their recovery controls. Local generation uses a fixed split action;
hosted generation remains a single-segment action with its credit quote.

### Character masks and segment range actions

`segmentCastHtml` / `bindSegmentCast` in `packages/editor-core/static/cast-ui.js`
render the shared reference card with an optional Mask selector. New references
use No mask. `characterMasks` / `applyCharacterMasks` in `domain/cast.ts` retain
older combined regions and derive the deduplicated edit-mask list. These are UI
associations, never subject-targeted conditioning. Local Python uses the same
contract in `h3workbench/cast.py`; segment deletion, replacement, layout changes,
and take restoration preserve/remap associations.

`generateSegments` in `static/segments.js` owns the shared split action. Hosted
range actions validate every segment before submitting individual Platform jobs;
`generation-pricing.js` sums individual quote prices, retaining per-job rounding.
Partial submission reports how many jobs were accepted and stops on failure.
Local range submission retains the native runner's scheduling.

Segment readiness uses `domain/segments.ts` and `static/segments.js`; the timeline
shows generated counts and accessible per-segment state rather than a second UI-only
cache. Local OpenAI settings configure both guide generation and clip transcription.
See [readiness and transcription](segment-readiness-and-transcription.md).

File drops use `bindFileDrop` in `packages/editor-core/web/src/file-drop.ts`.
It owns file-type validation, nested drag highlighting and listener cleanup.
The full Shots workspace (including sidebar/canvas padding) accepts video drops
and reuses `importShotFiles`; guide source, edited image and reference
drops reuse the guide upload handler. File-picker controls remain available.

Image uploads use `packages/editor-core/web/src/image-upload.ts` for guide source,
edited versions, guide references and character assets, with both adapters.
Browser-decodable images become bounded PNGs with alpha retained; animated inputs
become still frames. The sidebar displays imports newest first without reordering
the saved project or shots within an imported clip.

### Segment model selector

`renderSegmentControls` in `packages/editor-core/static/segments.js` uses the shared field/select styling and `MODEL_PRESETS` from `domain/workflow.ts`. The saved `lora_id` selects Viggle Remake Swap or Pika + Character Swap V1.5 for this segment only. Both use MiniMax H3 FL2VA. Switching changes the render signature; retained takes are never deleted.

Character picker search and New character share a wrapping toolbar; Manage project
characters uses the standard button. Both creation entry points open the Assets
naming dialog. `ProjectAssets.tsx` reuses the circular activity spinner for reference
uploads and keeps text editable. `character-drafts.ts` retains each keystroke by
account/project/character; serialized server writes flush newer text after the image
receipt, preserving revision checks.

Character detail headers group Back to characters with the prominent name and saved
status in one compact header. The usage region keeps its heading/count visible and
scrolls after three fixed-height shot rows; long names truncate with full title text.
