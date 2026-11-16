"""Editable helpers must reach the exact frozen paid request and survive reopen."""
import copy
import pytest
from h3workbench import guide_prompts, guide_gateway, guides, store
from test_guides import scene, add, upload


def test_legacy_guide_has_no_hidden_character_prompt(scene):
    client,_,_=scene;key=add(client)
    # Historical records keep their original helper selection.
    store.clip_mutate('c',lambda c:c['shots'][0]['guides'][0].pop('helpers'))
    data=client.get('/api/clips/c/shots/s/guides').json();guide=data['guides'][0]
    assert guide['id']==key and guide['helpers']==[]
    assert len(data['helper_presets'])==3
    assert guide_prompts.compose({'prompt':'Only edit the shirt.'})=='Only edit the shirt.'
    assert guide_prompts.compose({'prompt':'  My exact instruction.\n', 'reference_labels':['A character']})=='  My exact instruction.\n'


def test_new_guide_defaults_to_visible_character_swap_helper_and_can_disable_it(scene):
    client,_,_=scene;key=add(client)
    guide=client.get('/api/clips/c/shots/s/guides').json()['guides'][0]
    preset=next(p for p in guide_prompts.presets() if p['id']=='character-swap')
    assert guide['helpers']==[{'id':preset['id'],'text':preset['text']}]
    response=client.post('/api/clips/c/shots/s/guides/'+key+'/save',json={
        'revision':store.clip_get('c')['revision'],'changes':{'helpers':[]},'version_id':None})
    assert response.status_code==200,response.text
    assert client.get('/api/clips/c/shots/s/guides').json()['guides'][0]['helpers']==[]


def test_helpers_save_restore_freeze_and_reach_private_provider(scene,monkeypatch,tmp_path):
    client,_,_=scene;key=add(client);guide=upload(client,key,'reference')
    monkeypatch.setenv('H3_GUIDE_PROVIDER','private-openai')
    monkeypatch.setattr(guide_gateway,'status',lambda:{'configured':True,'available':True})
    original=guide_prompts.presets()[0];helper={'id':original['id'],'text':original['text']+' Preserve the hat exactly.'}
    changes={'prompt':'Replace the man in the orange shirt.','references':guide['references'],'quality':'medium','helpers':[helper]}
    path='/api/clips/c/shots/s/guides/'+key
    saved=client.post(path+'/save',json={'revision':store.clip_get('c')['revision'],'changes':changes,'version_id':None})
    assert saved.status_code==200,saved.text
    reopened=client.get('/api/clips/c/shots/s/guides').json()['guides'][0]
    assert reopened['helpers']==[helper]
    response=client.post(path+'/generate',json={'revision':store.clip_get('c')['revision'],'changes':changes})
    assert response.status_code==200,response.text
    job=store.job_get(response.json()['id']);frozen=job['data']['options']
    expected=guide_prompts.compose(frozen['guide'])
    assert frozen['full_prompt']==expected
    assert expected.index(changes['prompt'])<expected.index(helper['text'])
    assert expected==changes['prompt']+'\n\nCharacter swap:\n'+helper['text']
    assert store.clip_get('c')['shots'][0]['guides'][0]['versions']==[]
    # Later draft/catalog edits cannot alter the exact accepted request.
    later=copy.deepcopy(changes);later['helpers'][0]['text']='A later helper draft'
    assert client.patch(path,json={'revision':store.clip_get('c')['revision'],'changes':later}).status_code==200
    monkeypatch.setattr(guide_prompts,'PRESETS',[{'id':'changed-preset','label':'Changed','text':'Changed'}])
    seen=[]
    def provider(job,directory,prompt,quality,inputs,size):
        seen.append((prompt,quality,inputs))
        assert prompt==expected and quality=='medium' and len(inputs)==2 and size=='96x64'
        return inputs[0]
    monkeypatch.setattr(guide_gateway,'run',provider)
    directory=tmp_path/'generation';directory.mkdir()
    guides.execute(job,directory)
    current=store.clip_get('c')['shots'][0]['guides'][0]
    version=current['versions'][-1]
    assert version['helpers']==[helper] and version['full_prompt']==expected
    assert current['helpers']==later['helpers'] and current['approved']==job['id']
    assert len(seen)==1


@pytest.mark.parametrize('helpers',[None,{},[{'id':'unknown','text':'x'}],
    [{'id':'character-swap','text':'x','hidden':'x'}],
    [{'id':'character-swap','text':''}],
    [{'id':'character-swap','text':'x'*6001}],
    [{'id':'character-swap','text':'x'}]*2])
def test_invalid_helper_settings_rejected_at_save_and_generate(scene,helpers):
    client,_,_=scene;key=add(client);path='/api/clips/c/shots/s/guides/'+key
    original=store.clip_get('c')['shots'][0]['guides'][0]['helpers']
    body={'revision':store.clip_get('c')['revision'],'changes':{'helpers':helpers},'version_id':None}
    for route in ['/save','/generate']:
        response=client.post(path+route,json=body)
        assert response.status_code==400,response.text
    assert store.clip_get('c')['shots'][0]['guides'][0]['helpers']==original


def test_helper_only_prompt_is_valid_and_compiled(scene,monkeypatch):
    client,_,_=scene;key=add(client)
    monkeypatch.setenv('H3_GUIDE_PROVIDER','private-openai')
    monkeypatch.setattr(guide_gateway,'status',lambda:{'configured':True,'available':True})
    helpers=[{'id':'scene-edit','text':'Keep the framing and replace only the wall finish.'}]
    response=client.post('/api/clips/c/shots/s/guides/'+key+'/generate',json={
        'revision':store.clip_get('c')['revision'],'changes':{'prompt':'','helpers':helpers}})
    assert response.status_code==200,response.text
    frozen=store.job_get(response.json()['id'])['data']['options']
    assert frozen['full_prompt'].endswith(helpers[0]['text'])
    assert frozen['guide']['helpers']==helpers


def test_stock_upgrade_changes_only_legacy_drafts_once(scene, monkeypatch):
    client,_,_=scene;key=add(client);guide=upload(client,key)
    stock={'id':'character-swap','text':guide_prompts.LEGACY_CHARACTER_SWAP}
    custom={'id':'character-swap','text':stock['text']+' Keep my custom detail.'}
    def prepare(c):
        g=c['shots'][0]['guides'][0];g['helpers']=[copy.deepcopy(stock)]
        g['versions'][0].update(helpers=[copy.deepcopy(stock)],full_prompt='Historical exact request')
        second=copy.deepcopy(g);second.update(id='custom-guide',helpers=[custom]);c['shots'][0]['guides'].append(second)
    store.clip_mutate('c',prepare)
    before=store.clip_get('c')
    job_id=store.job_create('guide_generate','c','s',{'options':{'guide':copy.deepcopy(before['shots'][0]['guides'][0]),'full_prompt':'Frozen old prompt'}})
    accepted=store.job_get(job_id)['data']
    store.set_setting('guide_camera_lock_prompt_schema',0)
    guide_prompts.migrate_store()
    after=store.clip_get('c');guides_after=after['shots'][0]['guides']
    assert guides_after[0]['helpers']==[{'id':'character-swap','text':guide_prompts.presets()[0]['text']}]
    for field in ['prompt','references','quality','approved','versions','enabled','source_asset']:
        assert guides_after[0][field]==before['shots'][0]['guides'][0][field]
    assert guides_after[1]==before['shots'][0]['guides'][1]
    assert store.job_get(job_id)['data']==accepted
    # Explicitly restoring an older helper after the one-time upgrade remains exact.
    store.clip_mutate('c',lambda c:c['shots'][0]['guides'][0].update(helpers=[stock]))
    restored=store.clip_get('c')
    guide_prompts.migrate_store()
    assert store.clip_get('c')==restored


def test_multiple_full_references_and_source_canvas_reach_paid_request(scene,monkeypatch,tmp_path):
    import base64,io,json,httpx
    from PIL import Image
    client,_,_=scene;key=add(client)
    guide=upload(client,key,'reference')
    # Different aspect ratios and edge details must be retained, without generic crops.
    ref1=tmp_path/'one.png';ref2=tmp_path/'two.png';original=tmp_path/'original.png';working=tmp_path/'working.png'
    Image.new('RGB',(320,704),'#37aa55').save(ref1)
    Image.new('RGB',(912,304),'#aa3755').save(ref2)
    Image.new('RGB',(704,1408),'blue').save(original)
    Image.new('RGB',(887,1774),'yellow').save(working)
    ids=[store.asset_add(ref1),store.asset_add(ref2)]
    original_asset,working_asset=store.asset_add(original),store.asset_add(working)
    def setup(c):
        c['dimensions']=[704,1408]
        s=c['shots'][0];g=s['guides'][0]
        g.update(dimensions=[704,1408],source_asset=original_asset,edit_source_asset=working_asset,
                 versions=[{'id':'old-auto-sized-version','asset':working_asset}])
        s.setdefault('guide_references',[]).extend({'asset':v,'name':f'Character {i+1}','kind':'Uploaded image'} for i,v in enumerate(ids))
    store.clip_mutate('c',setup)
    monkeypatch.setenv('H3_GUIDE_PROVIDER','private-openai')
    monkeypatch.setenv('H3_GUIDE_OPENAI_SOCKET','/run/h3-guides/openai.sock')
    monkeypatch.setattr(guide_gateway,'status',lambda:{'configured':True,'available':True})
    requests=[]
    result=io.BytesIO();Image.new('RGB',(704,1408),'green').save(result,format='PNG')
    def handle(request):
        body=json.loads(request.content);requests.append(body)
        assert body['model']=='gpt-image-2.6-flare' and body['quality']=='medium'
        assert body['size']=='704x1408'
        assert body['prompt'].startswith('Replace the left actor with Image 2 and the right actor with Image 3.\n\nCharacter swap:\n')
        assert 'Image 1 is the sole authority' in body['prompt']
        assert 'Images 2 onward' in body['prompt'] and 'Do not merge different identities' in body['prompt']
        assert 'mask' not in body
        with Image.open(io.BytesIO(base64.b64decode(body['images'][0]['image_url'].split(',')[1]))) as sent:
            assert sent.size==(887,1774) and sent.tobytes()==Image.open(working).tobytes()
        for encoded,path in zip(body['images'][1:],[ref1,ref2],strict=True):
            with Image.open(io.BytesIO(base64.b64decode(encoded['image_url'].split(',')[1]))) as sent,Image.open(path) as original:
                assert sent.size==original.size and sent.tobytes()==original.tobytes()
        return httpx.Response(200,json={'state':'complete','image':base64.b64encode(result.getvalue()).decode()})
    monkeypatch.setattr(guide_gateway,'client',lambda socket=None:httpx.Client(transport=httpx.MockTransport(handle),base_url='http://guide.test'))
    response=client.post('/api/clips/c/shots/s/guides/'+key+'/generate',json={
        'revision':store.clip_get('c')['revision'],'changes':{'prompt':'Replace the left actor with Image 2 and the right actor with Image 3.','references':ids}})
    assert response.status_code==200,response.text
    job=store.job_get(response.json()['id'])
    assert job['data']['options']['image_size']=='704x1408'
    # Later canvas settings cannot change a queued request, including a follow-up source.
    store.clip_mutate('c',lambda c:c.update(dimensions=[1344,736]))
    directory=tmp_path/'multiple-refs';directory.mkdir()
    guides.execute(job,directory)
    saved=store.clip_get('c')['shots'][0]['guides'][0]['versions'][-1]
    assert saved['requested_size']=='704x1408' and saved['dimensions']==[704,1408]
    assert saved['references']==ids and len(requests)==1
