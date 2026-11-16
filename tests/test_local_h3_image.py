import json
from pathlib import Path
import pytest
from h3workbench import local_h3_image as image, guide_provider, remote_shared, pipeline


@pytest.mark.parametrize('quality,mp', [('low',1),('medium',2),('high',4)])
@pytest.mark.parametrize('source', [[1080,1920],[1920,1080],[1024,1024]])
def test_quality_preserves_aspect_and_maps_to_pixels(quality,mp,source):
    w,h=image.dimensions(source,quality)
    assert w%32==h%32==0
    assert w*h == pytest.approx(mp*1_000_000,rel=.04)
    assert w/h == pytest.approx(source[0]/source[1],rel=.04)


def test_native_image_graph_uses_all_references_image_vae_and_full_swap_lora():
    g=image.graph('Edit Image 1 using Image 2.', 'medium', [1080,1920], [f'{i}.png' for i in range(9)], 42)
    c=g['conditioning']['inputs']
    assert c['vae']==['vae',0]
    assert c['images.image_1']==['image_1',0]
    assert c['images.image_9']==['image_9',0]
    assert 'Edit <Picture 1> using <Picture 2>.' in c['prompt']
    assert g['lora']['inputs']['strength_model']==1
    assert g['sample']['inputs']['steps']==6
    assert g['sample']['inputs']['cfg']==1
    assert g['sample']['inputs']['sampler_name']=='euler'
    assert 'image_vae' in g['vae']['inputs']['vae_name']
    assert g['save']['class_type']=='SaveImage'


def test_optional_model_dependencies_are_complete():
    from runtime.configure_installation import features_for_model
    d=json.loads((Path(__file__).parents[1]/'docker/resource-catalog.json').read_text())
    f=next(f for f in d['features'] if f['id']==image.FEATURE)
    assert f['optional']
    assert set(f['models'])=={'model-h3-image','model-h3-image-vae','model-10','model-v1-5'}
    for m in d['models']:
        if m['id'] in f['models']:
            assert image.FEATURE in m['stages']
            assert image.FEATURE in features_for_model(m['relativePath'])


def test_local_readiness_does_not_need_openai_key(monkeypatch):
    from h3workbench import local_openai
    monkeypatch.setenv('H3_GUIDE_PROVIDER','local-openai')
    monkeypatch.setattr(local_openai,'configured',lambda:False)
    monkeypatch.setattr(remote_shared,'enabled',lambda:True)
    monkeypatch.setattr(remote_shared,'all_status',lambda:{'fey':{'features':[image.FEATURE]}})
    s=guide_provider.status()
    assert s['models'][1]['available']
    assert not s['models'][0]['available']
    image.ready('auto')
    monkeypatch.setattr(remote_shared,'all_status',lambda:{})
    with pytest.raises(ValueError,match='Install H3 Image Edit'):image.ready('auto')


def test_h3_guide_uses_gpu_scheduling_not_openai_slot():
    job={'kind':'guide_generate','data':{'worker':'auto','options':{'provider':'local-h3-image'}}}
    assert pipeline.resource(job)=='auto'
    assert remote_shared.operation(job,Path('/tmp'))==image.FEATURE
    job['data']['execution_resource']='cpu'
    assert pipeline.resource(job)=='cpu'
