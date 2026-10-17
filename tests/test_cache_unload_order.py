"""Cache-owned patchers must survive the native unload preceding executor reset."""
import asyncio
import gc
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import weakref

import pytest


@pytest.fixture(params=['shared', 'legacy'])
def runtime(request, monkeypatch, tmp_path):
    shared = request.param == 'shared'
    prefix = 'AKATZ_MANAGED' if shared else 'H3_WORKBENCH'
    monkeypatch.delenv('AKATZ_MANAGED_SESSION', raising=False)
    monkeypatch.delenv('AKATZ_SESSION_CLEANUP_HOOK', raising=False)
    monkeypatch.setenv(prefix + '_SESSION', '1')
    monkeypatch.setenv(prefix + '_SESSION_ID', 'owned')
    monkeypatch.setenv(prefix + '_SESSION_ROOT', str(tmp_path))
    monkeypatch.setenv('AKATZ_SESSION_MODEL_NODES', '["UNETLoader"]')
    routes, providers = {}, []

    class Routes:
        def get(self, path):
            def register(fn):
                routes['GET'] = fn
                return fn
            return register

        def post(self, path):
            def register(fn):
                routes['POST'] = fn
                return fn
            return register

    class Queue:
        mutex = threading.RLock()
        flags = {}
        active = False

        def get_current_queue(self):
            return (['active'] if self.active else [], [])

        def set_flag(self, name, value):
            self.flags[name] = value

    class Executor:
        def reset(self):
            pass

    def module(name, **attributes):
        value = ModuleType(name)
        value.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    execution = module('execution', PromptExecutor=Executor)
    module('folder_paths', set_output_directory=lambda path: None)
    mm = module('comfy.model_management', VRAMState=SimpleNamespace(
        HIGH_VRAM=1, NORMAL_VRAM=2, LOW_VRAM=3, NO_VRAM=4))
    module('comfy', model_management=mm)
    module('comfy_execution')
    module('comfy_execution.cache_provider', CacheProvider=object,
           register_cache_provider=providers.append)
    queue = Queue()
    module('server', PromptServer=SimpleNamespace(instance=SimpleNamespace(
        routes=Routes(), prompt_queue=queue)))
    module('aiohttp', web=SimpleNamespace(json_response=lambda value: value,
        HTTPConflict=RuntimeError, HTTPForbidden=RuntimeError, HTTPBadRequest=RuntimeError))
    relative = 'runtime/tools/managed-comfy-cache.py' if shared else 'worker_nodes/h3_workbench_masks/session.py'
    spec = importlib.util.spec_from_file_location('cache_under_test', Path(__file__).parents[1] / relative)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    executor = execution.PromptExecutor()
    header = 'X-Akatz-Session' if shared else 'X-H3-Session'

    def call(method, body=None):
        async def read_body():
            return body
        return asyncio.run(routes[method](SimpleNamespace(headers={header: 'owned'}, json=read_body)))

    def consume():
        # Pinned ComfyUI main.py consumes both flags in this order. The native
        # model manager keeps weakrefs; dereferencing a dead patcher is the bug.
        if queue.flags.get('unload_models'):
            assert patcher_ref() is not None, 'patcher died before native unload'
            assert patcher_ref().model_size() == 42
        if queue.flags.get('free_memory'):
            executor.reset()
        queue.flags.clear()

    class Patcher:
        def model_size(self):
            return 42

    patcher = Patcher()
    patcher_ref = weakref.ref(patcher)
    providers[0].values['model'] = patcher
    del patcher
    return SimpleNamespace(call=call, consume=consume, ref=patcher_ref,
        queue=queue, root=tmp_path)


def test_unload_keeps_patcher_alive_until_execution_thread_acknowledges(runtime):
    payload = {'operation': 'unload', 'request_id': 'release'}
    response = runtime.call('POST', payload)
    gc.collect()
    assert runtime.ref() is not None
    assert runtime.call('GET')['model_entries'] == 1
    assert runtime.call('POST', payload) == response  # Lost wakeup retry.
    runtime.consume()
    assert runtime.ref() is None
    state = runtime.call('GET')
    assert state['model_entries'] == 0
    assert state['reset_epoch'] > response['reset_after']
    assert runtime.call('POST', payload) == response
    assert runtime.queue.flags == {}  # Acknowledged retry does not enqueue work.


def test_settle_preserves_models(runtime):
    response = runtime.call('POST', {'operation': 'settle', 'request_id': 'settle'})
    runtime.consume()
    assert runtime.ref() is not None
    assert runtime.call('GET')['model_entries'] == 1
    assert runtime.call('GET')['reset_epoch'] > response['reset_after']


def test_family_switch_uses_same_safe_unload_order(runtime):
    runtime.call('POST', {'operation': 'prepare', 'request_id': 'switch',
        'family': 'new', 'placement': 'offload', 'output_directory': str(runtime.root / 'stage')})
    assert runtime.ref() is not None
    runtime.consume()
    assert runtime.ref() is None
    assert runtime.call('GET')['family'] == 'new'
