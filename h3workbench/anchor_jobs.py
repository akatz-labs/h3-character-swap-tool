"""Queued Qwen anchors: frozen inputs, retained originals, explicit adoption."""
import copy
import json
import math
import secrets
import shutil
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt
from . import masks, media, remote, remote_shared, segments, store

BASE = 'qwen_image_2.1_int8_convrot.safetensors'
TEXT = 'qwen3vl_8b_int8_convrot.safetensors'
VAE = 'qwen_image_2.1_vae_bf16.safetensors'
LORA = 'qwen21_character_swap_150_r32_2000.safetensors'
MODEL = 'qwen21-character-swap'
MODELS = [{'id': MODEL, 'name': 'Qwen Image 2.1 · Character Swap'}]
LORA_SHA = '0daf779861d4b902128634e7664b0a29585e7c5fe6d39add847145be5f3045c1'


def canvas(dimensions):
    w, h = dimensions
    return [max(32, round(math.sqrt(1024**2 * w / h) / 32) * 32),
            max(32, round(math.sqrt(1024**2 * h / w) / 32) * 32)]


def identity(c, s, options):
    character = next((x for x in s.get('cast_setup', {}).get('characters', [])
                      if x['id'] == options['character_id']), None)
    segment = next((x for x in segments.current(s) if x['id'] == options['segment_id']), None)
    if not character or not character.get('reference') or not segment:
        raise ValueError('Choose a character reference and a current segment')
    tracks = s.get('cast_setup', {}).get('mask_tracks', [])
    track = next((t for t in tracks if t['character_id'] == character['id']), None)
    if not track or not track['state'].get('mask'):
        raise ValueError('Apply a mask for this character before generating an anchor')
    return {'mask': track['state']['mask'], 'protected_masks': [t['state'].get('mask') for t in tracks if t['id'] != track['id']],
            'canvas_aligned': c.get('canvas_aligned', False), 'source': c.get('render_source') or c['source'], 'start': s['start'], 'end': s['end'],
            'dimensions': c['dimensions'], 'reference': character['reference'],
            'character_id': character['id'], 'segment_id': segment['id'],
            'segment_range': [segment['start'], segment['end']]}


def freeze(c, s, options, worker=None):
    if not remote_shared.enabled():
        raise ValueError('Anchor generation needs a shared worker with Qwen Image 2.1 installed')
    if set(options) - {'character_id', 'segment_id', 'frame', 'seed', 'revision', 'model_offloading', 'prompt', 'model'}:
        raise ValueError('Unknown anchor generation option')
    if options.get('revision') != c['revision']:
        raise store.Conflict('Shot changed; choose the frame again')
    frame = options.get('frame')
    if type(frame) is not int or not 0 <= frame < s['end'] - s['start']:
        raise ValueError('Choose a frame inside this shot')
    if not all(options.get(k) for k in ('character_id', 'segment_id')):
        raise ValueError('Choose a character and segment')
    frozen = identity(c, s, options)
    if not frozen['segment_range'][0] <= frame < frozen['segment_range'][1]:
        raise ValueError('Move the playhead into the selected segment')
    if not mask_frame(frozen['mask'], frame, c['dimensions'], s['end'] - s['start']).any():
        raise ValueError('This character has no mask at this frame; choose a visible frame')
    seed = options.get('seed', secrets.randbelow(2**32))
    if type(seed) is not int or not 0 <= seed < 2**53:
        raise ValueError('Seed must be a non-negative integer below 2^53')
    policy = store.setting('qwen_anchor_profiles', {})
    selected = policy.get(worker, {})
    if worker == 'auto' and remote_shared.WORKERS and all(policy.get(w, {}).get('profile') == 'full-offload' for w in remote_shared.WORKERS):
        selected = {'profile': 'full-offload', 'evidence': {w: policy[w].get('evidence') for w in remote_shared.WORKERS}}
    offload = options.get('model_offloading', selected.get('profile') == 'full-offload')
    if type(offload) is not bool: raise ValueError('Model offloading must be true or false')
    model = options.get('model', MODEL)
    if model != MODEL: raise ValueError('Choose an available anchor model')
    prompt = options.get('prompt', default_prompt(c, s, options))
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise ValueError('Enter an anchor prompt between 1 and 12000 characters')
    return {**options, 'prompt': prompt, 'model': model, 'seed': seed, 'identity': copy.deepcopy(frozen),
            'model_offloading': offload, 'capacity_evidence': selected.get('evidence')}


def default_prompt(c, s, options):
    character = next(x for x in s['cast_setup']['characters'] if x['id'] == options['character_id'])
    track = next(x for x in s['cast_setup']['mask_tracks'] if x['character_id'] == character['id'])
    target = mask_frame(track['state']['mask'], options['frame'], c['dimensions'], s['end'] - s['start'])
    ys, xs = np.where(target)
    subject = f"person centered at {100 * xs.mean() / target.shape[1]:.0f}% from the left and {100 * ys.mean() / target.shape[0]:.0f}% from the top"
    return (f"Replace only the {subject} in image 1 with {character['name']} from image 2. "
            "Match the identity, clothing and visual medium of the character reference in image 2. "
            "If the reference is a photograph, keep the replacement photographic with natural skin and fabric textures. "
            "Preserve the exact pose, expression, gaze, mouth opening, limb placement, scale, camera framing, "
            "background, lighting and every other person in image 1. "
            "Do not copy the reference pose or background. Return one edited image with the same composition as image 1.")


def graph(prompt, seed):
    def node(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    return {
        '1': node('UNETLoader', unet_name=BASE, weight_dtype='default'),
        '2': node('CLIPLoader', clip_name=TEXT, type='qwen_image', device='default'),
        '3': node('VAELoader', vae_name=VAE),
        '4': node('LoraLoaderModelOnly', model=['1', 0], lora_name=LORA, strength_model=1.0),
        '5': node('LoadImage', image='source.png'),
        '6': node('LoadImage', image='reference.png'),
        '7': node('TextEncodeQwenImage21', clip=['2', 0], vae=['3', 0], prompt=prompt,
                  negative_prompt='', resolution=1024, **{'images.image_1': ['5', 0], 'images.image_2': ['6', 0]}),
        '8': node('KSampler', model=['4', 0], positive=['7', 0], negative=['7', 1], latent_image=['7', 2],
                  seed=seed, steps=28, cfg=1.0, sampler_name='euler', scheduler='simple', denoise=1.0),
        '9': node('VAEDecode', samples=['8', 0], vae=['3', 0]),
        '10': node('SaveImage', images=['9', 0], filename_prefix='qwen-anchor'),
    }


def fit(source, edited, target=None, protected=None):
    """Normalize without cropping; accept only a small static-background transform."""
    h, w = source.shape[:2]
    resized = cv2.resize(edited, (w, h), interpolation=cv2.INTER_LANCZOS4)
    info = {'original_size': [edited.shape[1], edited.shape[0]], 'source_size': [w, h],
            'method': 'canvas normalization + bounded background similarity', 'accepted': False, 'matches': 0}
    matrix = np.eye(2, 3, dtype=np.float64)
    if target is None or not target.any():
        info['reason'] = 'No applied target mask; review framing before use'
        return resized, info
    protected = np.zeros((h, w), bool) if protected is None else protected
    excluded = cv2.dilate((target | protected).astype(np.uint8), np.ones((41, 41), np.uint8))
    mask = ((excluded == 0) * 255).astype(np.uint8)
    sift = cv2.SIFT_create(nfeatures=6000)
    ka, da = sift.detectAndCompute(cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY), mask)
    ks, ds = sift.detectAndCompute(cv2.cvtColor(source, cv2.COLOR_RGB2GRAY), mask)
    if da is not None and ds is not None and len(da) >= 8 and len(ds) >= 8:
        matches = [pair[0] for pair in cv2.BFMatcher().knnMatch(da, ds, k=2)
                   if len(pair) == 2 and pair[0].distance < .7 * pair[1].distance]
        info['matches'] = len(matches)
        if len(matches) >= 8:
            pa = np.float32([ka[m.queryIdx].pt for m in matches]); ps = np.float32([ks[m.trainIdx].pt for m in matches])
            fitted, inliers = cv2.estimateAffinePartial2D(pa, ps, method=cv2.RANSAC,
                ransacReprojThreshold=2, maxIters=5000, confidence=.999, refineIters=20)
            if fitted is not None and inliers is not None and inliers.sum() >= 8:
                keep = inliers.ravel().astype(bool)
                scale = math.hypot(fitted[0, 0], fitted[1, 0]); rotation = math.degrees(math.atan2(fitted[1, 0], fitted[0, 0]))
                before = float(np.median(np.linalg.norm(pa[keep] - ps[keep], axis=1)))
                after = float(np.median(np.linalg.norm(pa[keep] @ fitted[:, :2].T + fitted[:, 2] - ps[keep], axis=1)))
                info.update(inliers=int(keep.sum()), scale=scale, rotation_degrees=rotation,
                            translation=fitted[:, 2].tolist(), error_before=before, error_after=after)
                if .96 <= scale <= 1.04 and abs(rotation) <= 2 and abs(fitted[0, 2]) < w * .05 and abs(fitted[1, 2]) < h * .05 and after <= before + .1:
                    matrix = fitted; info['accepted'] = True
    info['matrix'] = matrix.tolist()
    if not info['accepted']: info['reason'] = 'Background alignment unverified; review framing before use'
    return cv2.warpAffine(resized, matrix, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE), info


def mask_frame(asset, frame, dimensions, frames):
    with np.load(store.path(asset), allow_pickle=False) as data:
        mask = data['mask']
        if mask.ndim != 3 or len(mask) != frames: raise ValueError('Saved mask does not match the shot')
        value = mask[frame] > .5
    if value.shape != (dimensions[1], dimensions[0]):
        value = np.asarray(Image.fromarray(value).resize(tuple(dimensions), Image.Resampling.NEAREST))
    return value


def finish(source, edited, original, replacement, protected=None):
    protected = np.zeros_like(original) if protected is None else protected
    overlap = (original & replacement).sum() / max(1, original.sum())
    if not replacement.any() or overlap < .2 or replacement.sum() > min(original.sum() * 3, original.size * .9):
        raise ValueError('Replacement mask could not isolate the character; raw image is retained for manual editing')
    band = max(1, round(min(original.shape) * 8 / 736))
    region = cv2.dilate((original | replacement).astype(np.uint8), np.ones((band * 2 + 1, band * 2 + 1), np.uint8)).astype(bool) & ~protected
    feather = max(1, round(min(original.shape) * 2 / 736))
    alpha = np.minimum(distance_transform_edt(region) / feather, 1).astype(np.float32)
    final = np.rint(edited * alpha[..., None] + source * (1 - alpha[..., None])).clip(0, 255).astype(np.uint8)
    assert np.array_equal(final[~region], source[~region])
    assert np.array_equal(final[protected], source[protected])
    return final, {'applied': True, 'outward_band_px': band, 'inward_feather_px': feather,
                   'target_overlap': float(overlap), 'edit_pixels': int(region.sum()),
                   'outside_region_equal': True, 'protected_equal': True}, region, alpha


def foreground(target):
    """A source-derived box lets SAM follow the replacement's full silhouette."""
    ys, xs = np.where(target)
    margin = max(8, round(min(target.shape) * .035))
    x, y = max(0, int(xs.min()) - margin), max(0, int(ys.min()) - margin)
    right, bottom = min(target.shape[1], int(xs.max()) + margin + 1), min(target.shape[0], int(ys.max()) + margin + 1)
    box = {'x': x, 'y': y, 'width': right-x, 'height': bottom-y}
    def node(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    return {
        '1': node('LoadImage', image='registered.png'),
        '2': node('CheckpointLoaderSimple', ckpt_name='sam3.1_multiplex_fp16.safetensors'),
        '3': node('SAM3_Detect', model=['2', 0], image=['1', 0], bboxes=box,
                  threshold=.5, refine_iterations=2, individual_masks=False),
        '4': node('H3RelaySaveMask', masks=['3', 0], filename_prefix='anchor-foreground', frames=1),
    }, box


def execute(job, directory):
    c, s, options = (job['data'][k] for k in ('snapshot', 'shot', 'options'))
    frame = options['frame']; frozen = options['identity']
    source = media.shot_frame(c, s, frame, directory / 'source.png')
    with Image.open(store.path(frozen['reference'])) as im: im.convert('RGB').save(directory / 'reference.png')
    character = next(x for x in s['cast_setup']['characters'] if x['id'] == options['character_id'])
    track = next((x for x in s['cast_setup']['mask_tracks'] if x['character_id'] == character['id']), None)
    target = protected = None
    if track and track['state'].get('mask'):
        target = mask_frame(track['state']['mask'], frame, c['dimensions'], s['end'] - s['start'])
        protected = np.zeros_like(target)
        for other in s['cast_setup']['mask_tracks']:
            if other['id'] != track['id'] and other['state'].get('mask'):
                protected |= mask_frame(other['state']['mask'], frame, c['dimensions'], s['end'] - s['start'])
    prompt = options['prompt'] if 'prompt' in options else default_prompt(c, s, options)
    api = graph(prompt, options['seed'])
    output = remote_shared.execute(job, api, {'source.png': directory / 'source.png', 'reference.png': directory / 'reference.png'}, directory / 'generation', profile='full-offload' if options['model_offloading'] else 'full')
    images = [p for p in output.rglob('*.png') if p.name.startswith('qwen-anchor')]
    if len(images) != 1: raise RuntimeError('Qwen did not return exactly one anchor image')
    remote.check_cancel(job)
    shutil.copy2(images[0], directory / 'raw.png')
    store.job_update(job['id'], result={'asset': store.asset_add(directory / 'raw.png')})
    src = np.asarray(Image.open(directory / 'source.png').convert('RGB'))
    raw = np.asarray(Image.open(directory / 'raw.png').convert('RGB'))
    normalized = cv2.resize(raw, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_LANCZOS4)
    Image.fromarray(normalized).save(directory / 'normalized.png')
    fitted, alignment = fit(src, raw, target, protected)
    Image.fromarray(fitted).save(directory / 'registered.png')
    composite = {'applied': False, 'reason': 'No applied target mask; background preservation needs review'}
    if target is not None and target.any():
        store.job_update(job['id'], phase='Fitting anchor to the source frame')
        foreground_graph, box = foreground(target)
        foreground_output = remote_shared.execute(job, foreground_graph,
            {'registered.png': directory / 'registered.png'}, directory / 'foreground')
        outputs = list(foreground_output.rglob('anchor-foreground.npz'))
        if len(outputs) != 1: raise RuntimeError('Anchor foreground mask was not returned')
        with np.load(outputs[0], allow_pickle=False) as data: replacement = data['mask'][0] > .5
        if replacement.shape != target.shape:
            replacement = np.asarray(Image.fromarray(replacement).resize((target.shape[1], target.shape[0]), Image.Resampling.NEAREST))
        fitted, composite, region, alpha = finish(src, fitted, target, replacement, protected)
        composite['foreground_box'] = box
        np.savez_compressed(directory / 'masks.npz', target=target, replacement=replacement,
                            protected=protected, edit_region=region, alpha=alpha)
        Image.fromarray((region * 255).astype(np.uint8)).save(directory / 'edit-region.png')
    Image.fromarray(fitted).save(directory / 'anchor.png')
    record = {'generator': 'Qwen Image 2.1 + character swap r32 step 2000', 'operation': 'qwen-anchor-v1',
              'job_id': job['id'], 'model': options.get('model', MODEL), 'seed': options['seed'], 'steps': 28, 'guidance': 1, 'lora_strength': 1,
              'lora_sha256': LORA_SHA, 'model_offloading': options['model_offloading'], 'capacity_evidence': options.get('capacity_evidence'), 'prompt': prompt, 'identity': frozen, 'source_frame': s['start'] + frame,
              'source_asset': source, 'reference_sha256': store.asset(frozen['reference'])['sha256'], 'alignment': alignment, 'composite': composite}
    (directory / 'provenance.json').write_text(json.dumps(record, indent=2))
    candidate = {'asset': store.asset_add(directory / 'anchor.png'), 'raw': store.asset_add(directory / 'raw.png'),
                 'source': source, 'provenance': store.asset_add(directory / 'provenance.json'), 'frame': frame,
                 'character_id': character['id'], 'character_name': character['name'], 'segment_id': options['segment_id'],
                 'seed': options['seed'], 'alignment': alignment, 'composite': composite}
    return {'asset': candidate['asset'], 'anchor_candidate': candidate}


def adopt(c, s, job, enabled=True, segment_id=None):
    if type(enabled) is not bool: raise ValueError('Use anchor must be true or false')
    options = job['data']['options']
    if job['id'] in s.get('dismissed_anchor_jobs', []):
        raise store.Conflict('This anchor was deleted')
    existing = next((a for a in s['cast_setup']['scene_anchors'] if a.get('provenance', {}).get('job_id') == job['id']), None)
    if existing:
        toggle(s, existing['id'], segment_id or options['segment_id'], enabled)
        return
    if not enabled: return
    if identity(c, s, options) != options['identity']:
        raise store.Conflict('Source, segment or character changed; generate a new anchor')
    candidate = job['data']['result']['anchor_candidate']
    with Image.open(store.path(candidate['asset'])) as image:
        if image.size != tuple(c['dimensions']):
            raise store.Conflict('This candidate does not match the current canvas; generate a new anchor')
    library = s['cast_setup']['scene_anchors']
    entry = {'id': store.uid('anchor-'), 'name': f'{candidate["character_name"]} · frame {candidate["frame"]}',
             'asset': candidate['asset'], 'source_frame': candidate['frame'],
             'provenance': {'generator': 'Qwen Image 2.1', 'job_id': job['id'], 'created': store.now(),
                            'source_asset': candidate['source'], 'raw_asset': candidate['raw'],
                            'record': candidate['provenance'], 'seed': candidate['seed']}}
    library.append(entry)
    toggle(s, entry['id'], segment_id or options['segment_id'], True)


def toggle(s, anchor_id, segment_id, enabled):
    if type(enabled) is not bool: raise ValueError('Use anchor must be true or false')
    if not any(a['id'] == anchor_id for a in s['cast_setup']['scene_anchors']):
        raise ValueError('Anchor no longer exists')
    segment = next((v for v in s['segments'] if v['id'] == segment_id), None)
    if segment is None: raise ValueError('Choose a current segment')
    chosen = segment['cast_inputs']['scene_anchors']
    if enabled and anchor_id not in chosen: chosen.append(anchor_id)
    elif not enabled and anchor_id in chosen: chosen.remove(anchor_id)


def remove(s, anchor_id=None, job_id=None):
    library = s['cast_setup']['scene_anchors']
    entry = next((a for a in library if a['id'] == anchor_id or (job_id and a.get('provenance', {}).get('job_id') == job_id)), None)
    if entry:
        library.remove(entry)
        for segment in s['segments']:
            chosen = segment['cast_inputs']['scene_anchors']
            if entry['id'] in chosen: chosen.remove(entry['id'])
        for capture in s.get('anchor_captures', []):
            if capture.get('anchor_id') == entry['id']: capture.pop('anchor_id')
        job_id = job_id or entry.get('provenance', {}).get('job_id')
    elif anchor_id: raise ValueError('Anchor no longer exists')
    if job_id and job_id not in s.setdefault('dismissed_anchor_jobs', []):
        s['dismissed_anchor_jobs'].append(job_id)
