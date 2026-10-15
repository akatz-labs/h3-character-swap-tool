import json
import subprocess

import numpy as np
import pytest
from h3workbench import comparisons, media, previews, store


def test_review_preview_scope_and_immutable_export_identity():
    c = dict(
        source="original",
        source_pair="matched",
        result="result",
        comparison="report",
        comparison_fit="fit",
        frames=12,
        dimensions=[64, 48],
        audio=True,
    )
    pair = comparisons.review_specification(c, "pair")
    assert pair["source"] == "matched" and pair["assets"] == ["matched", "result"]
    assert pair["start"] == 0 and pair["end"] == 12
    fixed = comparisons.review_specification(c, "16x9")
    assert fixed["asset"] == "report" and fixed["max_side"] == 960
    assert comparisons.key_for(fixed) == comparisons.key_for(
        comparisons.review_specification(
            {**c, "reference": "new-reference-not-assembled"}, "16x9"
        )
    )
    assert comparisons.key_for(fixed) != comparisons.key_for(
        comparisons.review_specification({**c, "comparison": "rebuilt"}, "16x9")
    )
    with pytest.raises(ValueError, match="Assemble"):
        comparisons.review_specification({**c, "result": None}, "pair")
    with pytest.raises(ValueError, match="format"):
        comparisons.review_specification(c, "arbitrary")


def test_final_export_proxy_preserves_layout_frames_audio_and_caches(
    tmp_path, monkeypatch
):
    for module in [store, comparisons, previews]:
        monkeypatch.setattr(module, "MEDIA", tmp_path)
    monkeypatch.setattr(store, "DATA", tmp_path)
    store.init()
    n, w, h = 4, 1920, 1080
    pixels = np.zeros((n, h, w, 3), np.uint8)
    for f in range(n):
        pixels[f, :, :640, 0] = 80 + f * 30
        pixels[f, :, 640:1280, 1] = 180
        pixels[f, :, 1280:, 2] = 60 + f * 30
    source = tmp_path / "report.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{w}x{h}",
            "-r",
            "24",
            "-i",
            "pipe:0",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.166667",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        input=pixels.tobytes(),
        capture_output=True,
        check=True,
    )
    original = store.asset_add(source)
    c = dict(
        source=original,
        result=original,
        comparison=original,
        comparison_fit=original,
        frames=n,
        dimensions=[w, h],
        audio=True,
    )
    real_run = media.run
    monkeypatch.setattr(
        media,
        "run",
        lambda args, **kw: (
            "checked" if args[0] == "hub-storage" else real_run(args, **kw)
        ),
    )
    spec = comparisons.review_specification(c, "16x9")
    key = comparisons.key_for(spec)
    directory = tmp_path / "comparisons" / key
    comparisons.build_review_export(key, spec, directory)
    result = comparisons.prepare_review(c, "16x9")
    assert result["state"] == "ready" and result["columns"] == ["report"]
    assert [result["width"], result["height"]] == [960, 540]
    assert result["frames"] == n and result["audio"] and result["fps"] == 24
    video = store.path(result["asset"])
    before = video.stat().st_mtime_ns
    assert media.probe(video)["frames"] == n
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    decoded = np.frombuffer(raw, np.uint8).reshape(n, 540, 960, 3)
    for f in range(n):
        assert abs(int(decoded[f, 270, 160, 0]) - (80 + f * 30)) < 7
        assert abs(int(decoded[f, 270, 480, 1]) - 180) < 7
        assert abs(int(decoded[f, 270, 800, 2]) - (60 + f * 30)) < 7
    assert comparisons.prepare_review(c, "16x9")["asset"] == result["asset"]
    assert video.stat().st_mtime_ns == before
    assert store.sha(source) == store.asset(original)["sha256"]
