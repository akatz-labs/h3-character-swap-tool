"""Visible, editable guide-image helpers and the exact image-edit prompt."""
import copy

LEGACY_CHARACTER_SWAP = (
        'Replace only the target person identified in the edit instructions in Image 1. '
        'Use the selected reference images for the replacement character’s identity, face, hair, '
        'clothing and visual style. When multiple people or references are present, follow the '
        'instructions’ identity-to-person mapping; do not merge identities or replace other people. '
        'Match the source actor’s exact body pose, body placement, head position and angle, '
        'facial expression, gaze, mouth shape and hand gesture. Preserve the performance from '
        'Image 1 rather than copying a reference image’s pose or expression. Adapt the character '
        'to the scene’s lighting, perspective, scale, shadows and occlusions while retaining '
        'their intended visual style. Preserve the original camera, crop, background, props and '
        'untargeted people. Do not add an extra person or redesign the scene. '
        'The scene-specific edit instructions take priority over these defaults.')

PRESETS = [
    {'id': 'character-swap', 'label': 'Character swap', 'text': (
        'Edit Image 1 in place as a camera-locked character swap. Image 1 is the sole authority '
        'for the camera, composition, scene geometry and performance. Images 2 onward are the '
        'selected appearance references, in their displayed input order. There may be multiple '
        'references for one character or references for several different characters. Follow '
        'the edit instructions to map each replacement character to the intended person in '
        'Image 1, and to choose which reference details to use. Do not merge different identities '
        'or replace untargeted people. Use the designated references for identity, face, hair, '
        'clothing, accessories and visual style as requested. If a reference is another scene '
        'or an earlier edited frame, use only its requested appearance details; do not copy its '
        'camera, framing, background, body placement, pose or expression. If no references are '
        'provided, follow the character description in the edit instructions. '
        'Preserve the complete source canvas, camera position and angle, field of view, '
        'perspective, crop and subject scale. Do not pan, tilt, zoom, reframe, recenter, extend '
        'the sky or reveal additional scenery. Keep the horizon, background objects and '
        'untargeted people at the same pixel locations as Image 1. Keep each target actor’s '
        'on-screen placement, head center and angle, shoulder line, hands, elbows, knees and '
        'feet matched to the source. Preserve the exact body pose, facial expression, gaze, '
        'mouth shape, hand gesture, object contact and occlusions; do not substitute a reference '
        'pose or invent another moment in the action. Adapt the requested character appearance '
        'to the source lighting, perspective and shadows while preserving its intended style. '
        'Change only the requested people and appearance details. Do not redesign the scene, '
        'add extra people, text, borders or panels. Scene-specific edit instructions take '
        'priority for the requested changes; keep the source camera and all other content '
        'fixed unless those instructions explicitly request changing them.')},
    {'id': 'motion-transfer', 'label': 'Motion transfer', 'text': (
        'Create one guide frame that preserves the performance shown in Image 1. '
        'Use Image 1 as the source of the exact pose, body silhouette and placement, head '
        'orientation, expression, gaze, mouth shape and hand configuration. Use the optional '
        'references for the requested character’s appearance and visual style, not for a new '
        'pose or action. Preserve contact with objects, occlusions, camera framing and all '
        'untargeted scene content. Do not invent an in-between pose or a different moment in '
        'the action. This is a single edited frame, not an animation or a contact sheet. '
        'The scene-specific edit instructions take priority over these defaults.')},
    {'id': 'scene-edit', 'label': 'Scene edit', 'text': (
        'Apply only the scene changes described in the edit instructions to Image 1. '
        'Use the optional references for the requested objects, materials, palette or style. '
        'Keep the source camera, crop, perspective and composition. Preserve everything outside '
        'the requested change, including untargeted people and their poses and expressions. '
        'Integrate edited content with the scene’s lighting, shadows, reflections and occlusions. '
        'Do not add unrelated details, text, borders or extra panels. '
        'The scene-specific edit instructions take priority over these defaults.')},
]


def presets():
    return copy.deepcopy(PRESETS)


def migrate_store():
    """Upgrade the old stock draft once; saved versions and custom helpers stay exact."""
    from . import store
    with store.LOCK:
        if store.setting('guide_camera_lock_prompt_schema', 0) >= 1:
            return
        def legacy(helper):
            return helper['id'] == 'character-swap' and helper['text'] == LEGACY_CHARACTER_SWAP
        for clip in store.clips():
            if not any(legacy(helper) for shot in clip['shots'] for guide in shot.get('guides', [])
                       for helper in guide.get('helpers', [])):
                continue
            def upgrade(c):
                for shot in c['shots']:
                    for guide in shot.get('guides', []):
                        for helper in guide.get('helpers', []):
                            if legacy(helper):
                                helper['text'] = PRESETS[0]['text']
            store.clip_mutate(clip['id'], upgrade, clip['revision'])
        store.set_setting('guide_camera_lock_prompt_schema', 1)


def validate_helpers(helpers):
    if not isinstance(helpers, list) or len(helpers) > len(PRESETS):
        raise ValueError('Choose up to three prompt helpers')
    available = {v['id'] for v in PRESETS}
    seen = set()
    for helper in helpers:
        if not isinstance(helper, dict) or set(helper) != {'id', 'text'}:
            raise ValueError('A prompt helper needs its preset and editable text')
        key, text = helper['id'], helper['text']
        if not isinstance(key, str) or key not in available or key in seen:
            raise ValueError('Choose each available prompt helper at most once')
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise ValueError('Helper text must contain 1–6,000 characters')
        seen.add(key)


def compose(guide):
    labels = {v['id']: v['label'] for v in PRESETS}
    if not guide.get('helpers'):
        return guide['prompt']
    parts = [guide['prompt']] if guide['prompt'] else []
    parts.extend(labels[h['id']] + ':\n' + h['text'] for h in guide.get('helpers', []))
    return '\n\n'.join(parts)
