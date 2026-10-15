import json
import subprocess
import numpy as np
import pytest
from h3workbench import comparisons, media, store, previews


def test_comparison_scope_and_cache_identity():
    c = {"source": "s", "dimensions": [64, 48], "audio": True}
    s = {"start": 3, "end": 9, "renders": [{"id": "r", "asset": "v"}]}
    a = comparisons.specification(c, s, "source", "r")
    assert comparisons.key_for(a) == comparisons.key_for(comparisons.specification(c, s, "r", "source"))
    assert comparisons.key_for(a) != comparisons.key_for(comparisons.specification(c, {**s, "end": 8}, "source", "r"))
    with pytest.raises(ValueError, match="this shot"):
        comparisons.specification(c, s, "source", "unrelated-render")


def test_packed_preview_exact_frame_alignment_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "MEDIA", tmp_path)
    monkeypatch.setattr(comparisons, "MEDIA", tmp_path)
    monkeypatch.setattr(previews, "MEDIA", tmp_path)
    store.init()
    # A nonzero source start catches comparisons that accidentally show frame zero.
    source = np.zeros((12, 48, 64, 3), dtype=np.uint8)
    render = np.zeros((6, 48, 64, 3), dtype=np.uint8)
    for i in range(12): source[i, :, :, 0] = i * 15
    for i in range(6): render[i, :, :, 2] = 50 + i * 20
    def encode(array, path, audio):
        args = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x48", "-r", "24", "-i", "pipe:0"]
        if audio: args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5"]
        args += ["-c:v", "ffv1", "-c:a", "pcm_s16le", str(path)]
        subprocess.run(args, input=array.tobytes(), check=True, capture_output=True)
    encode(source, tmp_path / "source.mkv", True)
    encode(render, tmp_path / "render.mkv", False)
    c = {"source": store.asset_add(tmp_path / "source.mkv"), "dimensions": [64, 48], "audio": True}
    s = {"start": 3, "end": 9, "renders": [{"id": "render", "asset": store.asset_add(tmp_path / "render.mkv")}]}
    checks = []
    monkeypatch.setattr(comparisons, "check_storage", lambda gib: checks.append(gib))
    monkeypatch.setattr(previews, "check_storage", lambda gib: checks.append(gib))
    spec = comparisons.specification(c, s, "source", "render")
    key = comparisons.key_for(spec)
    directory = tmp_path / "comparisons" / key
    comparisons.build(key, spec, directory)
    receipt = json.loads((directory / "preview.json").read_text())
    output = store.path(receipt["asset"])
    assert receipt["frames"] == 6 and receipt["audio"]
    assert media.probe(output)["fps"] == 24
    decoded = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
    pixels = np.frombuffer(decoded, dtype=np.uint8).reshape(6, 48, 128, 3)
    for i in range(6):
        assert abs(pixels[i, :, :64, 2].mean() - (50 + i * 20)) < 5
        assert abs(pixels[i, :, 64:, 0].mean() - ((3 + i) * 15)) < 5
    assert checks
    # Mask editor frames reuse the small source proxy, with the same local frame.
    from PIL import Image
    for i in range(6):
        jpg = previews.source_frame(c, s, i)
        assert abs(np.asarray(Image.open(jpg))[:, :, 0].mean() - ((3 + i) * 15)) < 6
    ready = comparisons.prepare(c, s, "render", "source")
    assert ready["state"] == "ready" and ready["asset"] == receipt["asset"]
    # A short historical render must fail visibly, without inventing repeated frames.
    bad = {**spec, "end": 10}
    badkey = comparisons.key_for(bad)
    comparisons.build(badkey, bad, tmp_path / "bad")
    assert comparisons.PENDING.pop(badkey)["state"] == "failed"
    assert not (tmp_path / "bad/preview.json").exists()


def test_mask_preview_padding_is_applied_before_downsampling(tmp_path, monkeypatch):
    from PIL import Image
    from scipy.ndimage import distance_transform_edt
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "MEDIA", tmp_path)
    monkeypatch.setattr(previews, "MEDIA", tmp_path)
    store.init()
    raw = np.zeros((3, 864, 1152), dtype=np.float32)
    raw[1, 211:613, 317:733] = 1
    path = tmp_path / "mask.npz"
    np.savez_compressed(path, mask=raw)
    asset = store.asset_add(path)
    for padding in [0, 20, 64]:
        mask = raw[1] > .5
        if padding: mask = distance_transform_edt(~mask) <= padding
        expected = np.asarray(Image.fromarray(mask).resize((640, 480), Image.Resampling.NEAREST))
        result = previews.mask_layer({"mask": asset}, [1152, 864], 1, padding)
        got = np.asarray(Image.open(result))[:, :, 3] > 0
        assert np.array_equal(got, expected)
    assert previews.size([864, 1152]) == (480, 640)


def test_stale_preview_urls_cannot_cache_a_new_selection(monkeypatch):
    from fastapi.testclient import TestClient
    from h3workbench.app import app
    c = {"source": "current-source", "dimensions": [64, 48], "audio": False,
         "shots": [{"id": "s", "start": 3, "end": 9, "mask": "current-mask", "expansion": 64}]}
    monkeypatch.setattr(store, "clip_get", lambda _: c)
    client = TestClient(app)
    url = "/api/clips/test/shots/s/mask?preview=1"
    for query in ["&view=source&source=old-source", "&view=layer&mask=old-mask", "&view=source&range=0-6", "&view=layer&dimensions=96x64"]:
        response = client.get(url + query)
        assert response.status_code == 400
        assert "changed" in response.json()["detail"]
        assert "immutable" not in response.headers.get("cache-control", "")


def test_playback_distance_fields_match_native_padding_all_frames(
    tmp_path, monkeypatch
):
    from PIL import Image
    from scipy.ndimage import distance_transform_edt

    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "MEDIA", tmp_path)
    store.init()
    raw = np.zeros((10, 864, 1152), np.uint8)
    for frame in range(9):
        raw[
            frame,
            80 + frame * 13 : 410 + frame * 17,
            190 + frame * 19 : 555 + frame * 23,
        ] = 1
    raw[4, 200:240, 300:340] = 0
    path = tmp_path / "tracked.npz"
    np.savez_compressed(path, mask=raw)
    fields = previews.distance_frames(store.asset_add(path), 10, [1152, 864])
    for frame, field in enumerate(fields):
        assert field.shape == (480, 640) and field.dtype == np.dtype("<u2")
        for padding in [0, 1, 12, 20, 64, 255, 256]:
            expected = raw[frame] > 0
            if padding and expected.any():
                expected = distance_transform_edt(~expected) <= padding
            expected = np.asarray(
                Image.fromarray(expected).resize((640, 480), Image.Resampling.NEAREST)
            )
            assert np.array_equal(field <= padding, expected), (frame, padding)


def test_mask_playback_is_lossless_paged_and_padding_independent(tmp_path, monkeypatch):
    import gzip

    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "MEDIA", tmp_path)
    store.init()
    raw = np.zeros((10, 48, 64), np.uint8)
    raw[:, 10:35, 15:40] = 1
    path = tmp_path / "mask.npz"
    np.savez_compressed(path, mask=raw)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test proxy; video fidelity tested separately")
    monkeypatch.setattr(previews, "proxy", lambda *args: video)
    monkeypatch.setattr(media, "run", lambda *args: "")
    c = {"source": "source", "audio": False, "dimensions": [64, 48]}
    s = {"start": 7, "end": 17, "mask": store.asset_add(path), "expansion": 12}
    result = previews.build_mask_playback(c, s, tmp_path / "pages")
    assert [p["frames"] for p in result["pages"]] == [8, 2]
    decoded = b"".join(
        gzip.decompress(store.path(p["asset"]).read_bytes()) for p in result["pages"]
    )
    data = (
        np.frombuffer(decoded, dtype="<u2")
        .reshape(10, 48, 64)
        .cumsum(axis=2, dtype=np.uint16)
    )
    assert np.array_equal(data <= 0, raw.astype(bool))
    before = previews.mask_playback_spec(c, s)
    assert before == previews.mask_playback_spec(c, {**s, "expansion": 256})
    assert before != previews.mask_playback_spec(c, {**s, "mask": "different-mask"})
