"""Optional reference-anchor and mask paths around the same native sampler."""
import copy


def replace_links(graph, node_id, outputs):
    for node in graph.values():
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and value[0] == node_id:
                node["inputs"][key] = copy.deepcopy(outputs[value[1]])
    del graph[node_id]


def conditioning_graph(expansion, use_anchor, use_mask, history_frames):
    graph = expansion["expand"]
    if not use_anchor:
        for node in graph.values():
            if node["class_type"] == "H3CharacterSwapReference":
                node["inputs"].pop("ref_images.ref_image_1", None)
        if history_frames == 0:
            for key, node in list(graph.items()):
                if node["class_type"] == "H3CharacterSwapHistory":
                    replace_links(graph, key, [node["inputs"]["positive"], node["inputs"]["latent"]])
    if not use_mask:
        for key, node in list(graph.items()):
            if node["class_type"] == "H3CharacterSwapMaskedRegeneration":
                replace_links(graph, key, [node["inputs"]["latent"]])
    return expansion
