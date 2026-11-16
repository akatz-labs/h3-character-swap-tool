"""Prompt ownership through real editing routes, without worker execution."""
import copy
import io

from PIL import Image

from h3workbench import cast, segments, store
from test_cast_integration import scene


def test_automatic_references_dialogue_and_source_audio_follow_edits(scene):
    client,c,s,_,_=scene
    key=s['segments'][0]['id'];path=f'/api/clips/c/shots/s/segments/{key}'
    c=client.post(path+'/prompt',json={'revision':c['revision']}).json()
    original_other=copy.deepcopy(c['shots'][0]['segments'][1])
    seg=c['shots'][0]['segments'][0]
    choice={**seg['cast_inputs'],'characters':list(reversed(seg['cast_inputs']['characters'])),'scene_anchors':[]}
    response=client.patch(path,json={'revision':c['revision'],'changes':{'cast_inputs':choice}})
    assert response.status_code==200,response.text
    c=response.json();s=c['shots'][0];seg=s['segments'][0]
    assert seg['prompt_mode']=='auto'
    assert 'Rowan from <Picture 1>' in seg['performance_prompt']
    assert '<Picture 3>' not in seg['performance_prompt']
    assert not segments.states(c,s)[0]['prompt_needs_update']
    assert s['segments'][1]==original_other
    c=client.patch('/api/clips/c/shots/s',json={'revision':c['revision'],'changes':{'use_source_audio':True}}).json()
    c=client.patch(path,json={'revision':c['revision'],'changes':{'dialogue':'Watch the door.','speaker':'offscreen'}}).json()
    text=c['shots'][0]['segments'][0]['performance_prompt']
    assert 'Watch the door.' in text and 'Speech is offscreen' in text and '<Audio 1>' in text
    assert c['shots'][0]['segments'][1]==original_other


def test_editing_even_unchanged_prompt_pauses_auto_until_explicit_update(scene):
    client,c,s,_,_=scene;key=s['segments'][0]['id'];path=f'/api/clips/c/shots/s/segments/{key}'
    c=client.post(path+'/prompt',json={'revision':c['revision']}).json()
    exact=c['shots'][0]['segments'][0]['performance_prompt']
    c=client.patch(path,json={'revision':c['revision'],'changes':{'performance_prompt':exact}}).json()
    assert c['shots'][0]['segments'][0]['prompt_mode']=='manual'
    choice={**c['shots'][0]['segments'][0]['cast_inputs'],'scene_anchors':[]}
    c=client.patch(path,json={'revision':c['revision'],'changes':{'cast_inputs':choice,'dialogue':'Different dialogue'}}).json()
    s=c['shots'][0];seg=s['segments'][0]
    assert seg['performance_prompt']==exact
    assert not segments.states(c,s)[0]['prompt_needs_update']
    assert cast.validate_generation(c,s,seg)
    c=client.post(path+'/prompt',json={'revision':c['revision']}).json()
    seg=c['shots'][0]['segments'][0]
    assert seg['prompt_mode']=='auto' and seg['performance_prompt']!=exact
    c=client.patch(path,json={'revision':c['revision'],'changes':{'cast_inputs':{**choice,'scene_anchors':['late']}}}).json()
    assert c['shots'][0]['segments'][0]['performance_prompt']==seg['performance_prompt']


def test_legacy_anchor_upload_and_toggle_do_not_change_picture_inputs(scene):
    client,c,s,_,_=scene;key=s['segments'][0]['id'];path=f'/api/clips/c/shots/s/segments/{key}'
    c=client.post(path+'/prompt',json={'revision':c['revision']}).json()
    expected=c['shots'][0]['segments'][0]['performance_prompt']
    image=io.BytesIO();Image.new('RGB',(64,64),'orange').save(image,format='PNG')
    response=client.post('/api/clips/c/shots/s/cast/images',data={'revision':c['revision'],'kind':'anchor','segment_id':key},files={'file':('anchor.png',image.getvalue(),'image/png')})
    assert response.status_code==200,response.text
    c=response.json();s=c['shots'][0];anchor=s['cast_setup']['scene_anchors'][-1]
    assert s['segments'][0]['performance_prompt']==expected
    c=client.post(f'/api/clips/c/shots/s/cast/anchors/{anchor["id"]}/use',json={'revision':c['revision'],'segment_id':key,'enabled':False}).json()
    assert c['shots'][0]['segments'][0]['performance_prompt']==expected


def test_project_character_updates_only_automatic_prompts(scene):
    client,c,s,_,_=scene;path=f'/api/clips/c/shots/s/segments/{s["segments"][0]["id"]}'
    c=client.post(path+'/prompt',json={'revision':c['revision']}).json()
    manual=copy.deepcopy(c['shots'][0]['segments'][1])
    key=s['cast_setup']['characters'][1]['project_character_id']
    response=client.patch(f'/api/batches/{c["batch_id"]}/characters/{key}',json={'revision':1,'changes':{'description':'Wearing a green coat'}})
    assert response.status_code==200,response.text
    s=store.clip_get('c')['shots'][0]
    assert 'Wearing a green coat' in s['segments'][0]['performance_prompt']
    assert s['segments'][1]==manual


def test_new_shots_default_to_appearance_references_and_automatic_prompts(scene):
    _,c,_,_,_=scene
    shot=cast.initialize_shot(c,{'id':'new','start':0,'end':240,'seed':10,'prompt':''})
    assert shot['anchor_conditioning']=='reference'
    assert shot['segments'][0]['prompt_mode']=='auto'
    assert shot['segments'][0]['performance_prompt']==cast.build_prompt(c,shot,shot['segments'][0])


def test_transcript_and_layout_refresh_auto_but_preserve_historical_text(scene):
    client,c,s,_,_=scene;path=f'/api/clips/c/shots/s/segments/{s["segments"][0]["id"]}'
    c=client.post(path+'/prompt',json={'revision':c['revision']}).json()
    c=client.patch('/api/clips/c/shots/s',json={'revision':c['revision'],'changes':{'use_source_audio':True}}).json()
    def transcribe(v):
        v['transcript']={'words':[{'word':' Early','start':0,'end':1},{'word':' Later','start':3,'end':4}]}
    c=store.clip_mutate('c',transcribe,c['revision'])
    assert 'Early Later' in c['shots'][0]['segments'][0]['performance_prompt']
    old=copy.deepcopy(c['shots'][0]['segments'])
    c=segments.apply_layout('c','s',c['revision'],{'operation':'split','frame':60})
    s=c['shots'][0]
    assert 'Early' in s['segments'][0]['performance_prompt'] and 'Later' not in s['segments'][0]['performance_prompt']
    assert 'Later' in s['segments'][1]['performance_prompt'] and 'Early' not in s['segments'][1]['performance_prompt']
    assert s['segment_layout_history'][-1]['segments']==old
    assert s['segments'][2]==old[1]
