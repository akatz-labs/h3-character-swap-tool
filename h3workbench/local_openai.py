"""Local, write-only credential storage and single-attempt Image Edits transport."""
import base64
from contextlib import ExitStack
import io
import json
import os
import stat
import threading
import uuid

import httpx
from PIL import Image
from . import store
from .guide_gateway import MODEL

KEY_LOCK = threading.RLock()
MAX_RESPONSE = 36 * 1024 * 1024


def key_path():
    return store.DATA / '.credentials' / 'openai-api-key'


def configured():
    try:
        info = key_path().lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077 and info.st_size > 0


def save_key(value):
    if (not isinstance(value, str) or not 10 <= len(value.strip()) <= 4096
            or not value.strip().isascii() or any(c.isspace() for c in value.strip())):
        raise ValueError('Enter a valid OpenAI API key')
    with KEY_LOCK:
        path = key_path()
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if path.parent.is_symlink():
            raise ValueError('Credential storage must be a private local directory')
        path.parent.chmod(0o700)
        temporary = path.with_name('pending-' + uuid.uuid4().hex)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as file:
                file.write(value.strip())
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def delete_key():
    with KEY_LOCK:
        key_path().unlink(missing_ok=True)


def secret():
    with KEY_LOCK:
        if not configured():
            raise ValueError('Add your OpenAI API key in Local settings → OpenAI')
        fd = os.open(key_path(), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as file:
            return file.read()


def status():
    ready = configured()
    return {'provider': 'local-openai', 'model': MODEL, 'label': 'GPT Image 2.6 Flare',
            'quality': 'medium', 'configured': ready, 'available': ready,
            'message': 'OpenAI API key saved' if ready else 'Add an OpenAI API key in Local settings → OpenAI'}


def verify_png(raw):
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != 'PNG' or image.width * image.height > 40_000_000:
            raise ValueError('OpenAI did not return a supported PNG image')
        image.verify()


def run(job, directory, prompt, quality, inputs, size=None):
    output = directory / 'generated.png'
    receipt = directory / 'local-openai-receipt.json'
    if output.exists():
        verify_png(output.read_bytes())
        return output
    if receipt.exists():
        raise ValueError('This edit was already submitted but no image was saved. It will not be submitted again automatically; review the attempt before starting a new generation.')
    if quality not in {'low', 'medium', 'high'} or not 1 <= len(inputs) <= 16:
        raise ValueError('Choose a supported quality and one source with at most 15 references')
    api_key = secret()
    directory.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        files = []
        for index, path in enumerate(inputs):
            with Image.open(path) as image:
                buffer = stack.enter_context(io.BytesIO())
                image.convert('RGBA' if image.mode == 'RGBA' else 'RGB').save(buffer, format='PNG')
            if buffer.tell() > 25 * 1024 * 1024:
                raise ValueError('A guide input exceeds the image size limit')
            buffer.seek(0)
            files.append(('image[]', ('source.png' if index == 0 else f'reference-{index}.png', buffer, 'image/png')))
        payload = {'model': MODEL, 'prompt': prompt, 'quality': quality,
                   'size': size or 'auto', 'output_format': 'png', 'n': '1'}
        # Persist before the paid call. Recovery can collect an existing file, never POST again.
        with receipt.open('x') as file:
            json.dump({'model': MODEL, 'quality': quality, 'size': size or 'auto', 'created': store.now()}, file)
            file.flush()
            os.fsync(file.fileno())
        store.job_update(job['id'], phase='Generating guide with Flare')
        try:
            with httpx.Client(timeout=httpx.Timeout(180, connect=20), follow_redirects=False) as client:
                with client.stream('POST', 'https://api.openai.com/v1/images/edits',
                                   headers={'Authorization': 'Bearer ' + api_key}, data=payload, files=files) as response:
                    if response.status_code != 200:
                        hints = {401: 'Replace the API key in Local settings.',
                                 403: 'Check this key’s model permissions.',
                                 404: 'Check this account’s access to the configured Flare model.',
                                 429: 'Check your OpenAI quota and rate limits.'}
                        raise ValueError(f'OpenAI image edit failed (HTTP {response.status_code}). ' +
                                         hints.get(response.status_code, 'Review the inputs and provider attempt before trying again.'))
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE:
                            raise ValueError('OpenAI image response exceeds the size limit')
            result = json.loads(raw)
            images = result['data']
            if len(images) != 1:
                raise ValueError('Expected one generated image')
            png = base64.b64decode(images[0]['b64_json'], validate=True)
            verify_png(png)
        except httpx.HTTPError:
            raise ValueError('OpenAI connection interrupted; this edit may have been billed. It will not be retried automatically.') from None
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError('OpenAI returned an invalid image response') from None
        partial = directory / 'generated.partial'
        with partial.open('wb') as file:
            file.write(png)
            file.flush()
            os.fsync(file.fileno())
        partial.replace(output)
    return output
