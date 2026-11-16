"""Supported recipes; historical project records remain readable."""
import copy
import json
from .config import ROOT
from . import segments, store
PRESET = json.loads((ROOT/'packages/editor-core/workflow.json').read_text())

SUPPORTED_LORAS = {PRESET["lora_id"], "v2-1000"}

def is_current(shot):
    return all(seg.get('render_mode') == PRESET['render_mode'] and seg.get('lora_id') in SUPPORTED_LORAS and seg['connection'] == ('continue' if i else 'start') and seg['history_frames'] == (1 if i and seg['start'] < PRESET['history_frames'] else PRESET['history_frames']) for i,seg in enumerate(segments.current(shot)))

def adopt(shot):
    if is_current(shot): return
    saved = segments.current(shot)
    shot.setdefault('segment_layout_history', []).append({'id':store.uid('segments-'),'created':store.now(),
        'reason':'workflow-upgrade','segments':copy.deepcopy(shot.get('segments',saved)),'selected_render':shot.get('selected_render')})
    shot['segments'] = [{**seg, 'render_mode':PRESET['render_mode'], 'lora_id':seg['lora_id'] if seg.get('lora_id') in SUPPORTED_LORAS else PRESET['lora_id'], 'connection':'continue' if i else 'start', 'history_frames':1 if i and seg['start'] < PRESET['history_frames'] else PRESET['history_frames']} for i,seg in enumerate(saved)]

def require(shot):
    if not is_current(shot):
        raise ValueError('Open this shot in the updated editor to apply the current workflow. Existing settings will be retained in layout history.')
