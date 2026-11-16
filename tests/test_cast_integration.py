import copy
import io
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from h3workbench import cast, cast_api, media, masks, pipeline, segment_jobs, segments, store
from h3workbench.app import app


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'MEDIA',tmp_path)
    monkeypatch.setattr(cast_api,'MEDIA',tmp_path)
    monkeypatch.setattr(pipeline,'start',lambda:None)
    store.init()
    refs=[]
    for i in range(4):
        path=tmp_path/f'ref-{i}.png';Image.new('RGB',(64,64),(i*50,40,20)).save(path);refs.append(store.asset_add(path))
    values=np.zeros((240,64,64),bool);values[:130,25:30,10:15]=True
    path=tmp_path/'mask.npz';np.savez_compressed(path,mask=values);mask=store.asset_add(path)
    shot={'id':'s','start':0,'end':240,'mode':'generate','seed':1,'prompt':'Keep the performance.',
          'dialogue':'','anchor':refs[1],'anchors':[{'asset':refs[1],'source_frame':29}],
          'use_mask':True,'use_source_audio':False,'mask':mask,'expansion':2,'renders':[],
          'mask_points':[],'mask_applied_points':[],'source_frame':refs[0]}
    shot['segments']=segments.layout(shot,{'operation':'partition','frames':120})
    c=store.clip_create(store.batch_create('Cast test'),{'id':'c','source':refs[0],'reference':refs[0],
        'dimensions':[64,64],'frames':240,'audio':False,'shots':[shot]})
    store.set_setting('shot_cast_schema', 0) # Simulate a database from before the cast migration.
    with TestClient(app) as client:
        c=store.clip_get('c')
        project_character=client.post(f'/api/batches/{c["batch_id"]}/characters',json={'name':'Rowan'}).json()
        response=client.post('/api/clips/c/shots/s/cast/characters',json={'revision':c['revision'],'character_ids':[project_character['id']]})
        assert response.status_code==200,response.text
        c=response.json();s=c['shots'][0]
        b=s['cast_setup']['characters'][1];t=s['cast_setup']['mask_tracks'][1]
        values=np.zeros((240,64,64),bool);values[110:,25:30,45:50]=True
        p=tmp_path/'mask-b.npz';np.savez_compressed(p,mask=values);b_mask=store.asset_add(p)
        def setup(v):
            sh=v['shots'][0];sh['cast_setup']['characters'][1]['reference']=refs[2]
            sh['cast_setup']['mask_tracks'][1]['state'].update(mask=b_mask,expansion=4)
            sh['cast_setup']['scene_anchors'].append({'id':'late','name':'Late pan','asset':refs[3],'source_frame':154})
            for seg in sh['segments']:
                seg['cast_inputs']={'characters':[cast.LEGACY_CHARACTER,b['id']], 'mask_tracks':[cast.LEGACY_MASK,t['id']], 'scene_anchors':['late']}
                seg['performance_prompt']=segments.build_prompt(v,sh,seg)
                seg['cast_prompt_signature']=cast.prompt_signature(v,sh,seg)
        c=store.clip_mutate('c',setup,c['revision'])
        yield client,c,c['shots'][0],tmp_path,refs


def test_api_revision_guard_and_upload_keeps_cast_separate(scene):
    client,c,s,d,refs=scene
    b=s['cast_setup']['characters'][1]
    response=client.patch(f'/api/batches/{c["batch_id"]}/characters/{b["project_character_id"]}',json={'revision':0,'changes':{'description':'stale'}})
    assert response.status_code==409
    image=io.BytesIO();Image.new('RGB',(64,64),'red').save(image,format='PNG')
    response=client.post('/api/clips/c/shots/s/cast/images',data={'revision':c['revision'],'kind':'anchor','source_frame':155},files={'file':('edited.png',image.getvalue(),'image/png')})
    assert response.status_code==200,response.text
    result=response.json()['shots'][0]
    assert result['cast_setup']['characters']==s['cast_setup']['characters']
    assert result['cast_setup']['scene_anchors'][-1]['source_frame']==155
    assert result['segments']==s['segments']


def test_swap_character_keeps_mask_identity_assignments_and_exact_prompts(scene):
    client,c,s,_,refs=scene
    original=s['cast_setup']['characters'][1]
    replacement=client.post(f'/api/batches/{c["batch_id"]}/characters',json={'name':'Replacement'}).json()
    response=client.patch(f'/api/clips/c/shots/s/cast/characters/{original["id"]}',json={
        'revision':c['revision'],'project_character_id':replacement['id']})
    assert response.status_code==200,response.text
    changed=response.json();result=changed['shots'][0]
    assert result['cast_setup']['characters'][1]['id']==original['id']
    assert result['cast_setup']['characters'][1]['project_character_id']==replacement['id']
    assert result['cast_setup']['mask_tracks']==s['cast_setup']['mask_tracks']
    assert result['segments']==s['segments']
    assert result['cast_setup']['scene_anchors']==s['cast_setup']['scene_anchors']
    assert segments.states(changed,result)[0]['prompt_mode']=='manual'
    assert not segments.states(changed,result)[0]['prompt_needs_update']
    # Re-adding the old project reference must never reuse the swapped local ID.
    response=client.post('/api/clips/c/shots/s/cast/characters',json={
        'revision':changed['revision'],'character_ids':[original['project_character_id']]})
    assert response.status_code==200,response.text
    chars=response.json()['shots'][0]['cast_setup']['characters']
    assert len({v['id'] for v in chars})==3
    assert chars[-1]['project_character_id']==original['project_character_id']
    assert client.patch(f'/api/clips/c/shots/s/cast/characters/{original["id"]}',json={
        'revision':c['revision'],'project_character_id':replacement['id']}).status_code==409


def test_duplicate_and_remove_shot_character_keep_independent_masks_and_assets(scene):
    client,c,s,_,_=scene;original=s['cast_setup']['characters'][1];track=s['cast_setup']['mask_tracks'][1]
    project_before=client.get(f'/api/batches/{c["batch_id"]}/characters').json()
    response=client.post(f'/api/clips/c/shots/s/cast/characters/{original["id"]}/duplicate',json={'revision':c['revision']})
    assert response.status_code==200,response.text
    changed=response.json();result=changed['shots'][0];duplicate=result['cast_setup']['characters'][2];copied=result['cast_setup']['mask_tracks'][2]
    assert duplicate['id']!=original['id'] and duplicate['project_character_id']==original['project_character_id']
    assert copied['id']!=track['id'] and copied['character_id']==duplicate['id'] and copied['state']==track['state']
    for before,after in zip(s['segments'],result['segments']):
        assert after['performance_prompt']==before['performance_prompt'] and after['takes']==before['takes']
        assert after['cast_inputs']['characters'][1:]==[original['id'],duplicate['id']]
        assert after['cast_inputs']['mask_tracks'][1:]==[track['id'],copied['id']]
    response=client.patch(f'/api/clips/c/shots/s?track_id={copied["id"]}',json={
        'revision':changed['revision'],'changes':{'expansion':19}})
    assert response.status_code==200,response.text
    changed=response.json();assert changed['shots'][0]['cast_setup']['mask_tracks'][1]==track
    response=client.request('DELETE',f'/api/clips/c/shots/s/cast/characters/{duplicate["id"]}',json={'revision':changed['revision']})
    assert response.status_code==200,response.text
    result=response.json()['shots'][0]
    assert result['cast_setup']['characters']==s['cast_setup']['characters'] and result['segments']==s['segments']
    assert result['cast_setup']['mask_tracks'][:2]==s['cast_setup']['mask_tracks']
    assert result['cast_setup']['mask_tracks'][2]['character_id'] is None
    assert result['cast_setup']['mask_tracks'][2]['state']['expansion']==19
    assert client.get(f'/api/batches/{c["batch_id"]}/characters').json()==project_before


def test_shot_character_actions_reject_missing_and_cross_project_targets(scene):
    client,c,s,_,_=scene;original=s['cast_setup']['characters'][1]
    other=store.batch_create('Other project')
    foreign=client.post(f'/api/batches/{other}/characters',json={'name':'Foreign'}).json()
    assert client.patch(f'/api/clips/c/shots/s/cast/characters/{original["id"]}',json={
        'revision':c['revision'],'project_character_id':foreign['id']}).status_code==404
    assert client.post('/api/clips/c/shots/s/cast/characters/missing/duplicate',json={'revision':c['revision']}).status_code==400
    assert client.request('DELETE','/api/clips/c/shots/s/cast/characters/missing',json={'revision':c['revision']}).status_code==400
    assert store.clip_get('c')==c


def test_manual_prompt_survives_reference_changes_and_update_rebuilds_tokens(scene):
    client,c,s,d,refs=scene;seg=s['segments'][0]
    choice={**seg['cast_inputs'],'characters':list(reversed(seg['cast_inputs']['characters']))}
    response=client.patch(f'/api/clips/c/shots/s/segments/{seg["id"]}',json={'revision':c['revision'],'changes':{'cast_inputs':choice}})
    assert response.status_code==200,response.text
    c=response.json();s=c['shots'][0];new=s['segments'][0]
    assert new['performance_prompt']==seg['performance_prompt']
    assert cast.validate_generation(c,s,new)
    assert segments.prompt_mode(new)=='manual'
    response=client.post(f'/api/clips/c/shots/s/segments/{seg["id"]}/prompt',json={'revision':c['revision']})
    assert response.status_code==200,response.text
    c=response.json();s=c['shots'][0];new=s['segments'][0]
    assert 'Rowan from <Picture 1>' in new['performance_prompt']
    assert '<Audio' not in new['performance_prompt']
    assert cast.validate_generation(c,s,new)


def test_split_and_merge_preserve_mask_tracks_and_union_segment_inputs(scene):
    _,c,s,_,_=scene
    before=copy.deepcopy(s['cast_setup']['mask_tracks'])
    split=segments.layout(s,{'operation':'split','frame':60})
    assert split[0]['cast_inputs']==s['segments'][0]['cast_inputs']
    assert split[1]['cast_inputs']=={'characters':[], 'mask_tracks':[], 'scene_anchors':[]}
    s['segments']=split
    a,b=s['cast_setup']['characters'];ta,tb=s['cast_setup']['mask_tracks']
    split[0]['cast_inputs']={'characters':[a['id']],'mask_tracks':[ta['id']],'scene_anchors':[]}
    split[1]['cast_inputs']={'characters':[b['id']],'mask_tracks':[tb['id']],'scene_anchors':['late']}
    merged=segments.layout(s,{'operation':'merge','index':1})
    assert merged[0]['cast_inputs']=={'characters':[a['id'],b['id']],'mask_tracks':[ta['id'],tb['id']],'scene_anchors':['late']}
    assert merged[0]['needs_review'] and merged[0]['cast_prompt_signature'] is None
    assert s['cast_setup']['mask_tracks']==before


@pytest.mark.parametrize('continuing',[False,True])
def test_graph_uses_selected_references_and_expanded_mask_union(scene,monkeypatch,continuing):
    _,c,s,d,refs=scene;items=s['segments'];i=int(continuing);seg=items[i]
    monkeypatch.setattr(media,'shot_media',lambda *a,**kw:store.path(c['source']))
    def history(*args):
        p=d/'history.npz';np.savez_compressed(p,frames=np.zeros((18,64,64,3),np.float32));return p
    monkeypatch.setattr(segment_jobs,'history_file',history)
    job={'id':'graph','data':{'snapshot':c,'shot':s}}
    g,files,stage=segment_jobs.graph(c,s,items,i,job,d)
    images={k:v for k,v in g['801']['inputs'].items() if k.startswith('ref_images.')}
    assert len(images)==2
    assert images['ref_images.ref_image_0']==['cast_image_0',0]
    assert files['graph-cast-1.png']==store.path(refs[2])
    assert store.path(refs[3]) not in files.values()
    assert g['801']['inputs']['ref_videos.ref_video_0']==['803',5]
    assert store.path(refs[1]) not in files.values() # unselected old anchor
    assert g['43']['inputs']['expansion_px']==0
    with np.load(files['graph-mask.npz']) as z: combined=z['mask']
    expected=cast.combined_mask(c,s,seg,[64,64])
    np.testing.assert_array_equal(combined,expected)
    assert len(combined)==120+(18 if continuing else 0)
    f=10 if continuing else 115
    assert combined[f,25,8] and not combined[f,25,7] # A expansion 2
    assert combined[f,25,41] and not combined[f,25,40] # B expansion 4
    assert '<Audio' not in g['801']['inputs']['prompt']


def test_character_mask_jobs_and_attachment_cannot_overwrite_other_track(scene,monkeypatch):
    client,c,s,_,_=scene;tracks=s['cast_setup']['mask_tracks'];b=tracks[1]['id']
    point={'id':'p','frame':110,'x':.7,'y':.5,'label':1}
    response=client.patch(f'/api/clips/c/shots/s?track_id={b}',json={'revision':c['revision'],'changes':{'mask_points':[point],'expansion':12}})
    assert response.status_code==200,response.text
    c=response.json();s=c['shots'][0]
    assert s['cast_setup']['mask_tracks'][0]==tracks[0]
    a_job=pipeline.queue('mask_edit','c','s', worker="fey",options={'mode':'shot','frame':0,'track_id':tracks[0]['id']})
    b_job=pipeline.queue('mask_edit','c','s', worker="fey",options={'mode':'shot','frame':110,'track_id':b})
    assert a_job!=b_job
    job=store.job_get(b_job)
    assert job['data']['shot']['mask_points']==[point]
    assert job['data']['shot']['mask']==tracks[1]['state']['mask']
    assert next(j for j in store.public_jobs() if j['id']==b_job)['data']['mask_track_id']==b
    monkeypatch.setattr(pipeline,'check_finalization_cancel',lambda j:None)
    pipeline.attach(job,lambda c,sh,ok:sh.update(mask='completed-b-mask'))
    live=store.clip_get('c')['shots'][0]
    assert live['cast_setup']['mask_tracks'][0]==tracks[0]
    assert live['cast_setup']['mask_tracks'][1]['state']['mask']=='completed-b-mask'
    assert live['mask']==s['mask']


def test_pending_other_character_blocks_generation_but_absence_does_not(scene):
    _,c,s,_,_=scene;seg=s['segments'][0]
    assert cast.validate_generation(c,s,seg)
    s['cast_setup']['mask_tracks'][1]['state']['mask_points']=[{'id':'p','frame':115,'x':.7,'y':.5,'label':1}]
    with pytest.raises(ValueError,match='pending corrections'):cast.validate_generation(c,s,seg)


def test_reference_limit_counts_characters_only(scene):
    _,c,s,_,refs=scene;seg=s['segments'][1]
    for i in range(7):
        a={'id':f'extra-{i}','name':f'Anchor {i}','asset':refs[1],'source_frame':None}
        s['cast_setup']['scene_anchors'].append(a);seg['cast_inputs']['scene_anchors'].append(a['id'])
    assert len(cast.validate_generation(c,s,seg)) == 2
    for i in range(8):
        character={'id':f'character-extra-{i}','name':f'Extra {i}','reference':refs[0],'description':''}
        s['cast_setup']['characters'].append(character);seg['cast_inputs']['characters'].append(character['id'])
    with pytest.raises(ValueError,match='at most 9'):cast.validate_generation(c,s,seg)


def test_absent_range_preserves_other_frames_tracks_and_history(scene):
    client,c,s,_,_=scene
    tracks=s['cast_setup']['mask_tracks'];a=tracks[0];b=tracks[1]
    response=client.post(f'/api/clips/c/shots/s/cast/masks/{a["id"]}/absent',json={'revision':c['revision'],'start':20,'end':30})
    assert response.status_code==200,response.text
    live=response.json()['shots'][0]
    updated=live['cast_setup']['mask_tracks'][0]['state']
    values=masks.read(updated['mask'],240,[64,64])
    assert values[:20].any() and values[30:130].any() and not values[20:30].any()
    assert live['cast_setup']['mask_tracks'][1]==b
    assert any(v['asset']==a['state']['mask'] for v in updated['mask_versions'])
    response=client.post(f'/api/clips/c/shots/s/cast/masks/{a["id"]}/absent',json={'revision':c['revision'],'start':0,'end':1})
    assert response.status_code==409


def test_boundary_move_preserves_each_segments_choices(scene):
    _,c,s,_,_=scene
    first,second=s['segments'];a,b=s['cast_setup']['characters'];ta,tb=s['cast_setup']['mask_tracks']
    first['cast_inputs']={'characters':[a['id']],'mask_tracks':[ta['id']],'scene_anchors':[]}
    second['cast_inputs']={'characters':[b['id']],'mask_tracks':[tb['id']],'scene_anchors':['late']}
    moved=segments.layout(s,{'operation':'boundary','index':1,'frame':150})
    assert moved[0]['cast_inputs']==first['cast_inputs']
    assert moved[1]['cast_inputs']==second['cast_inputs']
    assert moved[0]['end']==moved[1]['start']==150


def test_invalid_cast_upload_returns_actionable_error(scene):
    client,c,s,_,_=scene
    response=client.post('/api/clips/c/shots/s/cast/images',data={'revision':c['revision'],'kind':'anchor'},files={'file':('bad.png',b'not an image','image/png')})
    assert response.status_code==400,response.text
    assert 'readable image' in response.json()['detail']
    assert store.clip_get('c')['revision']==c['revision']


def test_capture_retains_exact_source_without_changing_generation(scene, monkeypatch):
    client,c,s,d,refs=scene
    captured=[]
    monkeypatch.setattr(media,'shot_frame',lambda clip,shot,frame,dest:captured.append((store.path(clip['source']),shot['start']+frame,dest)) or refs[0])
    before=copy.deepcopy(c)
    response=client.post('/api/clips/c/shots/s/cast/captures',json={'revision':c['revision'],'frame':155,'segment_id':s['segments'][1]['id']})
    assert response.status_code==200,response.text
    result=response.json();entry=result['capture'];current=result['clip']
    assert captured[0][1]==155 and captured[0][0]==store.path(c['source'])
    assert entry['source_frame']==entry['clip_frame']==155
    assert entry['asset']==refs[0] and entry['source']==c['source']
    assert current['shots'][0]['anchor_captures']==[entry]
    current['shots'][0].pop('anchor_captures');current.pop('revision');before.pop('revision')
    assert current==before
    stale=client.post('/api/clips/c/shots/s/cast/captures',json={'revision':c['revision'],'frame':155})
    assert stale.status_code==409
    current=store.clip_get('c')
    for frame in (-1,240,True):
        assert client.post('/api/clips/c/shots/s/cast/captures',json={'revision':current['revision'],'frame':frame}).status_code==400
    assert len(captured)==1


def test_capture_upload_assigns_only_target_segment_and_preserves_prompt(scene, monkeypatch):
    client,c,s,d,refs=scene
    monkeypatch.setattr(media,'shot_frame',lambda *args:refs[0])
    response=client.post('/api/clips/c/shots/s/cast/captures',json={'revision':c['revision'],'frame':155,'segment_id':s['segments'][1]['id']})
    captured=response.json();c=captured['clip'];entry=captured['capture']
    data={'revision':c['revision'],'kind':'anchor','capture_id':entry['id'],'segment_id':s['segments'][1]['id'],'source_frame':0}
    bad=client.post('/api/clips/c/shots/s/cast/images',data=data,files={'file':('bad.png',b'bad','image/png')})
    assert bad.status_code==400
    assert store.clip_get('c')['shots'][0]['anchor_captures']==[entry]
    image=io.BytesIO();Image.new('RGB',(64,64),'green').save(image,format='PNG')
    response=client.post('/api/clips/c/shots/s/cast/images',data=data,files={'file':('edited.png',image.getvalue(),'image/png')})
    assert response.status_code==200,response.text
    new=response.json()['shots'][0];a=new['cast_setup']['scene_anchors'][-1]
    assert a['source_frame']==155 # A capture cannot be accidentally relabelled by a client.
    assert a['provenance']['source_asset']==entry['asset'] and a['provenance']['capture_id']==entry['id']
    assert new['anchor_captures'][0]['anchor_id']==a['id']
    assert new['segments'][0]==s['segments'][0]
    assert new['segments'][1]['cast_inputs']['scene_anchors']==s['segments'][1]['cast_inputs']['scene_anchors']+[a['id']]
    assert new['segments'][1]['performance_prompt']==s['segments'][1]['performance_prompt']
    assert new['cast_setup']['mask_tracks']==s['cast_setup']['mask_tracks']
    data['revision']=response.json()['revision']
    repeated=client.post('/api/clips/c/shots/s/cast/images',data=data,files={'file':('edited.png',image.getvalue(),'image/png')})
    assert repeated.status_code==400


def test_capture_rejects_deleted_segment_and_uses_shot_offset(scene, monkeypatch):
    client,c,s,d,refs=scene
    seen=[];monkeypatch.setattr(media,'shot_frame',lambda clip,shot,frame,dest:seen.append(shot['start']+frame) or refs[0])
    c=store.clip_mutate('c',lambda v:v['shots'][0].update(start=24,end=264),c['revision'])
    response=client.post('/api/clips/c/shots/s/cast/captures',json={'revision':c['revision'],'frame':20,'segment_id':'missing'})
    assert response.status_code==400 and not seen
    response=client.post('/api/clips/c/shots/s/cast/captures',json={'revision':c['revision'],'frame':20,'segment_id':s['segments'][0]['id']})
    assert response.status_code==200,response.text
    assert seen==[44] and response.json()['capture']['clip_frame']==44


@pytest.mark.parametrize('continuing', [False, True])
def test_timeline_guides_keep_picture_numbers_and_include_history_offset(scene, monkeypatch, continuing):
    from h3workbench import guides
    _, c, s, d, refs = scene
    i = int(continuing); seg = s['segments'][i]
    monkeypatch.setattr(media, 'shot_media', lambda *a, **kw: store.path(c['source']))
    monkeypatch.setattr(segment_jobs, 'history_file', lambda *a: d/'history.npz')
    seg['prompt_mode'] = 'auto'
    before_prompt = segments.prompt(c, s, seg)
    before_signature = segments.setting_signature(c, s, seg)
    s['guides'] = [{'id': f'g{n}', 'frame': s['start']+seg['start']+n, 'source': c['source'],
        'source_asset': refs[0], 'dimensions': c['dimensions'], 'enabled': True,
        'approved': 'v', 'versions': [{'id': 'v', 'asset': refs[3]}]} for n in [0,39,83]]
    assert segments.prompt(c, s, seg) == before_prompt
    assert segments.setting_signature(c, s, seg) != before_signature
    g, files, _ = segment_jobs.graph(c, s, s['segments'], i, {'id':'timed','data':{}}, d)
    references = {k:v for k,v in g['801']['inputs'].items() if k.startswith('ref_images.')}
    assert list(references) == ['ref_images.ref_image_0','ref_images.ref_image_1']
    assert g['801']['inputs']['prompt'] == before_prompt
    assert '<Picture 3>' not in before_prompt and 'guide' not in before_prompt.lower()
    assert [g[f'timed_guide_{n}']['inputs']['frame_idx'] for n in range(3)] == [n+(18 if continuing else 0) for n in [0,39,83]]
    assert g['timed_guide_0']['inputs']['positive'] == ['history',0]
    assert g['guider']['inputs']['positive'] == ['timed_guide_2',0]
    assert g['sampling_memory']['inputs']['conditioning'] == ['timed_guide_2',0]
    assert all(files[f'timed-guide-{n}.png'] == store.path(refs[3]) for n in range(3))
    s['guides'][0]['source'] = 'obsolete'
    with pytest.raises(ValueError, match='another source'):
        segment_jobs.graph(c, s, s['segments'], i, {'id':'bad','data':{}}, d)
