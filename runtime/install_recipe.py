"""Install a supplied, verified H3 recipe using public ComfyGit operations.

No source environment or pilot tree is consulted. Model reuse is explicit and
verified; missing weights remain feature-specific requirements for Resources.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def owned_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Recipe path escapes its root")
    return path


def install(recipe, workspace_path, name, models_directory, *, reuse_root=None,
            package_root=None, cg=None, resume=False, controller=None, cache=None, uv_cache=None):
    from comfygit_core import Workspace

    recipe, workspace_path = Path(recipe).resolve(), Path(workspace_path).resolve()
    models_directory = Path(models_directory).resolve()
    captured = json.loads((recipe / "capture-receipt.json").read_text())
    catalog = json.loads((recipe / "model-identities.json").read_text())
    for relative, expected in captured["metadata"].items():
        assert sha(owned_path(recipe / "metadata", relative)) == expected, (relative, "Metadata integrity failure")
    for relative, identity in captured["files"].items():
        path = owned_path(recipe, relative)
        assert path.stat().st_size == identity["bytes"] and sha(path) == identity["sha256"], (relative, "Node integrity failure")
    portable = {"metadata": captured["metadata"], "files": captured["files"], "models": catalog,
                "controller_sha256": sha(controller), "cache_sha256": sha(cache)}
    # A new public Workspace must be empty. Create it before adding our temp,
    # package and uv-cache directories; existing workers had hidden this case.
    credential_overrides = {"github": None, "huggingface": None}
    existing_workspace = (workspace_path / ".metadata/workspace.json").exists()
    workspace = Workspace.open(workspace_path, credential_overrides=credential_overrides) if existing_workspace else Workspace.create(workspace_path, credential_overrides=credential_overrides)
    if uv_cache is not None:
        uv_cache = Path(uv_cache).resolve()
        if existing_workspace and workspace.get_external_uv_cache() != uv_cache:
            raise ValueError('Existing workspace cache differs; preserve its configuration')
        if not existing_workspace:
            workspace.set_external_uv_cache(uv_cache)
    temporary_root = workspace_path / "temp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(temporary_root)
    os.environ["UV_CACHE_DIR"] = str(workspace.get_external_uv_cache() or workspace_path / "uv_cache")
    os.environ["UV_PYTHON_INSTALL_DIR"] = str(workspace_path / "uv/python")
    import tempfile
    tempfile.tempdir = str(temporary_root)
    revision = hashlib.sha256(json.dumps(portable, sort_keys=True).encode()).hexdigest()
    package_root = Path(package_root or workspace_path / "packages" / revision).resolve()
    package_root.mkdir(parents=True, exist_ok=True)
    tools = package_root / "tools"
    tools.mkdir(exist_ok=True)
    for source_path in [Path(controller), Path(cache)]:
        target = tools / source_path.name
        if target.exists():
            assert sha(target) == sha(source_path), "Existing immutable tool revision differs"
        else:
            shutil.copy2(source_path, target)
    controller, cache = tools / Path(controller).name, tools / Path(cache).name
    for relative, identity in captured["files"].items():
        source, target = owned_path(recipe, relative), owned_path(package_root, relative)
        if target.exists():
            assert sha(target) == identity["sha256"], "Existing immutable package differs"
        else:
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    models_directory.mkdir(parents=True, exist_ok=True)
    reuse = Path(reuse_root).resolve() if reuse_root else None

    def model_item(item):
        relative, identity = item
        # Models may deliberately be symlinks into a verified existing cache.
        target = models_directory / relative
        source = reuse / relative if reuse else target
        if target.exists():
            source = target
        if not source.is_file():
            return relative, {**identity, "available": False}
        before = source.stat()
        assert before.st_size == identity["bytes"] and sha(source) == identity["sha256"], (relative, "Model content differs; original preserved")
        after = source.stat()
        assert (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) == (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev), "Model changed during verification"
        if not target.exists():
            assert not target.is_symlink(), "Broken existing model alias requires review"
            target.parent.mkdir(parents=True, exist_ok=True); target.symlink_to(source.resolve())
        actual = target.stat()
        return relative, {**identity, "available": True, "identity": {"size": actual.st_size,
            "mtime_ns": actual.st_mtime_ns, "inode": actual.st_ino, "device": actual.st_dev},
            "verified_at": time.time(), "cache_reuse": str(source.resolve())}

    with ThreadPoolExecutor() as pool:
        verified = dict(pool.map(model_item, catalog["models"].items()))
    environment_path = workspace_path / "environments" / name
    if environment_path.exists() and not resume:
        raise ValueError("Candidate environment already exists; inspect before resuming")
    if environment_path.exists():
        for process in Path("/proc").iterdir():
            if process.name.isdigit():
                try:
                    arguments = (process / "cmdline").read_bytes().decode().split("\0")
                    assert not any(arg.startswith(str(environment_path) + "/") for arg in arguments), "Candidate environment is in use"
                    working = (process / "cwd").resolve()
                    assert not working.is_relative_to(environment_path), "Candidate working directory is active"
                except (OSError, UnicodeDecodeError):
                    pass
    if not environment_path.exists():
        workspace.materialize_environment(recipe / "metadata", name, models_dir=models_directory,
            model_strategy="skip", torch_backend="cu128", no_manager=True)
    environment = workspace.get_environment(name, auto_sync=False)
    if workspace.get_models_directory().resolve() != models_directory:
        raise ValueError("Selected workspace model storage differs; preserve existing configuration")
    nodes = sorted({Path(path).parts[1] for path in captured["files"]})
    for node in nodes:
        target = package_root / "nodes" / node
        existing = environment_path / "ComfyUI/custom_nodes" / node
        if existing.exists():
            if existing.resolve() != target.resolve():
                for relative, value in captured["files"].items():
                    prefix = Path("nodes") / node
                    if Path(relative).parts[1] == node:
                        assert sha(existing / Path(relative).relative_to(prefix)) == value["sha256"], "Existing candidate node bytes differ"
                environment.link_development_node(node, target, name=node, replace_existing=True, force=True)
        else:
            environment.link_development_node(node, target, name=node)
    environment.sync()
    environment.sync_model_paths()
    runtime_sources = {node: {"root": str(package_root / "nodes" / node), "python_sha256": {
        str(Path(path).relative_to(Path("nodes") / node)): value["sha256"] for path, value in captured["files"].items()
        if Path(path).parts[1] == node and path.endswith(".py")}} for node in nodes}
    cg = str(cg or Path.home() / ".local/bin/cg")
    launch = {"COMFYGIT_HOME": str(workspace_path), "AKATZ_MANAGED_SESSION": "1",
              "TMPDIR": str(temporary_root), "TRITON_CACHE_DIR": str(workspace_path / "kernel-cache/triton"),
              "TORCHINDUCTOR_CACHE_DIR": str(workspace_path / "kernel-cache/torchinductor"),
              "CUDA_CACHE_PATH": str(workspace_path / "kernel-cache/cuda"),
              "AKATZ_SESSION_CACHE_MODULE": str(Path(cache).resolve()), "AKATZ_SESSION_CACHE_SHA256": sha(cache),
              "AKATZ_SESSION_MODEL_NODES": json.dumps(["UNETLoader", "CLIPLoader", "VAELoader", "CheckpointLoaderSimple",
                 "LoraLoaderModelOnly", "MiniMaxH3SigmaShift", "ModelAttentionBackend"])}
    contract = {"schema_version": 1, "comfygit_environment": name, "environment_path": str(environment_path),
        "comfy_revision": captured["comfy_revision"], "runtime_sources": runtime_sources,
        "required_launch_flags": ["--disable-dynamic-vram", "--reserve-vram", "0", "--enable-triton-backend", "--cache-classic", "--highvram"],
        "launch_environment": launch, "models": {k: {**v, "optional": True} for k, v in verified.items() if v["available"]},
        "environment_metadata_sha256": {p.name: sha(p) for p in (environment_path / ".cec").iterdir() if p.name in {"pyproject.toml", "uv.lock"}},
        "recipe_revision": revision}
    manifest = tomllib.loads((environment_path / ".cec/pyproject.toml").read_text())
    assert manifest["tool"]["comfygit"]["comfyui_commit_sha"] == captured["comfy_revision"]
    receipt = {"version": 1, "recipe_revision": revision, "environment": str(environment_path), "workspace": str(workspace_path),
        "models_directory": str(models_directory), "models": verified, "metadata": contract["environment_metadata_sha256"],
        "nodes": runtime_sources, "controller": {"path": str(controller), "sha256": sha(controller)},
        "cg": cg, "completed_at": time.time(), "platform": "linux-nvidia-cu128"}
    (package_root / "runtime-contract.json").write_text(json.dumps(contract, indent=2))
    (package_root / "installation-receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"environment": str(environment_path), "recipe_revision": revision, "package": str(package_root),
                      "available_models": sum(v["available"] for v in verified.values()), "declared_models": len(verified)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("recipe", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("name")
    parser.add_argument("models", type=Path)
    parser.add_argument("--reuse-root", type=Path)
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--cg", type=Path)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--uv-cache", type=Path)
    args = parser.parse_args()
    install(args.recipe, args.workspace, args.name, args.models, reuse_root=args.reuse_root,
            package_root=args.package_root, cg=args.cg, resume=args.resume, controller=args.controller, cache=args.cache, uv_cache=args.uv_cache)
