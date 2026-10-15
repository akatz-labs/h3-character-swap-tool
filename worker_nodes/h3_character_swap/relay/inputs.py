"""Source-aligned H3 window inputs. No face or pose inference."""
from pathlib import Path
import torch
from . import records
from ..identity import tensor_hash
def pad(tensor, length):
    tensor = tensor[:length]
    if len(tensor) < length:
        tensor = torch.cat([tensor, tensor[-1:].expand(length-len(tensor), *tensor.shape[1:])])
    return tensor


def audio_slice(audio, start, end):
    if audio is None:
        return None
    rate = audio['sample_rate']
    wave = audio['waveform'][..., round(start*rate/24):round(end*rate/24)]
    missing = round((end-start)*rate/24)-wave.shape[-1]
    if missing > 0:
        wave = torch.nn.functional.pad(wave, (0, missing))
    return {'waveform': wave, 'sample_rate': rate}


class Shot:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'source_video': ('VIDEO',), 'source': ('IMAGE',), 'source_audio': ('AUDIO',),
            'source_frames': ('INT',), 'fps': ('FLOAT',), 'reference': ('IMAGE',), 'anchor': ('IMAGE',),
            'body_mask': ('MASK',)}}
    RETURN_TYPES = ('H3_RELAY_SHOT',)
    FUNCTION = 'prepare'
    CATEGORY = 'H3 Relay/performance'
    def prepare(self, **kw):
        n = kw['source_frames']
        if abs(kw['fps']-24) > .001 or not 1 <= n <= len(kw['source']):
            raise ValueError('Provide a prepared 24 fps source with its real frame count')
        for k in ('source', 'body_mask'):
            kw[k] = kw[k][:n].cpu()
        if kw['body_mask'].shape != kw['source'].shape[:3]:
            raise ValueError('Masks must match the complete source timeline')
        native = kw['source_video'].get_stream_source()
        stat = Path(native).stat() if isinstance(native, str) else None
        identity = [str(native) if isinstance(native, str) else type(native).__name__,
                    (stat.st_size, stat.st_mtime_ns) if stat else None,
                    kw['source_video'].get_active_trim_window()]
        kw['key'] = records.digest([identity, n, {k: tensor_hash(kw[k]) for k in
            ('source', 'reference', 'anchor', 'body_mask')},
            tensor_hash(kw['source_audio']['waveform']), kw['source_audio']['sample_rate']])
        return (kw,)


class WindowInputs:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'shot': ('H3_RELAY_SHOT',), 'entry': ('H3_RELAY_ENTRY',), 'state': ('H3_RELAY_STATE',)}}
    RETURN_TYPES = ('IMAGE', 'MASK', 'AUDIO', 'IMAGE', 'AUDIO', 'IMAGE', 'AUDIO')
    RETURN_NAMES = ('source_window', 'body_mask', 'source_audio', 'body_history', 'history_audio', 'source_reference', 'source_audio_reference')
    FUNCTION = 'select'
    CATEGORY = 'H3 Relay/performance'
    def select(self, shot, entry, state):
        start, size, hist = entry['source_start'], entry['window_frames'], entry['history_frames']
        end = min(start+size, shot['source_frames'])
        # Reference context includes the same past as generated continuation.
        # Existing target outputs stay unchanged: never prepend past audio to
        # the native frame-zero target guide or extend the denoising mask.
        past = max(0, hist-1)
        begin = start-past
        if begin < 0:
            raise ValueError('Source reference history precedes the shot')
        previous = state['body'][start-hist+1:start+1] if hist else shot['anchor'][:1]
        if hist and len(previous) != hist:
            raise ValueError('Missing exact body history at the requested boundary')
        return (pad(shot['source'][start:end], size), pad(shot['body_mask'][start:end], size),
            audio_slice(shot['source_audio'], start, start+size),
            previous, audio_slice(shot['source_audio'], max(0,start-hist+1), start+1),
            pad(shot['source'][begin:end], size+past),
            audio_slice(shot['source_audio'], begin, start+size))

