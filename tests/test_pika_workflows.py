"""Standalone examples must retain the app recipe and media routing contract."""
import json
from pathlib import Path
import pytest
from scripts.export_pika_workflows import standalone

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('guided', [False, True])
def test_standalone_pika_recipe_and_routing(guided):
    graph = standalone(guided)
    def node(kind):
        return next((key, value['inputs']) for key, value in graph.items() if value['class_type'] == kind)
    loras = [n['inputs'] for n in graph.values() if n['class_type'] == 'LoraLoaderModelOnly']
    assert [n['strength_model'] for n in loras] == [1.0, 1.0]
    assert node('ManualSigmas')[1]['sigmas'] == '1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0'
    assert node('KSamplerSelect')[1]['sampler_name'] == 'lcm'
    prepare, _ = node('H3RelayPrepareSource')
    text = node('MiniMaxH3ReferenceToVideo')[1]
    assert text['ref_videos.ref_video_0'] == [prepare, 0]
    assert text['ref_video_audios.ref_video_audio_0'] == [prepare, 1]
    trim, trim_inputs = node('ImageFromBatch')
    assert trim_inputs['length'] == [prepare, 5]
    assert node('CreateVideo')[1]['images'] == [trim, 0]
    assert node('CreateVideo')[1]['audio'] == [prepare, 1]
    refs = [v for k, v in text.items() if k.startswith('ref_images.ref_image_')]
    assert len(refs) == 1
    if guided:
        guide, guide_inputs = node('MiniMaxH3AddGuide')
        assert guide_inputs['frame_idx'] == 0
        assert node('BasicGuider')[1]['conditioning'] == [guide, 0]
    else:
        assert not any(n['class_type'] == 'MiniMaxH3AddGuide' for n in graph.values())


@pytest.mark.parametrize('guided,name', [(False, 'Pika Character Swap'), (True, 'Pika Guided Character Swap')])
def test_downloads_match_generator_and_have_no_missing_links(guided, name):
    api = json.loads((ROOT / 'workflows/pika' / (name + '.api.json')).read_text())
    assert api == standalone(guided)
    canvas = json.loads((ROOT / 'workflows/pika' / (name + '.json')).read_text())
    nodes = {n['id']: n for n in canvas['nodes']}
    assert len(nodes) == len(api) + 4  # four setup/usage notes
    for link in canvas['links']:
        _, origin, output, target, input_slot, _ = link
        assert nodes[origin]['outputs'][output]['links']
        assert nodes[target]['inputs'][input_slot]['link'] == link[0]
    assert any(n.get('title') == 'Akatz Labs · links & support' for n in nodes.values())
