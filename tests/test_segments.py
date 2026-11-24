import copy
import json
import runpy
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from h3workbench import store, media, pipeline, segments, segment_jobs, render_models
from h3workbench.app import app


@pytest.fixture
def scene(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.setattr(store,'MEDIA',tmp_path)
    monkeypatch.setattr(pipeline,'start',lambda:None)
    store.init();image=tmp_path/'ref.png';Image.new('RGB',(64,64)).save(image);ref=store.asset_add(image)
    c=store.clip_create(store.batch_create('Segments'),{'id':'c','source':ref,'reference':ref,
        'dimensions':[64,64],'frames':480,'audio':True,
        'transcript':{'words':[{'word':' Hello','start':0,'end':1},{'word':' world','start':5.1,'end':6}]},
        'shots':[{'id':'s','start':0,'end':480,'mode':'generate','seed':1,'prompt':'Keep identity. Current shot dialogue: "Hello world".',
                  'dialogue':'Hello world','anchor':ref,'use_mask':False,'renders':[],'anchors':[]} ]})
    return c,c['shots'][0],tmp_path


def ready(c,s,items,i):
    seg=items[i];result={'id':'take-'+str(i)+'-'+str(len(seg['takes'])),'signature':segments.setting_signature(c,s,seg),
       'start':seg['start'],'end':seg['end'], 'dependencies':segments.dependencies(items,i),'master':'master','dimensions':media.render_spec(c,s)['dimensions']}
    seg['takes'].append(result);seg['selected_take']=result['id'];return result


def test_legacy_plan_visible_and_readonly(scene):
    c,s,_=scene;before=copy.deepcopy(s);items=segments.current(s)
    assert [(x['start'],x['end']) for x in items]==[(0,124),(124,247),(247,370),(370,480)]
    assert segments.validate(s,items)==items
    assert s==before
    assert segments.dialogue(c,s,items[0])=='Hello'
    assert '"Hello world"' not in segments.prompt(c,s,items[0])
    assert 'Current segment dialogue: "Hello"' in segments.prompt(c,s,items[0])
    assert segments.current({**s,'use_anchor':False})[0]['anchor_mode']=='none'


def test_audio_setting_invalidates_takes_and_restoring_it_restores_currentness(scene):
    c,s,_=scene
    items=segments.current(s)
    result=ready(c,s,items,0)
    original=result["signature"]
    assert segments.states(c,s,items)[0]["current"]
    s["use_source_audio"]=False
    assert segments.setting_signature(c,s,items[0]) != original
    assert not segments.states(c,s,items)[0]["current"]
    s["use_source_audio"]=True
    assert segments.setting_signature(c,s,items[0]) == original
    assert segments.states(c,s,items)[0]["current"]


def test_partition_exact_and_prompt_preserved_in_layout_history(scene):
    c,s,_=scene;s['segments']=segments.current(s);s['segments'][0]['prompt']='Smile then look down.'
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=s['segments']))
    c=store.clip_get('c')
    changed=segments.apply_layout('c','s',c['revision'],{'operation':'partition','frames':240})
    shot=changed['shots'][0]
    assert [(x['start'],x['end']) for x in shot['segments']]==[(0,240),(240,480)]
    assert shot['segments'][0]['prompt']=='Smile then look down.'
    assert shot['segments'][0]['needs_review']
    assert shot['segment_layout_history'][0]['segments'][0]['prompt']=='Smile then look down.'
    with pytest.raises(store.Conflict):segments.apply_layout('c','s',c['revision'],{'operation':'partition','frames':120})


def test_split_merge_bounds_and_no_hidden_windows(scene):
    c,s,_=scene;s['segments']=segments.layout(s,{'operation':'partition','frames':240})
    split=segments.layout(s,{'operation':'split','frame':120})
    assert [(v['start'],v['end']) for v in split]==[(0,120),(120,240),(240,480)]
    s['segments']=split
    assert len(segments.layout(s,{'operation':'merge','index':1}))==2
    s['segments']=segments.layout(s,{'operation':'merge','index':1})
    merged=segments.layout(s,{'operation':'merge','index':1})
    assert [(v['start'],v['end']) for v in merged]==[(0,480)]
    assert segments.states(c,s,merged)[0]['window_frames']==481


def test_reset_cannot_reach_across_cut(scene):
    c,s,_=scene;items=segments.layout(s,{'operation':'partition','frames':120})
    items[1]['connection']='start';items[2]['history_frames']=137
    with pytest.raises(ValueError,match='before this continuous'):segments.validate(s,items)


def test_settings_edits_preserve_but_invalidate_downstream_selected_frames(scene):
    c,s,_=scene;items=segments.current(s);s['segments']=items
    for i in range(len(items)):ready(c,s,items,i)
    assert all(x['current'] for x in segments.states(c,s))
    items[1]['prompt']='A new expression'
    assert [x['current'] for x in segments.states(c,s)]==[True,False,False,False]
    assert all(len(x['takes'])==1 for x in items)
    items[2]['connection']='start';ready(c,s,items,2);ready(c,s,items,3)
    assert [x['current'] for x in segments.states(c,s)]==[True,False,True,True]


def test_queue_freezes_exact_plan_and_requires_saved_predecessors(scene):
    c,s,_=scene;s['segments']=segments.current(s)
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=s['segments']))
    request={'mode':'segment','segment_id':s['segments'][1]['id'],'plan_revision':segments.plan_revision(s)}
    with pytest.raises(ValueError,match='Generate segment 1 first'):segment_jobs.freeze(c,s,request)
    request['mode']='all';frozen=segment_jobs.freeze(c,s,request)
    s['segments'][1]['prompt']='later edit'
    assert frozen['segments'][1]['prompt']==''
    with pytest.raises(store.Conflict):segment_jobs.freeze(c,s,request)


@pytest.mark.parametrize('field,value', [('anchor_conditioning', 'reference'), ('use_source_audio', False)])
def test_changed_settings_block_continuation_from_saved_take(scene, field, value):
    c,s,_=scene;s['anchor_conditioning']='first_frame'
    items=segments.current(s);s['segments']=items
    saved=copy.deepcopy(ready(c,s,items,0))
    s[field]=value
    state=segments.states(c,s)
    assert not state[0]['current']
    assert not state[0]['reusable']
    assert state[1]['blocked']
    with pytest.raises(ValueError, match='Generate segment 1 first'):
        segment_jobs.freeze(c,s,{'mode':'segment','segment_id':items[1]['id'], 'plan_revision':segments.plan_revision(s)})
    assert segments.take(items[0]) == saved



@pytest.mark.parametrize('change', [dict(master=None), dict(start=1), dict(end=1), dict(dimensions=[32,32]),
                                  dict(cast_inputs_snapshot={'source':'another-source','shot_start':0}),
                                  dict(cast_inputs_snapshot={'source':None,'shot_start':12})])
def test_incompatible_saved_take_cannot_supply_history(scene, change):
    c,s,_=scene;items=segments.current(s);s['segments']=items
    ready(c,s,items,0).update(change)
    assert not segments.states(c,s)[0]['reusable']
    assert segments.states(c,s)[1]['blocked']
    with pytest.raises(ValueError,match='matching source range and resolution'):
        segment_jobs.freeze(c,s,{'mode':'segment','segment_id':items[1]['id'],
                                'plan_revision':segments.plan_revision(s)})


def test_changing_selected_take_invalidates_full_chain(scene):
    c,s,_=scene;items=segments.current(s);s['segments']=items
    for i in range(len(items)):ready(c,s,items,i)
    ready(c,s,items,0)
    states=segments.states(c,s)
    assert [v['current'] for v in states]==[True,False,False,False]
    assert [v['reusable'] for v in states]==[True,False,False,False]
    assert [v['blocked'] for v in states]==[False,False,True,True]


def test_assembly_admits_selected_takes_after_settings_change(scene):
    c,s,_=scene;items=segments.current(s);s['segments']=items
    for i in range(len(items)):ready(c,s,items,i)
    s['use_source_audio']=False
    store.clip_mutate('c',lambda clip:clip['shots'][0].update(s))
    job=store.job_get(pipeline.queue('segment_assemble','c','s'))
    assert job['kind']=='segment_assemble'
    assert [v['selected_take'] for v in job['data']['shot']['segments']]==[v['selected_take'] for v in items]


@pytest.mark.parametrize('absent_frames', [list(range(92,124)), list(range(124))])
def test_target_absence_can_queue_and_preserves_empty_generation_masks(scene, monkeypatch, absent_frames):
    import numpy as np
    c,s,directory=scene
    spec={**media.render_spec(c,s),'dimensions':[64,64]}
    monkeypatch.setattr(media,'render_spec',lambda *args:spec)
    monkeypatch.setattr(media,'shot_media',lambda *args,**kwargs:store.path(c['source']))
    values=np.zeros((480,64,64),bool)
    values[:,20:40,20:40]=True
    values[absent_frames]=False
    path=directory/'actor-mask.npz';np.savez_compressed(path,mask=values)
    mask=store.asset_add(path)
    c=store.clip_mutate('c',lambda c:c['shots'][0].update(use_mask=True,mask=mask,mask_empty_frames=absent_frames))
    s=c['shots'][0];items=segments.current(s)
    job=store.job_get(pipeline.queue('render','c','s', worker="fey",options={'segments':{'mode':'segment','segment_id':items[0]['id'], 'plan_revision':segments.plan_revision(s)}}))
    assert job['state']=='queued'
    assert job['data']['shot']['mask_empty_frames']==absent_frames
    graph,files,stage=segment_jobs.graph(c,s,job['data']['segment_plan']['segments'],0,job,directory)
    with np.load(files[job['id']+'-mask.npz']) as generated:
        np.testing.assert_array_equal(generated['mask'],values[:124])
    assert graph['43']['inputs']['source_frames']==124
    assert store.clip_get('c')['shots'][0]['mask']==mask


@pytest.mark.parametrize('missing_mask', [True,False])
def test_target_absence_still_requires_saved_mask_and_applied_points(scene, missing_mask):
    c,s,_=scene
    s.update(use_mask=True,mask=None if missing_mask else c['reference'],mask_empty_frames=[100])
    if not missing_mask:
        s['mask_points']=[{'id':'pending','frame':100,'x':.5,'y':.5,'label':1}]
    items=segments.current(s)
    request={'mode':'segment','segment_id':items[0]['id'],'plan_revision':segments.plan_revision(s)}
    with pytest.raises(ValueError,match='Create a person mask' if missing_mask else 'pending mask corrections'):
        segment_jobs.freeze(c,s,request)


def test_segment_edit_optimistic_conflict_and_invalid_patch(scene):
    c,s,_=scene;key=segments.current(s)[1]['id']
    with TestClient(app) as client:
        a=client.patch(f'/api/clips/c/shots/s/segments/{key}',json={'revision':c['revision'],'changes':{'prompt':'A quiet smile.'}})
        assert a.status_code==200,a.text
        b=client.patch(f'/api/clips/c/shots/s/segments/{key}',json={'revision':c['revision'],'changes':{'prompt':'Overwrite'}})
        assert b.status_code==409
        c2=a.json()
        bad=client.patch(f'/api/clips/c/shots/s/segments/{key}',json={'revision':c2['revision'],'changes':{'seed':True}})
        assert bad.status_code==400
    assert store.clip_get('c')['shots'][0]['segments'][1]['prompt']=='A quiet smile.'


@pytest.mark.parametrize('target,h,expected',[(120,0,124),(120,18,124),(240,18,243),(360,18,362),(1,18,22),(362,0,362),(480,0,481),(480,18,481),(1440,35,1450)])
def test_explicit_pass_keeps_target_history_and_padding_separate(target,h,expected):
    plan=segments.plan_segment(target+h,h)
    assert len(plan)==1
    entry=plan[0];assert entry['delivered_frames']==target;assert entry['window_frames']==expected
    assert entry['source_start']==(h-1 if h else 0)
    assert entry['discarded_overlap']==bool(h)
    assert entry['source_end_exclusive']-entry['source_start']-entry['discarded_overlap']==target


def test_segment_planner_matches_existing_supported_ranges_and_worker_contract():
    from worker_nodes.h3_workbench_runtime.segment_planning import H3WorkbenchSegmentPlan
    for h in render_models.HISTORY_FRAMES:
        for target in [1,18,120,240,360]:
            assert segments.plan_segment(target+h,h)==render_models._windowing['plan_segment'](target+h,h)
    entries,timeline,count,windows=H3WorkbenchSegmentPlan().plan(498,17,initial_history=[None]*18)
    assert count==1 and entries==windows['windows']==json.loads(timeline)['windows']
    assert entries[0]['delivered_frames']==480 and entries[0]['window_frames']==481
    assert entries[0]['initial_history_frames']==18 and entries[0]['contract']=='h3-relay-window-v2'
    for source,h in [(0,0),(18,18),(100,2),(True,0)]:
        with pytest.raises(ValueError):segments.plan_segment(source,h)


def test_new_shot_defaults_to_one_full_length_segment_and_keeps_legacy_layouts(scene):
    from h3workbench import cast
    c,s,_=scene;legacy=segments.current(s)
    cast.initialize_shot(c,s)
    assert [(v['start'],v['end']) for v in s['segments']]==[(0,480)]
    assert s['cast_setup']['characters']==[] and s['segments'][0]['cast_inputs']['characters']==[]
    assert segments.view(c,s)['states'][0]['window_frames']==481
    s['segments']=legacy
    assert segments.current(s)==legacy


def test_lossless_master_tail_roundtrips_exact_16bit_history(scene):
    import numpy as np
    import subprocess
    c,s,directory=scene
    h,w=64,64
    pixels=(np.arange(24*h*w*3,dtype=np.uint32)%65536).astype('<u2').reshape(24,h,w,3)
    master=directory/'master.mkv'
    subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb48le','-s','64x64','-r','24','-i','pipe:0','-c:v','ffv1','-level','3','-pix_fmt','gbrp16le',str(master)],input=pixels.tobytes(),check=True)
    items=[segments.new(s,0,24,0),segments.new(s,24,48,1)]
    items[0]['takes']=[{'id':'t','master':store.asset_add(master),'dimensions':[w,h]}];items[0]['selected_take']='t'
    path=segment_jobs.history_file(items,1,[w,h],directory)
    with np.load(path) as data:
        np.testing.assert_array_equal(data['frames'],pixels[-18:].astype(np.float32)/np.float32(65535))


@pytest.mark.parametrize('masked',[False,True])
@pytest.mark.parametrize('audio',[False,True])
def test_segment_graph_crops_source_and_separates_extra_anchor(scene,monkeypatch,masked,audio):
    import numpy as np
    c,s,d=scene;s["use_source_audio"]=audio;items=segments.current(s);seg=items[1]
    if masked:
        spec={**media.render_spec(c,s),'dimensions':[64,64]}
        monkeypatch.setattr(media,'render_spec',lambda *args:spec)
        source_masks=np.zeros((480,64,64),bool)
        for n in range(480):source_masks[n,:,n%64]=True
        mask_file=d/'full-mask.npz';np.savez_compressed(mask_file,mask=source_masks)
        s.update(use_mask=True,mask=store.asset_add(mask_file))
    seg.update(anchor_mode='custom',anchor=c['reference'],anchors=[{'asset':c['reference']}])
    requested=[]
    monkeypatch.setattr(media,'shot_media',lambda c,s,d,**kw:requested.append((s['start'],s['end'])) or store.path(c['source']))
    def hist(*args):
        path=d/'history.npz';np.savez_compressed(path,frames=np.zeros((18,64,64,3),np.float32));return path
    monkeypatch.setattr(segment_jobs,'history_file',hist)
    job={'id':'job-segment','data':{'snapshot':c,'shot':s,'segment_plan':{'segments':items}}}
    graph,files,stage=segment_jobs.graph(c,s,items,1,job,d)
    assert requested==[(106,247)]
    assert graph['805']['class_type'] == 'H3WorkbenchSegmentPlan'
    assert graph['history']['inputs']['first_frame_guide'] is True
    assert graph['history']['inputs']['target_audio_guide'] is audio
    assert ('audio' in graph['history']['inputs']) is audio
    assert ('guide' in graph) is audio
    assert ('ref_audios.ref_audio_0' in graph['801']['inputs']) is audio
    assert graph['803']['inputs']['history_audio_mode']==('source' if audio else 'none')
    assert graph['807']['inputs']['audio_mode']=='source'
    assert graph['801']['inputs']['ref_images.ref_image_2']==['309',0]
    assert graph['801']['inputs']['ref_images.ref_image_1']==['803',11]
    assert graph['load_history']['inputs']['history_file']=='job-segment-history.npz'
    assert all(graph[k]['inputs']['initial_history'] == ['load_history',0] for k in ('805','803','append'))
    assert graph['save_history']['inputs']['frames'] == ['807',0]
    assert 'Current segment dialogue: "world"' in graph['801']['inputs']['prompt']
    assert '<Picture 3>' in graph['801']['inputs']['prompt']
    assert stage['data']['shot']['start']==106
    if masked:
        with np.load(files['job-segment-mask.npz']) as data:
            np.testing.assert_array_equal(data['mask'],source_masks[106:247])
        assert graph['43']['inputs']['source_frames']==141


def test_layout_changes_refuse_active_jobs(scene):
    c,s,_=scene
    store.job_create('render','c','s',{'options':{},'snapshot':c,'shot':s,'worker':'local'})
    with pytest.raises(store.Conflict,match='active jobs'):
        segments.apply_layout('c','s',c['revision'],{'operation':'partition','frames':120})


def test_batch_render_uses_saved_visible_segments(scene,monkeypatch):
    from h3workbench.app import batch_queue
    c,s,_=scene
    items=segments.layout(s,{'operation':'partition','frames':240})
    items[1]['prompt']='Smile on the final words.'
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=items,selected_render=None))
    queued=[]
    monkeypatch.setattr(pipeline,'queue',lambda *args,**kwargs:queued.append(kwargs) or 'test-job')
    monkeypatch.setattr(store,'job_get',lambda key:{'id':key,'data':{}})
    batch_queue(c['batch_id'],{'kind':'render','worker':'local'})
    options=queued[0]['options']
    assert options['segments']['mode']=='all'
    assert options['segments']['segment_id']==items[0]['id']
    assert options['segments']['plan_revision']==segments.plan_revision(store.clip_get('c')['shots'][0])


def test_select_old_take_restores_local_directions_and_checks_predecessors(scene):
    from h3workbench.segment_api import select
    c,s,_=scene;items=segments.current(s);s['segments']=items
    for i in range(len(items)):
        t=ready(c,s,items,i)
        t['settings']={k:copy.deepcopy(items[i][k]) for k in segments.EDITABLE if k in items[i]}
    old=items[1]['selected_take'];items[1]['prompt']='Changed directions';items[1]['seed']=99
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=items))
    c=store.clip_get('c');result=select('c','s',items[1]['id'],{'revision':c['revision'],'take_id':old})
    selected=result['clip']['shots'][0]
    assert selected['segments'][1]['prompt']==''
    assert selected['segments'][1]['seed']==2
    assert all(x['current'] for x in segments.states(result['clip'],selected))
    assert result['assembly_job']


@pytest.mark.parametrize('offload,profile',[(False,'full'),(True,'full-offload')])
def test_segment_dispatch_preserves_explicit_memory_policy(scene,monkeypatch,offload,profile):
    c,s,d=scene
    request={'mode':'all','segment_id':segments.current(s)[0]['id'],'plan_revision':segments.plan_revision(s)}
    job_id=pipeline.queue('render','c','s', worker="fey",options={'model_offloading':offload,'segments':request})
    job=store.job_get(job_id)
    monkeypatch.setattr(segment_jobs,'graph',lambda *a:({}, {},job))
    class ReachedWorker(Exception):pass
    def dispatch(*args,**kwargs):
        assert kwargs['profile']==profile
        raise ReachedWorker
    monkeypatch.setattr(segment_jobs.remote,'execute',dispatch)
    with pytest.raises(ReachedWorker):segment_jobs.execute(job,d)


def test_take_metadata_records_actual_pass_not_legacy_shot_defaults(scene,monkeypatch):
    c,s,d=scene;items=segments.layout(s,{'operation':'partition','frames':40});items[0]['anchor_mode']='none'
    output=d/'runtime';(output/'output').mkdir(parents=True)
    for filename in ['movie.mp4','master.mkv']:(output/'output'/filename).write_bytes(filename.encode())
    report={'windows':[{'window_frames':56}],'history_frames':0,'complete':True}
    (output/'history.json').write_text(json.dumps({'prompt':{'outputs':{
        '807':{'h3_relay_report':[report]},
        '809':{'images':[{'type':'output','subfolder':'','filename':'movie.mp4'}]},
        'save_history':{'h3_relay_history':[{'type':'output','subfolder':'','filename':'master.mkv'}]}}}}))
    (d/'api.json').write_text('{}')
    w,h=media.render_spec(c,s)['dimensions']
    monkeypatch.setattr(media,'probe',lambda _:dict(width=w,height=h,frames=40,fps=24))
    result=segment_jobs.collect({'id':'job','data':{'worker':'local'}},c,s,items,0,d,output,{})
    assert result['history_frames']==0
    assert result['window_frames']==56
    assert result['use_anchor'] is False


def test_segment_dialogue_and_speaker_do_not_silently_change_prompt(scene):
    c,s,_=scene;items=segments.current(s);key=items[1]['id']
    original=segments.prompt(c,s,items[1])
    with TestClient(app) as client:
        response=client.patch(f'/api/clips/c/shots/s/segments/{key}',json={
            'revision':c['revision'],'changes':{'dialogue':'Corrected second line.','speaker':'offscreen'}})
        assert response.status_code==200,response.text
        c=response.json();s=c['shots'][0];seg=s['segments'][1]
        assert segments.dialogue(c,s,seg)=='Corrected second line.'
        assert segments.speaker(s,seg)=='offscreen'
        assert segments.prompt(c,s,seg)==original
        assert segments.dialogue(c,s,s['segments'][0])=='Hello'
        assert s['dialogue']=='Hello world'
        revision=c['revision']
        updated=client.post(f'/api/clips/c/shots/s/segments/{key}/prompt',json={'revision':revision})
        assert updated.status_code==200,updated.text
        c=updated.json();s=c['shots'][0];text=segments.prompt(c,s,s['segments'][1])
        assert 'Current segment dialogue: "Corrected second line."' in text
        assert 'Speech is offscreen' in text
        assert 'generated boundary' in text
        assert 'exact first-frame scene' not in text
        stale=client.post(f'/api/clips/c/shots/s/segments/{key}/prompt',json={'revision':revision})
        assert stale.status_code==409


@pytest.mark.parametrize("index", [0, 1])
def test_update_prompt_respects_audio_toggle_without_erasing_dialogue(scene,index):
    c,s,_=scene
    key=segments.current(s)[index]["id"]
    exact="My edited prompt with <Audio 1> and custom timing."
    c=segments.edit("c","s",key,c["revision"],{
        "dialogue":"These are song lyrics.", "speaker":"target",
        "prompt":"Keep the hands visible.", "performance_prompt":exact})
    with TestClient(app) as client:
        disabled=client.patch("/api/clips/c/shots/s",json={
            "revision":c["revision"],"changes":{"use_source_audio":False}})
        assert disabled.status_code==200
        c=disabled.json();s=c["shots"][0]
        assert segments.prompt(c,s,s["segments"][index])==exact
        updated=client.post(f"/api/clips/c/shots/s/segments/{key}/prompt",
                           json={"revision":c["revision"]})
        assert updated.status_code==200,updated.text
        c=updated.json();s=c["shots"][0];seg=s["segments"][index]
        text=segments.prompt(c,s,seg)
        assert "<Audio" not in text and "audio" not in text.lower()
        assert "song lyrics" not in text and "dialogue" not in text.lower()
        assert "is speaking" not in text
        assert "<Video 1> determines exact timing." in text
        assert "mouth movement" in text and "Keep the hands visible." in text
        assert seg["dialogue"]=="These are song lyrics."
        assert s["segments"][1-index]["performance_prompt"] is None
        if index:
            assert "generated boundary" in text
        enabled=client.patch("/api/clips/c/shots/s",json={
            "revision":c["revision"],"changes":{"use_source_audio":True}})
        c=enabled.json();s=c["shots"][0]
        assert segments.prompt(c,s,s["segments"][index])==text
        rebuilt=client.post(f"/api/clips/c/shots/s/segments/{key}/prompt",
                           json={"revision":c["revision"]}).json()
        s=rebuilt["shots"][0]
        text=segments.prompt(rebuilt,s,s["segments"][index])
        assert "<Audio 1>" in text and "These are song lyrics." in text


def test_exact_segment_prompt_reaches_frozen_graph_unchanged(scene,monkeypatch):
    c,s,d=scene;key=segments.current(s)[0]['id']
    exact='  Replace only her.\nKeep <Picture 1> exactly.\n\nShe whispers: "Wait."  '
    c=segments.edit('c','s',key,c['revision'],{'performance_prompt':exact,'anchor_mode':'none'})
    s=c['shots'][0]
    request={'mode':'segment','segment_id':key,'plan_revision':segments.plan_revision(s)}
    job_id=pipeline.queue('render','c','s', worker="fey",options={'segments':request});job=store.job_get(job_id)
    # A later edit must not change the queued model input.
    segments.edit('c','s',key,c['revision'],{'performance_prompt':'Later prompt'})
    monkeypatch.setattr(media,'shot_media',lambda c,s,d,**kw:store.path(c['source']))
    graph,_,_=segment_jobs.graph(job['data']['snapshot'],job['data']['shot'],job['data']['segment_plan']['segments'],0,job,d)
    assert graph['801']['inputs']['prompt']==exact
    assert segments.prompt(c,s,s['segments'][0])==exact
    assert segments.prompt(c,{**s,'prompt':'Different old shared instructions'},s['segments'][0])==exact


def test_blank_segment_dialogue_is_preserved_after_transcription(scene):
    c,s,_=scene;items=segments.current(s);key=items[0]['id']
    c=segments.edit('c','s',key,c['revision'],{'dialogue':'','performance_prompt':'My exact prompt'})
    c['transcript']['words']=[{'word':' New transcription','start':0,'end':2}]
    s=c['shots'][0];assert segments.dialogue(c,s,s['segments'][0])==''
    assert segments.prompt(c,s,s['segments'][0])=='My exact prompt'


def test_new_layout_retains_exact_text_and_archives_previous(scene):
    c,s,_=scene;key=segments.current(s)[0]['id']
    c=segments.edit('c','s',key,c['revision'],{'performance_prompt':'Custom prompt','dialogue':'Custom words','speaker':'other'})
    c=segments.apply_layout('c','s',c['revision'],{'operation':'split','frame':60})
    s=c['shots'][0]
    for seg in s['segments'][:2]:
        assert seg['performance_prompt']=='Custom prompt'
        assert seg['dialogue']=='Custom words'
        assert seg['speaker']=='other'
        assert seg['needs_review']
    assert s['segment_layout_history'][-1]['segments'][0]['performance_prompt']=='Custom prompt'


def test_restore_legacy_take_clears_new_prompt_override(scene):
    from h3workbench.segment_api import select
    c,s,_=scene;items=segments.current(s);s['segments']=items
    chosen=ready(c,s,items,0)
    chosen['settings']={k:copy.deepcopy(items[0][k]) for k in segments.EDITABLE-segments.PROMPT_FIELDS if k in items[0]}
    items[0]['performance_prompt']='A new exact prompt';items[0]['dialogue']='Changed words'
    c=store.clip_mutate('c',lambda c:c['shots'][0].update(segments=items))
    restored=select('c','s',items[0]['id'],{'revision':c['revision'],'take_id':chosen['id']})['clip']
    s=restored['shots'][0]
    assert s['segments'][0]['performance_prompt'] is None
    assert segments.states(restored,s)[0]['current']


def queued_segment(index,mode='segment',worker='arch'):
    c=store.clip_get('c');s=c['shots'][0];items=segments.current(s)
    key=pipeline.queue('render','c','s',worker,{'segments':{'mode':mode,'segment_id':items[index]['id'],
        'plan_revision':segments.plan_revision(s)}})
    return store.job_get(key)


def completed_segment(job,index):
    c=job['data']['snapshot'];s=job['data']['shot'];items=job['data']['segment_plan']['segments']
    result=ready(c,s,items,index)
    result.update(id=job['id']+'-'+items[index]['id'],segment_id=items[index]['id'],start=items[index]['start'],end=items[index]['end'])
    return result


def test_independent_queue_binds_exact_outputs_and_keeps_prompts(scene):
    c,s,_=scene
    first=queued_segment(0)
    store.job_update(first['id'],'running')
    items=segments.current(s);items[1]['performance_prompt']='Exact second prompt at queue time'
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=items))
    second=queued_segment(1,worker='fey')
    third=queued_segment(2)
    assert len({first['id'],second['id'],third['id']})==3
    assert second['data']['segment_dependencies'][0]['job_id']==first['id']
    assert third['data']['segment_dependencies'][0]['job_id']==second['id']
    assert not segment_jobs.resolve_dependencies(second)
    assert store.job_get(second['id'])['data']['phase']=='Waiting for segment 1'
    with pytest.raises(store.Conflict,match='already queued'):queued_segment(1)
    store.clip_mutate('c',lambda c:c['shots'][0]['segments'][1].update(performance_prompt='Later prompt edit'))
    result=completed_segment(first,0)
    store.job_update(first['id'],segment_results=[result['id']],segment_outputs={result['segment_id']:result})
    assert segment_jobs.resolve_dependencies(store.job_get(second['id']))
    frozen=store.job_get(second['id'])['data']['segment_plan']['segments']
    assert frozen[0]['selected_take']==result['id']
    assert frozen[1]['performance_prompt']=='Exact second prompt at queue time'
    # Resolution survives a fresh database read and never substitutes a newer take.
    newer={**result,'id':'some-other-take'}
    store.job_update(first['id'],segment_outputs={result['segment_id']:newer})
    assert segment_jobs.resolve_dependencies(store.job_get(second['id']))
    assert store.job_get(second['id'])['data']['segment_plan']['segments'][0]['selected_take']==result['id']
    public=next(j for j in store.public_jobs() if j['id']==third['id'])
    assert public['data']['segment_scope']==[{'id':items[2]['id'],'index':2,'start':247,'end':370}]
    assert 'segment_plan' not in public['data']
    assert next(j for j in store.pending_jobs() if j['id']==third['id'])['data']['segment_dependencies']


@pytest.mark.parametrize('terminal',['failed','cancelled','interrupted'])
def test_unfinished_predecessor_fails_dependents_without_dispatch(scene,terminal):
    first=queued_segment(0);second=queued_segment(1);third=queued_segment(2)
    store.job_update(first['id'],terminal)
    assert not segment_jobs.resolve_dependencies(second)
    assert store.job_get(second['id'])['state']=='failed'
    assert terminal in store.job_get(second['id'])['data']['error']
    assert not segment_jobs.resolve_dependencies(third)
    assert store.job_get(third['id'])['state']=='failed'


def test_queue_all_skips_active_coverage_and_uses_its_promised_take(scene):
    first=queued_segment(0);rest=queued_segment(0,'all')
    assert rest['data']['segment_plan']['indices']==[1,2,3]
    assert rest['data']['segment_dependencies'][0]['job_id']==first['id']
    with pytest.raises(store.Conflict,match='already current'):queued_segment(0,'all')


def test_from_scope_and_future_parent_settings_conflict(scene):
    c,s,_=scene;items=segments.current(s)
    ready(c,s,items,0);ready(c,s,items,1)
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=items))
    job=queued_segment(2,'from')
    assert [v['index'] for v in store.public_job(job)['data']['segment_scope']]==[2,3]
    store.job_update(job['id'],'cancelled')
    first=queued_segment(0)
    store.clip_mutate('c',lambda c:c['shots'][0]['segments'][0].update(performance_prompt='changed since enqueue'))
    with pytest.raises(store.Conflict,match='settings changed after queueing'):queued_segment(1)


def test_scheduler_runs_frozen_chain_in_order_across_workers(scene,monkeypatch):
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    from h3workbench import remote_sessions
    c,s,d=scene
    first=queued_segment(0);second=queued_segment(1,worker='fey');third=queued_segment(2)
    order=[];seen=[]
    monkeypatch.setattr(remote_sessions,'enabled',lambda:False)
    monkeypatch.setattr(pipeline,'STOP',threading.Event())
    monkeypatch.setattr(pipeline,'ACTIVE',{})
    executor=ThreadPoolExecutor(max_workers=3);monkeypatch.setattr(pipeline,'EXEC',executor)
    monkeypatch.setattr(segment_jobs,'assemble',lambda *args:None)
    monkeypatch.setattr(segment_jobs,'graph',lambda c,s,items,i,j,d: (seen.append((i,copy.deepcopy(segments.dependencies(items,i)))) or {},{},j))
    monkeypatch.setattr(segment_jobs.remote,'execute',lambda *args,**kw:d)
    def collect(job,c,s,items,index,*args):
        result=completed_segment(job,index);result['dependencies']=segments.dependencies(items,index)
        return result
    monkeypatch.setattr(segment_jobs,'collect',collect)
    def run(id):
        job=store.job_get(id);order.append(id)
        directory=d/id;directory.mkdir()
        result=segment_jobs.execute(job,directory)
        store.job_update(id,'success',result=result)
    monkeypatch.setattr(pipeline,'execute',run)
    scheduler=threading.Thread(target=pipeline.scheduler);scheduler.start()
    try:
        limit=time.monotonic()+8
        while store.job_get(third['id'])['state']!='success' and time.monotonic()<limit:time.sleep(.05)
        assert order==[first['id'],second['id'],third['id']]
        assert store.job_get(third['id'])['state']=='success'
        assert seen[1][1][0]['take_id']==first['id']+'-'+segments.current(s)[0]['id']
        assert seen[2][1][0]['take_id']==second['id']+'-'+segments.current(s)[1]['id']
        current=segments.current(store.clip_get('c')['shots'][0])
        assert all(v['selected_take'] for v in current[:3])
        assert all(v['current'] for v in segments.states(store.clip_get('c'),store.clip_get('c')['shots'][0])[:3])
    finally:
        pipeline.STOP.set();scheduler.join(3);executor.shutdown()


def test_completed_predecessor_survives_later_batch_failure_and_legacy_receipt(scene):
    c,s,_=scene
    first=queued_segment(0)
    second=queued_segment(1)
    result=completed_segment(first,0)
    segment_jobs.attach_take(first,result)
    store.job_update(first['id'],'failed',segment_results=[result['id']])
    assert segment_jobs.resolve_dependencies(second)
    assert store.job_get(second['id'])['data']['segment_plan']['segments'][0]['selected_take']==result['id']


def test_incompatible_promised_master_never_dispatches(scene):
    first=queued_segment(0);second=queued_segment(1)
    result=completed_segment(first,0);result['dimensions']=[32,32]
    store.job_update(first['id'],'success',segment_results=[result['id']],segment_outputs={result['segment_id']:result})
    assert not segment_jobs.resolve_dependencies(second)
    assert store.job_get(second['id'])['state']=='failed'
    assert 'incompatible' in store.job_get(second['id'])['data']['error']


def test_concurrent_clicks_only_admit_one_segment_job(scene):
    from concurrent.futures import ThreadPoolExecutor
    def submit():
        try:return queued_segment(0)['id']
        except store.Conflict:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:submit(),range(2)))
    assert results.count('conflict')==1
    assert len(store.pending_jobs())==1


def test_minimum_applies_only_to_changed_ranges(scene):
    _, shot, _ = scene
    shot['end'] = shot['start'] + 107
    shot['segments'] = segments.layout(shot, {'operation': 'partition', 'frames': 48})
    assert [(s['start'], s['end']) for s in shot['segments']] == [(0, 48), (48, 107)]
    for op in ({'operation': 'split', 'frame': 23},
               {'operation': 'boundary', 'index': 1, 'frame': 84},
               {'operation': 'partition', 'frames': 23}):
        with pytest.raises(ValueError, match='at least 1 second'):
            segments.layout(shot, op)
    assert segments.layout(shot, {'operation': 'split', 'frame': 24})[1]['end'] == 48
    shot['segments'][0]['end'] = shot['segments'][1]['start'] = 1
    repaired = segments.layout(shot, {'operation':'boundary', 'index':1, 'frame':24})
    assert repaired[0]['end'] == 24
    shot['segments'] = None
    shot['end'] = shot['start'] + 12
    assert len(segments.layout(shot, {'operation':'partition', 'frames':24})) == 1
