"""Segment edits persist without changing another segment or shared masks."""
import copy
import pytest
from h3workbench import cast, media, segment_jobs, segments, store
from test_project_characters import project
from test_cast import scene
from test_cast_integration import scene as graph_scene


def test_add_reuse_replace_remove_and_reopen_are_segment_local(project):
    client, batch, _, original, _, _ = project
    c=store.clip_get(original['id']);s=c['shots'][0];cid=c['id'];sid=s['id']
    # Empty assignments represent two untouched segments; keep library/masks.
    def clear(c):
        for seg in c['shots'][0]['segments']:
            seg.update(cast_inputs={'characters':[],'mask_tracks':[],'scene_anchors':[], 'mask_assignments':{}},prompt_mode='auto')
    c=store.clip_mutate(cid,clear,c['revision']);s=c['shots'][0]
    a,b=s['segments'];mask=s['cast_setup']['mask_tracks'][0]['id']
    sam=client.post(f'/api/batches/{batch}/characters',json={'name':'Sam'}).json()['id']
    dario=client.post(f'/api/batches/{batch}/characters',json={'name':'Dario'}).json()['id']
    base=f'/api/clips/{cid}/shots/{sid}'
    def add(seg,ids):
        current=store.clip_get(cid)
        response=client.post(base+'/cast/characters',json={'revision':current['revision'],'segment_id':seg,'character_ids':ids,'create_mask':False})
        assert response.status_code==200,response.text
        return response.json()['shots'][0]
    added=add(a['id'],[sam]);selected=added['segments'][0]['cast_inputs'];character=selected['characters'][0]
    assert added['segments'][1]==b
    selected.update(mask_tracks=[mask],mask_assignments={character:mask})
    changes={'cast_inputs':selected,'dialogue':'Segment one dialogue','performance_prompt':'Keep my exact wording',
             'speaker':'other','seed':22,'lora_id':'none','use_mask':False,'use_source_audio':False}
    response=client.patch(base+'/segments/'+a['id'],json={'revision':store.clip_get(cid)['revision'],'changes':changes})
    assert response.status_code==200,response.text
    saved=copy.deepcopy(response.json()['shots'][0]['segments'][0])
    second=add(b['id'],[sam,dario]);assert second['segments'][0]==saved
    assert len(second['cast_setup']['mask_tracks'])==len(s['cast_setup']['mask_tracks'])
    assert len(second['segments'][1]['cast_inputs']['characters'])==2
    # Removing a segment assignment leaves the library and other segment intact.
    choice=copy.deepcopy(second['segments'][1]['cast_inputs']);choice['characters']=[character]
    response=client.patch(base+'/segments/'+b['id'],json={'revision':store.clip_get(cid)['revision'],'changes':{'cast_inputs':choice}})
    assert response.status_code==200,response.text
    before_other=copy.deepcopy(response.json()['shots'][0]['segments'][1])
    response=client.patch(base+'/cast/characters/'+character,json={'revision':store.clip_get(cid)['revision'],'segment_id':a['id'],'project_character_id':dario})
    assert response.status_code==200,response.text
    final=response.json()['shots'][0];replacement=final['segments'][0]['cast_inputs']['characters'][0]
    assert final['segments'][0]['cast_inputs']['mask_assignments']=={replacement:[mask]}
    assert final['segments'][0]['cast_inputs']['mask_tracks']==[mask]
    assert final['segments'][1]==before_other
    reopened=client.get(base+'/segments').json()['segments']
    for key,value in changes.items():
        if key!='cast_inputs':assert reopened[0][key]==value
    assert reopened[1]['cast_inputs']['characters']==[character]
    assert reopened[0]['performance_prompt']=='Keep my exact wording'
    assert reopened[1].get('use_source_audio') is None
    invalid=client.post(base+'/cast/characters',json={'revision':store.clip_get(cid)['revision'],'segment_id':'missing','character_ids':[sam],'create_mask':False})
    assert invalid.status_code==400


def test_split_keeps_original_assignment_and_new_piece_starts_empty(scene):
    c,old=scene;s=cast.prepare_upgrade(c,old);original=copy.deepcopy(s['segments'][0]['cast_inputs'])
    split=segments.layout(s,{'operation':'split','frame':60})
    assert split[0]['cast_inputs']==original
    assert split[1]['cast_inputs']['characters']==[]
    assert split[1]['cast_inputs']['mask_tracks']==[]
    assert split[1]['connection']=='continue' and split[1]['history_frames']==18
    assert split[2]==s['segments'][1]


def test_segment_render_overrides_reach_graph_without_changing_neighbor(graph_scene,monkeypatch):
    _,c,s,d,_=graph_scene;before=copy.deepcopy(s['segments'][1])
    monkeypatch.setattr(media,'shot_media',lambda *a,**kw:store.path(c['source']))
    signatures=[segments.setting_signature(c,s,v) for v in s['segments']]
    s['segments'][0].update(lora_id='none',use_mask=False,use_source_audio=True)
    graph,_,stage=segment_jobs.graph(c,s,s['segments'],0,{'id':'local-settings','data':{'snapshot':c,'shot':s}},d)
    assert '850' not in graph and '43' not in graph
    assert 'ref_audios.ref_audio_0' in graph['801']['inputs']
    assert stage['data']['generation']['use_source_audio'] is True
    assert s['segments'][1]==before
    assert segments.setting_signature(c,s,s['segments'][1])==signatures[1]
    assert segments.setting_signature(c,s,s['segments'][0])!=signatures[0]
    assert cast.generation_inputs(c,s,s['segments'][0])['mask_tracks']==[]


@pytest.mark.parametrize('changes',[{'lora_id':'unknown'},{'use_mask':'false'},{'use_source_audio':0}])
def test_invalid_segment_render_settings_rejected(project,changes):
    client,_,_,original,_,_=project;c=store.clip_get(original['id']);s=c['shots'][0]
    response=client.patch(f'/api/clips/{c["id"]}/shots/{s["id"]}/segments/{s["segments"][0]["id"]}',json={'revision':c['revision'],'changes':changes})
    assert response.status_code==400
