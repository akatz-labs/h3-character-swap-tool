"""Explicit shot segments: one 17n+5 pass, with no product duration ceiling.

This pure module is shared by the app and its pinned worker package. It emits
the existing Relay window contract; Relay still owns conditioning and sampling.
"""
import json

HISTORY = (0, 1, *range(18, 342, 17))


def plan_segment(source_frames, history_frames=0):
    if type(source_frames) is not int or type(history_frames) is not int or history_frames not in HISTORY:
        raise ValueError('Invalid segment frame counts')
    target = source_frames - history_frames
    if target < 1:
        raise ValueError('A segment must contain at least one output frame')
    needed = target + bool(history_frames)
    size = ((max(22, needed, history_frames + 1) - 5 + 16) // 17) * 17 + 5
    h = history_frames
    return [{'index': 0, 'source_start': h - 1 if h else 0,
             'source_end_exclusive': source_frames, 'window_frames': size,
             'padded_tail_frames': size - needed, 'history_frames': h,
             'history_start': 0 if h else None, 'history_end_exclusive': h if h else None,
             'discarded_overlap': int(bool(h)), 'delivered_frames': target}]


class H3WorkbenchSegmentPlan:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'source_frames': ('INT', {'default': 120, 'min': 1}),
                             'seed': ('INT', {'default': 194731, 'min': 0, 'max': 2**64 - 1})},
                'optional': {'initial_history': ('IMAGE',)}}

    RETURN_TYPES = ('H3_RELAY_WINDOW', 'STRING', 'INT', 'H3_RELAY_WINDOWS')
    RETURN_NAMES = ('window_list', 'timeline', 'count', 'windows')
    OUTPUT_IS_LIST = (True, False, False, False)
    FUNCTION = 'plan'
    CATEGORY = 'H3 Workbench/timeline'

    def plan(self, source_frames, seed, initial_history=None):
        if type(seed) is not int or not 0 <= seed < 2**64:
            raise ValueError('Window seed must be an unsigned 64-bit integer')
        initial = 0 if initial_history is None else len(initial_history)
        entries = plan_segment(source_frames, initial)
        for entry in entries:
            entry.update(contract='h3-relay-window-v2', source_frames=source_frames,
                         seed=seed, initial_history_frames=initial, schedule='segment')
        return entries, json.dumps({'contract': 'h3-relay-window-v2', 'fps': 24, 'windows': entries}), 1, {'windows': entries}
