import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import pytest

path=Path(__file__).resolve().parents[1]/'worker_nodes/h3_workbench_masks/tracking_provider.py'
spec=importlib.util.spec_from_file_location('tracking_provider_fixture',path)
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)


def test_registered_tracker_survives_a_renamed_portable_install(monkeypatch):
    namespace='captured-h3-performance.nodes';tracker_name='captured-h3-performance.core.tracking'
    module=ModuleType(namespace);tracker=ModuleType(tracker_name)
    def infer_track():pass
    infer_track.__module__=tracker_name
    module.infer_track=infer_track
    for name in ['infer_track','face_box','pose_model_path','ort']:setattr(tracker,name,infer_track)
    monkeypatch.setitem(sys.modules,namespace,module);monkeypatch.setitem(sys.modules,tracker_name,tracker)
    cls=type('FacePrepare',(),{'__module__':namespace})
    assert provider.load_tracking_provider({'H3PerformanceFacePrepare':cls}) is tracker
    with pytest.raises(ValueError,match='registered H3 performance'):provider.load_tracking_provider({})
    del tracker.face_box
    with pytest.raises(ValueError,match='incomplete'):provider.load_tracking_provider({'H3PerformanceFacePrepare':cls})
