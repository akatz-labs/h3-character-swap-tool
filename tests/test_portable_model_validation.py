"""Content identity remains authoritative when models arrive after image build."""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

spec=importlib.util.spec_from_file_location('portable_controller',Path(__file__).parents[1]/'runtime/tools/managed-comfy-session.py')
controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)


def test_missing_optional_model_does_not_block_an_unrelated_stage(tmp_path):
    session=SimpleNamespace(env=tmp_path,contract={'stage_model_validation':True,'models':{'a.bin':{'bytes':4,'sha256':hashlib.sha256(b'good').hexdigest()}}})
    controller.Session.verify_models(session,[])
    with pytest.raises(FileNotFoundError):controller.Session.verify_models(session,['a.bin'])
    path=tmp_path/'ComfyUI/models/a.bin';path.parent.mkdir(parents=True);path.write_bytes(b'good')
    controller.Session.verify_models(session,['a.bin'])
    assert 'a.bin' in session.verified_model_identities
    path.write_bytes(b'evil')
    with pytest.raises(AssertionError,match='Model bytes differ'):controller.Session.verify_models(session,['a.bin'])


def test_older_machine_identity_contract_remains_strict(tmp_path):
    path=tmp_path/'ComfyUI/models/a.bin';path.parent.mkdir(parents=True);path.write_bytes(b'good')
    session=SimpleNamespace(env=tmp_path,contract={'models':{'a.bin':{'identity':{'size':4,'mtime_ns':0,'inode':0,'device':0}}}})
    with pytest.raises(AssertionError,match='Verified model file changed'):controller.Session.verify_models(session)


@pytest.mark.parametrize('before,placement,expected', [('full','offload','offload'),('offload','full','full')])
def test_stage_restarts_owned_idle_runtime_when_offloading_changes(tmp_path, monkeypatch, before, placement, expected):
    import json
    graphfile=tmp_path/'graph.json';graphfile.write_text(json.dumps({'save':{'class_type':'SaveImage','inputs':{}}}))
    session=controller.Session.__new__(controller.Session)
    session.media=tmp_path;session.root=tmp_path/'session';session.root.mkdir();session.env=tmp_path
    session.schemas={'SaveImage':{}};session.contract={};session.launch_residency=before
    session.verify_idle=lambda:None;session.verify_models=lambda *args:None
    session.runtime_identity={'pid':123};session.process=SimpleNamespace(pid=123,wait=lambda timeout:None)
    session.publish=lambda **kw:None
    monkeypatch.setattr(controller.os,'killpg',lambda *args:None)
    monkeypatch.setattr(controller,'process_identity',lambda pid:None)
    monkeypatch.setattr(controller,'compute_pids',lambda:[])
    launches=[]
    session.start=lambda residency:launches.append(residency)
    class Prepared(Exception):pass
    def cleanup(*args,**kwargs):raise Prepared()
    session.cleanup=cleanup
    packet={'directory':str(tmp_path/'job'),'graph':str(graphfile),'graph_sha256':controller.sha(graphfile),
            'attempt':'attempt','placement':placement,'family':{'key':'image','label':'H3'}}
    with pytest.raises(Prepared):session.stage(packet)
    assert launches==[expected]
