"""Run with the pinned ComfyUI Python from its root; no model inference.

python /path/to/repo/tests/comfy_runtime_checks.py --comfy /path/to/ComfyUI \
  --package /path/to/h3_character_swap --source /path/to/26-frame-source.mkv
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--comfy', type=Path, required=True)
parser.add_argument('--package', type=Path, required=True)
parser.add_argument('--source', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.comfy))
sys.argv = [sys.argv[0], '--cpu']
import torch
import nodes
from comfy_api.latest._input_impl.video_types import VideoFromFile
spec = importlib.util.spec_from_file_location('h3_character_swap', args.package / '__init__.py',
    submodule_search_locations=[str(args.package)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from h3_character_swap.nodes import PrepareVideo, OptionalAnchor, Shot, ExpandMask
from h3_character_swap.relay.inputs import WindowInputs
from h3_character_swap.relay import records
from h3_character_swap.planning import plan_windows

video = VideoFromFile(str(args.source))
prepared = PrepareVideo().prepare(video, '768')
frames, audio, width, height, length, count, fps, summary, _ = prepared
probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames',
    '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(args.source)]))
assert count == int(probe['streams'][0]['nb_read_frames']), (count, probe)
assert len(frames) == length and fps == 24
assert OptionalAnchor().load('(none)') == (None, False)
assert OptionalAnchor.VALIDATE_INPUTS('(none)') is True
reference = torch.ones(1, 32, 64, 3)
kw = dict(source_video=video, source=frames, source_audio=audio, source_frames=count,
          fps=fps, reference=reference, anchor=reference, shot_id='qa')
without = Shot().prepare(use_anchor=False, use_mask=False, **kw)[0]
anchored = Shot().prepare(use_anchor=True, use_mask=False, **kw)[0]
assert anchored['anchor'].shape == (1, height, width, 3)
assert anchored['key'] != without['key']
assert without['generation'] == {'use_anchor': False, 'use_mask': False}
assert not without['body_mask'].any()
try:
    Shot().prepare(use_anchor=True, use_mask=False, **dict(kw, anchor=None))
except ValueError as e:
    assert 'anchor image' in str(e)
else:
    raise AssertionError('Missing anchor accepted')

# Exactly the app's Euclidean expansion, including padded tail masks.
raw = torch.zeros(count, height, width)
raw[:, height//2, width//2] = 1
expanded, _ = ExpandMask().expand(raw, frames, count, 2)
assert expanded.shape == frames.shape[:3]
assert int(expanded[0].sum()) == 13
assert torch.equal(expanded[-1], expanded[count-1])
raw[3] = 0
try:
    ExpandMask().expand(raw, frames, count, 2)
except ValueError as e:
    assert 'frames [3]' in str(e)
else:
    raise AssertionError('Empty mask accepted')

# H-1 reference history is separate from current-target audio and masks.
n = 26
source = torch.arange(n).float().view(n, 1, 1, 1).expand(n, 2, 2, 3)
shot = {'source': source, 'body_mask': source[..., 0], 'source_frames': n,
        'source_audio': {'waveform': torch.arange(n).float().view(1, 1, n), 'sample_rate': 24},
        'anchor': source[:1]}
entry = plan_windows(n, 22, 18)[1]
selected = WindowInputs().select(shot, entry, {'body': source[:22]})
assert selected[0][0, 0, 0, 0] == 21 and len(selected[0]) == 22
assert selected[5][0, 0, 0, 0] == 4 and len(selected[5]) == 39
assert selected[2]['waveform'][0, 0, 0] == 21
assert selected[6]['waveform'][0, 0, 0] == 4
assert len(selected[3]) == 18 and selected[3][0, 0, 0, 0] == 4

with tempfile.TemporaryDirectory(prefix='h3-silent-check-') as tmp:
    silent = Path(tmp) / 'silent.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(args.source), '-an', '-c:v', 'copy', str(silent)], check=True)
    silent_result = PrepareVideo().prepare(VideoFromFile(str(silent)), '768')
    assert silent_result[5] == count
    assert silent_result[1]['sample_rate'] == 48000
    assert not silent_result[1]['waveform'].any()
    assert silent_result[1]['waveform'].shape[-1] == round(count / 24 * 48000)

print(json.dumps({'checks': 'passed', 'source_frames': count, 'padded_frames': length,
    'dimensions': [width, height], 'anchor_resize': True, 'silent_input': True,
    'mask_expansion': True, 'history_audio_alignment': True}))
