"""Credential privacy and native guide-job integration, without paid requests."""
import base64
from email.parser import BytesParser
from email.policy import default
import io
import json
import stat

import httpx
from PIL import Image
import pytest
from h3workbench import guides, local_openai, store
from test_guides import scene, add, edit, upload

FAKE_KEY = 'synthetic-key-for-local-tests-only'
ROUTE = '/api/local-settings/openai'


@pytest.fixture
def local_scene(scene, monkeypatch):
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'local-openai')
    return scene


def test_write_only_credentials_replace_delete_and_permissions(local_scene):
    client, _, root = local_scene
    assert client.get(ROUTE).json() == {'configured': False}
    for key in (FAKE_KEY, FAKE_KEY + '-replacement'):
        r = client.put(ROUTE, json={'api_key': key})
        assert r.json() == {'configured': True}
        assert r.headers['cache-control'] == 'no-store'
        assert local_openai.secret() == key
        assert stat.S_IMODE(local_openai.key_path().stat().st_mode) == 0o600
        assert stat.S_IMODE(local_openai.key_path().parent.stat().st_mode) == 0o700
        for path in (ROUTE, '/api/guide-provider', '/api/state'):
            response = client.get(path)
            assert key not in response.text
        assert key.encode() not in (root / 'workbench.sqlite').read_bytes()
    assert client.delete(ROUTE).json() == {'configured': False}
    assert not local_openai.key_path().exists()
    assert not client.get('/api/guide-provider').json()['configured']


def test_credentials_reject_cross_origin_and_do_not_echo_invalid_bodies(local_scene, monkeypatch):
    client, _, _ = local_scene
    assert client.put(ROUTE, json={'api_key': FAKE_KEY}, headers={'origin': 'https://foreign.invalid'}).status_code == 403
    for body in ({'api_key': FAKE_KEY, 'extra': 1}, {'api_key': [FAKE_KEY]}, {'api_key': FAKE_KEY + '\ninside'}):
        response = client.put(ROUTE, json=body)
        assert response.status_code == 400
        assert FAKE_KEY not in response.text
    assert client.put(ROUTE, content=b'x' * 9000).status_code == 413
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'private-openai')
    for method in ('get', 'put', 'delete'):
        assert getattr(client, method)(ROUTE).status_code == 404


@pytest.mark.parametrize('reference_count', [1, 8])
@pytest.mark.parametrize('quality', ['low', 'medium', 'high'])
def test_local_guide_uses_ordered_images_and_never_reposts_saved_output(local_scene, monkeypatch, quality, reference_count):
    client, _, root = local_scene
    client.put(ROUTE, json={'api_key': FAKE_KEY})
    marker = add(client)
    for _ in range(reference_count):
        upload(client, marker, 'reference')
    edit(client, marker, {'prompt': 'Green jacket', 'quality': quality})
    result = client.post('/api/clips/c/shots/s/guides/' + marker + '/generate', json={'revision': store.clip_get('c')['revision']})
    assert result.status_code == 200, result.text
    job = store.job_get(result.json()['id'])
    assert job['data']['options']['provider'] == 'local-openai'
    generated = io.BytesIO(); Image.new('RGB', (96, 64), 'green').save(generated, format='PNG')
    calls = []
    def handle(request):
        calls.append(request)
        assert str(request.url) == 'https://api.openai.com/v1/images/edits'
        assert request.headers['authorization'] == 'Bearer ' + FAKE_KEY
        parsed = BytesParser(policy=default).parsebytes(('Content-Type: ' + request.headers['content-type'] + '\r\n\r\n').encode() + request.content)
        fields = {}; images = []
        for part in parsed.iter_parts():
            name = part.get_param('name', header='content-disposition')
            if name == 'image[]': images.append(part)
            else: fields[name] = part.get_payload(decode=True).decode()
        assert fields['quality'] == quality and fields['model'] == local_openai.MODEL
        assert fields['size'] == '96x64' and fields['prompt'].startswith('Green jacket')
        assert [part.get_filename() for part in images] == ['source.png', *[f'reference-{n}.png' for n in range(1, reference_count + 1)]]
        assert Image.open(io.BytesIO(images[0].get_payload(decode=True))).getpixel((0, 0)) == (0, 0, 255)
        assert Image.open(io.BytesIO(images[1].get_payload(decode=True))).getpixel((0, 0)) == (255, 0, 0)
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(generated.getvalue()).decode()}]})
    real_client = httpx.Client
    monkeypatch.setattr(local_openai.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    directory = root / 'jobs' / job['id']
    guides.execute(job, directory)
    guides.execute(job, directory)
    assert len(calls) == 1
    current = store.clip_get('c')['shots'][0]['guides'][0]
    assert current['approved'] == job['id']
    assert len(current['versions']) == 1
    assert current['versions'][0]['quality'] == quality
    assert FAKE_KEY not in json.dumps(store.job_get(job['id']))
    for path in directory.iterdir():
        assert FAKE_KEY.encode() not in path.read_bytes()


@pytest.mark.parametrize('failure', ['timeout', 'rejected'])
def test_uncertain_or_rejected_edit_is_never_resubmitted(local_scene, monkeypatch, failure):
    client, _, root = local_scene
    client.put(ROUTE, json={'api_key': FAKE_KEY})
    calls = []
    def handle(request):
        calls.append(request)
        if failure == 'timeout': raise httpx.ReadTimeout(FAKE_KEY)
        return httpx.Response(401, text='Sensitive provider response ' + FAKE_KEY)
    real_client = httpx.Client
    monkeypatch.setattr(local_openai.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    job_id = store.job_create('guide_generate', 'c', 's', {})
    directory = root / 'jobs' / job_id
    for _ in range(2):
        with pytest.raises(ValueError) as error:
            local_openai.run({'id': job_id}, directory, 'Edit', 'medium', [root / 'source.png'])
        assert FAKE_KEY not in str(error.value)
    assert len(calls) == 1
