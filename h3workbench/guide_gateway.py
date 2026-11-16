"""Private guide service client. This process never holds an OpenAI credential."""
import base64
import io
import json
import os
from pathlib import Path
import httpx
from PIL import Image
from . import store

MODEL = 'gpt-image-2.6-flare'


def client(socket=None):
    path = socket or os.environ.get('H3_GUIDE_OPENAI_SOCKET', '')
    if not path or not Path(path).is_absolute():
        raise ValueError('The administrator must connect the private guide service')
    return httpx.Client(transport=httpx.HTTPTransport(uds=path), base_url='http://guide-service', timeout=960)


def status():
    result = {'provider': 'private-openai', 'model': MODEL, 'quality': 'medium',
              'label': 'GPT Image 2.6 Flare', 'configured': False, 'available': False,
              'message': 'Private OpenAI connection needs administrator setup'}
    try:
        with client() as connection:
            response = connection.get('/health', timeout=5)
            response.raise_for_status()
            health = response.json()
        if health.get('model') == MODEL and health.get('configured') is True:
            result.update(configured=True, available=True, message='OpenAI connected through the private backend')
    except (ValueError, httpx.HTTPError):
        pass
    return result


def run(job, directory, prompt, quality, inputs, size=None):
    receipt_path = directory / 'openai-service-receipt.json'
    endpoint = '/edits/' + job['id']
    try:
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            with client(receipt['socket']) as connection:
                response = connection.get(endpoint)
        else:
            images = []
            for path in inputs:
                with Image.open(path) as image:
                    buffer = io.BytesIO()
                    image.convert('RGB').save(buffer, format='PNG')
                data = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
                if len(data) > 20_971_520:
                    raise ValueError('A guide reference exceeds the OpenAI image size limit')
                images.append({'image_url': data})
            socket = os.environ.get('H3_GUIDE_OPENAI_SOCKET', '')
            with client(socket) as connection:
                with receipt_path.open('x') as file:
                    json.dump({'socket': socket, 'model': MODEL, 'quality': quality, 'size': size, 'created': store.now()}, file)
                    file.flush()
                    os.fsync(file.fileno())
                store.job_update(job['id'], guide_service_job=True, phase='Generating guide with Flare')
                request = {'model': MODEL, 'quality': quality, 'prompt': prompt, 'images': images}
                if size is not None:
                    request['size'] = size
                response = connection.post(endpoint, json=request)
        if response.status_code == 400:
            raise ValueError(f'The guide service rejected the inputs at the requested {size or "automatic"} source size. '
                             'Check the image inputs and supported canvas size, or upload an edited guide.')
        if response.status_code != 200:
            raise ValueError('The private guide service did not return an image. Collect the existing result before trying a new generation.')
        result = response.json()
        if result.get('state') != 'complete':
            raise ValueError('No saved image yet. Collect the existing result later; this attempt is never automatically resubmitted.')
        raw = base64.b64decode(result['image'], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        output = directory / 'generated.png'
        temporary = directory / 'generated.partial'
        temporary.write_bytes(raw)
        temporary.replace(output)
        return output
    except httpx.HTTPError:
        raise ValueError('Private guide connection interrupted. Collect the existing result; do not repeat the paid request.') from None
