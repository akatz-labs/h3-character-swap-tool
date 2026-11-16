"""Optional local H3 Image guides through the same admitted worker as video jobs."""
import math
import re
import secrets
import shutil

from . import remote_shared
from .config import WORKERS

FEATURE = 'h3-image-edit-v1'
MEGAPIXELS = {'low': 1, 'medium': 2, 'high': 4}
MODEL = 'minimax_h3_image_pruned_int8_convrot.safetensors'
VAE = 'minimax_h3_image_vae_fp16.safetensors'
TEXT_ENCODER = 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'
LORA = 'h3-v2-challenge-eval-20261013/v2-1000.safetensors'


def dimensions(source, quality):
    scale = math.sqrt(MEGAPIXELS[quality] * 1_000_000 / (source[0] * source[1]))
    return [max(32, round(side * scale / 32) * 32) for side in source]


def status():
    workers = remote_shared.all_status() if remote_shared.enabled() else {}
    ready_workers = [key for key, value in workers.items() if FEATURE in value.get('features', [])]
    return {'id': 'h3-image', 'label': 'H3 Image Edit', 'available': bool(ready_workers),
            'workers': ready_workers, 'message': '' if ready_workers else
            'Install H3 Image Edit and its optional models in Local settings → Models.'}


def ready(worker):
    if not isinstance(worker, str) or (worker != 'auto' and worker not in WORKERS):
        raise ValueError('Choose a configured local worker')
    current = status()
    if not current['available'] or (worker != 'auto' and worker not in current['workers']):
        raise ValueError('Install H3 Image Edit and its optional models on the selected worker in Local settings → Models.')


def prompt_for_h3(prompt, references):
    # Keep user instructions and helper text intact, adapting only image labels
    # to H3's native reference tokens. The source always occupies Picture 1.
    prompt = re.sub(r'\bImage (\d+)\b', lambda m: '<Picture ' + m[1] + '>', prompt)
    prompt = prompt.replace('Images 2 onward', 'Pictures 2 onward')
    labels = ['<Picture 1> is the source frame to edit.']
    labels.extend(f'<Picture {i}> is reference image {i}.' for i in range(2, references + 2))
    return '\n'.join(labels) + '\n\n' + prompt


def graph(prompt, quality, source_dimensions, names, seed):
    width, height = dimensions(source_dimensions, quality)
    result = {f'image_{i}': {'class_type': 'LoadImage', 'inputs': {'image': name}}
              for i, name in enumerate(names, 1)}
    result.update({
        'model': {'class_type': 'UNETLoader', 'inputs': {'unet_name': MODEL, 'weight_dtype': 'default'}},
        'lora': {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': ['model', 0], 'lora_name': LORA, 'strength_model': 1.0}},
        'clip': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': TEXT_ENCODER, 'type': 'minimax', 'device': 'default'}},
        'vae': {'class_type': 'VAELoader', 'inputs': {'vae_name': VAE}},
        'conditioning': {'class_type': 'MiniMaxH3ImageConditioning', 'inputs': {
            'clip': ['clip', 0], 'vae': ['vae', 0], 'prompt': prompt_for_h3(prompt, len(names) - 1),
            'width': width, 'height': height, **{f'images.image_{i}': [f'image_{i}', 0] for i in range(1, len(names) + 1)}}},
        'negative': {'class_type': 'ConditioningZeroOut', 'inputs': {'conditioning': ['conditioning', 0]}},
        'sample': {'class_type': 'KSampler', 'inputs': {'model': ['lora', 0], 'positive': ['conditioning', 0],
            'negative': ['negative', 0], 'latent_image': ['conditioning', 1], 'seed': seed,
            'steps': 6, 'cfg': 1.0, 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0}},
        'decode': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sample', 0], 'vae': ['vae', 0]}},
        'save': {'class_type': 'SaveImage', 'inputs': {'images': ['decode', 0], 'filename_prefix': 'h3-image-guide'}},
    })
    return result


def run(job, directory, prompt, inputs):
    options = job['data']['options']
    guide = options['guide']
    # Capture the seed once so collection/recovery retains the exact graph.
    seed_path = directory / 'image-seed.txt'
    if not seed_path.exists():
        seed_path.write_text(str(secrets.randbits(63)))
    names = [f'guide-input-{i}{path.suffix}' for i, path in enumerate(inputs)]
    workflow = graph(prompt, guide['quality'], guide['dimensions'], names, int(seed_path.read_text()))
    output = remote_shared.execute(job, workflow, dict(zip(names, inputs)), directory / 'generation',
        profile='full-offload' if options.get('model_offloading', True) else 'full')
    images = list(output.rglob('h3-image-guide*.png'))
    if len(images) != 1:
        raise RuntimeError('H3 Image Edit did not return exactly one guide image')
    destination = directory / 'generated.png'
    shutil.copy2(images[0], destination)
    return destination
