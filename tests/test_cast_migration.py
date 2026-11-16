import copy
import pytest
from h3workbench import cast, segments, store, media
from test_cast import scene


def test_migration_preserves_exact_work_and_current_take_chain(scene):
    c,s=scene
    c['dimensions']=[512,512]
    for i,seg in enumerate(s['segments']):
        seg['performance_prompt']=f'My exact prompt {i} with <Picture 1>.'
        take={'id':f'take-{i}','asset':f'video-{i}','master':f'master-{i}',
              'start':seg['start'],'end':seg['end'],'dimensions':media.render_spec(c,s)['dimensions'],
              'prompt':segments.prompt(c,s,seg),'signature':segments.setting_signature(c,s,seg),
              'dependencies':segments.dependencies(s['segments'],i),'settings':{k:copy.deepcopy(seg[k]) for k in segments.EDITABLE if k in seg}}
        seg['takes']=[take];seg['selected_take']=take['id']
    before=copy.deepcopy(s); assert all(v['current'] for v in segments.states(c,s))
    assert cast.migrate_shot(c,s)
    assert all(v['current'] for v in segments.states(c,s))
    assert s['selected_render']==before['selected_render']
    assert s['cast_setup']['mask_tracks'][0]['state']['mask_versions']==before['mask_versions']
    for a,b in zip(before['segments'],s['segments']):
        assert a['performance_prompt']==b['performance_prompt']
        assert a['selected_take']==b['selected_take']
        assert b['cast_prompt_signature']==cast.prompt_signature(c,s,b)
        assert b['takes'][0]['pre_cast_signature']==a['takes'][0]['signature']
        for k in ('id','asset','master','prompt','dependencies'):assert a['takes'][0][k]==b['takes'][0][k]
    unchanged=copy.deepcopy(s);assert not cast.migrate_shot(c,s);assert s==unchanged


def test_migration_keeps_stale_takes_stale_and_empty_prompt_exact(scene):
    c,s=scene;seg=s['segments'][0];seg['performance_prompt']=''
    seg['takes'][0].update(signature='stale-signature',prompt='old prompt')
    cast.migrate_shot(c,s)
    assert segments.prompt(c,s,s['segments'][0])==''
    assert s['segments'][0]['takes'][0]['signature']=='stale-signature'
    assert not segments.states(c,s)[0]['current']


def test_migration_includes_saved_shot_and_segment_layouts(scene):
    c,s=scene
    old=copy.deepcopy(s);old['id']='older-shot'
    c.update(shots=[s],shot_layout_history=[{'shots':[old]}])
    assert cast.migrate_clip(c)
    for sh in (s,old):
        assert sh['cast_setup']
        for seg in sh['segment_layout_history'][0]['segments']:
            cast.validate_selection(sh['cast_setup'],seg['cast_inputs'])
            assert seg['cast_prompt_signature']==cast.prompt_signature(c,sh,seg)
    assert not cast.migrate_clip(c)


def test_new_shot_chooses_characters_from_project():
    c={'source':'video','reference':None,'character':''}
    s={'id':'new','start':0,'end':90,'seed':1,'prompt':'','mask':None,'anchor':None,'anchors':[]}
    cast.initialize_shot(c,s)
    assert s['cast_setup']['characters']==[]
    seg=s['segments'][0]
    assert seg['cast_inputs']=={'characters':[],'mask_tracks':[],'scene_anchors':[],'mask_assignments':{}}
    assert not seg['use_mask'] and s['cast_setup']['mask_tracks']==[]
    assert '<Picture' not in seg['performance_prompt']
    cast.validate_generation(c,s,seg)  # Prompts/history may guide a swap without references.


def test_store_migration_is_persisted_once_and_preserves_manifests(scene,tmp_path,monkeypatch):
    c,s=scene
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'MEDIA',tmp_path)
    store.init();c.update(id='clip',shots=[s]);c=store.clip_create(store.batch_create('Migration'),c)
    store.set_setting('shot_cast_schema', 0)
    cast.migrate_store();after=store.clip_get('clip')
    assert after['revision']==c['revision']+1
    assert after['shots'][0]['cast_setup']
    cast.migrate_store();assert store.clip_get('clip')==after
    assert (tmp_path/'batches'/c['batch_id']/'clip'/'revision-000001.json').exists()
