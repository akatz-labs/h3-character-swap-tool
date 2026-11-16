import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from h3workbench import media, store, video_previews
from h3workbench.app import app


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'MEDIA', tmp_path)
    monkeypatch.setattr(video_previews, 'check_storage', lambda _: None)
    store.init()
    path = tmp_path / 'original.mp4'
    media.ff(['-f', 'lavfi', '-i', 'testsrc2=size=1080x1920:rate=24',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '1',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '12',
        '-c:a', 'aac', '-movflags', '+faststart', path])
    return store.asset_add(path), path


def ready(key):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = video_previews.prepare(key)
        if result['state'] != 'preparing':
            return result
        time.sleep(.02)
    pytest.fail('Video preview did not complete')


def test_video_preview_preserves_frames_audio_original_and_reuses_cache(source, monkeypatch):
    key, original = source
    digest = store.sha(original)
    real_ff = media.ff
    calls = []
    def encode(args):
        calls.append(args)
        return real_ff(args)
    monkeypatch.setattr(media, 'ff', encode)
    for _ in range(8):
        assert video_previews.prepare(key)['state'] in {'preparing', 'ready'}
    result = ready(key)
    assert result['state'] == 'ready', result
    assert len(calls) == 1
    preview = store.path(result['asset'])
    info = media.probe(preview)
    assert (info['width'], info['height'], info['frames'], info['fps']) == (540, 960, 24, 24)
    assert info['audio'] and abs(info['duration'] - 1) < .05
    assert preview.stat().st_size < original.stat().st_size
    metadata = json.loads(media.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name,pix_fmt', '-of', 'json', preview]))
    assert metadata['streams'][0] == {'codec_name': 'h264', 'pix_fmt': 'yuv420p'}
    data = preview.read_bytes()
    assert data.index(b'moov') < data.index(b'mdat')
    assert store.sha(original) == digest
    assert ready(key)['asset'] == result['asset'] and len(calls) == 1
    client = TestClient(app)
    response = client.get(f"/api/assets/{result['asset']}", headers={'range': 'bytes=0-99'})
    assert response.status_code == 206 and response.content == data[:100]
    download = client.get(f'/api/assets/{key}?download=true')
    assert 'attachment' in download.headers['content-disposition']
    assert hashlib.sha256(download.content).hexdigest() == digest


def test_preview_failure_needs_explicit_retry_and_request_validation(source, monkeypatch):
    key, _ = source
    real_ff = media.ff
    attempts = []
    def fail(args):
        attempts.append(args)
        raise ValueError('Fixture encoder failed')
    monkeypatch.setattr(media, 'ff', fail)
    assert ready(key)['state'] == 'failed'
    assert ready(key)['state'] == 'failed' and len(attempts) == 1
    monkeypatch.setattr(media, 'ff', real_ff)
    assert video_previews.prepare(key, retry=True)['state'] == 'preparing'
    assert ready(key)['state'] == 'ready'
    client = TestClient(app)
    for body in [{'retry': 'yes'}, {'size': 500}, {'retry': None}]:
        assert client.post(f'/api/assets/{key}/video-preview', json=body).status_code == 400
    image = store.MEDIA / 'image.png'
    from PIL import Image
    Image.new('RGB', (8, 8)).save(image)
    image_id = store.asset_add(image)
    assert client.post(f'/api/assets/{image_id}/video-preview', json={}).status_code == 400
