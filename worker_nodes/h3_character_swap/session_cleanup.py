"""H3-specific tensor history cleanup supplied to the shared cache provider."""
import importlib


def on_prompt_end(prompt_id):
    import nodes
    cls = nodes.NODE_CLASS_MAPPINGS.get("H3CharacterSwapBodyWindow")
    windows = 0
    if cls:
        records = importlib.import_module(cls.__module__.rsplit(".", 1)[0] + ".records")
        records.begin("owned-stage-released", [])
        windows = records.status()["cached_windows"]
    return {"prompt_id": prompt_id, "relay_cached_windows": windows}
