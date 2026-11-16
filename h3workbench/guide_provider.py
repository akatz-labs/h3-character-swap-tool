"""Private-app Comfy partner transport. Credentials never enter saved graphs/jobs."""
import json
import os
import stat
import time
import uuid
from urllib.parse import urlparse
import httpx
from . import store

MODEL = 'gpt-image-2'


def provider():
    value = os.environ.get('H3_GUIDE_PROVIDER', 'private-openai')
    if value not in {'comfy', 'private-openai', 'local-openai'}:
        raise ValueError('Unknown guide provider configuration')
    return value


def ready():
    if provider() == 'local-openai':
        from . import local_openai
        if not local_openai.configured():
            raise ValueError('Add an OpenAI API key in Local settings → OpenAI')
    elif provider() == 'private-openai':
        from . import guide_gateway
        if not guide_gateway.status()['configured']:
            raise ValueError('The administrator must connect the private OpenAI backend before generating')
    else:
        secret()
        origin()


def origin():
    value = os.environ.get('H3_GUIDE_COMFY_ORIGIN', '').rstrip('/')
    parsed = urlparse(value)
    if not value or parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('The administrator must configure the guide ComfyUI connection')
    return value


def key_path():
    return store.DATA / '.guide-comfy-key'


def save_key(value):
    if provider() != 'comfy':
        raise ValueError('The private backend manages its own credentials')
    if not isinstance(value, str) or not 10 <= len(value.strip()) <= 4096 or any(c.isspace() for c in value.strip()):
        raise ValueError('Enter a valid Comfy API key')
    path = key_path()
    temporary = path.with_name(path.name + '-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write(value.strip())
    os.replace(temporary, path)


def secret():
    path = key_path()
    if not path.exists():
        raise ValueError('Connect a Comfy API key before generating a guide')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('The Comfy key file must be private to the app account')
    return path.read_text().strip()


def status():
    if provider() == 'local-openai':
        from . import local_openai
        return local_openai.status()
    if provider() == 'private-openai':
        from . import guide_gateway
        return guide_gateway.status()
    result = {'provider': 'comfy', 'model': MODEL, 'label': 'GPT Image 2', 'quality': 'medium',
              'configured': key_path().is_file(), 'available': False}
    try:
        with httpx.Client(base_url=origin(), timeout=8) as client:
            response = client.get('/object_info/OpenAIGPTImageNodeV2')
            response.raise_for_status()
            choices = response.json()['OpenAIGPTImageNodeV2']['input']['required']['model'][1]['options']
        result['available'] = any(v['key'] == MODEL for v in choices)
        result['message'] = 'GPT Image 2 available' if result['available'] else 'This ComfyUI does not offer GPT Image 2'
    except (ValueError, KeyError, httpx.HTTPError):
        result['message'] = 'Guide ComfyUI is unavailable; check the server connection'
    return result


def image_graph(prompt, quality, files, prefix):
    graph = {f'image_{i}': {'class_type': 'LoadImage', 'inputs': {'image': name}}
             for i, name in enumerate(files, 1)}
    graph['edit'] = {'class_type': 'OpenAIGPTImageNodeV2', 'inputs': {
        'prompt': prompt, 'model': MODEL, 'model.size': 'auto', 'model.quality': quality,
        'model.background': 'opaque', 'model.custom_width': 1024, 'model.custom_height': 1024, 'n': 1, 'seed': 0,
        **{f'model.images.image_{i}': [f'image_{i}', 0] for i in range(1, len(files) + 1)}}}
    graph['save'] = {'class_type': 'SaveImage', 'inputs': {'images': ['edit', 0], 'filename_prefix': prefix}}
    return graph


def run(job, directory, graph_factory, inputs, *, paid=False):
    """One submission, durable receipt, then history reconciliation. Never resubmit."""
    from . import pipeline
    receipt_path = directory / 'comfy-receipt.json'
    with httpx.Client(base_url=origin(), timeout=60) as client:
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt['origin'] != origin():
                raise ValueError('ComfyUI connection changed; reconcile this job on its original server')
        else:
            names = []
            for index, path in enumerate(inputs):
                with open(path, 'rb') as file:
                    response = client.post('/upload/image', files={'image': (f'{job["id"]}-{index}{path.suffix}', file)},
                                           data={'subfolder': 'h3-guides', 'overwrite': 'false'})
                response.raise_for_status()
                uploaded = response.json()
                names.append('/'.join(filter(None, [uploaded.get('subfolder'), uploaded['name']])))
            graph = graph_factory(names)
            (directory / 'api.json').write_text(json.dumps(graph, indent=2))
            credential = secret() if paid else None
            receipt = {'prompt_id': str(uuid.uuid4()), 'origin': origin(), 'submitted_at': store.now()}
            # Written before POST: a lost response must not cause another paid request.
            receipt_path.write_text(json.dumps(receipt))
            store.job_update(job['id'], comfy_prompt_id=receipt['prompt_id'])
            payload = {'prompt_id': receipt['prompt_id'], 'prompt': graph, 'client_id': 'h3-workbench-guides'}
            if paid:
                payload['extra_data'] = {'api_key_comfy_org': credential, 'comfy_usage_source': 'h3-workbench'}
            response = client.post('/prompt', json=payload)
            if response.status_code != 200:
                # Provider responses may contain request context; never persist or echo them.
                raise ValueError(f'ComfyUI rejected the workflow (HTTP {response.status_code}). Check the connection and model setup.')
            accepted = response.json()
            if accepted.get('prompt_id') != receipt['prompt_id']:
                raise ValueError('ComfyUI returned a different job identity; inspect before retrying')
        store.job_update(job['id'], phase='Generating guide' if paid else 'Rendering guided segment')
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            # Cancellation must never interrupt another user's ComfyUI job.
            # Once submitted, collect the result even if the local user closes/cancels.
            response = client.get('/history/' + receipt['prompt_id'])
            response.raise_for_status()
            history = response.json().get(receipt['prompt_id'])
            if history:
                if history.get('status', {}).get('status_str') == 'error':
                    raise ValueError('ComfyUI generation failed. Review this job in ComfyUI; saved versions are unchanged.')
                if history.get('status', {}).get('completed'):
                    outputs = history['outputs']
                    descriptors = outputs.get('save', {}).get('images', [])
                    if len(descriptors) != 1:
                        raise ValueError('ComfyUI did not return exactly one saved output')
                    descriptor = descriptors[0]
                    response = client.get('/view', params={k: descriptor[k] for k in ('filename', 'subfolder', 'type') if k in descriptor})
                    response.raise_for_status()
                    suffix = '.png' if paid else '.mp4'
                    output = directory / ('generated' + suffix)
                    output.write_bytes(response.content)
                    (directory / 'output-receipt.json').write_text(json.dumps({'prompt_id': receipt['prompt_id'], 'output': descriptor}))
                    return output
            if pipeline.STOP.wait(2):
                raise ValueError('App stopped while ComfyUI was working. Resume collection to retrieve the existing job.')
        raise ValueError('ComfyUI is still pending. Resume collection; do not submit another paid generation.')
