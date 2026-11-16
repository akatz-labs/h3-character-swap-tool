"""Local transcription preference, resolved once when a job is admitted."""
from . import local_openai, store, transcription_resources

SETTING = 'transcription_provider'


def status():
    preference = store.setting(SETTING, 'auto')
    local = transcription_resources.installed()
    openai = local_openai.configured()
    selected = ('local' if local else 'openai' if openai else None) if preference == 'auto' else preference
    available = local if selected == 'local' else openai if selected == 'openai' else False
    return {'preference': preference, 'localAvailable': local, 'openaiAvailable': openai,
            'selected': selected, 'ready': available}


def resolve():
    current = status()
    if not current['ready']:
        if current['preference'] == 'local':
            raise ValueError('Download Whisper in Local settings → Models, or choose Auto/OpenAI in Transcription.')
        if current['preference'] == 'openai':
            raise ValueError('Add an API key in Local settings → OpenAI, or choose Auto/Local in Transcription.')
        raise ValueError('To transcribe, download Whisper in Local settings → Models or add an API key in Local settings → OpenAI.')
    return 'cpu' if current['selected'] == 'local' else 'openai'
