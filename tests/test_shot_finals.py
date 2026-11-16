import copy
import pytest
from PIL import Image
from h3workbench import store, media, pipeline, segments, shot_finals


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'MEDIA', tmp_path)
    store.init()
    source = tmp_path / 'source.mkv'
    media.ff(['-f','lavfi','-i','testsrc2=size=64x64:rate=24:duration=2',
              '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','ffv1','-c:a','pcm_s16le',source])
    ref = tmp_path / 'ref.png'; Image.new('RGB', (32, 64), 'red').save(ref)
    source_id, ref_id = store.asset_add(source), store.asset_add(ref)
    clip = store.clip_create(store.batch_create('Final tests'), {'id':'c', 'name':'Source',
        'source': source_id, 'reference': ref_id, 'dimensions':[64,64], 'frames':48, 'audio':True,
        'shots':[{'id':'s','start':0,'end':48,'mode':'generate','seed':1,'prompt':'Keep this editable prompt',
                  'renders':[],'render_mp':1,'use_mask':False,'use_anchor':False}]})
    shot=clip['shots'][0]; items=segments.layout(shot, {'operation':'partition','frames':24})
    for i, seg in enumerate(items):
        path=tmp_path/f'take-{i}.mkv'
        media.ff(['-ss',str(i),'-i',source,'-t','1','-an','-c:v','ffv1',path])
        asset=store.asset_add(path)
        take={'id':f'take-{i}','master':asset,'asset':asset,'reference':ref_id,'start':seg['start'],
              'end':seg['end'],'dimensions':[64,64],'dependencies':segments.dependencies(items,i),
              'settings':{'performance_prompt':'Old prompt'},'prompt':'Old prompt'}
        seg['takes']=[take]; seg['selected_take']=take['id']
    store.clip_mutate('c', lambda c:c['shots'][0].update(segments=items))
    clip=store.clip_get('c')
    body={'revision':clip['revision'],'request_id':'final-request-1',
          'take_ids':{s['id']:s['selected_take'] for s in items},'acknowledge_continuity':False}
    return clip, body, tmp_path


def test_final_is_immutable_and_has_exact_exports(scene):
    clip, body, directory=scene; before=copy.deepcopy(clip['shots'][0])
    job=shot_finals.queue('c','s',body)
    assert shot_finals.queue('c','s',body)['id']==job['id']
    assert store.clip_get('c')==clip
    # A subsequent editor change cannot change the queued final.
    store.clip_mutate('c',lambda c:c['shots'][0].update(prompt='New editable prompt'))
    result=shot_finals.execute(job,directory/'final')
    current=store.clip_get('c')['shots'][0]
    assert current['prompt']=='New editable prompt'
    assert current['segments']==before['segments']
    assert current['renders']==[]
    assert current['finals']==[result]
    for key in ('asset','comparison','comparison_reference'):
        info=media.probe(store.path(result[key]))
        assert info['frames']==48
        assert info['audio'] is True
    store.job_update(job['id'],state='success')
    assert shot_finals.queue('c','s',body)['id']==job['id']
    with pytest.raises(store.Conflict):
        shot_finals.queue('c','s',{**body,'acknowledge_continuity':True})
    next_body={**body,'revision':store.clip_get('c')['revision'],'request_id':'final-request-2'}
    assert shot_finals.queue('c','s',next_body)['id']!=job['id']


def test_validation_and_continuity(scene):
    clip, body, _=scene; shot=clip['shots'][0]
    with pytest.raises(store.Conflict):shot_finals.freeze(clip,shot,{**body,'revision':-1})
    with pytest.raises(ValueError):shot_finals.freeze(clip,shot,{**body,'take_ids':{}})
    first=shot['segments'][0]; take=first['takes'][0]
    take['end']-=1
    with pytest.raises(ValueError,match='frames'):shot_finals.freeze(clip,shot,body)
    take['end']+=1
    take['dimensions']=[128,64]
    with pytest.raises(ValueError,match='resolution'):shot_finals.freeze(clip,shot,body)
    take['dimensions']=[64,64]
    second=shot['segments'][1]['takes'][0]
    second['dependencies'][0]['take_id']='other-take'
    with pytest.raises(ValueError,match='continuity'):shot_finals.freeze(clip,shot,body)
    assert shot_finals.freeze(clip,shot,{**body,'acknowledge_continuity':True})['continuity_warnings']


def test_attempt_is_scoped_and_redacted(scene):
    clip, body, _=scene
    job=shot_finals.queue('c','s',body)
    store.job_update(job['id'],private_token='do-not-return')
    assert 'do-not-return' not in str(shot_finals.attempt('c','s',job['id']))
    with pytest.raises(KeyError):shot_finals.attempt('other','s',job['id'])


def test_http_contract_and_no_reference_export(scene, monkeypatch):
    from fastapi.testclient import TestClient
    from h3workbench.app import app
    clip, body, directory = scene
    monkeypatch.setattr(pipeline, 'start', lambda: None)
    with TestClient(app) as client:
        response = client.post('/api/clips/c/shots/s/segments/final', json=body)
        assert response.status_code == 200, response.text
        job_id = response.json()['job']['id']
        assert client.post('/api/clips/c/shots/s/segments/final', json=body).json()['job']['id'] == job_id
        assert client.get(f'/api/clips/c/shots/s/segments/attempts/{job_id}').status_code == 200
    job = store.job_get(job_id)
    for seg in job['data']['shot']['segments']:
        for take in seg['takes']:
            take.pop('reference', None)
    result = shot_finals.execute(job, directory/'no-references')
    assert result['comparison']
    assert 'comparison_reference' not in result
    assert store.clip_get('c')['shots'][0]['segments'] == clip['shots'][0]['segments']
