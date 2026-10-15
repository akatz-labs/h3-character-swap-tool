"""Owned-runtime hooks: model-only cache, idle cleanup, stage output isolation.

Enabled only in the Workbench session environment. ComfyUI's public cache
provider is used for model retention; the pinned executor reset is instrumented
to acknowledge asynchronous /free operations rather than assuming HTTP success.
"""
import gc
import importlib
import os
from pathlib import Path
import threading
import time

MODEL_NODES = frozenset({
    "UNETLoader", "CLIPLoader", "VAELoader", "CheckpointLoaderSimple",
    "LoraLoaderModelOnly", "MiniMaxH3SigmaShift",
    "ModelAttentionBackend",
})


def install():
    if os.environ.get("AKATZ_MANAGED_SESSION") == "1":
        os.environ.setdefault("AKATZ_SESSION_CLEANUP_HOOK", __package__ + ".session_cleanup")
        import importlib.util
        module_path = Path(os.environ["AKATZ_SESSION_CACHE_MODULE"])
        expected = os.environ["AKATZ_SESSION_CACHE_SHA256"]
        import hashlib
        if hashlib.sha256(module_path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Shared cache module revision changed")
        spec = importlib.util.spec_from_file_location("akatz_managed_cache", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return
    if os.environ.get("H3_WORKBENCH_SESSION") != "1":
        return
    import execution
    import folder_paths
    import comfy.model_management as mm
    from comfy_execution.cache_provider import CacheProvider, register_cache_provider
    from server import PromptServer
    from aiohttp import web

    session_id = os.environ["H3_WORKBENCH_SESSION_ID"]
    session_root = Path(os.environ["H3_WORKBENCH_SESSION_ROOT"]).resolve()
    lock = threading.RLock()
    state = {"reset_epoch": 0, "reset_at": None, "family": None, "placement": "full",
             "hits": 0, "stores": 0, "prompt": None, "last_prompt": None}

    class ModelCache(CacheProvider):
        def __init__(self):
            self.values = {}

        def should_cache(self, context, value=None):
            return context.class_type in MODEL_NODES

        async def on_lookup(self, context):
            with lock:
                value = self.values.get(context.cache_key_hash)
                if value is not None:
                    state["hits"] += 1
                return value

        async def on_store(self, context, value):
            if self.should_cache(context, value):
                with lock:
                    self.values[context.cache_key_hash] = value
                    state["stores"] += 1

        def on_prompt_start(self, prompt_id):
            with lock:
                state["prompt"] = prompt_id

        def on_prompt_end(self, prompt_id):
            # Release relay's CPU tensors on the execution thread, outside the
            # queue mutex. Its expiry timer takes records -> queue locks, so
            # clearing these from an HTTP handler holding the queue lock could
            # deadlock. Continuation windows within this prompt are finished.
            import nodes
            cls = nodes.NODE_CLASS_MAPPINGS.get("H3CharacterSwapBodyWindow")
            windows = 0
            if cls:
                records = importlib.import_module(cls.__module__.rsplit(".", 1)[0] + ".records")
                records.begin("workbench-stage-released", [])
                windows = records.status()["cached_windows"]
            with lock:
                state["prompt"] = None
                state["last_prompt"] = prompt_id
                state["relay_cached_windows"] = windows

    provider = ModelCache()
    register_cache_provider(provider)
    original = execution.PromptExecutor

    class SessionExecutor(original):
        def reset(self):
            super().reset()
            # Finish releasing old graph objects before acknowledging cleanup.
            # libc may otherwise retain large freed CPU model allocations
            # across a LoRA/family switch on the 62 GiB worker.
            gc.collect()
            if os.name == "posix":
                import ctypes
                trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
                if trim:
                    trim(0)
            with lock:
                state["reset_epoch"] += 1
                state["reset_at"] = time.time()

    execution.PromptExecutor = SessionExecutor

    def identity(request):
        if request.headers.get("X-H3-Session") != session_id:
            raise web.HTTPForbidden(text="Session identity mismatch")

    @PromptServer.instance.routes.get("/h3-workbench/session")
    async def inspect(request):
        identity(request)
        with lock:
            return web.json_response({**state, "session_id": session_id,
                                      "model_entries": len(provider.values)})

    @PromptServer.instance.routes.post("/h3-workbench/session")
    async def control(request):
        identity(request)
        q = PromptServer.instance.prompt_queue
        body = await request.json()
        operation = body.get("operation")
        if operation not in {"prepare", "settle", "unload"}:
            raise web.HTTPBadRequest(text="Unknown session operation")
        # Atomic queue guard and both flags prevent get_flags() from consuming
        # half of a release request. Retries use the same request ID and reset
        # target, so a notification lost before q.get() can be woken safely.
        with q.mutex, lock:
            running, pending = q.get_current_queue()
            if running or pending or state["prompt"]:
                raise web.HTTPConflict(text="Runtime queue is active; preserving state")
            repeated = body.get("request_id") == state.get("cleanup_request_id")
            if repeated and state["reset_epoch"] > state["cleanup_reset_after"]:
                return web.json_response({"reset_after": state["cleanup_reset_after"], "models_cleared": state["cleanup_clear_models"]})
            clear_models = state["cleanup_clear_models"] if repeated else operation == "unload"
            if operation == "prepare" and not repeated:
                family = body["family"]
                placement = body["placement"]
                if placement not in {"full", "offload", "stream", "layer-stream"}:
                    raise web.HTTPBadRequest(text="Unknown placement")
                path = Path(body["output_directory"]).resolve()
                if not path.is_relative_to(session_root) or path == session_root:
                    raise web.HTTPBadRequest(text="Output path outside owned session")
                path.mkdir(parents=True, exist_ok=True)
                folder_paths.set_output_directory(str(path))
                clear_models = family != state["family"] or placement != state["placement"]
                state.update(family=family, placement=placement)
                # Placement is changed only between owned prompts. This is the
                # same enum used by the pinned model manager's runtime loaders;
                # OS limits, reservations and kernels are untouched.
                mm.vram_state = {
                    "full": mm.VRAMState.HIGH_VRAM,
                    "offload": mm.VRAMState.NORMAL_VRAM,
                    "stream": mm.VRAMState.LOW_VRAM,
                    "layer-stream": mm.VRAMState.NO_VRAM,
                }[placement]
            if clear_models and not repeated:
                provider.values.clear()
                if operation == "unload":
                    state["family"] = None
            epoch = state["cleanup_reset_after"] if repeated else state["reset_epoch"]
            state.update(cleanup_request_id=body["request_id"], cleanup_reset_after=epoch,
                         cleanup_clear_models=clear_models)
            # The main execution thread applies these flags after returning to
            # its idle loop. The controller waits for reset_epoch to advance.
            q.set_flag("unload_models", clear_models)
            q.set_flag("free_memory", True)
        gc.collect()
        return web.json_response({"reset_after": epoch, "models_cleared": clear_models})


install()
