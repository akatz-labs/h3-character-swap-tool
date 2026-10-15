"""Portable operation without workstation mounts or image-provider sign-in."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from h3workbench import anchors, store


@pytest.mark.parametrize("portable", [None, "1"])
def test_portable_local_import_with_no_shared_mount(tmp_path, portable):
    program = '''
import io,json,os,subprocess
from pathlib import Path
os.path.ismount=lambda _: False
from h3workbench import config,store,pipeline,anchors
from h3workbench.app import app
from fastapi.testclient import TestClient
store.init()
assert config.PORTABLE and config.MEDIA==config.DATA/'media'
assert config.WORKERS=={} and anchors.providers()['selected']=='manual'
assert config.PILOT==config.DATA/'legacy-imports/pilot'
pipeline.start=lambda: None
video=config.MEDIA/'source.mkv'
subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x64:rate=24','-frames:v','8','-c:v','ffv1',str(video)],check=True)
with TestClient(app) as client:
 batch=client.post('/api/batches',json={'name':'Portable local data'}).json()['id']
 response=client.post('/api/import',data={'batch_id':batch},files=[('files',('source.mkv',video.read_bytes(),'video/x-matroska'))])
 assert response.status_code==200,response.text
 result=response.json();assert len(result['clips'])==1 and not result['errors'],result
 clip=result['clips'][0];assert clip['frames']==8
 assert Path(store.asset(clip['source'])['path']).is_relative_to(config.MEDIA)
 from h3workbench import previews
 preview=previews.proxy(clip['render_source'],0,clip['frames'],clip['dimensions'])
 assert preview.is_file() and json.loads(preview.with_name('preview.json').read_text())
 previews.build_mask_playback(clip,clip['shots'][0],config.MEDIA/'portable-mask-playback')
 assert (config.MEDIA/'portable-mask-playback'/'manifest.json').is_file()
 assert client.get('/api/state').json()['anchor_providers']['selected']=='manual'
 queued=client.post('/api/jobs',json={'kind':'cuts','clip_id':clip['id'],'worker':'cpu'})
 assert queued.status_code==200,queued.text
 assert store.job_get(queued.json()['id'])['data']['worker']=='cpu'
 print(json.dumps({'frames':clip['frames'],'data':str(config.DATA),'media':str(config.MEDIA),'workers':config.WORKERS}))
'''
    env = {**os.environ, "H3_WORKBENCH_PORTABLE": "1", "H3_WORKBENCH_STATE": str(tmp_path / "state")}
    if portable is None:
        env.pop("H3_WORKBENCH_PORTABLE", None)
    for key in ["H3_WORKBENCH_MEDIA_MOUNT", "H3_WORKBENCH_SHARED_ROOT", "H3_WORKBENCH_MEDIA", "H3_WORKBENCH_WORKERS", "H3_WORKBENCH_LEGACY_PILOT"]:
        env.pop(key, None)
    result = subprocess.run([sys.executable, "-c", program], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["frames"] == 8


def test_unavailable_anchor_provider_preserves_existing_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    store.init()
    batch = store.batch_create("Provider test")
    original = store.clip_create(batch, {"id": "anchor-provider-test", "reference": "existing-reference",
        "shots": [{"id": "shot", "anchor": "existing-anchor", "anchor_approved": True, "renders": [{"id": "keep"}]}]})
    store.set_setting("anchor_provider", "manual")
    with pytest.raises(ValueError, match="Import an anchor"):
        anchors.generate(original, original["shots"][0], tmp_path)
    store.set_setting("anchor_provider", "codex-local")
    monkeypatch.setattr(anchors.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="not installed"):
        anchors.generate(original, original["shots"][0], tmp_path)
    assert store.clip_get(original["id"]) == original
    assert not (tmp_path / "anchor.png").exists()


def test_missing_media_mount_refuses_local_fallback(tmp_path):
    mount = tmp_path / "missing-drive"
    env = {**os.environ, "H3_WORKBENCH_STATE": str(tmp_path / "state"),
           "H3_WORKBENCH_MEDIA": str(mount / "media"), "H3_WORKBENCH_MEDIA_MOUNT": str(mount)}
    result = subprocess.run([sys.executable, "-c", "import h3workbench.config"],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "refusing local fallback" in result.stderr
    assert not mount.exists()
    assert not (tmp_path / "state").exists()


def test_remote_paths_use_configured_root(tmp_path, monkeypatch):
    from h3workbench import remote
    monkeypatch.setattr(remote, "SHARED_ROOT", tmp_path / "media")
    monkeypatch.setattr(remote, "WORKERS", {"example": {"shared": "/worker/media"}})
    assert remote.remote_path(tmp_path / "media/jobs/input.png", "example") == "/worker/media/jobs/input.png"
    with pytest.raises(ValueError):
        remote.remote_path(tmp_path / "outside/input.png", "example")
