"""Pure tensor helpers for the native H3 17n+5 timeline and sparse guide layout."""
import copy
import torch
import torch.nn.functional as F


def pool_person_mask(mask, latent_shape):
    """Union every pixel in each H3 temporal span and 32px DiT spatial cell."""
    _, channels, t, h, w = latent_shape
    spans = [((1, 4, 4, 4, 4)[i % 5]) for i in range(t)]
    if channels != 24 or tuple(mask.shape) != (sum(spans), h * 16, w * 16):
        raise ValueError(f"H3 mask/latent timeline mismatch: {tuple(mask.shape)} vs {latent_shape}")
    if h % 2 or w % 2:
        raise ValueError("This experiment requires a canvas divisible by 32")
    rows, offset = [], 0
    for span in spans:
        rows.append(mask[offset:offset + span].amax(dim=0))
        offset += span
    pooled = F.max_pool2d(torch.stack(rows)[:, None].float(), 32, 32)[:, 0]
    video_mask = pooled.repeat_interleave(2, -2).repeat_interleave(2, -1)[None, None]
    return video_mask, (pooled > 0).reshape(-1)


def prune_guide_layout(layout, keyframes):
    """Physically remove only marked keyframe rows; retain all original coordinates."""
    keep = torch.ones(layout.seq_len, dtype=torch.bool)
    video_guides = iter(k for k in keyframes if k.get("latent") is not None)
    for start, stop, kind in layout.segments:
        if kind != "cond":
            continue
        kf = next(video_guides)
        holes = kf.get("h3_person_hole_rows")
        if holes is not None:
            holes = holes.detach().cpu().bool().reshape(-1)
            if holes.numel() != stop - start:
                raise ValueError("Guide mask patch count does not match native packed layout")
            keep[start:stop] = ~holes
    cond_keep = keep[layout.img_pos[~layout.img_update]]
    if keep.all():
        return layout, cond_keep, keep
    new = copy.copy(layout)
    remap = torch.cumsum(keep.long(), 0) - 1
    new.seq_len = int(keep.sum())
    new.position_ids = layout.position_ids[keep]
    for modality in ("img", "audio"):
        pos = getattr(layout, modality + "_pos")
        chosen = keep[pos]
        setattr(new, modality + "_pos", remap[pos[chosen]])
        setattr(new, modality + "_update", getattr(layout, modality + "_update")[chosen])
    new.segments, offset = [], 0
    for start, stop, kind in layout.segments:
        count = int(keep[start:stop].sum())
        if count:
            new.segments.append((offset, offset + count, kind))
            offset += count
    assert offset == new.seq_len
    assert new.img_update.sum() == layout.img_update.sum()
    assert new.audio_update.sum() == layout.audio_update.sum()
    return new, cond_keep, keep
