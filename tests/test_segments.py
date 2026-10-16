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
       'dependencies':segments.dependencies(items,i),'master':'master','dimensions':media.render_spec(c,s)['dimensions']}
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
    with pytest.raises(ValueError,match='single|one diffusion'):segments.layout(s,{'operation':'merge','index':1})


def test_reset_cannot_reach_across_cut(scene):
    c,s,_=scene;items=segments.layout(s,{'operation':'partition','frames':120})
    items[1]['connection']='start';items[2]['history_frames']=137
    with pytest.raises(ValueError,match='before this continuous'):segments.validate(s,items)


def test_take_dependencies_invalidate_downstream_without_deleting(scene):
    c,s,_=scene;items=segments.current(s);s['segments']=items
    for i in range(len(items)):ready(c,s,items,i)
    assert all(x['current'] for x in segments.states(c,s))
    items[1]['prompt']='A new expression'
    assert [x['current'] for x in segments.states(c,s)]==[True,False,False,False]
    assert all(len(x['takes'])==1 for x in items)
    items[2]['connection']='start';ready(c,s,items,2);ready(c,s,items,3)
    assert [x['current'] for x in segments.states(c,s)]==[True,False,True,True]


def test_queue_freezes_exact_plan_and_requires_current_predecessors(scene):
    c,s,_=scene;s['segments']=segments.current(s)
    store.clip_mutate('c',lambda c:c['shots'][0].update(segments=s['segments']))
    request={'mode':'segment','segment_id':s['segments'][1]['id'],'plan_revision':segments.plan_revision(s)}
    with pytest.raises(ValueError,match='Generate segment 1 first'):segment_jobs.freeze(c,s,request)
    request['mode']='all';frozen=segment_jobs.freeze(c,s,request)
    s['segments'][1]['prompt']='later edit'
    assert frozen['segments'][1]['prompt']==''
    with pytest.raises(store.Conflict):segment_jobs.freeze(c,s,request)


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


@pytest.mark.parametrize('target,h,expected',[(120,0,124),(120,18,124),(240,18,243),(360,18,362),(1,18,22),(362,0,362)])
def test_explicit_pass_keeps_target_history_and_padding_separate(target,h,expected):
    plan=render_models._windowing['plan_segment'](target+h,h)
    assert len(plan)==1
    entry=plan[0];assert entry['delivered_frames']==target;assert entry['window_frames']==expected
    assert entry['source_start']==(h-1 if h else 0)
    assert entry['discarded_overlap']==bool(h)
    assert entry['source_end_exclusive']-entry['source_start']-entry['discarded_overlap']==target


def test_explicit_pass_rejects_hidden_overflow():
    with pytest.raises(ValueError):render_models._windowing['plan_segment'](362+18,18)


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
def test_segment_graph_crops_source_and_separates_extra_anchor(scene,monkeypatch,masked):
    import numpy as np
    c,s,d=scene;items=segments.current(s);seg=items[1]
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
    assert graph['803']['inputs']['segment_mode'] is True
    assert graph['803']['inputs']['use_anchor'] is True
    assert graph['803']['inputs']['segment_reference']==['309',0]
    assert graph['803']['inputs']['history_file']=='job-segment-history.npz'
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
        t['settings']={k:copy.deepcopy(items[i][k]) for k in segments.EDITABLE}
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
    job_id=pipeline.queue('render','c','s',options={'model_offloading':offload,'segments':request})
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
    report={'output':{'subfolder':'','filename':'movie.mp4'},'continuation_master':{'subfolder':'','filename':'master.mkv'},
            'windows':[{'window_frames':56}],'history_frames':0}
    (output/'history.json').write_text(json.dumps({'prompt':{'outputs':{'807':{'h3_character_swap':[report]}}}}))
    (d/'api.json').write_text('{}')
    w,h=media.render_spec(c,s)['dimensions']
    monkeypatch.setattr(media,'probe',lambda _:dict(width=w,height=h,frames=40,fps=24))
    result=segment_jobs.collect({'id':'job','data':{'worker':'local'}},c,s,items,0,d,output,{})
    assert result['history_frames']==0
    assert result['window_frames']==56
    assert result['use_anchor'] is False
