"""Segment mask ownership, migration, frame bounds and independent persistence."""
import copy
import numpy as np
import pytest
from h3workbench import cast, masks, segments, segment_masks, store, media
from test_cast import scene
from test_project_characters import project


def test_migration_is_independent_and_keeps_saved_takes(scene):
    c, old = scene
    s = cast.prepare_upgrade(c, old)
    original = copy.deepcopy(s)
    assert segment_masks.migrate_shot(c, s)
    a, b = s['segments']; first, second = s['cast_setup']['mask_tracks']
    assert first['id'] != second['id']
    assert first['state']['mask'] == second['state']['mask'] == 'legacy-mask-asset'
    assert first['segment_id'] == a['id'] and second['segment_id'] == b['id']
    before = segments.setting_signature(c, s, a)
    second['state']['expansion'] = 128
    second['state']['mask_points'] = [{'id':'b','frame':130,'x':.2,'y':.4,'label':1}]
    assert segments.setting_signature(c, s, a) == before
    assert a['takes'] == original['segments'][0]['takes']
    assert a['selected_take'] == original['segments'][0]['selected_take']
    assert not segment_masks.migrate_shot(c, s)
    assert second['state']['mask_applied_points'] == []
    a['cast_inputs']['mask_tracks'] = [second['id']]
    with pytest.raises(ValueError, match='belonging'): segments.validate(s, s['segments'])


def test_lazy_mask_creation_delete_recreate_stays_optional(project):
    client, _, _, original, _, _ = project
    cid, sid = original['id'], original['shots'][0]['id']
    def migrate(c):
        segment_masks.migrate_shot(c, c['shots'][0])
    c = store.clip_mutate(cid, migrate); s=c['shots'][0]; a,b=s['segments']
    before=copy.deepcopy(a); first_track=copy.deepcopy(s['cast_setup']['mask_tracks'][0])
    base=f'/api/clips/{cid}/shots/{sid}'
    # Removing the existing second-segment mask leaves an empty editable slot.
    key=b['cast_inputs']['mask_tracks'][0]
    result=client.request('DELETE',base+'/cast/masks/'+key,json={'revision':c['revision']})
    assert result.status_code==200,result.text
    c=result.json();assert not c['shots'][0]['segments'][1]['use_mask']
    result=client.post(base+'/cast/masks',json={'revision':c['revision'],'segment_id':b['id'],
        'points':[{'id':'point','frame':130,'x':.5,'y':.4,'label':1}]})
    assert result.status_code==200,result.text
    c=result.json();sh=c['shots'][0];track=sh['cast_setup']['mask_tracks'][-1]
    assert track['name']=='Mask 1' and track['segment_id']==b['id']
    assert not sh['segments'][1]['use_mask'] and sh['segments'][1]['cast_inputs']['mask_tracks']==[]
    assert sh['segments'][0]==before and sh['cast_setup']['mask_tracks'][0]==first_track
    bad=client.patch(base+'?track_id='+track['id'],json={'revision':c['revision'],'changes':{'mask_points':[{'id':'bad','frame':2,'x':.5,'y':.4,'label':1}]}})
    assert bad.status_code==400
    chosen=copy.deepcopy(sh['segments'][1]['cast_inputs']);chosen['mask_tracks']=[]
    result=client.patch(base+'/segments/'+b['id'],json={'revision':c['revision'],'changes':{'cast_inputs':chosen}})
    assert result.status_code==200,result.text
    saved=store.clip_get(cid)['shots'][0]
    assert not saved['segments'][1]['use_mask'] and saved['segments'][0]==before
    assert saved['cast_setup']['mask_tracks'][-1]==track


def test_tracking_only_reads_segment_and_stores_shot_coordinates(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'MEDIA',tmp_path);store.init()
    c={'source':'video','dimensions':[8,8]}
    s={'start':50,'end':70,'mask_segment_range':[7,15],'mask_points':[{'id':'p','frame':10,'x':.5,'y':.5,'label':1}]}
    job={'id':'track','data':{'options':{'mode':'shot','frame':10}}}
    ranges=[]
    monkeypatch.setattr(media,'shot_media',lambda c,s,d:ranges.append([s['start'],s['end']]) or tmp_path/'source.mkv')
    monkeypatch.setattr(media,'ff',lambda args:None)
    graph,files,plan=masks.graph(c,s,job,tmp_path)
    assert ranges==[[57,65]]
    assert graph['100']['inputs']['batch_index']==3
    assert graph['102']['inputs']['batch_index']==3 and graph['102']['inputs']['length']==5
    assert graph['45']['inputs']['frames']==4
    assert [(v['start'],v['end']) for v in plan['segments']]==[(10,15),(7,10)]
    (tmp_path/'output').mkdir()
    for part in plan['segments']:
        n=part.get('frames',part['end']-part['start'])
        np.savez_compressed(tmp_path/'output'/part['file'],mask=np.ones((n,8,8)))
    result=masks.finish(c,s,job,tmp_path,tmp_path,plan)
    values=masks.read(result['asset'],20,[8,8])
    assert values[7:15].all() and not values[:7].any() and not values[15:].any()


def test_new_layout_cannot_share_mutable_mask_records(scene):
    c,old=scene;s=cast.prepare_upgrade(c,old);segment_masks.migrate_shot(c,s)
    items=segments.layout(s,{'operation':'split','frame':60})
    old_first=copy.deepcopy(s['cast_setup']['mask_tracks'][0])
    segment_masks.rebind_layout(s,items);s['segments']=items
    segments.validate(s,items);cast.validate_catalog(s,s['cast_setup'])
    assert items[1]['cast_inputs']['mask_tracks']==[] and not items[1]['use_mask']
    first=cast.track(s,items[0]['cast_inputs']['mask_tracks'][0])
    assert first['id']!=old_first['id'] and first['segment_id']==items[0]['id']
    assert any(t['id']==old_first['id'] for t in s['mask_track_history'])


def test_restoring_take_retains_mask_history_and_other_segment(scene):
    c,old=scene;s=cast.prepare_upgrade(c,old);segment_masks.migrate_shot(c,s)
    a,b=s['segments'];first,second=s['cast_setup']['mask_tracks']
    unchanged=copy.deepcopy(second)
    take={'cast_inputs_snapshot':{'mask_tracks':[{'id':first['legacy_id'],'mask':'take-mask','expansion':12}]}}
    segment_masks.restore_take(s,a,take)
    assert first['state']['mask']=='take-mask' and first['state']['expansion']==12
    assert {'original-mask','legacy-mask-asset','take-mask'} <= {v['asset'] for v in first['state']['mask_versions']}
    assert second==unchanged
    assert a['cast_inputs']['mask_tracks']==[first['id']]
    cast.validate_catalog(s,s['cast_setup'])


def test_old_mask_version_filters_points_to_owner(project,monkeypatch):
    client,_,_,original,_,_=project
    cid,sid=original['id'],original['shots'][0]['id']
    def prepare(c):
        c['dimensions']=[2,2]
        segment_masks.migrate_shot(c,c['shots'][0])
    c=store.clip_mutate(cid,prepare)
    track=c['shots'][0]['cast_setup']['mask_tracks'][1]
    monkeypatch.setattr(masks,'read',lambda *args:np.zeros((240,2,2),dtype=bool))
    result=client.patch(f'/api/clips/{cid}/shots/{sid}?track_id={track["id"]}',json={
        'revision':c['revision'],'changes':{'mask_version':'legacy-mask-asset'}})
    assert result.status_code==200,result.text
    saved=result.json()['shots'][0]['cast_setup']['mask_tracks'][1]
    assert saved['state']['mask_points']==[]


def test_mask_rename_preserves_generation_settings_and_outputs(project):
    client,_,_,original,_,_=project
    cid,sid=original['id'],original['shots'][0]['id']
    c=store.clip_mutate(cid,lambda c:segment_masks.migrate_shot(c,c['shots'][0]))
    track=c['shots'][0]['cast_setup']['mask_tracks'][0]
    before=copy.deepcopy(c)
    sh=c['shots'][0]
    signatures=[segments.setting_signature(c,sh,seg) for seg in sh['segments']]
    path=f'/api/clips/{cid}/shots/{sid}/cast/masks/{track["id"]}'
    response=client.patch(path,json={'revision':c['revision'],'name':'  Lead actor  '})
    assert response.status_code==200,response.text
    after=response.json()
    expected=copy.deepcopy(before)
    expected['revision']+=1
    expected['shots'][0]['cast_setup']['mask_tracks'][0]['name']='Lead actor'
    assert after==expected
    sh=after['shots'][0]
    assert [segments.setting_signature(after,sh,seg) for seg in sh['segments']]==signatures
    assert store.clip_get(cid)==expected
    bad=client.patch(path,json={'revision':after['revision'],'name':'  '})
    assert bad.status_code==400
    assert store.clip_get(cid)==expected
    stale=client.patch(path,json={'revision':before['revision'],'name':'Stale name'})
    assert stale.status_code==409
    assert store.clip_get(cid)==expected


def test_shared_character_mask_survives_character_removal_and_mask_delete(project):
    client, batch, _, original, _, _ = project
    cid, sid = original['id'], original['shots'][0]['id']
    c = store.clip_mutate(cid, lambda c: segment_masks.migrate_shot(c, c['shots'][0]))
    s = c['shots'][0]; seg = s['segments'][0]; mask = seg['cast_inputs']['mask_tracks'][0]
    base = f'/api/clips/{cid}/shots/{sid}'
    new = client.post(f'/api/batches/{batch}/characters', json={'name':'Second actor'}).json()['id']
    response = client.post(base+'/cast/characters', json={'revision':c['revision'],'segment_id':seg['id'],'character_ids':[new],'create_mask':False})
    assert response.status_code == 200, response.text
    c = response.json(); chosen = c['shots'][0]['segments'][0]['cast_inputs']; a,b=chosen['characters']
    cast.apply_character_masks(chosen,{a:[mask],b:[mask]})
    response=client.patch(base+'/segments/'+seg['id'],json={'revision':c['revision'],'changes':{'cast_inputs':chosen}})
    assert response.status_code==200,response.text
    c=response.json();other=copy.deepcopy(c['shots'][0]['segments'][1])
    response=client.request('DELETE',base+'/cast/characters/'+a,json={'revision':c['revision']})
    assert response.status_code==200,response.text
    c=response.json();chosen=c['shots'][0]['segments'][0]['cast_inputs']
    assert chosen['mask_tracks']==[mask] and chosen['mask_assignments']=={b:[mask]}
    # The deleted character may also be removed from another segment, but deleting
    # this segment's mask must leave the resulting other segment untouched.
    other=copy.deepcopy(c['shots'][0]['segments'][1])
    response=client.request('DELETE',base+'/cast/masks/'+mask,json={'revision':c['revision']})
    assert response.status_code==200,response.text
    c=response.json();seg=c['shots'][0]['segments'][0]
    assert seg['cast_inputs']['mask_assignments']=={} and not seg['use_mask']
    assert c['shots'][0]['segments'][1]==other
