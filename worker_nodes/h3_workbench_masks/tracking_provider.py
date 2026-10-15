"""Resolve the registered performance tracker without assuming installation names."""
import importlib


def load_tracking_provider(mapping):
    provider = mapping.get("H3PerformanceFacePrepare")
    if provider is None:
        raise ValueError("Automatic face tracking requires the registered H3 performance provider")
    module = importlib.import_module(provider.__module__)
    infer = getattr(module, "infer_track", None)
    if not callable(infer):
        raise ValueError("The registered H3 performance provider has no tracking contract")
    tracking = importlib.import_module(infer.__module__)
    if not all(hasattr(tracking, name) for name in ["infer_track", "face_box", "pose_model_path", "ort"]):
        raise ValueError("The registered H3 tracking provider contract is incomplete")
    return tracking
