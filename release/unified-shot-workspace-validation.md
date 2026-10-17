# Unified shot workspace validation

2026-10-17: Anchors and Actor mask now share the shot viewer. Actor mask appears
first with a play icon; Anchors follows with an image icon. Generation settings,
segment text and takes remain mounted when changing viewer modes.

The Viewing dropdown selects Source video or Shot anchor. Missing anchors are
disabled; uploading an anchor enables the option without replacing the editor.
Anchor inspection uses the shot's first-frame mask and preserves the source
playhead. Point editing and playback are disabled while inspecting the still;
expansion remains editable through the existing autosave endpoint.

Validation completed against the candidate build and its installed private
frontend, using real source, anchor and mask media:

- Web build and whitespace checks passed.
- Viewer order, icons, keyboard-accessible tabs and legacy Anchors links passed.
- Prompts, generation controls, takes and unsaved correction points survived
  mode switches without replacing their DOM nodes.
- The anchor overlay matched the source frame-zero mask pixel-for-pixel.
  Returning to source frame 17 restored the same source pixels.
- Expansion changes updated the overlay; selected segment and source frame were
  preserved. Missing-anchor and simulated upload responses behaved correctly.
- Both modes fit desktop and 390px mobile layouts without horizontal overflow.
- No browser exceptions or unexpected mutation requests occurred.

Setting writes and uploads in browser regression checks were intercepted with
isolated responses. Existing user settings, masks, takes and render jobs were
not modified. No generation was launched; this UI change does not constitute
a new GPU qualification or a public release.
