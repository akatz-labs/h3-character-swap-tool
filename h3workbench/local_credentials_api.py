"""Local runner settings: credentials are accepted, never returned."""
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import Literal
from . import guide_provider, local_openai

router = APIRouter(prefix='/api/local-settings')


def require_local():
    if guide_provider.provider() != 'local-openai':
        raise HTTPException(404, 'Local credential settings are unavailable')


def response():
    return JSONResponse({'configured': local_openai.configured()}, headers={'Cache-Control': 'no-store'})


@router.get('/openai')
def status():
    require_local()
    return response()


@router.put('/openai')
async def save(request: Request):
    require_local()
    # Avoid validation responses that echo an invalid credential request body.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(413, 'Credential request is too large')
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, 'Invalid credential request') from None
    if not isinstance(body, dict) or set(body) != {'api_key'}:
        raise HTTPException(400, 'Enter an OpenAI API key')
    local_openai.save_key(body['api_key'])
    return response()


@router.delete('/openai')
def delete():
    require_local()
    local_openai.delete_key()
    return response()


class TranscriptionPreference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preference: Literal['auto', 'local', 'openai']


@router.get('/transcription')
def transcription_status():
    require_local()
    from . import transcription_provider
    return JSONResponse(transcription_provider.status(), headers={'Cache-Control': 'no-store'})


@router.put('/transcription')
def transcription_preference(body: TranscriptionPreference):
    require_local()
    from . import store, transcription_provider
    store.set_setting(transcription_provider.SETTING, body.preference)
    return transcription_status()
