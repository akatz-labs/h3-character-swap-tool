"""Workbench conditioning switches around the pinned performance relay."""

import copy
import hashlib
import importlib
import json


def relay(name):
    import nodes

    return nodes.NODE_CLASS_MAPPINGS[name]


def relay_module(name):
    root = relay("H3RelayPerformanceBodyWindow").__module__.rsplit(".", 1)[0]
    return importlib.import_module(root + "." + name)


def replace_links(graph, node_id, outputs):
    """Bypass a node without changing any other native inputs."""
    for node in graph.values():
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and value[0] == node_id:
                node["inputs"][key] = copy.deepcopy(outputs[value[1]])
    del graph[node_id]


def conditioning_graph(expansion, use_anchor, use_mask, history_frames):
    graph = expansion["expand"]
    if not use_anchor:
        for node in graph.values():
            if node["class_type"] == "H3PerformanceReference":
                node["inputs"].pop("ref_images.ref_image_1", None)
        if history_frames == 0:
            for key, node in list(graph.items()):
                if node["class_type"] == "H3RelayPerformanceHistory":
                    replace_links(graph, key, [node["inputs"]["positive"], node["inputs"]["latent"]])
    if not use_mask:
        for key, node in list(graph.items()):
            if node["class_type"] == "H3PerformanceMaskedRegeneration":
                replace_links(graph, key, [node["inputs"]["latent"]])
    return expansion


class GenerationShot:
    @classmethod
    def INPUT_TYPES(cls):
        schema = copy.deepcopy(relay("H3PilotBodyShot").INPUT_TYPES())
        optional = schema.setdefault("optional", {})
        for name in ("anchor", "body_mask"):
            optional[name] = schema["required"].pop(name)
        schema["required"].update(use_anchor=("BOOLEAN", {"default": True}),
                                  use_mask=("BOOLEAN", {"default": True}))
        return schema

    RETURN_TYPES = ("H3_RELAY_SHOT",)
    FUNCTION = "prepare"
    CATEGORY = "H3 Workbench/generation"

    def prepare(self, shot_id, use_anchor, use_mask, **kwargs):
        import torch

        if use_anchor and kwargs.get("anchor") is None:
            raise ValueError("Anchor conditioning requires an anchor image")
        if use_mask and kwargs.get("body_mask") is None:
            raise ValueError("Masked diffusion requires a person mask")
        # Shape placeholders satisfy the shared shot/window contract. Neither is
        # passed to conditioning when its switch is off; the native expanded
        # reference, guide and masking nodes are explicitly removed below.
        if not use_anchor:
            kwargs["anchor"] = kwargs["source"][:1]
        if not use_mask:
            kwargs["body_mask"] = torch.zeros(kwargs["source"].shape[:3])
        shot = relay_module("inputs").Shot().prepare(
            **kwargs, pose=kwargs["source"], actor_performance={})[0]
        shot["generation"] = {"use_anchor": use_anchor, "use_mask": use_mask}
        shot["key"] = hashlib.sha256(json.dumps(
            ["workbench-conditioning-v1", shot["key"], shot["generation"]], sort_keys=True
        ).encode()).hexdigest()
        return (shot,)


class GenerationBodyWindow:
    @classmethod
    def INPUT_TYPES(cls):
        return relay("H3RelayPerformanceBodyWindow").INPUT_TYPES()

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "expand"
    CATEGORY = "H3 Workbench/generation"

    def expand(self, models, shot, entry, state):
        expansion = relay("H3RelayPerformanceBodyWindow")().expand(models, shot, entry, state)
        options = shot["generation"]
        return conditioning_graph(expansion, options["use_anchor"], options["use_mask"], entry["history_frames"])


def route_generation(expansion):
    if isinstance(expansion, dict):
        replacements = {"H3RelayPerformanceRun": "H3WorkbenchGenerationRun",
                        "H3RelayPerformanceBodyWindow": "H3WorkbenchGenerationBodyWindow"}
        for node in expansion.get("expand", {}).values():
            node["class_type"] = replacements.get(node["class_type"], node["class_type"])
    return expansion


class GenerationRun:
    @classmethod
    def INPUT_TYPES(cls):
        return relay("H3RelayPerformanceRun").INPUT_TYPES()

    RETURN_TYPES = ("H3_RELAY_STATE",)
    FUNCTION = "run"
    CATEGORY = "H3 Workbench/generation"

    def run(self, **kwargs):
        return route_generation(relay("H3RelayPerformanceRun")().run(**kwargs))


class GenerationController:
    @classmethod
    def INPUT_TYPES(cls):
        return relay("H3RelayPerformanceController").INPUT_TYPES()

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    RETURN_TYPES = ("H3_RELAY_STATE", "H3_RELAY_SHOT", "STRING")
    RETURN_NAMES = ("completed_windows", "shot", "status")
    FUNCTION = "run"
    CATEGORY = "H3 Workbench/generation"

    def run(self, **kwargs):
        return route_generation(relay("H3RelayPerformanceController")().run(**kwargs))


NODE_CLASS_MAPPINGS = {
    "H3WorkbenchGenerationShot": GenerationShot,
    "H3WorkbenchGenerationBodyWindow": GenerationBodyWindow,
    "H3WorkbenchGenerationRun": GenerationRun,
    "H3WorkbenchGenerationController": GenerationController,
}
