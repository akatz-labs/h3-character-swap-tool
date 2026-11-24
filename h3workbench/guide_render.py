"""FL2VA video editing with Pika and a recipe-specific edit LoRA."""
import copy
import json
import math
import numpy as np
from PIL import Image
from pathlib import Path
from . import cast, guides, media, pipeline, remote_shared, render_models, segments, segment_jobs, store
from .workflow import PRESET
from . import remake_render
PIKA_PRESET = json.loads((Path(__file__).resolve().parents[1] / "packages/editor-core/recipes/pika-swap-relay-v2.json").read_text())


def validate(clip, shot, segment):
    if not remote_shared.enabled():
        raise ValueError('Guided edit requires a managed render worker. Set up the shared executor first.')
    if shot['mode'] != 'generate':
        raise ValueError('Set this shot to Generate before rendering')
    if not 5 <= segment['end'] - segment['start'] <= 362:
        raise ValueError('Guided edit supports 5–362 frames per segment. Split this segment first.')
    swap = segment['render_mode'] == 'swap_pika'
    selected = guides.selected(clip, shot, segment, strict=True, required=not swap)
    if swap:
        references = cast.reference_plan(clip, shot, segment)
        if len(references) > 9:
            raise ValueError('H3 accepts at most 9 character reference images')
        cast.validate_masks(clip, shot, segment)


def graph(names, width, height, length, selected, start, seed, prompt, prefix, *, offload=False, mode='guides', references=(), source_audio=False, mask_file=None, source_frames=None, history_file=None, history_frames=0):
    if mode not in {'guides', 'swap_pika'}:
        raise ValueError('Choose a supported FL2VA editing recipe')
    edit_node = 'swap' if mode == 'swap_pika' else 'ripple'
    edit_lora = render_models.LORAS['v2-1000']['filename'] if mode == 'swap_pika' else 'minimax_H3_Ripple-v1.safetensors'
    def n(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    result = {
        'model': n('UNETLoader', unet_name=PIKA_PRESET['model'], weight_dtype='default'),
        'pika': n('LoraLoaderModelOnly', model=['model', 0], lora_name=PIKA_PRESET['pika'], strength_model=PIKA_PRESET['pika_strength']),
        edit_node: n('LoraLoaderModelOnly', model=['pika', 0], lora_name=edit_lora, strength_model=PIKA_PRESET['swap_strength'] if mode == 'swap_pika' else 1.),
        'clip': n('CLIPLoader', clip_name='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors', type='minimax', device='default'),
        'vae': n('VAELoader', vae_name='minimax_h3_video_vae_fp16.safetensors'),
        'text': n('CLIPTextEncode', clip=['clip', 0], text=prompt),
        'latent': n('EmptyMiniMaxH3LatentAV', width=width, height=height, length=length),
        'noise': n('RandomNoise', noise_seed=seed),
        'sampler': n('KSamplerSelect', sampler_name=PIKA_PRESET['sampler']),
        'schedule': n('ManualSigmas', sigmas=PIKA_PRESET['sigmas']),
        'source': n('LoadVideo', file=names[0]),
        'frames': n('GetVideoComponents', video=['source', 0]),
    }
    positive = ['text', 0]
    latent = ['latent', 0]
    if mode == 'guides':
        result['source_guide'] = n('MiniMaxH3AddGuide', positive=positive, latent=['latent', 0],
                                   vae=['vae', 0], image=['frames', 0], frame_idx=0)
        positive = ['source_guide', 0]
    else:
        result['text'] = n('MiniMaxH3ReferenceToVideo', clip=['clip', 0], vae=['vae', 0],
                           prompt=prompt, width=width, height=height, length=length,
                           ref_image_size='match', **{'ref_videos.ref_video_0': ['frames', 0]})
        for i, name in enumerate(references):
            result[f'reference_{i}'] = n('LoadImage', image=name)
            result['text']['inputs'][f'ref_images.ref_image_{i}'] = [f'reference_{i}', 0]
        if source_audio:
            result['audio_vae'] = n('VAELoader', vae_name='minimax_h3_audio_vae_fp32.safetensors')
            result['text']['inputs'].update(audio_vae=['audio_vae', 0],
                                           **{'ref_video_audios.ref_video_audio_0': ['frames', 1]})
    if history_frames:
        if history_frames not in (1, 18) or not history_file:
            raise ValueError('Continuation requires its frozen generated history')
        result['audio_vae'] = n('VAELoader', vae_name='minimax_h3_audio_vae_fp32.safetensors')
        result['load_history'] = n('H3RelayLoadHistory', history_file=history_file)
        result['history'] = n('H3RelayApplyHistory', positive=positive, latent=latent,
                              vae=['vae', 0], audio_vae=['audio_vae', 0], frames=['load_history', 0],
                              history_frames=history_frames, first_frame_guide=True, target_audio_guide=False)
        positive, latent = ['history', 0], ['history', 1]
    if mask_file is not None:
        if mode != 'swap_pika' or source_frames is None or not 5 <= source_frames <= length:
            raise ValueError('A Pika edit mask requires the original segment frame count')
        # The saved union is already expanded and sliced to this segment. The
        # loader repeats its last frame to match the padded 17n+5 source video.
        mask_frames = ['frames', 0]
        if history_frames:
            result['mask_source'] = n('LoadVideo', file='target-source.mkv')
            result['mask_frames'] = n('GetVideoComponents', video=['mask_source', 0])
            mask_frames = ['mask_frames', 0]
        result['mask'] = n('H3RelayLoadMask', mask_file=mask_file, images=mask_frames,
                           source_frames=source_frames + bool(history_frames), expansion_px=0)
        result['encode_source'] = n('VAEEncode', pixels=mask_frames, vae=['vae', 0])
        result['edit_mask'] = n('H3RelayApplyEditMask', latent=latent,
                                source_latent=['encode_source', 0], mask=['mask', 0])
        latent = ['edit_mask', 0]
    for i, guide in enumerate(selected):
        result[f'image_{i}'] = n('LoadImage', image=names[i + 1])
        result[f'guide_{i}'] = n('MiniMaxH3AddGuide', positive=positive, latent=latent, vae=['vae', 0],
                                image=[f'image_{i}', 0], frame_idx=guide['frame'] - start + bool(history_frames))
        positive = [f'guide_{i}', 0]
    result.update({
        'guider': n('BasicGuider', model=[edit_node, 0], conditioning=positive),
        'sample': n('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0], sampler=['sampler', 0], sigmas=['schedule', 0], latent_image=latent),
        'separate': n('LTXVSeparateAVLatent', av_latent=['sample', 0]),
        'decode': n('VAEDecode', samples=['separate', 0], vae=['vae', 0]),
        'video': n('CreateVideo', images=['decode', 0], fps=24.),
        'save': n('SaveVideo', video=['video', 0], filename_prefix=prefix, format='mp4', **{'format.codec': 'h264', 'format.codec.encoding': 'auto'})})
    if offload:
        result['text_memory'] = n('H3RelayTextMemory', clip=['clip', 0])
        result['text']['inputs']['clip'] = ['text_memory', 0]
        result['sampling_memory'] = n('H3RelaySamplingMemory', model=[edit_node, 0], latent=latent,
                                      conditioning=positive, activation_memory_gib=17.)
        result['guider']['inputs']['model'] = ['sampling_memory', 0]
    return result


def execute(job, directory, *, attach=True):
    clip, shot = job['data']['snapshot'], job['data']['shot']
    segment = next(s for s in segments.current(shot) if s['id'] == job['data']['options']['segment_id'])
    validate(clip, shot, segment)
    mode = segment['render_mode']
    remake = segment.get('lora_id') == remake_render.PRESET['lora_id']
    chosen = guides.selected(clip, shot, segment, strict=True, required=mode == 'guides')
    spec = media.render_spec(clip, shot)
    width, height = spec['dimensions']
    items=segments.current(shot); index=next(i for i,v in enumerate(items) if v['id']==segment['id'])
    h=segments.effective_history(segment)
    virtual = {**shot, 'start': shot['start'] + segment['start'] - h, 'end': shot['start'] + segment['end']}
    source = media.shot_media(clip, virtual, directory, dimensions=spec['dimensions'])
    count = segment['end'] - segment['start']
    length = 5 + 17 * math.ceil((count + h - 5) / 17)
    # AddGuide crops a clip down to 17n+5. Pad its input too, so a segment
    # between grid points retains motion guidance through its final real frame.
    guide_source = source
    if length > count + h:
        guide_source = directory / 'guide-source.mkv'
        media.ff(['-i', source, '-map', '0:v:0', '-map', '0:a?', '-vf', f'tpad=stop_mode=clone:stop={length - count - h}',
                  '-frames:v', str(length), '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le', '-c:a', 'copy', guide_source])
    # Pika uses the source as Video 1, never as a dense frame-zero guide.
    inputs = [guide_source]
    for i, guide in enumerate(chosen):
        path = directory / f'guide-{i}.png'
        with Image.open(store.path(guide['asset'])) as image:
            image.convert('RGB').resize((width, height), Image.Resampling.LANCZOS).save(path)
        inputs.append(path)
    prompt = segments.prompt(clip, shot, segment)
    names = ['source.mkv'] + [f'guide-{i}.png' for i in range(len(chosen))]
    references = cast.reference_plan(clip, shot, segment) if mode == 'swap_pika' else []
    reference_names = [f'character-{i}.png' for i in range(len(references))]
    files = dict(zip(names, inputs)) | {name: store.path(ref['asset']) for name, ref in zip(reference_names, references)}
    use_mask = mode == 'swap_pika' and segments.render_shot(shot, segment).get('use_mask', True)
    use_mask = use_mask and bool(cast.resolve(clip, shot, segment)['mask_tracks'])
    mask_file = None
    if use_mask:
        mask_file = 'segment-mask.npz'
        mask_path = directory / mask_file
        np.savez_compressed(mask_path, mask=cast.combined_mask(clip, shot, segment, spec['dimensions']))
        files[mask_file] = mask_path
    history_name=None
    if h:
        history_name='continuation-history.npz'
        files[history_name]=segment_jobs.history_file(items,index,spec['dimensions'],directory)
        if mask_file and not remake:
            raw=np.load(files[mask_file])['mask'][h:]
            # Only the regenerated boundary precedes this segment's mask.
            np.savez_compressed(files[mask_file],mask=np.concatenate([np.ones_like(raw[:1]),raw]))
            target=directory/'target-source.mkv'
            media.ff(['-i',guide_source,'-an','-vf',f'trim=start_frame={h-1},setpts=PTS-STARTPTS','-c:v','ffv1',target])
            files['target-source.mkv']=target
    source_audio = mode == 'swap_pika' and segments.render_shot(shot, segment).get('use_source_audio', True)
    offload = job['data']['options'].get('model_offloading', False)
    build_graph = remake_render.graph if remake else graph
    captured = build_graph(names, width, height, length, chosen, virtual['start'] + h, segment['seed'], prompt, 'h3-guides/' + job['id'], offload=offload, **({} if remake else {'mode': mode}), references=reference_names, source_audio=source_audio, mask_file=mask_file, source_frames=count, history_file=history_name, history_frames=h)
    runtime = remote_shared.execute(job, captured, files, directory,
                                    profile='full-offload' if offload else 'full')
    history = json.loads((runtime / 'history.json').read_text())
    descriptors = next(iter(history.values()))['outputs'].get('save', {}).get('images', []) if len(history) == 1 else []
    if len(descriptors) != 1 or descriptors[0].get('type') != 'output':
        raise ValueError('Guided render did not return exactly one retained video')
    descriptor = descriptors[0]
    root = (runtime / 'output').resolve()
    output = (root / descriptor.get('subfolder', '') / descriptor['filename']).resolve()
    if not output.is_relative_to(root) or not output.is_file():
        raise ValueError('Guided video is missing from the retained worker output')
    pipeline.check_finalization_cancel(job)
    store.job_update(job['id'], finalizing=True, execution_resource='cpu', phase='Saving guided segment')
    master, video = directory / 'master.mkv', directory / 'segment.mp4'
    # Exact source audio is remuxed; generated audio is deliberately not used.
    trim = h if remake else int(bool(h))
    media.ff(['-i', output, '-i', source, '-map', '0:v:0', '-map', '1:a?', '-vf',f'trim=start_frame={trim}:end_frame={count+trim},setpts=PTS-STARTPTS', '-af',f'atrim=start={h/24},asetpts=PTS-STARTPTS', '-frames:v', str(count),
              '-t', str(count / 24), '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le', '-c:a', 'pcm_s16le', master])
    media.ff(['-i', master, '-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', video])
    info = media.probe(video)
    if info['frames'] != count or [info['width'], info['height']] != [width, height] or abs(info['fps'] - 24) > .001:
        raise ValueError('Guided render does not match the requested segment')
    result = {**spec, 'id': job['id'] + '-' + segment['id'], 'segment_id': segment['id'],
        'start': segment['start'], 'end': segment['end'], 'asset': store.asset_add(video), 'master': store.asset_add(master),
        'api': store.asset_add(directory / 'api.json'), 'created': store.now(), 'signature': guides.signature(clip, shot, segment),
        'dependencies': segments.dependencies(items,index), 'prompt': prompt, 'dialogue': segments.dialogue(clip, shot, segment), 'seed': segment['seed'],
        'settings': {k: copy.deepcopy(segment[k]) for k in segments.EDITABLE if k in segment},
        'render_mode': mode, 'guide_inputs_snapshot': {'source': guides.source(clip), 'shot_start': shot['start'], 'guides': chosen},
        'lora': copy.deepcopy(render_models.LORAS[segment.get('lora_id', 'v2-1000')]) if mode == 'swap_pika' else {'id': 'ripple-v1', 'label': 'H3 Ripple V1'},
        'recipe': copy.deepcopy(remake_render.PRESET) if remake else {'model': captured['model']['inputs']['unet_name'], 'pika_strength': captured['pika']['inputs']['strength_model'], 'edit_strength': captured['swap' if mode == 'swap_pika' else 'ripple']['inputs']['strength_model'], 'steps': len(captured['schedule']['inputs']['sigmas'].split(',')) - 1, 'sampler': captured['sampler']['inputs']['sampler_name'], 'scheduler': 'manual', 'sigmas': captured['schedule']['inputs']['sigmas'], 'source_video_guide': mode == 'guides', 'source_video_reference': mode == 'swap_pika'},
        **({'cast_inputs_snapshot': cast.generation_inputs(clip, shot, segment)} if mode == 'swap_pika' else {}),
        'history_frames': h, 'window_frames': length,
        'model_offloading': offload,
        'use_mask': use_mask, 'edit_mask': store.asset_add(files[mask_file]) if mask_file else None,
        'reference': references[0]['asset'] if references else None, 'anchor': None, 'use_source_audio': source_audio, 'raw_decoded': False, 'continuation_format': 'ffv1-from-mp4', 'worker': job['data']['worker']}
    if not attach:
        return result
    segment_jobs.attach_take(job, result)
    store.job_update(job['id'], segment_results=[result['id']])
    return {'asset': result['asset']}
