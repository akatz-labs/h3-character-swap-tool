import io
import json
import wave
from email.parser import BytesParser
from email.policy import default
import httpx
import pytest
from h3workbench import openai_transcription as api, local_openai, media, store

@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    local_openai.save_key('synthetic-user-key-for-transcription')
    audio=tmp_path/'audio.wav'
    with wave.open(str(audio),'wb') as f:
        f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*32000)
    clip={'frames':48,'shots':[{'start':0,'end':48,'segments':[{'id':'a','start':0,'end':24},{'id':'b','start':24,'end':48}]}]}
    return clip,audio,tmp_path

def run(sample):
    return api.transcribe(*sample,progress=lambda _:None,check_cancel=lambda:None)

def test_same_private_key_bounded_multipart_and_cached_chunks(sample,monkeypatch):
    calls=[]
    def handler(req):
        calls.append(req)
        assert str(req.url)=='https://api.openai.com/v1/audio/transcriptions'
        assert req.headers['authorization']=='Bearer synthetic-user-key-for-transcription'
        parts=BytesParser(policy=default).parsebytes(('Content-Type: '+req.headers['content-type']+'\r\n\r\n').encode()+req.content)
        fields={p.get_param('name',header='content-disposition'):p.get_payload(decode=True) for p in parts.iter_parts()}
        assert fields['model']==b'gpt-transcribe'
        with wave.open(io.BytesIO(fields['file'])) as f:assert f.getnframes()==16000
        return httpx.Response(200,json={'text':'Hello' if len(calls)==1 else 'Goodbye','languages':[{'code':'en'}]})
    real=httpx.Client;monkeypatch.setattr(api.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    result=run(sample);assert result['text']=='Hello Goodbye';assert result['words']==[]
    assert media.dialogue({'transcript':result},24,48)=='Goodbye'
    assert run(sample)==result;assert len(calls)==2
    assert not list(sample[2].glob('transcription-*.wav'))
    for p in sample[2].glob('*.json'):assert 'synthetic-user-key' not in p.read_text()

@pytest.mark.parametrize('code',[401,403,404,429,500])
def test_failed_submission_never_reposts_or_echoes_provider_body(sample,monkeypatch,code):
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(code,text='sensitive provider body')
    real=httpx.Client;monkeypatch.setattr(api.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(ValueError,match=f'HTTP {code}') as error:run(sample)
    assert 'sensitive' not in str(error.value)
    with pytest.raises(ValueError,match='already submitted'):run(sample)
    assert len(calls)==1
