"""Pinned Viggle Remake recipe, independent of the retained Pika recipe."""
import json
from pathlib import Path

PRESET = json.loads((Path(__file__).resolve().parents[1] / 'packages/editor-core/recipes/viggle-remake-swap-v1.json').read_text())


def model_prompt(prompt, reference_count):
    """Keep user text verbatim inside the six-section inference presentation."""
    if all(key + ':' in prompt for key in ('subject_definitions', 'summary', 'retention_analysis',
                                           'detailed_description', 'overall_soundscape', 'non_diegetic_music')):
        return prompt
    pictures = ' '.join(f'<Picture {i + 1}> supplies a target character appearance.' for i in range(reference_count))
    return '\n'.join([
        'subject_definitions: <Video 1> supplies the source people, motion and scene. ' + pictures,
        'summary: The source performance with the requested target character appearances.',
        'retention_analysis: Retain source motion, timing, camera, framing and background except where the edit requests a change.',
        'detailed_description: ' + prompt,
        'overall_soundscape: Retain the source soundtrack and speech timing.',
        'non_diegetic_music: Retain any source music.'])


def graph(names, width, height, length, selected, start, seed, prompt, prefix, *,
          offload=False, references=(), source_audio=False, mask_file=None,
          source_frames=None, history_file=None, history_frames=0):
    if history_frames not in (0, 1, 18) or (history_frames and not history_file):
        raise ValueError('Continuation requires its frozen generated history')
    if len(references) > 9 or width % 32 or height % 32 or (length - 5) % 17:
        raise ValueError('Invalid Remake canvas, frame grid or reference count')
    if mask_file and (source_frames is None or not 5 <= source_frames + history_frames <= length):
        raise ValueError('An edit mask requires the unpadded source frame count')
    def n(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    result = {
        'model': n('UNETLoader', unet_name=PRESET['model'], weight_dtype='default'),
        'swap': n('LoraLoaderBypassModelOnly', model=['model', 0], lora_name=PRESET['lora'], strength_model=PRESET['strength']),
        'keyframe_release': n('H3RemakeKeyframeUntil', model=['swap', 0], keyframe_until=PRESET['keyframe_until']),
        'clip': n('CLIPLoader', clip_name='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors', type='minimax', device='default'),
        'vae': n('VAELoader', vae_name='minimax_h3_video_vae_fp16.safetensors'),
        'audio_vae': n('VAELoader', vae_name='minimax_h3_audio_vae_fp32.safetensors'),
        'source': n('LoadVideo', file=names[0]),
        'frames': n('GetVideoComponents', video=['source', 0]),
        'text': n('H3RemakeConditioning', clip=['clip', 0], vae=['vae', 0], audio_vae=['audio_vae', 0],
                  source=['frames', 0], frames=length, prompt=model_prompt(prompt, len(references))),
        'sampler': n('KSamplerSelect', sampler_name=PRESET['sampler']),
        'schedule': n('ManualSigmas', sigmas=', '.join(str(s) for s in PRESET['sigmas'])),
    }
    if source_audio:
        result['text']['inputs']['audio'] = ['frames', 1]
    for i, name in enumerate(references):
        result[f'reference_{i}'] = n('LoadImage', image=name)
        result['text']['inputs'][f'ref_{i + 1}'] = [f'reference_{i}', 0]
    positive, latent = ['text', 0], ['text', 1]
    if mask_file:
        result['mask'] = n('H3RelayLoadMask', mask_file=mask_file, images=['frames', 0],
                           source_frames=source_frames + history_frames, expansion_px=0)
        result['edit_mask'] = n('H3RemakeEditMask', latent=latent, mask=['mask', 0], vae=['vae', 0], source=['frames', 0])
        latent = ['edit_mask', 0]
    if history_frames:
        result['load_history'] = n('H3RelayLoadHistory', history_file=history_file)
        result['history'] = n('H3RemakeHistory', positive=positive, latent=latent, vae=['vae', 0],
                              frames=['load_history', 0], history_frames=history_frames)
        positive, latent = ['history', 0], ['history', 1]
    for i, guide in enumerate(selected):
        at = guide['frame'] - start + history_frames
        if not history_frames <= at < length:
            raise ValueError('Guide frame is outside this segment')
        result[f'image_{i}'] = n('LoadImage', image=names[i + 1])
        result[f'guide_{i}'] = n('MiniMaxH3AddGuide', positive=positive, latent=latent, vae=['vae', 0], image=[f'image_{i}', 0], frame_idx=at)
        positive = [f'guide_{i}', 0]
    model = ['keyframe_release', 0]
    if offload:
        result['text_memory'] = n('H3RelayTextMemory', clip=['clip', 0])
        result['text']['inputs']['clip'] = ['text_memory', 0]
        result['sampling_memory'] = n('H3RelaySamplingMemory', model=model, latent=latent, conditioning=positive, activation_memory_gib=17.)
        model = ['sampling_memory', 0]
    result.update({
        'noise': n('H3RemakeNoise', positive=positive, seed=seed),
        'guider': n('BasicGuider', model=model, conditioning=positive),
        'sample': n('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0], sampler=['sampler', 0], sigmas=['schedule', 0], latent_image=latent),
        'separate': n('LTXVSeparateAVLatent', av_latent=['sample', 0]),
        'decode': n('VAEDecode', samples=['separate', 0], vae=['vae', 0]),
        'video': n('CreateVideo', images=['decode', 0], fps=24.),
        'save': n('SaveVideo', video=['video', 0], filename_prefix=prefix, format='mp4', **{'format.codec': 'h264', 'format.codec.encoding': 'auto'}),
    })
    return result
