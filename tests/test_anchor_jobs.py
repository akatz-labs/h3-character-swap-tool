import copy
import numpy as np
import cv2
import pytest
from h3workbench import anchor_jobs, store
from test_cast_integration import scene


def request(c, s):
    return {'revision': c['revision'], 'character_id': s['cast_setup']['characters'][0]['id'],
            'segment_id': s['segments'][0]['id'], 'frame': 30, 'worker': 'fey', 'seed': 123}


def test_queue_freezes_inputs_deduplicates_and_never_changes_shot(scene):
    client,c,s,_,refs=scene
    store.set_setting('shared_executor', True)
    body=request(c,s)
    one=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json=body)
    assert one.status_code==200,one.text
    two=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json=body)
    assert two.json()['id']==one.json()['id']
    job=store.job_get(one.json()['id'])
    assert job['data']['options']['identity']['reference']==s['cast_setup']['characters'][0]['reference']
    assert job['data']['execution_backend']=='shared'
    assert store.clip_get('c')==c
    changed={**body,'frame':31}
    assert client.post('/api/clips/c/shots/s/cast/anchor-jobs',json=changed).status_code==409
    assert client.post('/api/clips/c/shots/s/cast/anchor-jobs',json={**body,'frame':130}).status_code==400
    assert client.post('/api/clips/c/shots/s/cast/anchor-jobs',json={**body,'revision':0}).status_code==409


def test_candidate_adoption_is_explicit_idempotent_and_rejects_changed_reference(scene):
    client,c,s,_,refs=scene
    store.set_setting('shared_executor',True)
    j=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json=request(c,s)).json()
    result={'asset':refs[1],'anchor_candidate':{'asset':refs[1],'raw':refs[2],'source':refs[0],
        'provenance':refs[3],'frame':30,'character_name':'Test','seed':123,'segment_id':s['segments'][0]['id']}}
    store.job_update(j['id'],'success',result=result)
    assert store.clip_get('c')==c
    assert store.public_jobs()[0]['data']['result']['anchor_candidate']==result['anchor_candidate']
    job=store.job_get(j['id']);changed=copy.deepcopy(s);changed['cast_setup']['characters'][0]['reference']=refs[3]
    with pytest.raises(store.Conflict):anchor_jobs.adopt(c,changed,job)
    path=f'/api/clips/c/shots/s/cast/anchor-jobs/{j["id"]}/use'
    response=client.post(path,json={'revision':c['revision']})
    assert response.status_code==200,response.text
    updated=response.json();sh=updated['shots'][0]
    assert len(sh['cast_setup']['scene_anchors'])==len(s['cast_setup']['scene_anchors'])+1
    assert sh['segments'][1]==s['segments'][1]
    twice=client.post(path,json={'revision':updated['revision']}).json()
    assert twice['shots'][0]['cast_setup']['scene_anchors']==sh['cast_setup']['scene_anchors']


def test_alignment_uses_background_and_rejects_large_motion():
    rng=np.random.default_rng(42)
    src=rng.integers(0,256,(384,640,3),dtype=np.uint8)
    src=cv2.GaussianBlur(src,(3,3),.7)
    target=np.zeros(src.shape[:2],bool);target[90:300,240:400]=True
    moved=cv2.warpAffine(src,np.array([[1.,0,4],[0,1,3]]),(640,384),borderMode=cv2.BORDER_REFLECT)
    moved[100:290,250:390]=(200,100,20)
    fit,info=anchor_jobs.fit(src,moved,target)
    assert fit.shape==src.shape and info['accepted']
    assert info['error_after']<.5
    too_far=cv2.warpAffine(src,np.array([[1.,0,70],[0,1,0]]),(640,384),borderMode=cv2.BORDER_REFLECT)
    fitted,info=anchor_jobs.fit(src,too_far,target)
    assert not info['accepted']
    assert np.array_equal(fitted,too_far)
    arbitrary=rng.integers(0,256,(500,700,3),dtype=np.uint8)
    fitted,info=anchor_jobs.fit(src,arbitrary)
    assert fitted.shape==src.shape and not info['accepted']


def test_composite_keeps_unrelated_people_and_background_pixel_exact():
    src=np.full((80,120,3),40,np.uint8);edited=np.full_like(src,220)
    old=np.zeros((80,120),bool);old[20:70,50:85]=True
    new=np.zeros_like(old);new[15:70,55:95]=True
    protected=np.zeros_like(old);protected[:, :60]=True
    result,info,region,alpha=anchor_jobs.finish(src,edited,old,new,protected)
    assert np.array_equal(result[~region],src[~region])
    assert np.array_equal(result[protected],src[protected])
    assert (result[40:60,70:80]>src[40:60,70:80]).all()
    assert region[30,90] # new silhouette
    assert region[30,60] # old silhouette included
    assert info['outside_region_equal'] and info['protected_equal']
    with pytest.raises(ValueError,match='isolate'):
        anchor_jobs.finish(src,edited,old,np.zeros_like(old),protected)


def test_source_capture_uses_render_canvas_including_padding(scene):
    from h3workbench import media
    from PIL import Image
    client,c,s,d,refs=scene
    source=d/'source.mkv'
    media.ff(['-f','lavfi','-i','testsrc=size=160x90:rate=24','-t','0.2','-c:v','ffv1',source])
    c={**c,'source':store.asset_add(source),'dimensions':[320,192]}
    asset=media.shot_frame(c,s,2,d/'captured.png')
    capture=np.asarray(Image.open(store.path(asset)))
    assert capture.shape[:2]==(192,320)
    assert not capture[:6].any() and not capture[-6:].any()


def test_queue_without_seed_deduplicates_and_requires_mask(scene):
    client,c,s,_,refs=scene
    store.set_setting('shared_executor',True)
    body=request(c,s);body.pop('seed')
    one=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json=body)
    two=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json=body)
    assert one.status_code==two.status_code==200
    assert one.json()['id']==two.json()['id']
    changed=copy.deepcopy(s)
    changed['cast_setup']['mask_tracks'][0]['state']['mask']=None
    with pytest.raises(ValueError,match='Apply a mask'):
        anchor_jobs.identity(c,changed,body)


def test_foreground_box_covers_full_silhouette_without_old_mask_prior():
    target=np.zeros((736,1344),bool);target[80:736,550:1100]=True
    graph,box=anchor_jobs.foreground(target)
    assert box['x']<550 and box['y']<80
    assert box['x']+box['width']>1100 and box['y']+box['height']==736
    assert graph['3']['class_type']=='SAM3_Detect'
    assert graph['3']['inputs']['bboxes']==box
    assert 'prior_file' not in graph['3']['inputs']


def test_draft_previews_exact_inputs_without_enqueuing_and_custom_prompt_is_frozen(scene,monkeypatch):
    client,c,s,d,refs=scene
    store.set_setting('shared_executor',True)
    captures=[]
    monkeypatch.setattr(anchor_jobs.media,'shot_frame',lambda clip,shot,frame,path:(captures.append(frame) or refs[0]))
    body=request(c,s)
    response=client.post('/api/clips/c/shots/s/cast/anchor-draft',json=body)
    assert response.status_code==200,response.text
    draft=response.json()
    assert captures==[30] and not store.public_jobs()
    assert draft['source']==refs[0] and draft['reference']==s['cast_setup']['characters'][0]['reference']
    assert draft['models'][0]['id']==anchor_jobs.MODEL
    assert 'anime' not in draft['request']['prompt'].lower()
    assert 'cel shading' not in draft['request']['prompt'].lower()
    custom='Replace the person with a photorealistic Sam Altman wearing his gray sweater.'
    response=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json={**draft['request'],'worker':'fey','prompt':custom})
    assert response.status_code==200,response.text
    job=store.job_get(response.json()['id'])
    assert job['data']['options']['prompt']==custom
    assert anchor_jobs.graph(job['data']['options']['prompt'],123)['7']['inputs']['prompt']==custom
    for changes in [{'model':'gpt-image'},{'prompt':''},{'prompt':'x'*12001}]:
        bad=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json={**body,**changes})
        assert bad.status_code==400,bad.text


def test_anchor_toggle_delete_preserves_history_and_deleted_candidate_cannot_return(scene):
    client,c,s,d,refs=scene
    store.set_setting('shared_executor',True)
    job=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json=request(c,s)).json()
    candidate={'asset':refs[1],'raw':refs[2],'source':refs[0],'provenance':refs[3],
        'frame':30,'character_name':'Sam','seed':123,'segment_id':s['segments'][0]['id']}
    store.job_update(job['id'],'success',result={'asset':refs[1],'anchor_candidate':candidate})
    base=f'/api/clips/c/shots/s/cast/anchor-jobs/{job["id"]}'
    current=client.post(base+'/use',json={'revision':c['revision'],'enabled':True,'segment_id':s['segments'][0]['id']}).json()
    entry=next(a for a in current['shots'][0]['cast_setup']['scene_anchors'] if a.get('provenance',{}).get('job_id')==job['id'])
    current=client.post(base+'/use',json={'revision':current['revision'],'enabled':False,'segment_id':s['segments'][0]['id']}).json()
    assert entry in current['shots'][0]['cast_setup']['scene_anchors']
    assert entry['id'] not in current['shots'][0]['segments'][0]['cast_inputs']['scene_anchors']
    current=client.post(base+'/use',json={'revision':current['revision'],'enabled':True,'segment_id':s['segments'][1]['id']}).json()
    assert entry['id'] in current['shots'][0]['segments'][1]['cast_inputs']['scene_anchors']
    response=client.request('DELETE',base,json={'revision':current['revision']})
    assert response.status_code==200,response.text
    current=response.json();shot=current['shots'][0]
    assert entry not in shot['cast_setup']['scene_anchors']
    assert all(entry['id'] not in v['cast_inputs']['scene_anchors'] for v in shot['segments'])
    assert job['id'] in shot['dismissed_anchor_jobs']
    assert store.job_get(job['id'])['data']['result']['anchor_candidate']==candidate
    assert store.path(refs[1]).is_file()
    assert client.post(base+'/use',json={'revision':current['revision']}).status_code==409
    assert shot['renders']==s['renders']


def test_uploaded_anchor_toggle_and_delete(scene):
    client,c,s,d,refs=scene
    anchor=s['cast_setup']['scene_anchors'][0];seg=s['segments'][0]
    base=f'/api/clips/c/shots/s/cast/anchors/{anchor["id"]}'
    on=client.post(base+'/use',json={'revision':c['revision'],'enabled':True,'segment_id':seg['id']})
    assert on.status_code==200,on.text
    current=on.json()
    deleted=client.request('DELETE',base,json={'revision':current['revision']})
    assert deleted.status_code==200,deleted.text
    assert store.path(anchor['asset']).is_file()
    assert all(anchor['id'] not in v['cast_inputs']['scene_anchors'] for v in deleted.json()['shots'][0]['segments'])


def test_replacing_generated_anchor_keeps_card_lineage_and_original_provenance(scene):
    client,c,s,d,refs=scene
    store.set_setting('shared_executor',True)
    job=client.post('/api/clips/c/shots/s/cast/anchor-jobs',json=request(c,s)).json()
    candidate={'asset':refs[1],'raw':refs[2],'source':refs[0],'provenance':refs[3],
        'frame':30,'character_name':'Sam','seed':123,'segment_id':s['segments'][0]['id']}
    store.job_update(job['id'],'success',result={'asset':refs[1],'anchor_candidate':candidate})
    current=client.post(f'/api/clips/c/shots/s/cast/anchor-jobs/{job["id"]}/use',json={'revision':c['revision']}).json()
    entry=next(a for a in current['shots'][0]['cast_setup']['scene_anchors'] if a.get('provenance',{}).get('job_id')==job['id'])
    response=client.post('/api/clips/c/shots/s/cast/images',data={'revision':current['revision'],'kind':'anchor','entry_id':entry['id'],'source_frame':30},files={'file':('edited.png',store.path(refs[2]).read_bytes(),'image/png')})
    assert response.status_code==200,response.text
    updated=next(a for a in response.json()['shots'][0]['cast_setup']['scene_anchors'] if a['id']==entry['id'])
    assert updated['asset']!=entry['asset']
    assert updated['provenance']['generator']=='Manual import'
    assert updated['provenance']['job_id']==job['id']
    assert updated['provenance']['previous']==entry
    assert store.job_get(job['id'])['data']['result']['anchor_candidate']==candidate
