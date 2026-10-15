import copy
import numpy as np
from h3workbench import masks, pipeline, refine_jobs, store
from test_refinement import scene


def point(f, x=0.5):
    return {"id": f"p{f}-{x}", "frame": f, "x": x, "y": 0.5, "label": 1}


def job_result(c, s, f, tmp_path):
    s["refine"]["source"]["mask_points"].append(point(f))
    job = {
        "id": f"job-{f}",
        "kind": "refine_mask",
        "clip_id": c["id"],
        "shot_id": s["id"],
        "data": {
            "snapshot": copy.deepcopy(c),
            "shot": copy.deepcopy(s),
            "options": {"mode": "frame", "side": "source", "frame": f},
        },
    }
    arr = masks.read(s["refine"]["source"]["mask"], 3, c["dimensions"])
    arr[f, 2:5, 2:5] = True
    p = tmp_path / f"result-{f}.npz"
    np.savez_compressed(p, mask=arr)
    return job, {
        "side": "source",
        "mask": store.asset_add(p),
        "mask_base": s["refine"]["source"]["mask_base"],
        "mask_proof": store.asset_add(p),
        "mask_applied_points": [point(f)],
        **masks.coverage(arr),
    }


def test_frame_burst_merges_and_preserves_later_drafts(scene, tmp_path, monkeypatch):
    c, s, _ = scene
    monkeypatch.setattr(pipeline.remote, "check_cancel", lambda j: None)
    monkeypatch.setattr(store, "job_update", lambda *a, **kw: None)
    jobs = [job_result(c, s, f, tmp_path) for f in range(3)]
    s["refine"]["source"]["gate_px"] = 11
    store.clip_mutate(c["id"], lambda v: v.update(shots=copy.deepcopy(c["shots"])))
    for i, (job, result) in enumerate(jobs):
        d = tmp_path / f"merge{i}"
        d.mkdir()
        refine_jobs.attach_frame(job, result, d)
        saved = store.clip_get(c["id"])["shots"][0]["refine"]["source"]
        arr = masks.read(saved["mask"], 3, c["dimensions"])
        for f in range(i + 1):
            assert arr[f, 2:5, 2:5].all()
        assert saved["mask_points"] == [point(f) for f in range(3)]
        assert saved["gate_px"] == 11
        assert len(saved["mask_applied_points"]) == i + 1
        assert result["attachment"] == "merged"


def test_newer_same_frame_points_keep_old_result_in_history(
    scene, tmp_path, monkeypatch
):
    c, s, _ = scene
    monkeypatch.setattr(pipeline.remote, "check_cancel", lambda j: None)
    monkeypatch.setattr(store, "job_update", lambda *a, **kw: None)
    job, result = job_result(c, s, 0, tmp_path)
    old = s["refine"]["source"]["mask"]
    s["refine"]["source"]["mask_points"].append(point(0, 0.7))
    store.clip_mutate(c["id"], lambda v: v.update(shots=copy.deepcopy(c["shots"])))
    refine_jobs.attach_frame(job, result, tmp_path)
    saved = store.clip_get(c["id"])["shots"][0]
    assert saved["refine"]["source"]["mask"] == old
    assert len(saved["refine"]["source"]["mask_points"]) == 2
    assert saved["foreground_versions"][-1]["job"] == job["id"]
    assert result["attachment"] == "history_only_newer_frame_edits"


def test_edit_save_rebases_across_mask_completion_but_rejects_setting_conflict(scene):
    c, s, _ = scene
    from fastapi.testclient import TestClient
    from h3workbench.app import app

    client = TestClient(app)
    current = store.clip_get(c["id"])
    base = copy.deepcopy(current["shots"][0]["refine"])
    store.clip_mutate(
        c["id"], lambda v: v["shots"][0]["refine"]["source"].update(coverage=[7, 8, 9])
    )
    url = f"/api/clips/{c['id']}/shots/{s['id']}/refine"
    response = client.patch(
        url,
        json={
            "revision": current["revision"],
            "base": base,
            "changes": {"source": {"mask_points": [point(1)]}},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["shots"][0]["refine"]["source"]["coverage"] == [7, 8, 9]
    conflict = client.patch(
        url,
        json={
            "revision": current["revision"],
            "base": base,
            "changes": {"source": {"mask_points": [point(2)]}},
        },
    )
    assert conflict.status_code == 409


def test_frame_requests_are_independent_and_exact_duplicates_reuse(scene):
    c, s, _ = scene
    a = {
        "options": {"mode": "frame", "side": "source", "frame": 0},
        "frame_request": "a",
    }
    first = store.job_create("refine_mask", c["id"], s["id"], a)
    assert store.job_create("refine_mask", c["id"], s["id"], a) == first
    assert (
        store.job_create(
            "refine_mask", c["id"], s["id"], {**a, "frame_request": "new-points"}
        )
        != first
    )
    assert (
        store.job_create(
            "refine_mask",
            c["id"],
            s["id"],
            {"options": {**a["options"], "frame": 1}, "frame_request": "b"},
        )
        != first
    )


def test_frame_graph_loads_one_exact_image_and_uses_unique_output(scene, monkeypatch):
    c, s, d = scene
    s["mask_points"] = [point(1)]
    calls = []
    monkeypatch.setattr(masks.media, "ff", lambda args: calls.append(args))
    job = {"id": "frame-unique", "data": {"options": {"mode": "frame", "frame": 1}}}
    graph, files, plan = masks.graph(c, s, job, d)
    assert "30" not in graph
    assert graph["31"]["class_type"] == "LoadImage"
    assert graph["100"]["inputs"]["batch_index"] == 0
    assert graph["101"]["inputs"]["frame"] == 1
    assert graph["33"]["inputs"]["ckpt_name"] == "sam3.1_multiplex_fp16.safetensors"
    assert set(files) == {"frame-unique-frame.png", "frame-unique-prior.npz"}
    assert plan["segments"] == [
        {"file": "frame-unique-edit-0001.npz", "start": 1, "end": 2}
    ]
    assert "select=eq(n\\,1)" in calls[0][calls[0].index("-vf") + 1]
