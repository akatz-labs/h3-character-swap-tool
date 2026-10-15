import sys
import wave
from types import SimpleNamespace
import numpy as np
import pytest
from h3workbench import transcription, transcription_resources as resources, store


@pytest.fixture
def asr(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, 'DATA', tmp_path / 'app')
    monkeypatch.setattr(resources, 'MODELS', tmp_path / 'shared-models')
    monkeypatch.setattr(resources, 'FILE_SIZES', {name: 3 for name in transcription.FILES})
    monkeypatch.setattr(resources, 'SIZE', 12)
    snapshot = resources.snapshot_path()
    snapshot.mkdir(parents=True)
    for name in transcription.FILES:
        (snapshot / name).write_bytes(b'abc')
    audio_path = tmp_path / 'audio.wav'
    with wave.open(str(audio_path), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x40' * 16000)
    phases = []

    class Model:
        def __init__(self, path, **kwargs):
            assert path == str(snapshot)
            assert kwargs['device'] == 'cpu' and kwargs['compute_type'] == 'int8'
            assert kwargs['local_files_only'] is True

        def transcribe(self, samples, **kwargs):
            assert kwargs['word_timestamps']
            assert samples.dtype == np.float32 and samples.shape == (16000,)
            assert np.all(samples == 0.5)
            word = SimpleNamespace(word=' Hello', start=0.2, end=0.7, probability=0.99)
            return iter([SimpleNamespace(text=' Hello', end=0.7, words=[word])]), SimpleNamespace(language='en')

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    return audio_path, snapshot, phases


def test_existing_model_runs_offline_with_word_timing(asr):
    audio, snapshot, phases = asr
    result = transcription.transcribe(audio, progress=phases.append, check_cancel=lambda: None)
    assert result['text'] == 'Hello' and result['words'][0]['start'] == 0.2
    assert not any('Downloading' in phase for phase in phases)
    assert resources.inventory()['state'] == 'installed'


def test_missing_or_incomplete_model_requires_explicit_resources_install(asr):
    audio, snapshot, phases = asr
    (snapshot / 'tokenizer.json').unlink()
    with pytest.raises(ValueError, match='Resources'):
        transcription.transcribe(audio, progress=phases.append, check_cancel=lambda: None)
    assert resources.inventory()['state'] == 'missing'
    assert not phases


def test_cancellation_before_model_load(asr):
    def cancel():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        transcription.transcribe(asr[0], progress=asr[2].append, check_cancel=cancel)


def test_model_in_use_blocks_removal_from_another_thread(asr):
    import threading
    errors = []
    with resources.model_use():
        def attempt():
            try:
                resources.removal_plan()
            except store.Conflict as error:
                errors.append(str(error))
        thread = threading.Thread(target=attempt)
        thread.start()
        thread.join()
    assert errors and 'busy' in errors[0]
