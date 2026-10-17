"""App inputs and retained outputs for the pinned upstream Relay recipe."""
from pathlib import Path
from runtime.relay_dependency import pure_module

IDS = dict(model='1', shift='2', attention='3', turbo='4', text='6', video_vae='7',
           audio_vae='8', reference='9', source='30', prepare='31', text_device='58',
           anchor='309', adapter='850', expand_mask='43', plan='805',
           reference_conditioning='801', window='803', finish='807', video='808', save='809')


def recipe(use_anchor=True, use_mask=True):
    graph = pure_module('workflows').character_swap_graph(
        use_anchor=use_anchor, use_mask=use_mask, saved_mask=True)
    def remap(value):
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int:
            return [IDS.get(value[0], value[0]), value[1]]
        if isinstance(value, dict):
            return {k: remap(v) for k, v in value.items()}
        return value
    return {IDS.get(k, k): remap(v) for k, v in graph.items()}


def outputs(history, root, *, continuation=False):
    """Read native file descriptors and Relay's timeline report without globs."""
    if len(history) != 1:
        raise ValueError('Expected exactly one completed prompt')
    result = next(iter(history.values()))['outputs']
    reports = result.get('807', {}).get('h3_relay_report', [])
    videos = result.get('809', {}).get('images', [])
    if len(reports) != 1 or not reports[0].get('complete') or len(videos) != 1:
        raise ValueError('Render is missing its Relay report or native video output')
    root = Path(root).resolve()
    def local(descriptor):
        if descriptor.get('type') != 'output':
            raise ValueError('Render descriptor is not a retained output')
        path = (root / descriptor.get('subfolder', '') / descriptor['filename']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Render output is missing or outside its retained directory')
        return path
    video = local(videos[0])
    master = None
    if continuation:
        masters = result.get('save_history', {}).get('h3_relay_history', [])
        if len(masters) != 1:
            raise ValueError('Render is missing its continuation master')
        master = local(masters[0])
    return reports[0], video, master
