import copy
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from h3workbench import media, pipeline, remote, store, render_models, image_previews
from h3workbench.app import app


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "MEDIA", tmp_path)
    monkeypatch.setattr(image_previews, "MEDIA", tmp_path)
    monkeypatch.setattr(pipeline, "start", lambda: None)
    store.init()
    image = tmp_path / "reference.png"
    Image.new("RGBA", (1024, 512), (200, 10, 20, 128)).save(image)
    asset = store.asset_add(image)
    c = store.clip_create(store.batch_create("QA"), {
        "id": "c", "name": "QA", "source": "source", "reference": asset,
        "dimensions": [512, 512], "frames": 24, "audio": True, "cuts_approved": True,
        "shots": [{"id": "s", "start": 0, "end": 24, "mode": "generate",
                   "seed": 42, "prompt": "Preserve performance", "expansion": 64,
                   "anchor": asset, "mask": asset, "anchors": [], "renders": []}],
    })
    monkeypatch.setattr(media, "shot_media", lambda *args, **kwargs: image)
    return c, tmp_path


@pytest.mark.parametrize("lora", ["v1-1000", "v2-1000", "none"])
@pytest.mark.parametrize("anchor", [True, False])
@pytest.mark.parametrize("mask", [True, False])
def test_generation_graph_and_snapshot(scene, lora, anchor, mask):
    c, directory = scene
    def edit(v):
        v["shots"][0].update(lora_id=lora, use_anchor=anchor, use_mask=mask)
        if not anchor:
            v["shots"][0]["anchor"] = None
        if not mask:
            v["shots"][0].update(mask=None, mask_empty_frames=[3], mask_points=[{"frame": 3, "x": .2, "y": .3, "label": 1}])
    c = store.clip_mutate("c", edit)
    id = pipeline.queue("render", "c", "s")
    job = store.job_get(id)
    config = {"lora_id": lora, "use_anchor": anchor, "use_mask": mask, "window_frames": 124, "history_frames": 18}
    assert job["data"]["generation"] == config
    store.clip_mutate("c", lambda v: v["shots"][0].update(lora_id="v2-1000", use_mask=True))
    g, files = remote.graph_render(job["data"]["snapshot"], job["data"]["shot"], job, directory)
    assert job["data"]["generation"] == config
    assert g["4"]["inputs"]["lora_name"].startswith("minimax_h3_fl2v_turbo")
    if lora == "none":
        assert "850" not in g and g["800"]["inputs"]["model"] == ["4", 0]
    else:
        assert g["850"]["inputs"]["lora_name"] == render_models.LORAS[lora]["filename"]
    assert ("309" in g) is anchor
    assert ("43" in g) is mask
    assert any("anchor" in name for name in files) is anchor
    assert any("mask" in name for name in files) is mask
    assert g["801"]["inputs"]["audio_guide"] is True
    assert g["803"]["inputs"]["source_audio"] == ["31", 1]
    for node in g.values():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in g


def test_conditioning_validation_and_immediate_queue_record(scene):
    with TestClient(app) as client:
        for changes in ({"lora_id": "file/path"}, {"lora_id": []}, {"use_anchor": "false"}, {"use_mask": 0}):
            response = client.patch("/api/clips/c/shots/s", json={"revision": store.clip_get("c")["revision"], "changes": changes})
            assert response.status_code == 400
        store.clip_mutate("c", lambda c: c["shots"][0].update(anchor=None))
        assert client.post("/api/jobs", json={"kind": "render", "clip_id": "c", "shot_id": "s"}).status_code == 400
        store.clip_mutate("c", lambda c: c["shots"][0].update(use_anchor=False, use_mask=False, mask=None))
        response = client.post("/api/jobs", json={"kind": "render", "clip_id": "c", "shot_id": "s"}).json()
        assert response["job"]["id"] == response["id"]
        assert response["job"]["state"] == "queued"
        assert "snapshot" not in response["job"]["data"]


def test_submission_rejects_settings_changed_after_save(scene):
    expected = {"seed": 42, "prompt": "Preserve performance", "render_mp": 1.0,
                "lora_id": "v1-1000", "use_anchor": True, "use_mask": True}
    store.clip_mutate("c", lambda c: c["shots"][0].update(lora_id="v2-1000"))
    with TestClient(app) as client:
        response = client.post("/api/jobs", json={"kind": "render", "clip_id": "c", "shot_id": "s", "expected_render": expected})
    assert response.status_code == 409
    assert not store.jobs()


def test_compact_state_preserves_rerun_and_canonical_evidence(scene):
    huge = "x" * 1000000
    id = store.job_create("refine_extract", "c", "s", {
        "snapshot": {"old_history": huge}, "graph": huge, "worker": "fey",
        "options": {"side": "source"}, "phase": "Queued",
        "shot": {"seed": 42, "refine": {"source": {"alpha_seed": 17, "mask_points": [], "alpha": huge}}},
        "result": {"asset": "output", "evidence": huge, "attachment": "history_only_newer_frame_edits"},
    })
    with TestClient(app) as client:
        state = client.get("/api/state").json()
    public = next(j for j in state["jobs"] if j["id"] == id)
    assert public["data"]["shot"]["refine"]["source"]["alpha_seed"] == 17
    assert public["data"]["result"]["asset"] == "output"
    assert len(json.dumps(public)) < 3000
    assert store.job_get(id)["data"]["snapshot"]["old_history"] == huge


def test_unchanged_state_revalidates_without_payload_and_edits_refresh(scene):
    with TestClient(app) as client:
        response = client.get("/api/state")
        etag = response.headers["etag"]
        cached = client.get("/api/state", headers={"If-None-Match": etag})
        assert cached.status_code == 304 and cached.content == b""
        store.clip_mutate("c", lambda c: c.update(name="Edited elsewhere"))
        changed = client.get("/api/state", headers={"If-None-Match": etag})
        assert changed.status_code == 200 and changed.headers["etag"] != etag


def test_byte_range_downloads_stay_uncompressed(scene):
    c, _ = scene
    with TestClient(app) as client:
        response = client.get(f"/api/assets/{c['reference']}?download=true", headers={"Range": "bytes=0-1023", "Accept-Encoding": "gzip"})
    assert response.status_code == 206 and len(response.content) == 1024
    assert "content-encoding" not in response.headers
    assert "attachment" in response.headers["content-disposition"]


def test_still_previews_preserve_alpha_cache_and_master(scene):
    c, _ = scene
    original = store.asset(c["reference"])
    with TestClient(app) as client:
        response = client.get(f"/api/assets/{c['reference']}/preview?size=256")
        assert response.status_code == 200
        assert "immutable" in response.headers["cache-control"]
        assert client.get(f"/api/assets/{c['reference']}/preview?size=999").status_code == 400
    preview = image_previews.prepare(c["reference"], 256)
    before = preview.stat().st_mtime_ns
    with Image.open(preview) as image:
        assert image.size == (256, 128)
        assert image.getpixel((30, 30))[3] == 128
    assert image_previews.prepare(c["reference"], 256).stat().st_mtime_ns == before
    assert store.sha(original["path"]) == original["sha256"]


def test_offload_retry_requires_capacity_receipt_and_preserves_failed_inputs(scene, monkeypatch):
    from h3workbench import refinement

    id = pipeline.queue("render", "c", "s")
    with pytest.raises(ValueError, match="recorded"):
        render_models.retry_offload(id)
    store.job_update(id, "failed")
    monkeypatch.setattr(refinement, "capacity_failure", lambda j: {"offloading": False, "worker": "fey"})
    store.clip_mutate("c", lambda c: c["shots"][0].update(lora_id="none", use_mask=False, seed=99))
    retry = store.job_get(render_models.retry_offload(id))
    assert retry["data"]["shot"]["seed"] == 42
    assert retry["data"]["generation"]["lora_id"] == "v1-1000"
    assert retry["data"]["options"] == {"offload_after": id}
    assert store.job_get(id)["state"] == "failed"
    assert store.clip_get("c")["shots"][0]["seed"] == 99


def adapter():
    spec = importlib.util.spec_from_file_location("generation", Path(__file__).parents[1] / "worker_nodes/h3_character_swap/conditioning.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("anchor", [True, False])
@pytest.mark.parametrize("mask", [True, False])
@pytest.mark.parametrize("history", [0, 18])
def test_expanded_conditioning_bypass_preserves_source_audio_and_continuation(anchor, mask, history):
    module = adapter()
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}
    graph = {"ref": node("H3CharacterSwapReference", **{"ref_images.ref_image_0": "character", "ref_images.ref_image_1": "anchor", "ref_videos.ref_video_0": "source", "ref_audios.ref_audio_0": "audio"}),
             "history": node("H3CharacterSwapHistory", positive=["ref", 0], latent=["ref", 1]),
             "mask": node("H3CharacterSwapMaskedRegeneration", latent=["history", 1]),
             "audio": node("MiniMaxH3AddGuide", positive=["history", 0], latent=["mask", 0], audio="audio", frame_idx=0),
             "sample": node("SamplerCustomAdvanced", latent_image=["mask", 0])}
    expanded = module.conditioning_graph({"expand": copy.deepcopy(graph)}, anchor, mask, history)["expand"]
    assert expanded["ref"]["inputs"]["ref_videos.ref_video_0"] == "source"
    assert expanded["ref"]["inputs"]["ref_audios.ref_audio_0"] == "audio"
    assert ("ref_images.ref_image_1" in expanded["ref"]["inputs"]) is anchor
    assert ("mask" in expanded) is mask
    assert ("history" in expanded) is (anchor or history > 0)
    assert expanded["audio"]["inputs"]["frame_idx"] == 0
    for node in expanded.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in expanded


def test_offloading_default_explicit_opt_out_and_legacy_jobs(scene):
    from h3workbench import session_policy
    default = store.job_get(pipeline.queue('render', 'c', 's'))
    assert default['data']['options'] == {'model_offloading': True}
    assert render_models.offloading(default)
    store.job_update(default['id'], 'success')
    explicit = store.job_get(pipeline.queue('render', 'c', 's', options={'model_offloading': False}))
    assert not render_models.offloading(explicit)
    assert default['data']['generation'] == explicit['data']['generation']
    assert default['data']['render_spec'] == explicit['data']['render_spec']
    assert not render_models.offloading({'data': {}})
    assert render_models.offloading({'data': {'options': {'offload_after': 'old-failure'}}})
    workers = {name: {'state': 'warm_idle', 'placement': placement, 'family': {'label': 'H3', 'loras': [default['data']['lora']['filename']]}}
               for name, placement in [('a-full', 'full'), ('z-offload', 'offload')]}
    assert session_policy.choose_worker(default, workers) == 'z-offload'
    assert session_policy.choose_worker(explicit, workers) == 'a-full'
    for options in ({'model_offloading': 'false'}, {'offload_after': 'fake'}, []):
        with pytest.raises(ValueError):
            pipeline.queue('render', 'c', 's', options=options)


@pytest.mark.parametrize('frames,window,history,count', [(459,124,18,4),(480,124,18,4),(360,362,18,1),(120,56,18,3),(459,243,35,2),(459,124,0,4)])
def test_window_preview_uses_worker_plan_without_mutating_shot(scene, frames, window, history, count):
    store.clip_mutate('c', lambda c: c['shots'][0].update(end=frames))
    before = store.clip_get('c')
    with TestClient(app) as client:
        response = client.get(f'/api/clips/c/shots/s/render-plan?window_frames={window}&history_frames={history}')
    assert response.status_code == 200, response.text
    plan = response.json()
    assert len(plan['windows']) == count
    assert sum(w['delivered_frames'] for w in plan['windows']) == frames
    assert plan['windows'][0]['history_frames'] == 0
    assert store.clip_get('c') == before


@pytest.mark.parametrize('changes', [dict(window_frames=120), dict(window_frames=480), dict(window_frames=True),
    dict(window_frames='124'), dict(history_frames=17), dict(history_frames=124),
    dict(history_frames=False), dict(window_frames=22,history_frames=35)])
def test_window_settings_reject_invalid_values_without_writes(scene, changes):
    before = store.clip_get('c')
    with TestClient(app) as client:
        response = client.patch('/api/clips/c/shots/s', json={'revision':before['revision'],'changes':changes})
    assert response.status_code == 400, response.text
    assert store.clip_get('c') == before


def test_window_controls_freeze_in_queue_and_reach_actual_graph(scene):
    _, directory = scene
    with TestClient(app) as client:
        response = client.patch('/api/clips/c/shots/s', json={'revision':store.clip_get('c')['revision'],
            'changes':{'window_frames':243,'history_frames':35}})
        assert response.status_code == 200
        expected = {'seed':42,'prompt':'Preserve performance','render_mp':1.0,
                    **render_models.settings(response.json()['shots'][0])}
        queued = client.post('/api/jobs',json={'kind':'render','clip_id':'c','shot_id':'s','expected_render':expected})
        assert queued.status_code == 200, queued.text
        job = store.job_get(queued.json()['id'])
    store.clip_mutate('c', lambda c: c['shots'][0].update(window_frames=362,history_frames=18))
    graph, _ = remote.graph_render(job['data']['snapshot'],job['data']['shot'],job,directory)
    assert graph['805']['inputs']['window_frames'] == '243'
    assert graph['805']['inputs']['history_frames'] == '35'
    assert job['data']['generation']['window_frames'] == 243
    assert job['data']['generation']['history_frames'] == 35


def test_old_queued_render_keeps_original_window_defaults(scene):
    _, directory = scene
    job = store.job_get(pipeline.queue('render','c','s'))
    job['data']['generation'].pop('window_frames')
    job['data']['generation'].pop('history_frames')
    job['data']['shot'].update(window_frames=362,history_frames=35)
    graph, _ = remote.graph_render(job['data']['snapshot'],job['data']['shot'],job,directory)
    assert graph['805']['inputs']['window_frames'] == '124'
    assert graph['805']['inputs']['history_frames'] == '18'


def test_stale_client_cannot_silently_queue_different_window_settings(scene):
    store.clip_mutate('c', lambda c: c['shots'][0].update(window_frames=362,history_frames=18))
    old_expected = {'seed':42,'prompt':'Preserve performance','render_mp':1.0,
                    'lora_id':'v1-1000','use_anchor':True,'use_mask':True}
    with TestClient(app) as client:
        response=client.post('/api/jobs',json={'kind':'render','clip_id':'c','shot_id':'s','expected_render':old_expected})
    assert response.status_code == 409
    assert not store.jobs()
