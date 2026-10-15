"""Run real kernels from the frozen worker, without starting an executor.

Invoke with the worker environment's Python in a one-shot GPU container.
Foreign GPU work is a reason to wait, never a reason to stop that work.
"""
import json
import subprocess
import time
from importlib import metadata


def main():
    started = time.monotonic()
    report = {'schemaVersion': 1, 'status': 'failed', 'stage': 'gpu-ownership',
              'checks': [], 'executorStarted': False, 'modelsLoaded': False}
    try:
        compute = subprocess.check_output([
            'nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'], text=True)
        pids = [line.strip() for line in compute.splitlines() if line.strip()]
        if pids:
            raise RuntimeError('GPU compute processes are present; wait for an idle ownership handoff')
        report['stage'] = 'torch'
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable in this container')
        report.update(torch=torch.__version__, torchCuda=torch.version.cuda,
                      comfyKitchen=metadata.version('comfy-kitchen'),
                      gpu=torch.cuda.get_device_name(0),
                      computeCapability=list(torch.cuda.get_device_capability(0)))
        report['driver'] = subprocess.check_output([
            'nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'], text=True).strip()
        torch.manual_seed(194731)
        x = torch.randn((64, 128), device='cuda', dtype=torch.float16)
        torch.cuda.synchronize()
        report['checks'].append('torch-cuda-allocation')

        report['stage'] = 'native-fp8'
        import comfy_kitchen as kitchen
        from comfy_kitchen.backends import cuda as native
        if not native._EXT_AVAILABLE or native._C is None:
            raise RuntimeError('The frozen native CUDA extension did not load')
        # Call the native backend directly, so a successful eager/Triton fallback
        # cannot hide a CUDA build/driver mismatch.
        scale = torch.ones((), device='cuda', dtype=torch.float32)
        quantized = native.quantize_per_tensor_fp8(x, scale)
        torch.cuda.synchronize()
        if not torch.isfinite(quantized.float()).all().item():
            raise RuntimeError('Native FP8 quantization produced non-finite output')
        report['checks'].append('native-cuda-fp8-quantization')

        report['stage'] = 'native-attention'
        q = torch.randn((1, 2, 128, 128), device='cuda', dtype=torch.float16)
        k = torch.randn_like(q)
        v = torch.ones_like(q)
        with kitchen.use_backend('cuda'):
            output = kitchen.int8_attention(q, k, v)
        torch.cuda.synchronize()
        torch.testing.assert_close(output, torch.ones_like(output), atol=0.05, rtol=0.05)
        report['checks'].append('native-cuda-int8-attention')
        report.update(status='passed', stage='complete')
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)[:2000]}
    report['elapsedSeconds'] = round(time.monotonic() - started, 3)
    print(json.dumps(report), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
