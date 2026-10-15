import copy
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from h3workbench import media, pipeline, refine_jobs, store
from h3workbench import refinement as r
from h3workbench.app import app


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "MEDIA", tmp_path)
    monkeypatch.setattr(pipeline, "start", lambda: None)
    store.init()
    n, h, w = 3, 32, 32

    def video(name, color):
        p = tmp_path / (name + ".mkv")
        r.encode(np.full((n, h, w, 3), color, dtype=np.uint8), p)
        return store.asset_add(p)

    source = video("source", 120)
    character = video("character", 190)
    plate = video("plate", 35)
    mask = np.zeros((n, h, w), bool)
    mask[:, 4:28, 8:24] = True
    p = tmp_path / "mask.npz"
    np.savez_compressed(p, mask=mask)
    mask_id = store.asset_add(p)
    a = tmp_path / "alpha.npz"
    np.savez_compressed(a, alpha=mask.astype(np.float32))
    alpha_id = store.asset_add(a)
    s = {
        "id": "s",
        "start": 0,
        "end": n,
        "mode": "generate",
        "mask": mask_id,
        "mask_approved": True,
        "renders": [{"id": "r", "asset": character}],
        "selected_render": "r",
    }
    c = {
        "id": "c",
        "name": "test",
        "source": source,
        "reference": None,
        "original": source,
        "audio": False,
        "frames": n,
        "dimensions": [w, h],
        "width": w,
        "height": h,
        "cuts_approved": True,
        "shots": [s],
    }
    s["backplates"] = [
        {"id": "plate", "asset": plate, "binding": r.source_binding(c, s)}
    ]
    x = r.draft(c, s)
    x.update(plate_id="plate", plate_reviewed=True)
    for side in r.SIDES:
        x[side].update(mask=mask_id, alpha=alpha_id, reviewed=True)
        x[side]["review_key"] = r.settings_key(x[side])
    s["refine"] = x
    batch = store.batch_create("Test")
    c = store.clip_create(batch, c)
    yield c, c["shots"][0], tmp_path


def test_lossless_pair_and_foreground_pixels(scene):
    c, s, d = scene
    out = d / "compose"
    out.mkdir()
    f = r.compose(c, s, {"id": "finish"}, out)
    import json

    proof = json.loads(store.path(f["verification"]).read_text())
    assert proof["outside_union_changed_pixels"] == 0
    assert proof["outside_union_pixels"] > 0
    a = r.decode(f["source"], 0, 3, c["dimensions"])
    b = r.decode(f["character"], 0, 3, c["dimensions"])
    assert (a[:, 10, 16] == 120).all() and (b[:, 10, 16] == 190).all()
    assert (a[:, 0, 0] == 35).all() and np.array_equal(a[:, 0], b[:, 0])


def test_face_fills_enclosed_hole_without_expanding_silhouette():
    alpha = np.zeros((80, 80), np.float32)
    alpha[8:72, 8:72] = 0.5
    alpha[36:43, 36:43] = 0
    face = {
        "enabled": True,
        "keyframes": [
            {"frame": 0, "polygon": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}
        ],
        "feather_px": 4,
        "guard_px": 3,
    }
    result = r.protect_face(alpha, face, 0)
    assert result[39, 39] == 1 and (result >= alpha).all()
    assert np.array_equal(result[:8], alpha[:8])
    # A real opening connected to the silhouette remains open.
    alpha[:40, 36:43] = 0
    result = r.protect_face(alpha, face, 0)
    assert result[35, 39] == 0


def test_face_interpolation_and_validation():
    f = {
        "enabled": True,
        "feather_px": 4,
        "guard_px": 3,
        "keyframes": [
            {"frame": 0, "polygon": [[0.1, 0.1], [0.3, 0.1], [0.2, 0.3]]},
            {"frame": 2, "polygon": [[0.3, 0.1], [0.5, 0.1], [0.4, 0.3]]},
        ],
    }
    r.validate_face(f, 3)
    assert r.polygon_at(f, 1)[0] == pytest.approx([0.2, 0.1])
    f["keyframes"][1]["frame"] = 3
    with pytest.raises(ValueError):
        r.validate_face(f, 3)


def test_changed_render_preserves_source_but_resets_character(scene):
    c, s, _ = scene
    s["renders"].append({"id": "other", "asset": "other-asset"})
    s["selected_render"] = "other"
    x = r.draft(c, s)
    assert x["source"]["alpha"] and x["plate_id"] == "plate"
    assert x["character"]["alpha"] is None


def test_stale_finish_cannot_assemble(scene):
    c, s, _ = scene
    s.update(
        selected_finish="f",
        finishes=[{"id": "f", "binding": {**r.binding(c, s), "render_id": "other"}}],
    )
    with pytest.raises(ValueError, match="stale"):
        r.selected(c, s)


def test_review_requires_current_mattes_and_strict_boolean(scene):
    c, s, _ = scene
    with pytest.raises(ValueError):
        r.patch(c, s, {"source": {"reviewed": 1}})
    r.patch(c, s, {"source": {"gate_px": 2}})
    assert not s["refine"]["source"]["reviewed"]
    r.validate_job("refine_compose", c, s, {})
    r.patch(c, s, {"source": {"reviewed": True}})
    r.validate_job("refine_compose", c, s, {})


def test_import_refuses_short_and_wrong_clock_backplate(scene):
    c, s, d = scene
    s["end"] = 4
    with pytest.raises(ValueError, match="has 3 frames"):
        r.plate_record(c, s, c["source"], d, {}, 0)


def test_api_conflict_preserves_foreground_edits(scene):
    c, _s, _ = scene
    with TestClient(app) as client:
        path = "/api/clips/c/shots/s/refine"
        ok = client.patch(
            path,
            json={"revision": c["revision"], "changes": {"source": {"gate_px": 2}}},
        )
        assert ok.status_code == 200
        conflict = client.patch(
            path,
            json={"revision": c["revision"], "changes": {"source": {"gate_px": 8}}},
        )
        assert conflict.status_code == 409
        assert store.clip_get("c")["shots"][0]["refine"]["source"]["gate_px"] == 2


def test_completed_old_extraction_retained_without_overwriting_edits(scene):
    c, s, _ = scene
    job = {
        "id": "j",
        "kind": "refine_extract",
        "data": {"snapshot": copy.deepcopy(c), "shot": copy.deepcopy(s)},
    }
    r.patch(c, s, {"source": {"gate_px": 5}})
    refine_jobs.attach(
        c, s, job, {"side": "source", "alpha": "new-alpha", "mask": "new-mask"}
    )
    assert s["refine"]["source"]["alpha"] != "new-alpha"
    assert s["foreground_versions"][-1]["alpha"] == "new-alpha"


def test_source_and_character_extraction_have_independent_queue_entries(scene):
    c, s, _ = scene
    a = pipeline.queue("refine_extract", c["id"], s["id"], "arch", {"side": "source"})
    b = pipeline.queue("refine_extract", c["id"], s["id"], "fey", {"side": "character"})
    assert a != b
    assert (
        pipeline.queue("refine_extract", c["id"], s["id"], "arch", {"side": "source"})
        == a
    )


def test_offload_requires_recorded_capacity_failure(scene):
    c, s, _ = scene
    j = pipeline.queue("refine_extract", c["id"], s["id"], "arch", {"side": "source"})
    with pytest.raises(ValueError, match="memory failure"):
        pipeline.queue(
            "refine_extract",
            c["id"],
            s["id"],
            "arch",
            {"side": "source", "offload_after": j},
        )


def test_source_mask_reuse_keeps_alpha_and_resets_review(scene):
    c, s, _ = scene
    with TestClient(app) as client:
        response = client.post(
            "/api/clips/c/shots/s/refine/source-mask", json={"revision": c["revision"]}
        )
    assert response.status_code == 200
    f = response.json()["shots"][0]["refine"]["source"]
    assert f["alpha"] == s["refine"]["source"]["alpha"] and f["mask"] == s["mask"]
    assert f["reviewed"] is False


def test_pair_assembly_uses_same_refinement_on_both_sides(scene, monkeypatch):
    c, s, d = scene
    finished = d / "finish"
    finished.mkdir()
    f = r.compose(c, s, {"id": "finish"}, finished)
    s.update(finishes=[f], selected_finish=f["id"])
    # A matching image reference gives the real report export a middle panel.
    from PIL import Image

    reference = d / "ref.png"
    Image.new("RGB", (32, 32), "orange").save(reference)
    c["reference"] = store.asset_add(reference)
    out = d / "assembly"
    out.mkdir()
    receipt = pipeline.assemble({"data": {"snapshot": c}}, out)
    assert receipt["raw_decoded"] is False and receipt["selected_finishes"] == {
        "s": "finish"
    }
    a = r.decode(receipt["source_master"], 0, 3, c["dimensions"])
    b = r.decode(receipt["master"], 0, 3, c["dimensions"])
    assert (a[:, 10, 16] == 120).all() and (b[:, 10, 16] == 190).all()
    assert np.array_equal(a[:, 0], b[:, 0])
    assert media.probe(store.path(receipt["comparison"]))["frames"] == 3


def test_restore_foreground_version_rejects_other_render(scene):
    c, _s, _ = scene

    def install(v):
        sh = v["shots"][0]
        sh["foreground_versions"] = [
            {
                "job": "old",
                "side": "character",
                "binding": {**r.binding(v, sh), "render_id": "other"},
                "mask": sh["mask"],
            }
        ]

    c = store.clip_mutate(c["id"], install, c["revision"])
    with TestClient(app) as client:
        response = client.post(
            "/api/clips/c/shots/s/refine/foreground-version",
            json={"revision": c["revision"], "side": "character", "job": "old"},
        )
    assert response.status_code == 400
    assert "different input frames" in response.json()["detail"]


def test_clear_last_correction_can_restore_base_mask(scene):
    c, s, _ = scene
    s["refine"]["source"]["mask_applied_points"] = [
        {"id": "old", "frame": 1, "x": 0.5, "y": 0.5, "label": 1}
    ]
    s["refine"]["source"]["mask_points"] = []
    r.validate_job("refine_mask", c, s, {"side": "source", "mode": "frame", "frame": 1})


def test_seed_and_automatic_face_defaults_and_validation(scene):
    c,s,d=scene
    s['refine']['source'].pop('alpha_seed',None)
    s['refine']['source'].pop('auto_face',None)
    assert r.draft(c,s)['source']['alpha_seed']==1234
    assert r.draft(c,s)['source']['auto_face'] is True
    r.patch(c,s,{'source':{'alpha_seed':987654,'auto_face':False}})
    assert s['refine']['source']['alpha_seed']==987654
    with pytest.raises(ValueError):r.patch(c,s,{'source':{'alpha_seed':1.5}})
    with pytest.raises(ValueError):r.patch(c,s,{'source':{'auto_face':'yes'}})


def test_running_cancel_requires_confirmation_and_waits_for_release(scene):
    c,s,d=scene
    job=store.job_create('refine_extract',c['id'],s['id'],{'worker':'fey','options':{'side':'source'}})
    store.job_update(job,'running')
    client=TestClient(app)
    assert client.post(f'/api/jobs/{job}/cancel',json={}).status_code==400
    response=client.post(f'/api/jobs/{job}/cancel',json={'confirm_running':True})
    assert response.status_code==200 and response.json()['state']=='cancelling'
    assert store.job_get(job)['data']['cancel_requested'] is True
    assert client.post(f'/api/jobs/{job}/cancel',json={'confirm_running':True}).json()['state']=='cancelling'
    from h3workbench import remote
    with pytest.raises(remote.Cancelled):remote.check_cancel(store.job_get(job))


def test_preview_matches_timeline_and_saved_recipe(scene,monkeypatch):
    c,s,d=scene
    from h3workbench import refine_preview
    original=media.run
    monkeypatch.setattr(media,'run',lambda args,**kw: '' if args[0]=='hub-storage' else original(args,**kw))
    result=refine_preview.render(c,s,r.draft(c,s),'character','checker',d/'preview')
    assert result['frames']==3 and result['state']=='ready'
    assert media.probe(store.path(result['asset']))['frames']==3


def test_preview_cache_ignores_next_seed_and_review_metadata(scene,monkeypatch):
    from h3workbench import refine_preview
    c,s,d=scene
    monkeypatch.setattr(refine_preview,'MEDIA',d)
    monkeypatch.setattr(refine_preview,'PENDING',{})
    from types import SimpleNamespace
    submitted=[]
    monkeypatch.setattr(refine_preview,'POOL',SimpleNamespace(submit=lambda fn:submitted.append(fn)))
    refine_preview.prepare(c,s,'character','checker')
    s['refine']['character'].update(alpha_seed=123456,reviewed=False,face_proof='new-proof')
    refine_preview.prepare(c,s,'character','checker')
    assert len(submitted)==1
    s['refine']['character']['face']['feather_px']+=1
    refine_preview.prepare(c,s,'character','checker')
    assert len(submitted)==2


@pytest.mark.parametrize('view', ['checker', 'alpha', 'black', 'white', 'plate'])
def test_missing_alpha_rejects_before_decode_or_preview_queue(scene, monkeypatch, view):
    from h3workbench import refine_preview
    c, s, _ = scene
    s['refine']['source']['alpha'] = None
    def unexpected(*args, **kwargs):
        pytest.fail('Missing alpha must be rejected before expensive work')
    monkeypatch.setattr(r, 'decode', unexpected)
    monkeypatch.setattr(refine_preview.POOL, 'submit', unexpected)
    with pytest.raises(ValueError, match='AlphaGen extraction is needed'):
        r.frame_image(c, s, 'source', 0, view)
    with pytest.raises(ValueError, match='AlphaGen extraction is needed'):
        refine_preview.prepare(c, s, 'source', view)
    # Mask and original previews remain valid without AlphaGen.
    r.validate_view(s, s['refine'], 'source', 'sam')
    r.validate_view(s, s['refine'], 'source', 'rgb')


def test_compose_and_backplate_do_not_require_review_flags(scene):
    c, s, _ = scene
    x = s['refine']
    x.update(plate_reviewed=False, anchor_reviewed=False, anchor=c['source'])
    s['mask_approved'] = False
    for side in r.SIDES:
        x[side].update(reviewed=False, review_key=None)
    r.validate_job('refine_compose', c, s, {})
    r.validate_job('backplate', c, s, {})
    assert not x['plate_reviewed'] and not x['source']['reviewed']
    x['source']['alpha'] = None
    with pytest.raises(ValueError, match='Complete the source'):
        r.validate_job('refine_compose', c, s, {})


@pytest.mark.parametrize('side', ['source', 'character'])
@pytest.mark.parametrize('worker', ['arch', 'fey'])
def test_alpha_defaults_to_offloading_without_previous_failure(scene, monkeypatch, side, worker):
    c, s, d = scene
    # Exercise legacy dispatch policy with a synthetic graph, without shipping
    # the retired AlphaGen workflow as a public runtime dependency.
    (d / 'templates').mkdir(exist_ok=True)
    (d / 'templates/alpha.json').write_text(json.dumps({
        '7': {'inputs': {'file': ''}}, '15': {'inputs': {}},
        '17': {'inputs': {}}, 'seed': {'inputs': {'noise_seed': 0}},
    }))
    monkeypatch.setattr(refine_jobs, 'ROOT', d)
    s['refine'][side]['alpha_seed'] = 9876
    job = {'id': 'default-alpha', 'data': {'worker': worker, 'options': {'side': side}}}
    (d / 'input.mkv').write_bytes(b'isolated configuration fixture')
    monkeypatch.setattr(media, 'shot_media', lambda *args: d / 'input.mkv')
    monkeypatch.setattr(media, 'ff', lambda *args: None)
    monkeypatch.setattr(media, 'frozen_file', lambda target, recipe, build: target)
    class Dispatched(Exception):
        pass
    def dispatch(actual_job, graph, files, directory, profile):
        assert actual_job is job and profile == 'alpha-offload'
        assert graph['15']['inputs']['width'] == 32
        assert graph['15']['inputs']['height'] == 32
        assert graph['15']['inputs']['length'] == 17
        assert graph['17']['inputs']['frames_number'] == 17
        seeds = [n['inputs']['noise_seed'] for n in graph.values() if 'noise_seed' in n['inputs']]
        assert seeds and all(seed == 9876 for seed in seeds)
        raise Dispatched()
    monkeypatch.setattr(refine_jobs.remote, 'execute', dispatch)
    with pytest.raises(Dispatched):
        refine_jobs.alpha(c, s, job, d, side)


def test_padding_preserves_soft_alpha_without_expanding_opaque_core():
    mask = np.zeros((32, 32), bool)
    mask[10:22, 10:22] = True
    raw = np.full(mask.shape, .4, np.float32)
    f = r.foreground()
    f['gate_px'] = 0
    tight = r.matte_frame(raw, mask, f, 0)
    f['gate_px'] = 4
    padded = r.matte_frame(raw, mask, f, 0)
    assert tight[15, 7] == 0 and padded[15, 7] == pytest.approx(.4)
    assert padded[15, 5] == 0
    assert np.array_equal(tight[mask], padded[mask])
    assert not r.sam_support(np.zeros_like(mask), 64).any()


def test_character_padding_defaults_are_versioned_and_freeze_in_jobs(scene):
    c, s, _ = scene
    original = copy.deepcopy(s['refine'])
    default = r.defaults()
    assert default['character_padding_px'] == 8
    with TestClient(app) as client:
        updated = client.patch('/api/refine-defaults', json={**default, 'character_padding_px': 12})
        assert updated.status_code == 200
        assert client.patch('/api/refine-defaults', json={**default, 'character_padding_px': 16}).status_code == 409
        assert client.patch('/api/refine-defaults', json={**updated.json(), 'character_padding_px': -1}).status_code == 400
    assert r.draft(c, s) == original
    store.clip_mutate(c['id'], lambda v: v['shots'][0].pop('refine'))
    fresh = store.clip_get(c['id'])
    assert r.draft(fresh, fresh['shots'][0])['character']['gate_px'] == 12
    job = pipeline.queue('refine_extract', c['id'], s['id'], 'fey', {'side': 'character'})
    r.set_defaults({**r.defaults(), 'character_padding_px': 20})
    saved = store.job_get(job)['data']
    assert r.draft(saved['snapshot'], saved['shot'])['character']['gate_px'] == 12
    assert 'refine' not in store.clip_get(c['id'])['shots'][0]


def test_character_alpha_reuses_corrected_sam_and_point_history(scene, monkeypatch):
    c, s, d = scene
    f = s['refine']['character']
    point = {'id': 'p', 'frame': 0, 'x': .5, 'y': .5, 'label': 1}
    f.update(mask_points=[point], mask_applied_points=[point], mask_base='original-sam-base', auto_face=False)
    def unexpected(*args, **kwargs):
        pytest.fail('A corrected SAM mask must not be regenerated for AlphaGen')
    monkeypatch.setattr(refine_jobs.remote, 'graph_mask', unexpected)
    monkeypatch.setattr(refine_jobs, 'alpha', lambda *args: {'alpha': 'new-alpha'})
    result = refine_jobs.extract(c, s, {'id': 'test', 'data': {'options': {'side': 'character'}}}, d)
    assert result['mask'] == f['mask'] and result['mask_base'] == f['mask_base']
    assert result['mask_applied_points'] == result['mask_points'] == [point]
    f['mask_applied_points'] = []
    with pytest.raises(ValueError, match='Apply the SAM point corrections'):
        r.validate_job('refine_extract', c, s, {'side': 'character'})


def test_sam_preview_padding_matches_support(scene):
    c, s, _ = scene
    s['refine']['character']['gate_px'] = 5
    raw = r.masks.read(s['refine']['character']['mask'], 3, c['dimensions'])[0]
    image = np.asarray(r.frame_image(c, s, 'character', 0, 'sam'))
    assert np.array_equal(image > 0, r.sam_support(raw, 5))
