import copy
import pytest
from fastapi.testclient import TestClient
from h3workbench import workflow, segments, store, pipeline
from h3workbench.app import app
from test_segments import scene


def test_cutover_retains_exact_history(scene):
    clip, shot, _ = scene
    shot['segments']=segments.current(shot)
    shot['segments'][0].update(performance_prompt='My exact prompt',prompt_mode='manual',takes=[{'id':'retained','asset':'retained-video'}])
    before=copy.deepcopy(shot)
    workflow.adopt(shot)
    assert workflow.is_current(shot)
    assert shot['segment_layout_history'][-1]['segments']==before['segments']
    assert shot['segments'][0]['performance_prompt']=='My exact prompt'
    assert shot['segments'][0]['takes']==before['segments'][0]['takes']
    once=copy.deepcopy(shot);workflow.adopt(shot);assert shot==once


def test_revision_guarded_api_cutover_and_persisted_reload(scene):
    c,s,_=scene
    client=TestClient(app)
    path=f"/api/clips/{c['id']}/shots/{s['id']}/segments/workflow"
    response=client.post(path,json={'revision':c['revision']})
    assert response.status_code==200,response.text
    after=store.clip_get(c['id'])
    assert workflow.is_current(after['shots'][0])
    assert len(after['shots'][0]['segment_layout_history'])==1
    assert client.post(path,json={'revision':c['revision']}).status_code==409


def test_old_render_request_cannot_bypass_single_workflow(scene):
    c,s,_=scene
    response=TestClient(app).post('/api/jobs',json={'kind':'render','clip_id':c['id'],'shot_id':s['id'],'worker':'auto'})
    assert response.status_code==400,response.text
    assert 'updated editor' in response.text


def test_retired_guide_route_cannot_bypass_workflow(scene):
    c,s,_=scene
    response=TestClient(app).post(f"/api/clips/{c['id']}/shots/{s['id']}/guides/render/segment-0",json={'revision':c['revision']})
    assert response.status_code==400,response.text
    assert 'updated editor' in response.text


def test_local_catalog_provisions_exact_workflow():
    import json
    from h3workbench.config import ROOT
    catalog = json.loads((ROOT / 'docker/resource-catalog.json').read_text())
    features = {item['id']: item for item in catalog['features']}
    assert set(features) == {'sam-frame', 'sam-track', 'sam-recover', 'h3-swap-remake-v1', 'h3-swap-pika-v1'}
    models = {item['id']: item for item in catalog['models']}
    render = features['h3-swap-remake-v1']
    assert not render['optional']
    assert all(models[key]['requirement'] == 'required' for key in render['models'])
    paths = {models[key]['relativePath'] for key in render['models']}
    assert 'diffusion_models/' + workflow.PRESET['model'] in paths
    assert 'loras/' + workflow.PRESET['lora'] in paths
    assert 'vae/minimax_h3_audio_vae_fp32.safetensors' in paths
    assert any(path.endswith('/viggle_remake_swap_comfyui.safetensors') for path in paths)
    from runtime.configure_installation import features_for_model
    assert all('h3-swap-remake-v1' in features_for_model(path) for path in paths)


def test_pika_choice_is_preserved_and_switch_invalidates_without_losing_takes(scene):
    from h3workbench import guides
    c, shot, _ = scene
    shot['segments'] = segments.current(shot)
    seg = shot['segments'][0]
    seg.update(render_mode='swap_pika', lora_id='v2-1000', takes=[{'id':'pika-take','asset':'pika-output'}])
    old = guides.signature(c, shot, seg)
    workflow.adopt(shot)
    current = shot['segments'][0]
    assert current['lora_id'] == 'v2-1000'
    current['lora_id'] = 'viggle-remake-swap'
    assert guides.signature(c, shot, current) != old
    assert current['takes'] == [{'id':'pika-take','asset':'pika-output'}]
    current['lora_id'] = 'v2-1000'
    assert guides.signature(c, shot, current) == old
