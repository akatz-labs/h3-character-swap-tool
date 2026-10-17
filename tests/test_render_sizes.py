import copy
import json

import numpy as np
import pytest
from PIL import Image

from h3workbench import (
    comparisons,
    media,
    pipeline,
    previews,
    refine_jobs,
    remote,
    store,
)
from h3workbench import refinement as ref
from test_contract import setup
from test_refinement import scene


@pytest.mark.parametrize(
    "ratio", [(16, 9), (9, 16), (4, 3), (640, 928), (1, 1), (20, 1), (1, 20)]
)
@pytest.mark.parametrize("mp", [0.1, 0.25, 0.5, 0.75])
def test_supported_pixel_budgets_and_aspect(ratio, mp):
    c = {"dimensions": media.canvas(*ratio)}
    legacy = media.render_spec(c, {})
    assert legacy["dimensions"] == c["dimensions"]
    assert legacy["resolution"] == "768"
    low = media.render_spec(c, {"render_mp": mp, "expansion": 64})
    w, h = low["dimensions"]
    assert w % 32 == h % 32 == 0
    assert mp * 0.75 < low["actual_mp"] <= mp
    assert abs(w / h / (c["dimensions"][0] / c["dimensions"][1]) - 1) < max(
        0.06, 32 / min(w, h)
    )
    assert (
        low["resolution"] == ("480" if mp == 0.5 else "custom")
        and low["expansion"] < 64
    )


@pytest.mark.parametrize("value", [0, -1, 0.05, 1.01, 1.5, 2, True, "0.5", None])
def test_invalid_render_size_is_rejected_without_changes(setup, value):
    client, c = setup
    response = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"render_mp": value}},
    )
    assert response.status_code == 400
    assert store.clip_get("test") == c


def test_size_is_frozen_when_queued_and_draft_keeps_previous_selection(setup):
    client, c = setup
    response = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"render_mp": 0.5}},
    )
    c = response.json()
    assert c["result"] == "old-result" and c["shots"][0]["selected_render"] == "old"
    jid = pipeline.queue("render", "test", "s")
    store.clip_mutate("test", lambda v: v["shots"][0].update(render_mp=1.0))
    job = store.job_get(jid)
    assert job["data"]["shot"]["render_mp"] == 0.5
    assert job["data"]["render_spec"]["dimensions"] == [800, 608]
    assert job["data"]["snapshot"]["dimensions"] == [1152, 864]


def test_low_resolution_graph_has_exact_source_and_mask_geometry(scene, tmp_path):
    c, s, _ = scene
    c["dimensions"] = [832, 1184]
    s.update(
        render_mp=0.5,
        expansion=64,
        prompt="same performance",
        seed=12,
        anchor=c["source"],
    )
    c["reference"] = c["source"]
    spec = media.render_spec(c, s)
    d = tmp_path / "low"
    d.mkdir()
    graph, files = remote.graph_render(
        c, s, {"id": "low", "data": {"render_spec": spec}}, d
    )
    info = media.probe(files["low-source.mkv"])
    assert [info["width"], info["height"]] == spec["dimensions"] == [576, 832]
    assert info["frames"] == 3 and info["fps"] == 24 and info["audio"]
    assert graph["31"]["class_type"] == "GetVideoComponents"
    assert (
        graph["805"]["inputs"]["source_frames"]
        == graph["43"]["inputs"]["source_frames"]
        == 3
    )
    assert graph["803"]["inputs"]["anchor"] == ["309", 0]
    assert graph["history"]["inputs"]["first_frame_guide"] is True
    assert graph["43"]["inputs"]["expansion_px"] == spec["expansion"]
    with np.load(files["low-mask.npz"]) as z:
        assert z["mask"].shape == (3, 832, 576)
        assert np.array_equal(
            z["mask"], pipeline.masks.read(s["mask"], 3, spec["dimensions"])
        )
    assert store.path(s["mask"]) != files["low-mask.npz"]
    before = copy.deepcopy(c)
    s["render_mp"] = 1.0
    high_dir = tmp_path / "high"
    high_dir.mkdir()
    graph, files = remote.graph_render(c, s, {"id": "high", "data": {}}, high_dir)
    assert graph["31"]["class_type"] == "GetVideoComponents"
    assert files["high-mask.npz"] == store.path(s["mask"])
    assert s["mask"] == before["shots"][0]["mask"]
    # A budget near 1 MP can quantize back to the legacy canvas. Its custom
    # budget must still avoid the preparation node's two-value enum.
    s["render_mp"] = 0.999
    custom_dir = tmp_path / "almost-high"
    custom_dir.mkdir()
    graph, _ = remote.graph_render(c, s, {"id": "almost-high", "data": {}}, custom_dir)
    assert graph["31"]["class_type"] == "GetVideoComponents"
    assert "resolution" not in graph["31"]["inputs"]
    c["dimensions"] = [448, 2176]
    s["render_mp"] = 1.0
    # This valid tall canvas would drift to 448x2208 on a second 1 MP resize.
    exact_dir = tmp_path / "exact-high"
    exact_dir.mkdir()
    graph, files = remote.graph_render(
        c, s, {"id": "exact-high", "data": {}}, exact_dir
    )
    assert graph["31"]["class_type"] == "GetVideoComponents"
    assert graph["803"]["class_type"] == "H3RelayWindowInputs"
    assert "311" not in graph
    info = media.probe(files["exact-high-source.mkv"])
    assert [info["width"], info["height"]] == c["dimensions"]


def test_different_size_render_has_aligned_preview_and_refine_inputs(
    scene, tmp_path, monkeypatch
):
    c, s, _ = scene
    # Different grid ratios must scale as one canvas, without new letterboxing.
    pixels = np.full((3, 64, 96, 3), [200, 20, 40], dtype=np.uint8)
    low = tmp_path / "low.mkv"
    ref.encode(pixels, low)
    render = {"id": "low", "asset": store.asset_add(low), "dimensions": [96, 64]}
    s["renders"].append(render)
    s["selected_render"] = "low"
    spec = comparisons.specification(c, s, "source", "low")
    assert spec["canvas_aligned"] == ["low"]
    vc, vs = refine_jobs.virtual(c, s, "character")
    out = tmp_path / "character"
    out.mkdir()
    staged = media.shot_media(vc, vs, out)
    decoded = ref.decode(store.asset_add(staged), 0, 3, c["dimensions"])
    assert np.all(decoded[..., 0] == 200)
    monkeypatch.setattr(previews, "MEDIA", tmp_path)
    real_run = media.run
    monkeypatch.setattr(
        media,
        "run",
        lambda args, **kw: (
            "checked" if args[0] == "hub-storage" else real_run(args, **kw)
        ),
    )
    proxy = previews.proxy(render["asset"], 0, 3, c["dimensions"], canvas_aligned=True)
    decoded = ref.decode(store.asset_add(proxy), 0, 3, c["dimensions"])
    assert decoded[..., 0].min() > 190
    assert json.loads((proxy.parent / "preview.json").read_text())["canvas_aligned"]


def test_mixed_render_sizes_assemble_to_one_canvas(scene, tmp_path):
    c, s, _ = scene
    c.update(dimensions=[64, 64], frames=6)
    source = tmp_path / "six-frames.mkv"
    ref.encode(np.full((6, 64, 64, 3), 120, np.uint8), source)
    c["source"] = store.asset_add(source)
    low = tmp_path / "low.mkv"
    ref.encode(np.full((3, 32, 32, 3), 190, np.uint8), low)
    s.update(
        selected_render="low",
        renders=[{"id": "low", "asset": store.asset_add(low), "dimensions": [32, 32]}],
    )
    s.pop("refine")
    c["shots"].append({"id": "original", "start": 3, "end": 6, "mode": "preserve"})
    image = tmp_path / "ref.png"
    Image.new("RGB", (32, 32), "orange").save(image)
    c["reference"] = store.asset_add(image)
    d = tmp_path / "assemble"
    d.mkdir()
    receipt = pipeline.assemble({"data": {"snapshot": c}}, d)
    info = media.probe(store.path(receipt["result"]))
    assert (info["frames"], info["width"], info["height"], info["fps"]) == (
        6,
        64,
        64,
        24,
    )
    pixels = ref.decode(receipt["master"], 0, 6, c["dimensions"])
    assert (pixels[:3] == 190).all() and (pixels[3:] == 120).all()


@pytest.mark.parametrize("edit_next_size", [False, True])
@pytest.mark.parametrize("edit_generation", [False, True])
@pytest.mark.parametrize("model_offloading", [True, False])
def test_completed_render_records_frozen_size_and_preserves_newer_settings(
    scene, tmp_path, monkeypatch, edit_next_size, edit_generation, model_offloading
):
    c, s, _ = scene
    image = tmp_path / "anchor.png"
    Image.new("RGB", (32, 32), "orange").save(image)
    asset = store.asset_add(image)

    def prepare(v):
        v.update(reference=asset, result="existing-assembly")
        v["shots"][0].update(
            anchor=asset, prompt="same performance", seed=1, expansion=64, render_mp=0.5
        )

    store.clip_mutate(c["id"], prepare)
    jid = pipeline.queue("render", c["id"], s["id"], options=None if model_offloading else {"model_offloading": False})
    monkeypatch.setattr(pipeline, "MEDIA", tmp_path)
    (tmp_path / "jobs").mkdir()
    real_run = media.run
    monkeypatch.setattr(
        media,
        "run",
        lambda args, **kw: (
            "checked" if args[0] == "hub-storage" else real_run(args, **kw)
        ),
    )

    def completed(job, graph, files, d, profile):
        assert profile == ("full-offload" if model_offloading else "full")
        (d / "api.json").write_text(json.dumps(graph))
        out = d / "runtime"
        (out / "output").mkdir(parents=True)
        w, h = job["data"]["render_spec"]["dimensions"]
        media.ff(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=orange:s={w}x{h}:r=24",
                "-frames:v",
                "3",
                "-c:v",
                "libx264",
                out / "output" / f"{jid}.mp4",
            ]
        )
        (out / "history.json").write_text(
            json.dumps({"prompt": {"outputs": {"807": {"h3_relay_report": [{"complete": True}]},
                "809": {"images": [{"type": "output", "subfolder": "", "filename": f"{jid}.mp4"}]}}}})
        )
        if edit_generation:
            store.clip_mutate(c["id"], lambda v: v["shots"][0].update(lora_id="none", use_anchor=False, use_mask=False))
        if edit_next_size:
            store.clip_mutate(c["id"], lambda v: v["shots"][0].update(render_mp=0.75))
        return out

    monkeypatch.setattr(remote, "execute", completed)
    pipeline.execute(jid)
    job = store.job_get(jid)
    assert job["state"] == "success"
    current = store.clip_get(c["id"])
    saved = current["shots"][0]
    rendered = saved["renders"][-1]
    assert rendered["residency"] == ("offload" if model_offloading else "full")
    assert rendered["render_mp"] == rendered["settings"]["render_mp"] == 0.5
    assert rendered["dimensions"] == job["data"]["render_spec"]["dimensions"]
    assert rendered["lora_id"] == "v1-1000" and rendered["use_anchor"] and rendered["use_mask"]
    if edit_next_size or edit_generation:
        assert saved["render_mp"] == (0.75 if edit_next_size else 0.5) and saved["selected_render"] == "r"
        assert current["result"] == "existing-assembly"
    else:
        assert saved["selected_render"] == jid and current["result"] is None
