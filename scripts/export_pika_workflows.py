"""Build portable Pika API examples from the editor's active generation recipe.

Canvas files are exported separately through ComfyUI's real frontend. This
builder changes input preparation/export only, not the sampling/model stack.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from h3workbench.guide_render import graph

PROMPT = 'Replace the person in <Video 1> with the character in <Picture 1>. Preserve the source movement, camera and background.'


def standalone(guided=False):
    names = ['source.mp4'] + (['guide.png'] if guided else [])
    nodes = graph(names, 512, 512, 124, [{'frame': 0}] if guided else [], 0,
                  904239, PROMPT, 'h3-pika/character-swap', offload=True,
                  mode='swap_pika', references=['character.png'], source_audio=True)
    nodes['prepare'] = {'class_type': 'H3RelayPrepareSource', 'inputs': {
        'video': ['source', 0], 'resolution': '480', 'duration_seconds': 5.0, 'missing_audio': 'silence'}}
    del nodes['frames']
    nodes['instructions'] = {'class_type': 'PrimitiveStringMultiline', 'inputs': {'value': PROMPT}}
    nodes['text']['inputs'].update(prompt=['instructions', 0], width=['prepare', 2],
                                  height=['prepare', 3], length=['prepare', 4])
    nodes['text']['inputs']['ref_videos.ref_video_0'] = ['prepare', 0]
    nodes['text']['inputs']['ref_video_audios.ref_video_audio_0'] = ['prepare', 1]
    nodes['latent']['inputs'].update(width=['prepare', 2], height=['prepare', 3], length=['prepare', 4])
    if guided:
        nodes['guide_size'] = {'class_type': 'ImageScale', 'inputs': {
            'image': ['image_0', 0], 'upscale_method': 'bicubic',
            'width': ['prepare', 2], 'height': ['prepare', 3], 'crop': 'center'}}
        nodes['guide_0']['inputs']['image'] = ['guide_size', 0]
    nodes['trim'] = {'class_type': 'ImageFromBatch', 'inputs': {
        'image': ['decode', 0], 'batch_index': 0, 'length': ['prepare', 5]}}
    nodes['video']['inputs'].update(images=['trim', 0], audio=['prepare', 1], fps=['prepare', 6])
    nodes['video']['inputs'].update(bit_depth='auto', color_space='sRGB', chroma_subsampling='auto', codec='none')
    nodes['save']['inputs']['codec'] = 'auto'
    nodes['save']['inputs'].update(filename_prefix='h3-pika/guided-swap' if guided else 'h3-pika/reference-swap')
    titles = {'source': 'Source video', 'prepare': 'Prepare source · size / duration',
              'reference_0': 'Character reference · Picture 1', 'instructions': 'Swap instructions',
              'image_0': 'Edited guide frame', 'guide_0': 'Add guide · frame index',
              'pika': 'Pika Flex · strength 1.0', 'swap': 'Character Swap V1.5 · strength 0.8',
              'text': 'H3 reference conditioning · source Video 1',
              'trim': 'Remove model padding', 'save': 'Save result · original soundtrack'}
    # Numeric IDs import consistently in both the API and the canvas frontend.
    ids = {name: str(i) for i, name in enumerate(nodes, 1)}
    return {ids[name]: {**node, '_meta': {'title': titles.get(name, node['class_type'])},
                       'inputs': {k: [ids[v[0]], v[1]] if isinstance(v, list) else v
                                  for k, v in node['inputs'].items()}}
            for name, node in nodes.items()}


if __name__ == '__main__':
    destination = ROOT / 'workflows' / 'pika'
    destination.mkdir(parents=True, exist_ok=True)
    for guided, name in [(False, 'Pika Character Swap'), (True, 'Pika Guided Character Swap')]:
        (destination / (name + '.api.json')).write_text(json.dumps(standalone(guided), indent=2) + '\n')
