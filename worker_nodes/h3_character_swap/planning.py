"""Source-aligned adaptive windows; history precedes the target timeline."""
WINDOWS = tuple(range(22, 363, 17))
HISTORY = (0, 1, *range(18, 342, 17))
def validate_settings(window_frames, history_frames):
    window, history = int(window_frames), int(history_frames)
    if window not in WINDOWS:
        raise ValueError("Window must be 22, 39, 56, ... 362 frames (17n+5).")
    if history not in HISTORY:
        raise ValueError("History must be 0, 1, or 18, 35, 52, ... (17n+1).")
    if history >= window:
        raise ValueError("History must be smaller than the generation window.")
    return window, history


def plan_windows(count, window_frames=124, history_frames=18):
    window, history = validate_settings(window_frames, history_frames)
    if count < 1:
        raise ValueError("Source video has no frames.")
    # History precedes the target; only its boundary frame is regenerated.
    # Do not move the tail backwards and resample an already accepted prefix.
    result, covered = [], 0
    while covered < count:
        effective_history = history if result else 0
        start = covered - (1 if effective_history else 0)
        needed = min(count - start, window)
        # The first target has no history, even when the configured history is long.
        size = next(v for v in WINDOWS if v >= needed and v > effective_history)
        end = min(count, start + size)
        result.append({"index": len(result), "source_start": start, "source_end_exclusive": end,
            "window_frames": size, "padded_tail_frames": size - (end - start),
            "history_frames": effective_history,
            "history_start": start - effective_history + 1 if effective_history else None,
            "history_end_exclusive": start + 1 if effective_history else None,
            "discarded_overlap": covered - start, "delivered_frames": end - covered})
        covered = end
    return result

