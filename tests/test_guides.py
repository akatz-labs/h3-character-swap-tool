import io
import json
import stat
import copy
import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from h3workbench import store, media, pipeline, segments, guides, guide_provider, guide_render
from h3workbench.app import app


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'MEDIA', tmp_path)
    monkeypatch.setenv('H3_GUIDE_COMFY_ORIGIN', 'http://comfy.test')
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'comfy')
    monkeypatch.setattr(pipeline, 'start', lambda: None)
    store.init()
    store.set_setting('shared_executor', True)
    monkeypatch.setattr(pipeline, 'WORKERS', {'local': {'target': {'kind': 'local', 'port': 8867}}})
    path = tmp_path / 'source.png'
    Image.new('RGB', (96, 64), 'blue').save(path)
    ref = store.asset_add(path)
    c = store.clip_create(store.batch_create('Guides'), {'id': 'c', 'source': ref, 'reference': ref,
        'dimensions': [96, 64], 'frames': 120, 'audio': False,
        'shots': [{'id': 's', 'start': 10, 'end': 100, 'mode': 'generate', 'seed': 3,
                   'prompt': '', 'anchors': [], 'renders': [], 'use_mask': False}]})
    def capture(clip, shot, index, target):
        assert shot['start'] + index == 32
        Image.new('RGB', (96, 64), 'blue').save(target)
        return store.asset_add(target)
    monkeypatch.setattr(media, 'shot_frame', capture)
    return TestClient(app), c, tmp_path


def frozen_guide_job(segment_id,worker='local',offload=True):
    """Fixture for a job admitted before workflow cutover; no retired HTTP path."""
    c=store.clip_get('c');shot=c['shots'][0];items=segments.current(shot)
    seg=next(item for item in items if item['id']==segment_id)
    return store.job_create('guide_render','c','s',{'snapshot':c,'shot':shot,
      'options':{'segment_id':segment_id,'model_offloading':offload},
      'segment_plan':{'segments':items,'indices':[items.index(seg)],'original_selections':{x['id']:x.get('selected_take') for x in items}},
      'segment_scope':[{'id':seg['id'],'start':seg['start'],'end':seg['end'],'index':items.index(seg)}],
      'worker':worker,'requested_worker':worker,'execution_backend':'shared','phase':'Queued','render_spec':media.render_spec(c,shot)})


def test_private_flare_default_and_collection_never_resubmits(scene, monkeypatch):
    import base64
    from h3workbench import guide_gateway
    client, _, root = scene
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'private-openai')
    monkeypatch.setenv('H3_GUIDE_OPENAI_SOCKET', '/run/h3-guides/openai.sock')
    image = io.BytesIO(); Image.new('RGB', (96, 64), 'green').save(image, format='PNG')
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/health':
            return httpx.Response(200, json={'model': guide_gateway.MODEL, 'configured': True})
        if request.method == 'POST':
            data = json.loads(request.content)
            assert data['model'] == 'gpt-image-2.6-flare' and data['quality'] == 'medium'
            assert data['size'] == '96x64'
            assert data['prompt'].startswith('Green jacket\n\nCharacter swap:\n')
            assert len(data['images']) == 2
            assert 'authorization' not in request.headers
            raise httpx.ReadTimeout('synthetic lost response')
        return httpx.Response(200, json={'state': 'complete', 'image': base64.b64encode(image.getvalue()).decode()})
    monkeypatch.setattr(guide_gateway, 'client', lambda socket=None: httpx.Client(transport=httpx.MockTransport(handle), base_url='http://private.test'))
    key = add(client)
    g = upload(client, key, 'reference')
    assert g['quality'] == 'medium'
    edit(client, key, {'prompt': 'Green jacket'})
    status = client.get('/api/guide-provider').json()
    assert status['configured'] and status['model'] == guide_gateway.MODEL
    assert client.post('/api/guide-provider', json={'api_key': 'should-not-be-accepted'}).status_code == 405
    job_id = client.post('/api/clips/c/shots/s/guides/'+key+'/generate', json={'revision': store.clip_get('c')['revision']}).json()['id']
    job = store.job_get(job_id)
    assert job['data']['options']['provider'] == 'private-openai'
    directory = store.MEDIA / 'jobs' / job_id; directory.mkdir(parents=True)
    with pytest.raises(ValueError, match='Collect the existing result'):
        guides.execute(job, directory)
    store.job_update(job_id, 'failed')
    assert client.post('/api/guide-jobs/'+job_id+'/collect').status_code == 200
    # Changing the configured default cannot redirect an accepted job.
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'comfy')
    guides.execute(job, directory)
    current = store.clip_get('c')['shots'][0]['guides'][0]
    assert current['approved'] == job_id
    assert current['versions'][-1]['model'] == guide_gateway.MODEL
    assert len([v for v in calls if v[0] == 'POST']) == 1


def add(client):
    c = store.clip_get('c')
    response = client.post('/api/clips/c/shots/s/guides', json={'revision': c['revision'], 'frame': 22})
    assert response.status_code == 200, response.text
    return response.json()['guide_id']


def edit(client, key, changes):
    r = client.patch('/api/clips/c/shots/s/guides/'+key, json={'revision': store.clip_get('c')['revision'], 'changes': changes})
    return r


def upload(client, key, kind='version'):
    image = io.BytesIO(); Image.new('RGB', (96, 64), 'red').save(image, format='PNG')
    response = client.post('/api/clips/c/shots/s/guides/'+key+'/upload',
        data={'revision': store.clip_get('c')['revision'], 'kind': kind}, files={'file': ('edit.png', image.getvalue(), 'image/png')})
    assert response.status_code == 200, response.text
    return response.json()['shots'][0]['guides'][0]


def approve(client, key, version):
    return client.post('/api/clips/c/shots/s/guides/'+key+'/approve',
        json={'revision': store.clip_get('c')['revision'], 'version_id': version})


def test_source_capture_approval_and_stale_source(scene):
    client, c, root = scene
    key = add(client)
    assert add(client) == key
    g = upload(client, key)
    assert g['frame'] == 32 and g['approved'] is None
    assert approve(client, key, g['versions'][0]['id']).status_code == 200
    second = upload(client, key)
    assert second['approved'] == g['versions'][0]['id']
    c = store.clip_get('c'); shot = c['shots'][0]; seg = segments.current(shot)[0]
    selected = guides.selected(c, shot, seg, strict=True)
    assert selected[0]['asset'] == g['versions'][0]['asset']
    signature = guides.signature(c, shot, seg)
    assert edit(client, key, {'prompt': 'User text, not character swap', 'quality': 'low'}).status_code == 200
    c = store.clip_get('c')
    assert guides.signature(c, c['shots'][0], seg) == signature
    store.clip_mutate('c', lambda c: c.update(render_source='another-source'))
    assert approve(client, key, second['versions'][-1]['id']).status_code == 400
    c = store.clip_get('c')
    with pytest.raises(ValueError, match='another source'):
        guides.selected(c, c['shots'][0], seg, strict=True)
    assert edit(client, key, {'enabled': False}).status_code == 200


def test_project_reference_boundary_and_revision(scene):
    client, _, root = scene; key = add(client)
    assert edit(client, key, {'references': ['some-other-project-asset']}).status_code == 400
    assert edit(client, key, {'source_asset': 'arbitrary'}).status_code == 400
    g = upload(client, key, 'reference')
    assert len(g['references']) == 1
    assert edit(client, key, {'references': g['references'] * 2}).status_code == 400
    assert edit(client, key, {'references': g['references'], 'prompt': 'Change the background'}).status_code == 200
    response = client.patch('/api/clips/c/shots/s/guides/'+key, json={'revision': 0, 'changes': {'prompt': 'old'}})
    assert response.status_code == 409


def test_saving_disabled_guide_keeps_it_disabled_and_preserves_versions(scene):
    client, _, _ = scene
    key = add(client)
    g = upload(client, key)
    version = g['versions'][0]['id']
    assert approve(client, key, version).status_code == 200
    assert edit(client, key, {'enabled': False}).status_code == 200
    response = client.post('/api/clips/c/shots/s/guides/'+key+'/save', json={
        'revision': store.clip_get('c')['revision'], 'changes': {'prompt': 'Keep this edit for later'}, 'version_id': version})
    assert response.status_code == 200
    c = store.clip_get('c'); shot = c['shots'][0]; saved = shot['guides'][0]
    assert saved['enabled'] is False and saved['approved'] == version
    assert saved['versions'] == g['versions']
    assert guides.selected(c, shot, shot['segments'][0]) == []
    assert edit(client, key, {'enabled': True}).status_code == 200
    c = store.clip_get('c'); shot = c['shots'][0]
    assert guides.selected(c, shot, shot['segments'][0])[0]['version_id'] == version


@pytest.mark.parametrize('mode', ['guides', 'swap_pika'])
def test_disabled_guides_are_omitted_from_video_graph(scene, mode):
    client, _, _ = scene
    key = add(client); g = upload(client, key)
    assert approve(client, key, g['versions'][0]['id']).status_code == 200
    def second(c):
        s = c['shots'][0]
        s['guides'].append({**copy.deepcopy(s['guides'][0]), 'id': 'second', 'frame': 60})
    store.clip_mutate('c', second)
    assert edit(client, key, {'enabled': False}).status_code == 200
    c = store.clip_get('c'); s = c['shots'][0]; seg = s['segments'][0]
    chosen = guides.selected(c, s, seg, strict=True)
    assert [v['id'] for v in chosen] == ['second']
    graph = guide_render.graph(['source.mkv', 'second.png'], 96, 64, 90, chosen, s['start'], 3, 'Edit', 'test', mode=mode)
    assert [n['inputs']['frame_idx'] for key,n in graph.items() if key.startswith('guide_')] == [50]
    assert [n['inputs']['image'] for n in graph.values() if n['class_type'] == 'LoadImage'] == ['second.png']


def test_approved_guides_never_become_picture_references(scene):
    from h3workbench import cast
    client, _, root = scene
    key = add(client); first = upload(client, key)
    store.clip_mutate('c', lambda c: c['shots'][0].update(prompt='Keep my original shot instructions'))
    assert approve(client, key, first['versions'][0]['id']).status_code == 200
    c = store.clip_get('c'); shot = c['shots'][0]; seg = shot['segments'][0]
    assert 'Keep my original shot instructions' in seg['performance_prompt']
    assert seg['prompt_mode'] == 'manual'
    before = cast.reference_plan(c, shot, seg)
    second = upload(client, key)
    assert approve(client, key, second['versions'][-1]['id']).status_code == 200
    c = store.clip_get('c'); shot = c['shots'][0]; seg = shot['segments'][0]
    assert cast.reference_plan(c, shot, seg) == before
    assert not seg['cast_inputs']['scene_anchors']
    assert not shot['cast_setup']['scene_anchors']
    assert guides.selected(c, shot, seg)[0]['asset'] == second['versions'][-1]['asset']
    assert edit(client, key, {'enabled': False}).status_code == 200
    c = store.clip_get('c'); assert not guides.selected(c, c['shots'][0], seg)
    assert edit(client, key, {'enabled': True}).status_code == 200
    response = client.request('DELETE', '/api/clips/c/shots/s/guides/'+key,
        json={'revision': store.clip_get('c')['revision']})
    assert response.status_code == 200
    assert len(store.clip_get('c')['shots'][0]['guides'][0]['versions']) == 2


def test_generation_activates_raw_image_without_mask_and_preserves_newer_draft(scene, monkeypatch):
    client, _, root = scene; key = add(client)
    g = upload(client, key); approve(client, key, g['versions'][0]['id'])
    edit(client, key, {'prompt': 'Make the jacket green.'})
    guide_provider.save_key('test-secret-key')
    c = store.clip_get('c'); shot = c['shots'][0]
    job = store.job_get(store.job_create('guide_generate', 'c', 's', {'snapshot': c, 'shot': shot, 'options': {'guide': copy.deepcopy(shot['guides'][0])}, 'worker': 'cpu'}))
    edit(client, key, {'prompt': 'A later draft'})
    def run(job, directory, graph_factory, inputs, paid):
        graph = graph_factory(['source.png'])
        assert paid and graph['edit']['inputs']['model'] == 'gpt-image-2'
        assert graph['edit']['inputs']['prompt'].startswith('Make the jacket green.\n\nCharacter swap:\n')
        path = directory / 'raw.png'; Image.new('RGB', (96, 64), 'green').save(path)
        return path
    monkeypatch.setattr(guide_provider, 'run', run)
    result = guides.execute(job, root)
    guides.execute(job, root)  # collection/attachment is idempotent
    current = store.clip_get('c')['shots'][0]['guides'][0]
    assert current['approved'] == current['auto_saved_version'] == job['id']
    assert current['prompt'] == 'A later draft'
    assert len(current['versions']) == 2
    assert current['versions'][-1]['asset'] == result['asset']
    assert current['versions'][-1]['prompt'] == 'Make the jacket green.'
    assert 'test-secret-key' not in json.dumps(store.public_job(job))
    assert stat.S_IMODE(guide_provider.key_path().stat().st_mode) == 0o600


def test_partner_transport_receipt_prevents_duplicate_paid_submission(scene, monkeypatch):
    client, c, root = scene; key = add(client)
    edit(client, key, {'prompt': 'Edit this frame'})
    guide_provider.save_key('test-secret-key')
    c = store.clip_get('c'); shot = c['shots'][0]
    # Historical partner jobs may still collect, but new MVP requests use Labs.
    assert client.post('/api/clips/c/shots/s/guides/'+key+'/generate', json={'revision': c['revision']}).status_code == 400
    job = store.job_get(store.job_create('guide_generate', 'c', 's', {'snapshot': c, 'shot': shot, 'options': {'guide': copy.deepcopy(shot['guides'][0])}, 'worker': 'cpu'}))
    directory = root/'attempt'; directory.mkdir()
    seen=[]; prompt_id=None
    image=io.BytesIO(); Image.new('RGB', (96,64)).save(image,format='PNG')
    def handle(request):
        nonlocal prompt_id
        seen.append((request.method, request.url.path))
        if request.url.path == '/upload/image': return httpx.Response(200,json={'name':'source.png','subfolder':'h3-guides'})
        if request.url.path == '/prompt':
            body=json.loads(request.content);prompt_id=body['prompt_id']
            assert body['extra_data']['api_key_comfy_org'] == 'test-secret-key'
            assert (directory/'comfy-receipt.json').exists()
            raise httpx.ReadTimeout('lost response',request=request)
        if request.url.path.startswith('/history/'):
            return httpx.Response(200,json={prompt_id:{'status':{'completed':True},'outputs':{'save':{'images':[{'filename':'output.png','subfolder':'','type':'output'}]}}}})
        if request.url.path == '/view': return httpx.Response(200,content=image.getvalue())
        raise AssertionError(request.url)
    real_client=httpx.Client
    monkeypatch.setattr(guide_provider.httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw))
    args=(job,directory,lambda names:guide_provider.image_graph('edit','high',names,'prefix'),[store.path(c['source'])])
    with pytest.raises(httpx.ReadTimeout):guide_provider.run(*args,paid=True)
    output=guide_provider.run(*args,paid=True)
    assert output.exists() and seen.count(('POST','/prompt'))==1
    assert seen.count(('POST','/upload/image'))==1
    for path in directory.iterdir():assert b'test-secret-key' not in path.read_bytes()






@pytest.mark.parametrize('mode,masked', [('guides',False),('swap_pika',False),('swap_pika',True)])
@pytest.mark.parametrize('frames', [89, 90])
def test_ripple_collects_managed_video_and_retains_exact_source_audio(scene, monkeypatch, frames, mode, masked):
    client,c,root=scene;key=add(client);g=upload(client,key);approve(client,key,g['versions'][0]['id'])
    c=store.clip_get('c');items=segments.current(c['shots'][0]);seg=items[0];seg.update(render_mode=mode,end=frames)
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=items,end=10+frames))
    if masked:
        import numpy as np
        mask_path=root/'tracked.npz'
        values=np.zeros((frames,64,96),dtype=bool);values[:,10:40,20:50]=True
        np.savez_compressed(mask_path,mask=values)
        mask_asset=store.asset_add(mask_path)
        def enable(c):
            sh=c['shots'][0]; item=sh['segments'][0];item['use_mask']=True
            track=sh['cast_setup']['mask_tracks'][0]
            track['state'].update(mask=mask_asset,expansion=0)
            item['cast_inputs'].update(mask_tracks=[track['id']],mask_assignments={})
        store.clip_mutate('c',enable)
    job=store.job_get(frozen_guide_job(seg['id']));directory=root/'render';directory.mkdir()
    source=root/'source.mp4'
    media.ff(['-f','lavfi','-i','color=c=blue:s=96x64:r=24','-f','lavfi','-i','sine=frequency=400:sample_rate=48000',
              '-t',str(frames/24),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',source])
    assert media.probe(source)['frames']==frames
    monkeypatch.setattr(media,'shot_media',lambda *a,**kw:source)
    def execute(job,captured,files,target,profile):
        assert profile=='full-offload'
        expected={'source.mkv','guide-0.png'} | ({'character-0.png'} if mode=='swap_pika' else set())
        assert set(files)==expected | ({'segment-mask.npz'} if masked else set())
        if masked:
            import numpy as np
            with np.load(files['segment-mask.npz']) as z:
                assert z['mask'].shape==(frames,64,96)
                assert np.array_equal(z['mask'],values)
            assert captured['mask']['inputs']['source_frames']==frames
            assert captured['sample']['inputs']['latent_image']==['edit_mask',0]
        else:
            assert 'edit_mask' not in captured
        assert media.probe(files['source.mkv'])['frames']==90
        if frames==89: assert files['source.mkv']!=source
        if mode=='swap_pika':
            assert 'source_guide' not in captured
            assert captured['text']['class_type']=='MiniMaxH3ReferenceToVideo'
            assert captured['text']['inputs']['ref_videos.ref_video_0']==['frames',0]
        assert captured['guide_0']['inputs']['frame_idx']==22
        if mode=='guides':assert captured['source']['inputs']['file']=='source.mkv'
        (target/'api.json').write_text(json.dumps(captured))
        runtime=target/'runtime';(runtime/'output').mkdir(parents=True)
        import shutil
        shutil.copyfile(source,runtime/'output'/'generated.mp4')
        (runtime/'history.json').write_text(json.dumps({'prompt':{'outputs':{'save':{'images':[
            {'filename':'generated.mp4','type':'output','subfolder':''}]}}}}))
        store.job_update(job['id'],shared_phase='completed')
        return runtime
    monkeypatch.setattr(guide_render.remote_shared,'execute',execute)
    result=guide_render.execute(job,directory)
    info=media.probe(store.path(result['asset']))
    assert info['frames']==frames and info['fps']==24 and info['audio']
    saved=segments.current(store.clip_get('c')['shots'][0])[0]
    assert saved['selected_take']==job['id']+'-'+seg['id']


def test_ripple_offloading_is_frozen_and_preserves_model_and_guide_schedule(scene):
    client,c,root=scene;key=add(client);g=upload(client,key);approve(client,key,g['versions'][0]['id'])
    c=store.clip_get('c');shot=c['shots'][0];items=segments.current(shot);seg=items[0];seg['render_mode']='guides'
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=items))
    path='/api/clips/c/shots/s/guides/render/'+seg['id']
    assert client.post(path,json={'revision':store.clip_get('c')['revision'],'model_offloading':'yes'}).status_code==400
    job=store.job_get(frozen_guide_job(seg['id']))
    assert job['data']['options']['model_offloading'] is True
    chosen=guides.selected(c,shot,seg,strict=True)
    args=(['source.mkv','guide.png'],96,64,90,chosen,shot['start'],3,'Follow edits','p')
    full=guide_render.graph(*args);offloaded=guide_render.graph(*args,offload=True)
    for name in ['model','pika','ripple','guide_0','latent','noise','schedule']:
        assert full[name]==offloaded[name]
    assert offloaded['guider']['inputs']['model']==['sampling_memory',0]




def guided_batch(scene, modes=('swap_pika', 'swap_pika', 'swap_pika')):
    client,_,root=scene;key=add(client);g=upload(client,key);approve(client,key,g['versions'][0]['id'])
    c=store.clip_get('c');shot=c['shots'][0];base=segments.current(shot)[0]
    items=[{**copy.deepcopy(base),'id':f'segment-{i}','start':i*30,'end':(i+1)*30,
            'render_mode':mode,'lora_id':'viggle-remake-swap','connection':'continue' if i else 'start',
            'anchor_mode':'none'} for i,mode in enumerate(modes)]
    original=copy.deepcopy(shot['guides'][0])
    markers=[{**copy.deepcopy(original),'id':f'guide-{i}','frame':shot['start']+i*30+10} for i in range(3)]
    c=store.clip_mutate('c',lambda c:c['shots'][0].update(segments=items,guides=markers))
    return client,c,c['shots'][0],root


@pytest.mark.parametrize(('mode','indices'), [('segment',[1]),('from',[1,2]),('all',[0,1,2])])
def test_guided_segments_use_shared_queue_scopes_and_explicit_offloading(scene, mode, indices):
    client,c,shot,_=guided_batch(scene)
    if mode != 'all':
        first=shot['segments'][0]
        first['takes']=[{'id':'prior','master':'prior-asset','start':0,'end':30,'dimensions':media.render_spec(c,shot)['dimensions'],'signature':segments.setting_signature(c,shot,first),'dependencies':[]}]
        first['selected_take']='prior'
        c=store.clip_mutate('c',lambda c:c['shots'][0].update(segments=shot['segments']))
    request={'mode':mode,'segment_id':'segment-1','plan_revision':segments.plan_revision(shot)}
    response=client.post('/api/jobs',json={'kind':'render','clip_id':'c','shot_id':'s','worker':'auto',
                                         'options':{'segments':request,'model_offloading':True}})
    assert response.status_code==200,response.text
    job=store.job_get(response.json()['id'])
    assert job['data']['segment_plan']['indices']==indices
    assert job['data']['options']['model_offloading'] is True
    assert job['data']['execution_backend']=='shared'
    assert [v['index'] for v in job['data']['segment_scope']]==indices
    store.clip_mutate('c',lambda c:c['shots'][0]['guides'][1].update(enabled=False))
    assert store.job_get(job['id'])['data']['shot']['guides'][1]['enabled'] is True


def test_guided_batch_validates_every_segment_before_creating_a_job(scene):
    client,c,shot,_=guided_batch(scene)
    c=store.clip_mutate('c',lambda c:c['shots'][0]['guides'][2].update(source='old-source'))
    response=client.post('/api/jobs',json={'kind':'render','clip_id':'c','shot_id':'s','worker':'local',
        'options':{'segments':{'mode':'all','segment_id':'segment-0','plan_revision':segments.plan_revision(shot)}}})
    assert response.status_code==400 and 'Segment 3' in response.text and 'another source' in response.text
    assert not store.jobs()


def test_guided_single_and_batch_jobs_cannot_overlap(scene):
    client,c,shot,_=guided_batch(scene)
    def submit(mode,index):
        return client.post('/api/jobs',json={'kind':'render','clip_id':'c','shot_id':'s','worker':'local',
          'options':{'segments':{'mode':mode,'segment_id':f'segment-{index}','plan_revision':segments.plan_revision(shot)}}})
    first=submit('segment',0);assert first.status_code==200,first.text
    response=submit('all',0);assert response.status_code==200,response.text
    batch=store.job_get(response.json()['id'])
    assert batch['data']['segment_plan']['indices']==[1,2]
    assert len(batch['data']['segment_dependencies'])==1
    assert submit('segment',1).status_code==409
    assert submit('segment',0).status_code==409
    assert len(store.jobs())==2



@pytest.mark.parametrize('guided_mode', ['guides', 'swap_pika'])
@pytest.mark.parametrize('stop_after_first', [False, True])
def test_mixed_segment_batch_routes_each_method_and_keeps_completed_takes(scene, monkeypatch, stop_after_first, guided_mode):
    from h3workbench import segment_jobs, remote
    client,c,shot,root=guided_batch(scene,(guided_mode,'swap',guided_mode))
    job=store.job_get(pipeline.queue('render','c','s','local',{'segments':{'mode':'all','segment_id':'segment-0','plan_revision':segments.plan_revision(shot)}}));calls=[]
    def result(items,i):
        seg=items[i]
        return {'id':job['id']+'-'+seg['id'],'segment_id':seg['id'],'start':seg['start'],'end':seg['end'],
                'signature':segments.setting_signature(c,shot,seg),'dependencies':segments.dependencies(items,i),
                'master':c['source'],'asset':c['source'],'dimensions':media.render_spec(c,shot)['dimensions']}
    def ripple(stage,directory,*,attach):
        assert not attach and stage['kind']=='guide_render'
        assert stage['data']['options']['model_offloading'] is True
        i=stage['data']['segment_scope'][0]['index'];calls.append(('guides',i))
        assert stage['data']['options']['segment_id']==f'segment-{i}'
        store.job_update(job['id'],shared_phase='completed',cancel_requested=stop_after_first)
        return result(stage['data']['shot']['segments'],i)
    def classic_graph(c,shot,items,i,job,directory):
        calls.append(('swap',i));assert items[0]['selected_take']==job['id']+'-segment-0'
        return {},{},job
    monkeypatch.setattr(guide_render,'execute',ripple)
    monkeypatch.setattr(segment_jobs,'graph',classic_graph)
    monkeypatch.setattr(segment_jobs.remote,'execute',lambda *a,**kw:root)
    monkeypatch.setattr(segment_jobs,'collect',lambda job,c,shot,items,i,*a:result(items,i))
    monkeypatch.setattr(segment_jobs,'assemble',lambda *a:None)
    directory=root/'batch';directory.mkdir()
    if stop_after_first:
        with pytest.raises(remote.Cancelled):segment_jobs.execute(job,directory)
    else:
        segment_jobs.execute(job,directory)
    expected=[('guides',0)] if stop_after_first else [('guides',0),('swap',1),('guides',2)]
    assert calls==expected
    saved=store.job_get(job['id'])['data']
    assert saved['segment_results']==[job['id']+f'-segment-{i}' for _,i in expected]
    assert list(saved['segment_outputs'])==[f'segment-{i}' for _,i in expected]
    live=store.clip_get('c')['shots'][0]['segments']
    for _,i in expected:assert live[i]['selected_take']==job['id']+f'-segment-{i}'


def test_explicit_save_applies_version_and_settings_atomically(scene):
    client, c, root = scene
    key = add(client)
    g = upload(client, key)
    version = g['versions'][0]['id']
    path = f'/api/clips/c/shots/s/guides/{key}/save'
    revision = store.clip_get('c')['revision']
    changes = {'prompt': 'Keep this exact edit', 'quality': 'medium', 'references': []}
    invalid = client.post(path, json={'revision': revision, 'changes': changes, 'version_id': 'missing'})
    assert invalid.status_code == 400, invalid.text
    unchanged = store.clip_get('c')['shots'][0]['guides'][0]
    assert unchanged['prompt'] == '' and unchanged['approved'] is None
    saved = client.post(path, json={'revision': revision, 'changes': changes, 'version_id': version})
    assert saved.status_code == 200, saved.text
    guide = saved.json()['shots'][0]['guides'][0]
    assert guide['prompt'] == changes['prompt'] and guide['approved'] == version
    stale = client.post(path, json={'revision': revision, 'changes': {'prompt': 'stale'}, 'version_id': None})
    assert stale.status_code == 409
    assert store.clip_get('c')['shots'][0]['guides'][0] == guide


def test_generation_uses_unsaved_draft_without_applying_it(scene, monkeypatch):
    client, c, root = scene
    key = add(client)
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'private-openai')
    monkeypatch.setattr(guide_provider, 'ready', lambda: None)
    response = client.post(f'/api/clips/c/shots/s/guides/{key}/generate', json={
        'revision': store.clip_get('c')['revision'],
        'changes': {'prompt': 'Generate this draft', 'quality': 'medium', 'references': []}})
    assert response.status_code == 200, response.text
    frozen = store.job_get(response.json()['id'])['data']['options']['guide']
    assert frozen['prompt'] == 'Generate this draft'
    saved = store.clip_get('c')['shots'][0]['guides'][0]
    assert saved['prompt'] == '' and saved['approved'] is None


def test_reference_upload_can_wait_for_explicit_save(scene):
    client, c, root = scene
    key = add(client)
    image = io.BytesIO(); Image.new('RGB', (96, 64)).save(image, format='PNG')
    response = client.post(f'/api/clips/c/shots/s/guides/{key}/upload',
        data={'revision': store.clip_get('c')['revision'], 'kind': 'reference', 'select_reference': 'false'},
        files={'file': ('reference.png', image.getvalue(), 'image/png')})
    assert response.status_code == 200, response.text
    shot = response.json()['shots'][0]
    assert shot['guides'][0]['references'] == []
    assert len(shot['guide_references']) == 1


def test_follow_up_edit_freezes_the_selected_source_and_retains_original(scene, monkeypatch):
    from h3workbench import guide_gateway
    client, _, root = scene
    key = add(client)
    g = upload(client, key)
    original, edited = g['source_asset'], g['versions'][0]['asset']
    assert edit(client, key, {'edit_source_asset': edited}).status_code == 200
    source_upload = upload(client, key, 'source')
    uploaded = source_upload['source_uploads'][-1]
    assert source_upload['edit_source_asset'] == edited
    assert source_upload['source_asset'] == original and len(source_upload['versions']) == 1
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'private-openai')
    monkeypatch.setattr(guide_provider, 'ready', lambda: None)
    response = client.post(f'/api/clips/c/shots/s/guides/{key}/generate', json={
        'revision': store.clip_get('c')['revision'],
        'changes': {'edit_source_asset': uploaded, 'prompt': 'Remove only the glasses', 'helpers': []}})
    assert response.status_code == 200, response.text
    job = store.job_get(response.json()['id'])
    assert job['data']['options']['guide']['edit_source_asset'] == uploaded
    # A reset after submission cannot redirect an already queued edit.
    assert edit(client, key, {'edit_source_asset': original}).status_code == 200
    def generate(job, directory, prompt, quality, inputs, size):
        assert inputs == [store.path(uploaded)]
        assert prompt == 'Remove only the glasses' and quality == 'medium'
        assert size == '96x64'
        path = directory / 'follow-up.png'
        Image.new('RGB', (96, 64), 'green').save(path)
        return path
    monkeypatch.setattr(guide_gateway, 'run', generate)
    guides.execute(job, root)
    saved = store.clip_get('c')['shots'][0]['guides'][0]
    assert saved['source_asset'] == original and saved['edit_source_asset'] == original
    assert saved['versions'][-1]['source_asset'] == uploaded
    assert saved['versions'][-1]['original_source_asset'] == original
    assert saved['versions'][0] == g['versions'][0]


def test_source_selection_is_scoped_to_this_guide_and_revision_guarded(scene, monkeypatch):
    client, _, root = scene
    key = add(client)
    g = upload(client, key, 'reference')
    path = f'/api/clips/c/shots/s/guides/{key}/save'
    revision = store.clip_get('c')['revision']
    for value in [g['references'][0], 'missing-asset', None, ['not-an-asset']]:
        response = client.post(path, json={'revision': revision, 'changes': {'edit_source_asset': value}, 'version_id': None})
        assert response.status_code == 400, response.text
    g = upload(client, key)
    selected = g['versions'][0]['asset']
    response = client.post(path, json={'revision': store.clip_get('c')['revision'],
        'changes': {'edit_source_asset': selected}, 'version_id': None})
    assert response.status_code == 200, response.text
    assert client.get('/api/clips/c/shots/s/guides').json()['guides'][0]['edit_source_asset'] == selected
    stale = client.post(path, json={'revision': revision, 'changes': {'edit_source_asset': g['source_asset']}, 'version_id': None})
    assert stale.status_code == 409
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'private-openai')
    monkeypatch.setattr(guide_provider, 'ready', lambda: None)
    invalid = client.post(f'/api/clips/c/shots/s/guides/{key}/generate', json={
        'revision': store.clip_get('c')['revision'], 'changes': {'edit_source_asset': g['references'][0], 'prompt': 'Edit'}})
    assert invalid.status_code == 400
    assert store.clip_get('c')['shots'][0]['guides'][0]['edit_source_asset'] == selected


def test_each_generation_activates_and_replayed_results_keep_manual_choice(scene, monkeypatch):
    from h3workbench import guide_gateway
    client, _, root = scene
    key = add(client)
    g = upload(client, key, 'source')
    source_asset = g['source_uploads'][0]
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'private-openai')
    monkeypatch.setattr(guide_provider, 'ready', lambda: None)
    def generate(job, directory, *args):
        path = directory / (job['id'] + '.png')
        Image.new('RGB', (96, 64), 'green').save(path)
        return path
    monkeypatch.setattr(guide_gateway, 'run', generate)
    path = f'/api/clips/c/shots/s/guides/{key}'
    changes = {'prompt': 'Replace the runner', 'quality': 'high', 'edit_source_asset': source_asset}
    first_response = client.post(path + '/generate', json={'revision': store.clip_get('c')['revision'], 'changes': changes})
    assert first_response.status_code == 200, first_response.text
    first = store.job_get(first_response.json()['id'])
    result = guides.execute(first, root)
    store.job_update(first['id'], 'success', result=result)
    c = store.clip_get('c'); shot = c['shots'][0]; saved = shot['guides'][0]
    assert saved['approved'] == saved['auto_saved_version'] == first['id']
    assert all(saved[k] == v for k, v in changes.items())
    assert guides.selected(c, shot, segments.current(shot)[0], strict=True)[0]['version_id'] == first['id']
    assert not shot['segments'][0]['cast_inputs']['scene_anchors']
    second_response = client.post(path + '/generate', json={'revision': c['revision'], 'changes': {'prompt': 'Remove glasses'}})
    assert second_response.status_code == 200, second_response.text
    second = store.job_get(second_response.json()['id'])
    guides.execute(second, root)
    saved = store.clip_get('c')['shots'][0]['guides'][0]
    assert saved['approved'] == saved['auto_saved_version'] == second['id']
    assert saved['prompt'] == 'Remove glasses'
    manual = client.post(path + '/save', json={'revision': store.clip_get('c')['revision'],
        'changes': {'prompt': 'My chosen edit', 'helpers': []}, 'version_id': second['id']})
    assert manual.status_code == 200, manual.text
    guides.execute(first, root)  # collecting an old result cannot replace the manual choice
    saved = client.get('/api/clips/c/shots/s/guides').json()['guides'][0]
    assert saved['approved'] == second['id'] and saved['prompt'] == 'My chosen edit'
    assert saved['helpers'] == [] and len(saved['versions']) == 2


@pytest.mark.parametrize('intervening_change', ['upload', 'disabled', 'archived', 'source-changed'])
def test_first_generation_does_not_override_intervening_guide_changes(scene, monkeypatch, intervening_change):
    from h3workbench import guide_gateway
    client, _, root = scene
    key = add(client)
    monkeypatch.setenv('H3_GUIDE_PROVIDER', 'private-openai')
    monkeypatch.setattr(guide_provider, 'ready', lambda: None)
    response = client.post(f'/api/clips/c/shots/s/guides/{key}/generate', json={
        'revision': store.clip_get('c')['revision'], 'changes': {'prompt': 'First result'}})
    job = store.job_get(response.json()['id'])
    if intervening_change == 'upload':
        g = upload(client, key)
        assert approve(client, key, g['versions'][0]['id']).status_code == 200
    else:
        def change(c):
            g = c['shots'][0]['guides'][0]
            if intervening_change == 'disabled': g['enabled'] = False
            elif intervening_change == 'archived': g['archived'] = True
            else: c['render_source'] = 'new-source'
        store.clip_mutate('c', change)
    before = store.clip_get('c')['shots'][0]['guides'][0]
    monkeypatch.setattr(guide_gateway, 'run', lambda *args: store.path(before['source_asset']))
    guides.execute(job, root)
    saved = store.clip_get('c')['shots'][0]['guides'][0]
    assert saved['approved'] == before['approved']
    assert 'auto_saved_version' not in saved
    assert len(saved['versions']) == len(before['versions']) + 1


@pytest.mark.parametrize('offload', [False, True])
def test_pika_recipe_accepts_new_jobs_and_retains_exact_sampling(scene, offload):
    client, c, root = scene
    key = add(client); g = upload(client, key); approve(client, key, g['versions'][0]['id'])
    c = store.clip_get('c'); shot = c['shots'][0]; items = segments.current(shot); seg = items[0]
    seg.update(render_mode='swap_pika', lora_id='v2-1000', guide_prompt='Keep the face and clothes from the guides.')
    shot['segments'] = items
    c = store.clip_mutate('c', lambda c: c['shots'][0].update(segments=items))
    response = client.post('/api/clips/c/shots/s/guides/render/' + seg['id'], json={'revision': c['revision'], 'model_offloading': offload})
    assert response.status_code == 200, response.text
    assert response.json()['data']['options']['model_offloading'] is offload
    chosen = guides.selected(c, shot, seg, strict=True)
    graph = guide_render.graph(['source.mkv','guide.png'],96,64,90,chosen,shot['start'],3,seg['guide_prompt'],'p',mode='swap_pika',offload=offload)
    loras = [n['inputs'] for n in graph.values() if n['class_type'] == 'LoraLoaderModelOnly']
    assert [n['lora_name'] for n in loras] == ['pika_h3_flex_fl2va_v1_bf16.safetensors', 'h3-v2-challenge-eval-20261013/v2-1000.safetensors']
    assert [n['strength_model'] for n in loras] == [1.0, 1.0]
    assert graph['swap']['inputs']['model'] == ['pika',0]
    assert graph['schedule'] == {'class_type': 'ManualSigmas', 'inputs': {'sigmas': '1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0'}}
    assert graph['guide_0']['inputs']['frame_idx'] == 22
    assert 'source_guide' not in graph
    assert graph['text']['inputs']['ref_videos.ref_video_0']==['frames',0]
    assert graph['guide_0']['inputs']['positive'] == ['text',0]
    assert graph['image_0']['inputs']['image'] == 'guide.png'
    assert graph['guider']['inputs']['conditioning'] == ['guide_0',0]
    assert graph['text']['inputs']['prompt'] == seg['guide_prompt']
    assert 'ripple' not in graph
    assert guides.signature(c, shot, seg) != guides.signature(c, shot, {**seg,'render_mode':'guides'})



def test_pika_swap_batch_accepts_characters_or_timed_guides(scene):
    from h3workbench import segment_jobs
    client, c, shot, root = guided_batch(scene, ('swap_pika','guides','swap_pika'))
    plan = segment_jobs.freeze(c, shot, {'mode':'all','segment_id':shot['segments'][0]['id'],'plan_revision':segments.plan_revision(shot)})
    assert plan['indices'] == [0,1,2]
    assert plan['dependencies'] == []
    shot['segments'][2]['cast_inputs'].update(characters=[],mask_tracks=[],mask_assignments={})
    from h3workbench import cast
    seg = shot['segments'][2]
    seg['cast_prompt_signature'] = cast.prompt_signature(c, shot, seg)
    assert segment_jobs.freeze(c, shot, {'mode':'all','segment_id':shot['segments'][0]['id'],'plan_revision':segments.plan_revision(shot)})['indices'] == [0,1,2]
    shot['guides'] = [g for g in shot['guides'] if g['frame'] < shot['start'] + seg['start']]
    assert segment_jobs.freeze(c, shot, {'mode':'all','segment_id':shot['segments'][0]['id'],'plan_revision':segments.plan_revision(shot)})['indices'] == [0,1,2]


def test_pika_edited_guides_chain_to_sampler_at_exact_segment_offsets():
    selected = [{'frame':100}, {'frame':183}]
    graph = guide_render.graph(['source.mkv','start.png','end.png'],480,992,90,selected,100,194729,'Keep the edited character.','test',mode='swap_pika',offload=True)
    assert [graph[f'guide_{i}']['inputs']['frame_idx'] for i in range(2)] == [0,83]
    assert graph['guide_0']['inputs']['positive'] == ['text',0]
    assert graph['guide_1']['inputs']['positive'] == ['guide_0',0]
    assert graph['guider']['inputs']['conditioning'] == ['guide_1',0]
    assert graph['sampling_memory']['inputs']['conditioning'] == ['guide_1',0]
    assert graph['source']['class_type']=='LoadVideo'
    assert graph['text']['class_type']=='MiniMaxH3ReferenceToVideo'
    assert len([n for n in graph.values() if n['class_type']=='MiniMaxH3AddGuide']) == 2


def test_pika_reference_order_audio_and_guides_are_distinct():
    g = guide_render.graph(['source.mkv','g0.png','g39.png','g83.png'],480,992,90,
        [{'frame':100+n} for n in [0,39,83]],100,194729,'Replace the person in <Video 1> with <Picture 1>.','test',
        mode='swap_pika',references=['sam.png','dario.png'],source_audio=True,offload=True)
    r = g['text']['inputs']
    assert g['text']['class_type'] == 'MiniMaxH3ReferenceToVideo'
    assert r['ref_videos.ref_video_0'] == ['frames',0]
    assert r['ref_images.ref_image_0'] == ['reference_0',0]
    assert r['ref_images.ref_image_1'] == ['reference_1',0]
    assert len([k for k in r if k.startswith('ref_images.')]) == 2
    assert r['ref_video_audios.ref_video_audio_0'] == ['frames',1]
    assert 'source_guide' not in g
    assert [g[f'guide_{i}']['inputs']['frame_idx'] for i in range(3)] == [0,39,83]
    assert g['guider']['inputs']['conditioning'] == ['guide_2',0]
    empty = guide_render.graph(['source.mkv'],480,992,90,[],100,1,'Edit','test',mode='swap_pika',references=['sam.png'])
    assert empty['guider']['inputs']['conditioning'] == ['text',0]
    assert 'ref_video_audios.ref_video_audio_0' not in empty['text']['inputs']
    assert 'audio_vae' not in empty


def test_input_migration_preserves_manual_text_takes_and_guide_versions(scene):
    client, _, root = scene
    key = add(client); g = upload(client,key); approve(client,key,g['versions'][0]['id'])
    def legacy(c):
        s=c['shots'][0];seg=s['segments'][0]
        s['cast_setup']['scene_anchors'].append({'id':'old-alias','name':'Guide','asset':g['versions'][0]['asset'],
            'source_frame':22,'provenance':{'guide_id':key}})
        seg['cast_inputs']['scene_anchors']=['old-alias']
        seg.update(performance_prompt='My exact <Picture 2> instruction.',prompt_mode='manual')
        seg['takes']=[{'id':'old-take','prompt':'Original text','signature':'old'}]
    c=store.clip_mutate('c',legacy);before=copy.deepcopy(c['shots'][0])
    store.set_setting('timed_guide_inputs_schema',0);guides.migrate_store()
    after=store.clip_get('c')['shots'][0];seg=after['segments'][0]
    assert seg['performance_prompt']==before['segments'][0]['performance_prompt']
    assert seg['takes']==before['segments'][0]['takes']
    assert after['guides']==before['guides']
    assert after['cast_setup']==before['cast_setup']
    assert not seg['cast_inputs']['scene_anchors']
    migrated=store.clip_get('c');guides.migrate_store();assert store.clip_get('c')==migrated


@pytest.mark.parametrize('offload', [False, True])
def test_pika_mask_preserves_reference_and_guide_contract(offload):
    g=guide_render.graph(['source.mkv','g0.png','g83.png'],480,992,90,
        [{'frame':100},{'frame':183}],100,1,'Swap the person','test',
        mode='swap_pika',references=['sam.png'],offload=offload,
        mask_file='region.npz',source_frames=89)
    assert g['edit_mask']['inputs']=={'latent':['latent',0], 'source_latent':['encode_source',0], 'mask':['mask',0]}
    assert g['encode_source']['inputs']=={'pixels':['frames',0], 'vae':['vae',0]}
    assert g['mask']['inputs']=={'mask_file':'region.npz','images':['frames',0],'source_frames':89,'expansion_px':0}
    assert g['sample']['inputs']['latent_image']==['edit_mask',0]
    assert [g[f'guide_{i}']['inputs']['frame_idx'] for i in range(2)]==[0,83]
    assert all(g[f'guide_{i}']['inputs']['latent']==['edit_mask',0] for i in range(2))
    assert g['guide_1']['inputs']['positive']==['guide_0',0]
    assert g['text']['inputs']['ref_images.ref_image_0']==['reference_0',0]
    assert not any(k.startswith('ref_images.ref_image_1') for k in g['text']['inputs'])
    assert g['text']['inputs']['ref_videos.ref_video_0']==['frames',0]
    if offload: assert g['sampling_memory']['inputs']['latent']==['edit_mask',0]


def test_pika_mask_validation_is_independent_of_characters(scene):
    from h3workbench import cast
    client,c,root=scene;key=add(client);g=upload(client,key);approve(client,key,g['versions'][0]['id'])
    c=store.clip_get('c');sh=c['shots'][0];seg=segments.current(sh)[0]
    seg.update(render_mode='swap_pika',use_mask=True)
    seg['cast_inputs'].update(mask_tracks=[],mask_assignments={})
    with pytest.raises(ValueError,match='Select at least one mask'):guide_render.validate(c,sh,seg)
    track=sh['cast_setup']['mask_tracks'][0];seg['cast_inputs']['mask_tracks']=[track['id']]
    with pytest.raises(ValueError,match='Track each selected mask'):guide_render.validate(c,sh,seg)
    track['state'].update(mask=c['source'],mask_points=[{'id':'point','frame':1,'x':.2,'y':.2,'label':1}],mask_applied_points=[])
    with pytest.raises(ValueError,match='pending corrections'):guide_render.validate(c,sh,seg)
    track['state']['mask_applied_points']=copy.deepcopy(track['state']['mask_points'])
    guide_render.validate(c,sh,seg)
    assert cast.resolve(c,sh,seg)['mask_tracks'][0]['character_id'] is None
    seg['use_mask']=False;track['state']['mask']=None
    guide_render.validate(c,sh,seg)

@pytest.mark.parametrize('history', [1,18])
def test_relay_history_precedes_masks_and_shifts_only_the_regenerated_boundary(history):
    graph=guide_render.graph(['source.mkv','guide.png'],96,64,56,[{'frame':30}],30,7,'Swap','test',
        mode='swap_pika',history_file='history.npz',history_frames=history,mask_file='mask.npz',source_frames=22)
    assert graph['history']['inputs']['history_frames']==history
    assert graph['history']['inputs']['frames']==['load_history',0]
    assert graph['history']['inputs']['first_frame_guide'] is True
    assert graph['history']['inputs']['target_audio_guide'] is False
    assert graph['edit_mask']['inputs']['latent']==['history',1]
    assert graph['mask']['inputs']['source_frames']==23
    assert graph['guide_0']['inputs']['frame_idx']==1
    assert graph['guide_0']['inputs']['positive']==['history',0]
    assert graph['text']['inputs']['ref_videos.ref_video_0']==['frames',0]
    assert not any(k.startswith('ref_images') for k in graph['text']['inputs'])


def test_guide_only_segment_validates_and_can_be_queued(scene):
    from h3workbench import cast
    client, _, _ = scene
    key = add(client)
    g = upload(client, key)
    approve(client, key, g['versions'][0]['id'])
    c = store.clip_get('c'); sh = c['shots'][0]; seg = segments.current(sh)[0]
    seg.update(render_mode='swap_pika', use_mask=False)
    seg['cast_inputs'] = {'characters': [], 'mask_tracks': [], 'scene_anchors': [], 'mask_assignments': {}}
    seg['cast_prompt_signature'] = cast.prompt_signature(c, sh, seg)
    guide_render.validate(c, sh, seg)
    assert cast.validate_generation(c, sh, seg) == []
    assert cast.generation_inputs(c, sh, seg)['pictures'] == []
    for change in ({'enabled': False}, {'approved': None}):
        saved = copy.deepcopy(sh['guides'])
        sh['guides'][0].update(change)
        guide_render.validate(c, sh, seg)
        sh['guides'] = saved


def test_guide_reference_limit_is_eight(scene):
    client, _, _ = scene
    key = add(client)
    for _ in range(8):
        guide = upload(client, key, 'reference')
    assert len(guide['references']) == 8
    shot = store.clip_get('c')['shots'][0]
    # A ninth existing project image cannot be selected as another reference.
    assert edit(client, key, {'references': [*guide['references'], shot['guides'][0]['source_asset']]}).status_code == 400
    assert edit(client, key, {'references': guide['references']}).status_code == 200


def test_ripple_strength_is_unchanged_by_swap_tuning():
    graph = guide_render.graph(["source.mkv"], 96, 64, 22, [], 0, 3, "Edit", "test", mode="guides")
    assert graph["ripple"]["inputs"]["strength_model"] == 1.0


def test_local_image_generation_freezes_choice_worker_offload_and_restores_draft(scene, monkeypatch):
    from h3workbench import local_h3_image, local_openai
    client, c, _ = scene
    monkeypatch.setenv('H3_GUIDE_PROVIDER','local-openai')
    monkeypatch.setattr(local_openai,'configured',lambda:False)
    checked=[]
    monkeypatch.setattr(local_h3_image,'ready',lambda worker:checked.append(worker))
    key=add(client)
    changes={'edit_model':'h3-image','quality':'high','prompt':'Replace the person'}
    assert edit(client,key,changes).status_code==200
    r=client.post(f'/api/clips/c/shots/s/guides/{key}/generate',json={
        'revision':store.clip_get('c')['revision'],'changes':changes,'worker':'local','model_offloading':True})
    assert r.status_code==200,r.text
    job=store.job_get(r.json()['id'])
    assert checked==['local']
    assert job['data']['options']['provider']=='local-h3-image'
    assert job['data']['options']['model_offloading'] is True
    assert job['data']['options']['guide']['edit_model']=='h3-image'
    assert job['data']['execution_backend']=='shared'
    assert job['data']['render_spec']['dimensions']==local_h3_image.dimensions([96,64],'high')


def test_private_provider_cannot_run_local_image(scene,monkeypatch):
    client,_,_=scene
    monkeypatch.setenv('H3_GUIDE_PROVIDER','private-openai')
    key=add(client)
    r=client.post(f'/api/clips/c/shots/s/guides/{key}/generate',json={
        'revision':store.clip_get('c')['revision'],'changes':{'edit_model':'h3-image'}})
    assert r.status_code==400
    assert 'local runner' in r.text
