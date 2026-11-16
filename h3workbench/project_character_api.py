"""Project-scoped authoring of character sheets."""
from fastapi import APIRouter, File, Form, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from . import project_characters as characters, store
from .config import MEDIA

router = APIRouter(prefix='/api/batches/{project_id}/characters')


@router.get('')
def listing(project_id: str):
    return characters.listing(project_id)


@router.post('')
def create(project_id: str, body: dict):
    return characters.create(project_id, body.get('name', 'New character'))


@router.patch('/{character_id}')
def edit(project_id: str, character_id: str, body: dict):
    changes = body.get('changes')
    if not isinstance(changes, dict) or set(changes) - {'name', 'description'}:
        raise ValueError('Edit the character name and description')
    return characters.update(project_id, character_id, body.get('revision'), changes)


@router.post('/{character_id}/image')
def image(project_id: str, character_id: str, revision: int=Form(...), file: UploadFile=File(...)):
    with store.db() as con:
        current = characters.get(con, project_id, character_id)
    if current['revision'] != revision:
        raise store.Conflict('This character changed elsewhere. Reload it before uploading.')
    path = MEDIA/'batches'/project_id/'characters'/character_id/f'{store.uid()}.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(file.file) as im:
            image = ImageOps.exif_transpose(im)
            image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB').save(path)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError('Upload a readable image file') from exc
    return characters.update(project_id, character_id, revision, {'reference':store.asset_add(path)})
