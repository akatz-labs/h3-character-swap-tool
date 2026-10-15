import copy
import pytest
from fastapi.testclient import TestClient
from h3workbench import store, pipeline, media
from h3workbench.app import app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "MEDIA", tmp_path)
    monkeypatch.setattr(pipeline, "start", lambda: None)
    store.init()
    batch = store.batch_create("Test")
    c = store.clip_create(
        batch,
        {
            "id": "test",
            "name": "Test",
            "source": "source",
            "reference": "reference",
            "frames": 24,
            "dimensions": [1152, 864],
            "audio": True,
            "character": "Test character",
            "cuts_approved": True,
            "result": "old-result",
            "shots": [
                {
                    "id": "s",
                    "start": 0,
                    "end": 24,
                    "source_frame": "frame",
                    "mode": "generate",
                    "speaker": "target",
                    "dialogue": "Hi",
                    "prompt": "original",
                    "seed": 1,
                    "expansion": 64,
                    "selection": "largest",
                    "subject_index": 0,
                    "anchor": "anchor",
                    "mask": "mask",
                    "mask_approved": True,
                    "anchor_approved": True,
                    "anchors": [{"asset": "anchor"}],
                    "renders": [{"id": "old", "asset": "old-result"}],
                    "selected_render": "old",
                }
            ],
        },
    )
    with TestClient(app) as client:
        yield client, c


def test_revision_conflict_preserves_other_editor(setup):
    client, c = setup
    first = client.patch(
        "/api/clips/test",
        json={"revision": c["revision"], "changes": {"name": "Changed elsewhere"}},
    )
    assert first.status_code == 200
    stale = client.patch(
        "/api/clips/test",
        json={"revision": c["revision"], "changes": {"name": "Overwrite"}},
    )
    assert stale.status_code == 409
    assert store.clip_get("test")["name"] == "Changed elsewhere"


def test_confirmed_running_cancel_uses_frozen_worker_without_host_name_assumptions(setup):
    client, _ = setup
    job=store.job_create('mask','test','s',{'worker':'local','execution_backend':'shared'})
    store.job_update(job,'running',execution_resource='local',shared_job_id='accepted-container-job')
    assert client.post(f'/api/jobs/{job}/cancel',json={}).status_code==400
    assert client.post(f'/api/jobs/{job}/cancel',json={'confirm_running':True}).status_code==200
    saved=store.job_get(job)
    assert saved['state']=='cancelling' and saved['data']['cancel_requested']
    assert saved['data']['shared_job_id']=='accepted-container-job'


def test_project_adapter_rename_rejects_stale_names_and_preserves_ids_and_contents(setup):
    client, clip = setup
    batch = clip["batch_id"]
    assert client.patch(f"/api/batches/{batch}", json={"name": "Renamed", "expected_name": "Test"}).status_code == 200
    assert client.patch(f"/api/batches/{batch}", json={"name": "Overwrite", "expected_name": "Test"}).status_code == 409
    assert next(b for b in store.batches() if b["id"] == batch)["name"] == "Renamed"
    assert store.clip_get("test") == clip


@pytest.mark.parametrize(
    "changes", [{"seed": 42}, {"prompt": "New prompt"}, {"expansion": 65}, {"render_mp": 0.5}]
)
def test_draft_edits_preserve_selected_generation_and_assembly(setup, changes):
    client, c = setup
    r = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": changes},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["result"] == "old-result"
    assert out["shots"][0]["selected_render"] == "old"
    assert out["shots"][0]["renders"][0]["asset"] == "old-result"


def test_unchanged_dialogue_does_not_lock_out_asr(setup):
    client, c = setup
    r = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"dialogue": "Hi"}},
    )
    assert not r.json()["shots"][0].get("dialogue_edited")
    assert r.json()["shots"][0]["selected_render"] == "old"


def test_mask_holes_cannot_be_approved(setup):
    client, c = setup
    c = store.clip_mutate(
        "test",
        lambda v: v["shots"][0].update(mask_approved=False, mask_empty_frames=[3, 4]),
    )
    r = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"mask_approved": True}},
    )
    assert r.status_code == 400
    assert not store.clip_get("test")["shots"][0]["mask_approved"]


def test_render_uses_selected_inputs_without_review_flags(setup):
    client, c = setup
    store.clip_mutate("test", lambda v: v["shots"][0].update(anchor_approved=False, mask_approved=False))
    r = client.post(
        "/api/jobs", json={"kind": "render", "clip_id": "test", "shot_id": "s"}
    )
    assert r.status_code == 200 and len(store.jobs()) == 1
    shot = store.clip_get("test")["shots"][0]
    assert not shot["anchor_approved"] and not shot["mask_approved"]


def test_running_work_cannot_be_cancelled_as_queued(setup):
    client, c = setup
    id = pipeline.queue("render", "test", "s")
    store.job_update(id, "running")
    assert client.post(f"/api/jobs/{id}/cancel").status_code == 400
    assert store.job_get(id)["state"] == "running"


def test_duplicate_click_does_not_queue_two_renders(setup):
    client, c = setup
    assert pipeline.queue("render", "test", "s") == pipeline.queue(
        "render", "test", "s"
    )
    assert len(store.jobs()) == 1


def test_queued_shared_observation_cancels_accepted_identity_without_claiming_terminal(setup, tmp_path):
    client, _ = setup
    id = pipeline.queue("render", "test", "s")
    stage = tmp_path / "accepted-stage"
    stage.mkdir()
    (stage / "shared-binding.json").write_text("{}")
    (stage / "shared-request.json").write_text("{}")
    store.job_update(id, execution_backend="shared", shared_stages=[str(stage)])
    response = client.post(f"/api/jobs/{id}/cancel")
    assert response.status_code == 200
    saved = store.job_get(id)
    assert saved["state"] == "queued" and saved["data"]["cancel_requested"]
    assert store.claim_job(id)  # resume/cancel observation, never a new identity
    assert (stage / "shared-request.json").read_text() == "{}"


def test_verified_shared_completion_wins_finalization_cancel_race(setup):
    _, _ = setup
    id = pipeline.queue("render", "test", "s")
    store.job_update(id, "running", execution_backend="shared", shared_phase="completed", cancel_requested=True)
    pipeline.check_finalization_cancel(store.job_get(id))
    assert store.job_get(id)["data"]["completion_won_cancellation"]
    store.job_update(id, shared_phase="rendering", completion_won_cancellation=False)
    with pytest.raises(pipeline.remote.Cancelled):
        pipeline.check_finalization_cancel(store.job_get(id))


def test_cross_origin_write_rejected(setup):
    client, c = setup
    r = client.post(
        "/api/batches",
        json={"name": "bad"},
        headers={"Origin": "https://outside.example"},
    )
    assert r.status_code == 403


def test_changed_shot_does_not_receive_stale_anchor(setup):
    client, c = setup
    id = pipeline.queue("anchor", "test", "s")
    j = store.job_get(id)
    store.clip_mutate("test", lambda v: v["shots"][0].update(start=5))
    received = []
    pipeline.attach(j, lambda c, s, stable: received.append(stable))
    assert received == [False]


def test_anchor_extra_instructions_keep_scene_guidance():
    from h3workbench.anchors import anchor_brief

    instructions = "Replace the man on the left in the striped shirt. Keep the man on the right."
    brief = anchor_brief(
        {"character": "White-haired elf"},
        {"selection": "largest", "anchor_instructions": instructions},
    )
    assert instructions in brief
    assert "White-haired elf" in brief
    assert "Match the exact source pose" in brief
    assert "Preserve all other people" in brief
    assert "Replace ONLY the person identified in the extra instructions" in brief
    assert "Replace ONLY the selected person (largest person)" not in brief
    custom = {"anchor_prompt": "A legacy custom base prompt."}
    assert anchor_brief({}, custom) == custom["anchor_prompt"]
    assert anchor_brief({}, {**custom, "anchor_instructions": instructions}).startswith(custom["anchor_prompt"])
    assert "leftmost person" in anchor_brief({}, {"selection": "leftmost"})


@pytest.mark.parametrize("value", [None, 12, [], "x" * 20001])
def test_anchor_instructions_validate_text(setup, value):
    client, c = setup
    response = client.patch("/api/clips/test/shots/s", json={
        "revision": c["revision"], "changes": {"anchor_instructions": value}
    })
    assert response.status_code == 400
    assert store.clip_get("test")["revision"] == c["revision"]


def test_anchor_instructions_frozen_for_batch_and_preserve_newer_selection(setup, monkeypatch, tmp_path):
    from h3workbench import anchors

    client, c = setup
    response = client.patch("/api/clips/test/shots/s", json={
        "revision": c["revision"], "changes": {"anchor_instructions": "Man on the left"}
    })
    assert response.status_code == 200
    assert response.json()["shots"][0]["anchor"] == "anchor"
    response = client.post(f"/api/batches/{c['batch_id']}/queue", json={
        "kind": "anchor", "worker": "fey", "missing_only": False
    })
    id = response.json()["jobs"][0]
    assert store.job_get(id)["data"]["shot"]["anchor_instructions"] == "Man on the left"
    store.clip_mutate("test", lambda v: v["shots"][0].update(anchor_instructions="Man on the right"))
    (tmp_path / "jobs").mkdir()
    monkeypatch.setattr(pipeline, "MEDIA", tmp_path)
    monkeypatch.setattr(media, "run", lambda *a, **k: "")
    def generate(clip, shot, directory):
        assert shot["anchor_instructions"] == "Man on the left"
        return {"asset": "new-anchor", "prompt": anchors.anchor_brief(clip, shot)}
    monkeypatch.setattr(anchors, "generate", generate)
    pipeline.execute(id)
    assert store.job_get(id)["state"] == "success"
    shot = store.clip_get("test")["shots"][0]
    assert shot["anchor"] == "anchor"
    assert shot["anchors"][-1]["asset"] == "new-anchor"
    assert "Man on the left" in shot["anchors"][-1]["prompt"]
    assert shot["anchor_instructions"] == "Man on the right"


def test_dialogue_uses_word_midpoint_at_cut():
    c = {
        "transcript": {
            "words": [
                {"word": " one", "start": 0, "end": 0.4},
                {"word": " two", "start": 0.4, "end": 0.6},
                {"word": " three", "start": 0.7, "end": 0.9},
            ]
        }
    }
    assert media.dialogue(c, 0, 12) == "one"
    assert media.dialogue(c, 12, 24) == "two three"


def test_canvas_matches_verified_pilot():
    assert media.canvas(896, 672) == [1152, 864]
    assert media.canvas(640, 928) == [832, 1184]


def test_custom_prompt_survives_a_save(setup):
    client, c = setup
    r = client.patch(
        "/api/clips/test/shots/s",
        json={
            "revision": c["revision"],
            "changes": {"prompt": "Custom dialogue and performance"},
        },
    )
    assert r.status_code == 200 and r.json()["shots"][0]["prompt_edited"]


def test_import_master_preserves_decoded_source(setup, tmp_path):
    client, c = setup
    source = tmp_path / "original.mkv"
    media.ff(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=24",
            "-frames:v",
            "5",
            "-c:v",
            "ffv1",
            source,
        ]
    )
    info = media.normalize(source, tmp_path / "source.mp4")
    master = store.path(info["render_source"])
    assert master.name == "source-master.mkv" and media.probe(master)["frames"] == 5

    def pixels(p):
        return media.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                p,
                "-map",
                "0:v",
                "-f",
                "hash",
                "-hash",
                "sha256",
                "-",
            ]
        )

    assert pixels(source) == pixels(master)


def test_silent_sources_get_silent_conditioning_without_extra_frames(setup, tmp_path):
    import subprocess

    client, c = setup
    source = tmp_path / "silent.mkv"
    media.ff(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=24",
            "-frames:v",
            "5",
            "-c:v",
            "ffv1",
            source,
        ]
    )
    id = store.asset_add(source)
    targetdir = tmp_path / "shot"
    targetdir.mkdir()
    out = media.shot_media(
        {"source": id, "dimensions": [320, 240], "audio": False},
        {"start": 1, "end": 4},
        targetdir,
    )
    info = media.probe(out)
    assert info["frames"] == 3 and info["audio"]
    pcm = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:a:0", "-f", "s16le", "-"]
    )
    assert pcm and not any(pcm)


def test_late_manifest_write_does_not_roll_back_current_revision(setup):
    client, old = setup
    new = store.clip_mutate("test", lambda c: c.update(name="Newer"))
    store.persist_manifest(old)
    import json

    root = store.MEDIA / "batches" / old["batch_id"] / "test"
    assert (
        json.loads((root / "manifest.json").read_text())["revision"] == new["revision"]
    )
    assert (root / f"revision-{old['revision']:06}.json").exists()


def test_scene_detection_returns_edit_frame_indices(setup, tmp_path):
    client, c = setup
    source = tmp_path / "cuts.mkv"
    media.ff(
        [
            "-f",
            "lavfi",
            "-i",
            "color=black:size=64x64:rate=24",
            "-vf",
            "drawbox=color=white:t=fill:enable='between(n,2,3)'",
            "-frames:v",
            "6",
            "-c:v",
            "ffv1",
            source,
        ]
    )
    assert media.detect_cuts({"source": store.asset_add(source), "frames": 6}) == [2, 4]


def test_loaded_comfy_runtime_is_busy_even_with_empty_compute_list(monkeypatch):
    import json
    from h3workbench import remote

    monkeypatch.setattr(
        remote,
        "ssh",
        lambda *a, **kw: json.dumps(
            {"runtimes": [123], "compute": "", "gpu": "RTX 4090"}
        ),
    )
    assert remote.status("fey", owned=False)["state"] == "busy"


def test_switching_saved_render_invalidates_assembly_without_changing_draft(setup):
    client, _ = setup
    c = store.clip_mutate(
        "test",
        lambda v: v["shots"][0]["renders"].append(
            {
                "id": "second",
                "asset": "second-result",
                "seed": 99,
                "prompt": "Historical prompt",
            }
        ),
    )
    r = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"selected_render": "second"}},
    )
    out = r.json()
    assert out["result"] is None
    assert out["shots"][0]["seed"] == 1
    assert out["shots"][0]["prompt"] == "original"
    assert len(out["shots"][0]["renders"]) == 2
    details = client.get("/api/clips/test/shots/s/renders/second").json()
    assert details["seed"] == 99 and details["prompt"] == "Historical prompt"
    assert client.get("/api/clips/test/shots/s/renders/missing").status_code == 404


def test_reselecting_same_render_preserves_assembly(setup):
    client, c = setup
    r = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"selected_render": "old"}},
    )
    assert r.json()["result"] == "old-result"


def test_video_thumbnail_is_cached_and_rejects_other_assets(
    setup, tmp_path, monkeypatch
):
    import importlib

    api_module = importlib.import_module("h3workbench.app")
    monkeypatch.setattr(api_module, "MEDIA", tmp_path)
    client, _ = setup
    source = tmp_path / "thumbnail-video.mp4"
    media.ff(
        ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24", "-frames:v", "2", source]
    )
    id = store.asset_add(source)
    first = client.get(f"/api/assets/{id}/thumbnail")
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"
    monkeypatch.setattr(
        media, "ff", lambda *args: pytest.fail("Cached thumbnail must not decode again")
    )
    assert client.get(f"/api/assets/{id}/thumbnail").content == first.content
    text = tmp_path / "text.txt"
    text.write_text("not a video")
    assert (
        client.get(f"/api/assets/{store.asset_add(text)}/thumbnail").status_code == 400
    )


def mask_fixture(c, tmp_path):
    import numpy as np

    mask = np.zeros((24, 32, 32), dtype=bool)
    mask[:, 8:24, 8:24] = True
    path = tmp_path / "original-mask.npz"
    np.savez_compressed(path, mask=mask)
    aid = store.asset_add(path)

    def edit(v):
        v["dimensions"] = [32, 32]
        v["shots"][0]["mask"] = aid

    return store.clip_mutate(c["id"], edit), mask


def point(frame=6, label=1, **kw):
    return {
        "id": f"p-{frame}-{label}",
        "frame": frame,
        "x": 0.5,
        "y": 0.5,
        "label": label,
        **kw,
    }


def test_point_edits_require_application_and_preserve_existing_outputs(setup, tmp_path):
    from h3workbench import masks

    client, c = setup
    c, original = mask_fixture(c, tmp_path)
    response = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"mask_points": [point()]}},
    )
    assert response.status_code == 200
    c = response.json()
    s = c["shots"][0]
    assert s["selected_render"] == "old" and c["result"] == "old-result"
    assert not s["mask_approved"] and len(s["mask_versions"]) == 1
    assert masks.pending_frames(s) == [6]
    assert (
        client.patch(
            "/api/clips/test/shots/s",
            json={"revision": c["revision"], "changes": {"mask_approved": True}},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/jobs", json={"kind": "render", "clip_id": "test", "shot_id": "s"}
        ).status_code
        == 400
    )
    r = client.post(
        "/api/jobs",
        json={
            "kind": "mask_edit",
            "clip_id": "test",
            "shot_id": "s",
            "options": {"mode": "frame", "frame": 6},
        },
    )
    assert r.status_code == 200
    job = store.job_get(r.json()["id"])
    assert job["data"]["shot"]["mask_points"] == [point()]
    assert pipeline.resource(job) == "fey"


@pytest.mark.parametrize(
    "bad",
    [
        point(frame=24),
        point(label=2),
        point(x=-0.1),
        point(y=1.1),
        {**point(), "unknown": 1},
    ],
)
def test_invalid_points_do_not_modify_clip(setup, tmp_path, bad):
    client, c = setup
    c, _ = mask_fixture(c, tmp_path)
    response = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"mask_points": [bad]}},
    )
    assert response.status_code == 400
    assert store.clip_get("test")["revision"] == c["revision"]


def test_frame_correction_changes_only_requested_frame_and_records_history(
    setup, tmp_path
):
    import numpy as np
    from h3workbench import masks

    client, c = setup
    c, original = mask_fixture(c, tmp_path)
    s = c["shots"][0]
    s["mask_points"] = [point(6), point(18)]
    job = {"id": "edit-test", "data": {"options": {"mode": "frame", "frame": 6}}}
    out = tmp_path / "runtime"
    (out / "output").mkdir(parents=True)
    replacement = np.ones((1, 32, 32), dtype=bool)
    np.savez_compressed(out / "output/edit.npz", mask=replacement)
    result = masks.finish(
        c,
        s,
        job,
        tmp_path,
        out,
        {"segments": [{"file": "edit.npz", "start": 6, "end": 7}], "derived": []},
    )
    with np.load(store.path(result["asset"])) as z:
        actual = z["mask"]
    assert np.array_equal(actual[:6], original[:6]) and np.array_equal(
        actual[7:], original[7:]
    )
    assert actual[6].all()
    masks.install(s, result, "Edited frame 6", result["points"])
    assert masks.pending_frames(s) == [18] and len(s["mask_versions"]) == 2
    assert s["mask_base"] == s["mask_versions"][0]["asset"]
    masks.select(s, s["mask_base"], c["dimensions"])
    assert s["mask_points"] == [] and s["selected_render"] == "old"
    with np.load(store.path(s["mask"])) as z:
        assert np.array_equal(z["mask"], original)


def test_reverse_head_merges_in_original_frame_order(setup, tmp_path):
    import numpy as np
    from h3workbench import masks

    _, c = setup
    c, _ = mask_fixture(c, tmp_path)
    s = c["shots"][0]
    out = tmp_path / "runtime"
    (out / "output").mkdir(parents=True)
    expected = np.zeros((24, 32, 32), dtype=bool)
    for f in range(24):
        expected[f, f, 0] = True
    np.savez_compressed(out / "output/head.npz", mask=expected[:7][::-1])
    np.savez_compressed(out / "output/tail.npz", mask=expected[6:])
    plan = {
        "segments": [
            {"file": "head.npz", "start": 0, "end": 6, "reverse": True, "frames": 7},
            {"file": "tail.npz", "start": 6, "end": 24},
        ],
        "derived": [],
    }
    result = masks.finish(
        c,
        s,
        {"id": "test", "data": {"options": {"mode": "shot", "frame": 6}}},
        tmp_path,
        out,
        plan,
    )
    with np.load(store.path(result["asset"])) as z:
        assert np.array_equal(z["mask"], expected)


def test_point_graph_uses_points_without_text_union_and_preserves_raw_clicks(
    setup, tmp_path, monkeypatch
):
    from h3workbench import masks

    _, c = setup
    c, _ = mask_fixture(c, tmp_path)
    s = c["shots"][0]
    s["mask_points"] = [point(6), point(18, 0, x=0.1, y=0.1)]
    monkeypatch.setattr(media, "shot_media", lambda *args: tmp_path / "source.mkv")
    monkeypatch.setattr(media, "ff", lambda args: None)
    g, files, plan = masks.graph(
        c,
        s,
        {"id": "test", "data": {"options": {"mode": "shot", "frame": 6}}},
        tmp_path,
    )
    nodes = [n for n in g.values() if n["class_type"] == "H3CharacterSwapRefineMask"]
    assert len(nodes) == 2 and all("conditioning" not in n["inputs"] for n in nodes)
    assert len(plan["derived"]) == 1 and plan["derived"][0]["frame"] == 18
    assert s["mask_points"][1]["label"] == 0 and len(s["mask_points"]) == 2
    assert sorted((p["start"], p["end"]) for p in plan["segments"]) == [
        (0, 6),
        (6, 18),
        (18, 24),
    ]


def test_stale_mask_result_is_kept_as_history_without_changing_selection(
    setup, tmp_path
):
    from h3workbench import masks

    _, c = setup
    c, _ = mask_fixture(c, tmp_path)
    s = c["shots"][0]
    old = s["mask"]
    result = {"asset": "older-job-mask", "coverage": [1] * 24, "empty_frames": []}
    masks.install(s, result, "Historical edit", [point()], current=False)
    assert s["mask"] == old and s["mask_versions"][-1]["asset"] == "older-job-mask"


def test_shot_draft_save_and_confirmation_preserve_artifacts(setup, monkeypatch, tmp_path):
    client, original = setup
    monkeypatch.setattr(media, 'MEDIA', tmp_path)
    monkeypatch.setattr(store, 'path', lambda asset: tmp_path / asset)
    monkeypatch.setattr(media, 'frame', lambda *args: 'new-frame')
    # Confirming existing shots must retain selected renders and assembled output.
    response = client.post('/api/clips/test/cuts', json={
        'revision': original['revision'], 'cuts': [], 'modes': ['generate'], 'reviewed': True})
    assert response.status_code == 200
    confirmed = store.clip_get('test')
    assert confirmed['shots'] == original['shots']
    assert confirmed['result'] == 'old-result' and confirmed['cuts_approved']
    # Saving a draft never changes current shots, their approval or assembly.
    response = client.post('/api/clips/test/cuts', json={
        'revision': confirmed['revision'], 'cuts': [], 'modes': ['preserve'], 'reviewed': False})
    assert response.status_code == 200
    changed = store.clip_get('test')
    assert changed['result'] == 'old-result' and changed['cuts_approved']
    assert changed['shots'] == original['shots']
    assert changed['shot_layout_draft'] == {'cuts': [], 'modes': ['preserve']}
    response = client.post('/api/clips/test/cuts', json={
        'revision': changed['revision'], 'cuts': [12], 'modes': ['preserve', 'generate'], 'reviewed': True})
    assert response.status_code == 200
    split = store.clip_get('test')
    assert [(s['start'], s['end'], s['mode']) for s in split['shots']] == [(0, 12, 'preserve'), (12, 24, 'generate')]
    assert all(s['id'] != 's' and not s['renders'] for s in split['shots'])
    assert split['cuts_approved'] and split['result'] is None
    assert 'shot_layout_draft' not in split
    assert split['shot_layout_history'][0]['shots'] == original['shots']
    assert split['shot_layout_history'][0]['result'] == 'old-result'
    assert 'shot_layout_history' not in original
    # A stale browser must not overwrite this split.
    stale = client.post('/api/clips/test/cuts', json={
        'revision': changed['revision'], 'cuts': [], 'modes': ['generate']})
    assert stale.status_code == 409
    assert store.clip_get('test')['shots'] == split['shots']


@pytest.mark.parametrize('cuts,modes', [([0], None), ([24], None), ([1.5], None), ([True], None),
                                       ('12', None), ([], []), ([12], ['generate']), ([], ['bad'])])
def test_shot_boundaries_reject_invalid_edits(setup, cuts, modes):
    client, c = setup
    response = client.post('/api/clips/test/cuts', json={'revision': c['revision'], 'cuts': cuts, 'modes': modes})
    assert response.status_code == 400
    assert store.clip_get('test')['revision'] == c['revision']


def test_padding_preserves_coverage_review_and_queued_snapshot(setup):
    client, c = setup
    r = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": c["revision"], "changes": {"expansion": 12}},
    )
    assert r.status_code == 200
    updated = r.json()
    s = updated["shots"][0]
    assert s["mask_approved"] and s["mask"] == "mask" and s["expansion"] == 12
    queued = client.post(
        "/api/jobs",
        json={"kind": "render", "clip_id": "test", "shot_id": "s", "worker": "fey"},
    )
    assert queued.status_code == 200
    job = store.job_get(queued.json()["id"])
    assert job["data"]["shot"]["expansion"] == 12
    assert job["data"]["shot"]["mask"] == "mask"
    points = [{"id": "point", "frame": 0, "x": 0.5, "y": 0.5, "label": 1}]
    changed = client.patch(
        "/api/clips/test/shots/s",
        json={"revision": updated["revision"], "changes": {"mask_points": points}},
    )
    assert changed.status_code == 200
    assert not changed.json()["shots"][0]["mask_approved"]


def test_import_creates_active_full_length_shot(setup, monkeypatch, tmp_path):
    _, original = setup
    monkeypatch.setattr(media, 'MEDIA', tmp_path)
    source = tmp_path / 'import.mp4'
    source.write_bytes(b'video')
    def normalize(src, out):
        out.write_bytes(b'normalized')
        return {'width': 1920, 'height': 1080, 'frames': 459,
                'fps': 24, 'duration': 19.125, 'audio': True}
    monkeypatch.setattr(media, 'normalize', normalize)
    monkeypatch.setattr(media, 'frame', lambda *args: 'poster')
    clip = media.import_clip(original['batch_id'], source)
    assert clip['cuts_approved']
    assert [(s['start'], s['end']) for s in clip['shots']] == [(0, 459)]
    assert not clip.get('shot_layout_history')


def test_existing_unconfirmed_shot_accepts_mask_preview_and_tracking(setup):
    client, c = setup
    store.clip_mutate(c['id'], lambda v: v.update(cuts_approved=False))
    for mode in ['frame', 'shot']:
        response = client.post('/api/jobs', json={'kind': 'mask_edit',
            'clip_id': c['id'], 'shot_id': 's', 'worker': 'fey',
            'options': {'mode': mode, 'frame': 0}})
        assert response.status_code == 200, response.text
        store.job_update(response.json()['id'], state='success')


def test_confirm_layout_preserves_unchanged_shots_and_blocks_active_jobs(setup, monkeypatch, tmp_path):
    client, c = setup
    monkeypatch.setattr(media, 'MEDIA', tmp_path)
    monkeypatch.setattr(store, 'path', lambda asset: tmp_path / asset)
    monkeypatch.setattr(media, 'frame', lambda *args: 'new-frame')
    job = store.job_create('mask_edit', c['id'], 's', {'worker': 'fey'})
    payload = {'revision': c['revision'], 'cuts': [12], 'reviewed': True}
    assert client.post('/api/clips/test/cuts', json=payload).status_code == 409
    assert store.clip_get('test')['shots'] == c['shots']
    store.job_update(job, state='success')
    response = client.post('/api/clips/test/cuts', json={
        'revision': c['revision'], 'cuts': [], 'modes': ['preserve'], 'reviewed': True})
    assert response.status_code == 200
    changed = response.json()
    assert changed['shots'][0]['id'] == 's'
    assert changed['shots'][0]['renders'] == c['shots'][0]['renders']
    assert changed['shot_layout_history'][0]['shots'] == c['shots']
