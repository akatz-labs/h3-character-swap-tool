"""Pin a new controller revision around an unchanged verified environment.

ComfyGit's materialized files, node bindings and models remain untouched. Old
runtime contracts continue to identify their original accepted attempts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def refresh(recipe, installed, output, controller, cache):
    original_receipt = installed / "installation-receipt.json"
    receipt = json.loads(original_receipt.read_text())
    contract = json.loads((installed / "runtime-contract.json").read_text())
    environment = Path(receipt["environment"])
    for name, expected in receipt["metadata"].items():
        assert sha(environment / ".cec" / name) == expected, "Environment metadata changed"
    for name, source in receipt["nodes"].items():
        node = environment / "ComfyUI/custom_nodes" / name
        assert node.resolve() == Path(source["root"]).resolve(), "Immutable node binding changed"
        for relative, expected in source["python_sha256"].items():
            assert sha(node / relative) == expected, "Immutable node source changed"
    for name, model in receipt["models"].items():
        if not model["available"]:
            continue
        stat = (Path(receipt["models_directory"]) / name).stat()
        actual = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino, "device": stat.st_dev}
        assert actual == model["identity"], "Previously verified model changed"
    captured = json.loads((recipe / "capture-receipt.json").read_text())
    portable = {"metadata": captured["metadata"], "files": captured["files"],
        "models": json.loads((recipe / "model-identities.json").read_text()),
        "controller_sha256": sha(controller), "cache_sha256": sha(cache)}
    revision = hashlib.sha256(json.dumps(portable, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    tools = output / "tools"
    tools.mkdir()
    shutil.copy2(controller, tools / "managed-comfy-session.py")
    shutil.copy2(cache, tools / "managed-comfy-cache.py")
    contract["recipe_revision"] = revision
    contract["launch_environment"]["AKATZ_SESSION_CACHE_MODULE"] = str(tools / "managed-comfy-cache.py")
    contract["launch_environment"]["AKATZ_SESSION_CACHE_SHA256"] = sha(tools / "managed-comfy-cache.py")
    receipt.update(recipe_revision=revision, completed_at=time.time(),
        controller={"path": str(tools / "managed-comfy-session.py"), "sha256": sha(tools / "managed-comfy-session.py")},
        unchanged_environment_receipt={"path": str(original_receipt), "sha256": sha(original_receipt)})
    (output / "runtime-contract.json").write_text(json.dumps(contract, indent=2))
    (output / "installation-receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"recipe_revision": revision, "environmentUnchanged": str(environment), "output": str(output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ["recipe", "installed", "output", "controller", "cache"]:
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    refresh(args.recipe, args.installed, args.output, args.controller, args.cache)
