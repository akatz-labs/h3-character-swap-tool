"""Bind portable captured content to a particular verified worker installation.

This creates a new machine contract/adapter manifest; it does not edit the
portable recipe, materialized environment, or another installed revision.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


FEATURES = ["sam-frame", "sam-track", "sam-recover", "h3-render", "h3-render-v1"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def features_for_model(path):
    if "sam3" in path:
        return ["sam-frame", "sam-track", "sam-recover"]
    if "character-swap" in path:
        return ["h3-render-v1"]
    if "v2-1000" in path:
        return ["h3-render-v2-1000"]
    return ["h3-render", "h3-render-v1", "h3-render-segments-v1"]


def configure(package, output, adapter, configuration, *, model_names=None, features=None):
    from comfygit_core.repositories.model_repository import calculate_model_short_hash
    receipt = json.loads((package / "installation-receipt.json").read_text())
    source = json.loads((package / "runtime-contract.json").read_text())
    if model_names is not None:
        if set(model_names) - set(receipt['models']):
            raise ValueError('Profile includes models outside the captured recipe')
        receipt['models'] = {name:receipt['models'][name] for name in model_names}
        source['models'] = {name:source['models'][name] for name in model_names}
    output.mkdir(parents=True, exist_ok=False)
    adapter_target = output / "h3-adapter.mjs"
    shutil.copy2(adapter, adapter_target)
    policy = configuration.pop("machinePolicy", {})
    if set(policy) - {"recycle_after_families", "transition_policy_reason"}:
        raise ValueError("Unknown machine policy")
    if policy and (not isinstance(policy.get("recycle_after_families"), list) or not isinstance(policy.get("transition_policy_reason"), str)):
        raise ValueError("Measured transition policy and reason are required")
    contract = {**source, **policy, "stage_model_validation": True, "startup_models": []}
    contract_path = output / "runtime-contract.json"
    contract_path.write_text(json.dumps(contract, indent=2))
    model_catalog = list(receipt["models"])
    configuration = {**configuration, "modelNames": model_catalog,
        "operationModels": {}, "controller": receipt["controller"]["path"],
        "controllerRevision": receipt["controller"]["sha256"], "contract": str(contract_path),
        "contractRevision": sha(contract_path), "cgExecutable": receipt["cg"],
        "environmentPath": receipt["environment"]}
    identity = sha(adapter_target)
    probes = [{"kind": "file", "path": str(contract_path), "sha256": sha(contract_path)},
        {"kind": "file", "path": receipt["controller"]["path"], "sha256": receipt["controller"]["sha256"]},
        {"kind": "directory", "path": receipt["environment"] + "/ComfyUI"}]
    models = []
    for name, record in receipt["models"].items():
        path = Path(receipt["models_directory"]) / name
        model_features = features_for_model(name)
        if features is not None:
            model_features = [f for f in model_features if f in features]
        model = {"name": name, "path": str(path), "sha256": record["sha256"], "bytes": record["bytes"],
            "available": record["available"], "features": model_features}
        if record["available"]:
            model["modelHash"] = calculate_model_short_hash(path)
        models.append(model)
        if model_features:
            probes.append({"kind": "file", "path": str(path), "expectedSize": record["bytes"],
                **({"modelHash": model["modelHash"], "pythonExecutable": configuration["python"]} if record["available"] else {}), "features": model_features})
    installation_identity = hashlib.sha256(json.dumps({"revision": identity, "configuration": configuration}, sort_keys=True).encode()).hexdigest()
    manifest = {"id": "h3-" + installation_identity[:20], "appId": "h3-workbench",
        "adapter": str(adapter_target), "revision": identity, "features": FEATURES if features is None else features, "probes": probes,
        "capabilities": ["managed-runtime-v1", "cooperative-cancel-v1", "accepted-attempt-v1"],
        "runtime": {"comfyUrls": ["http://127.0.0.1:" + str(configuration["port"])], "managed": {
            "version": 1, "driver": str(adapter_target), "driverRevision": identity,
            "recipeRevision": receipt["recipe_revision"], "environmentId": Path(receipt["environment"]).name,
            "idlePolicy": {"unloadSeconds": 300, "stopSeconds": 900}, "configuration": configuration}}}
    (output / "installation.json").write_text(json.dumps(manifest, indent=2))
    (output / "resources.json").write_text(json.dumps({"version": 1, "models": models, "portableRecipe": receipt["recipe_revision"]}, indent=2))
    print(json.dumps({"installation": manifest["id"], "revision": identity, "output": str(output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ["package", "output", "adapter", "configuration"]:
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    configure(args.package, args.output, args.adapter, json.loads(args.configuration.read_text()))
