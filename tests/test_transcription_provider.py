"""Local availability, explicit preference and immutable queued provider."""
import pytest
from h3workbench import local_openai, pipeline, store, transcription_provider as provider
from test_guides import scene

ROUTE = '/api/local-settings/transcription'


@pytest.fixture
def local_scene(scene, monkeypatch):
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'local-openai')
    store.set_setting(provider.SETTING, 'auto')
    return scene


@pytest.mark.parametrize('local,key,expected', [(True, True, 'cpu'), (True, False, 'cpu'), (False, True, 'openai'), (False, False, None)])
def test_auto_availability(local_scene, monkeypatch, local, key, expected):
    monkeypatch.setattr(provider.transcription_resources, 'installed', lambda: local)
    monkeypatch.setattr(local_openai, 'configured', lambda: key)
    if expected:
        assert provider.resolve() == expected
    else:
        with pytest.raises(ValueError, match='download Whisper.*API key'):
            provider.resolve()


def test_explicit_choice_is_persistent_and_never_falls_back(local_scene, monkeypatch):
    client, _, _ = local_scene
    monkeypatch.setattr(provider.transcription_resources, 'installed', lambda: True)
    monkeypatch.setattr(local_openai, 'configured', lambda: True)
    assert client.put(ROUTE, json={'preference': 'openai'}).json()['selected'] == 'openai'
    assert client.get(ROUTE).json()['preference'] == 'openai'
    assert provider.resolve() == 'openai'
    monkeypatch.setattr(local_openai, 'configured', lambda: False)
    with pytest.raises(ValueError, match='Add an API key'): provider.resolve()
    assert client.put(ROUTE, json={'preference': 'local'}).json()['ready']
    assert provider.resolve() == 'cpu'
    monkeypatch.setattr(provider.transcription_resources, 'installed', lambda: False)
    with pytest.raises(ValueError, match='Download Whisper'): provider.resolve()
    assert client.put(ROUTE, json={'preference': 'unknown'}).status_code == 422
    assert client.put(ROUTE, json={'preference': 'auto'}, headers={'origin': 'https://foreign.invalid'}).status_code == 403
    assert client.get(ROUTE).headers['cache-control'] == 'no-store'
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'private-openai')
    assert client.get(ROUTE).status_code == 404


def test_job_freezes_provider_before_settings_change(local_scene, monkeypatch):
    monkeypatch.setattr(provider.transcription_resources, 'installed', lambda: True)
    monkeypatch.setattr(local_openai, 'configured', lambda: True)
    store.clip_mutate('c', lambda clip: clip.update(audio=True))
    queued = pipeline.queue('transcribe', 'c', options={'transcription_provider': 'openai'})
    job_id = queued['id'] if isinstance(queued, dict) else queued
    assert store.job_get(job_id)['data']['options']['transcription_provider'] == 'cpu'
    store.set_setting(provider.SETTING, 'openai')
    assert store.job_get(job_id)['data']['options']['transcription_provider'] == 'cpu'
