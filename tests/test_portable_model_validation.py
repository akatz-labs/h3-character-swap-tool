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
