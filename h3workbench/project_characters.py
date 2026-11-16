"""Project character assets and immutable revisions; shots keep render snapshots.

Shot-local IDs never change during migration. Project links are metadata excluded
from generation signatures. Updating a project asset refreshes current shot inputs
atomically, leaving exact prompts, masks, historical layouts and takes intact.
"""
import copy
import json

from . import cast, segments, store

FIELDS = ('name', 'reference', 'description')


def require_project(con, project_id):
    if not con.execute('SELECT id FROM batches WHERE id=?', (project_id,)).fetchone():
        raise KeyError('Project not found')


def record(row):
    return {**json.loads(row['data']), 'id':row['id'], 'revision':row['revision'], 'created':row['created']}


def get(con, project_id, character_id):
    row = con.execute('SELECT * FROM project_characters WHERE batch_id=? AND id=?',
                      (project_id, character_id)).fetchone()
    if row is None:
        raise KeyError('Character not found in this project')
    return record(row)


def listing(project_id):
    with store.db() as con:
        require_project(con, project_id)
        result = [record(r) for r in con.execute(
            'SELECT * FROM project_characters WHERE batch_id=? ORDER BY created,id', (project_id,))]
        clips = con.execute('SELECT id,data FROM clips WHERE batch_id=?', (project_id,)).fetchall()
    uses = {c['id']:[] for c in result}
    for row in clips:
        clip = json.loads(row['data'])
        for index, shot in enumerate(clip.get('shots', [])):
            keys = {c.get('project_character_id') for c in shot.get('cast_setup', {}).get('characters', [])}
            for key in keys:
                if key in uses:
                    uses[key].append({'clip_id':row['id'], 'clip_name':clip.get('name', 'Clip'),
                                      'shot_id':shot['id'], 'shot_name':f'Shot {index+1}'})
    return [{**c, 'usage':uses[c['id']]} for c in result]


def validate(data):
    cast._text(data.get('name'), 'character name', 120, empty=False)
    cast._text(data.get('description'), 'character description')
    cast._asset(data.get('reference'), optional=True)


def insert(con, project_id, data):
    validate(data)
    key, created = store.uid('character-'), store.now()
    encoded = json.dumps(data)
    con.execute('INSERT INTO project_characters VALUES(?,?,?,?,?)', (key, project_id, 1, created, encoded))
    con.execute('INSERT INTO character_versions VALUES(?,?,?,?)', (key, 1, created, encoded))
    return {**data, 'id':key, 'revision':1, 'created':created, 'usage':[]}


def create(project_id, name):
    with store.LOCK, store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        require_project(con, project_id)
        return insert(con, project_id, {'name':name, 'reference':None, 'description':''})


def update(project_id, character_id, revision, changes):
    if not isinstance(changes, dict) or set(changes) - set(FIELDS):
        raise ValueError('Unknown character field')
    changed_clips = []
    with store.LOCK, store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        current = get(con, project_id, character_id)
        if type(revision) is not int or revision != current['revision']:
            raise store.Conflict('This character changed elsewhere. Reload it before saving.')
        data = {k:changes.get(k, current[k]) for k in FIELDS}
        validate(data)
        if data != {k:current[k] for k in FIELDS}:
            next_revision = revision + 1
            encoded = json.dumps(data)
            con.execute('UPDATE project_characters SET data=?,revision=? WHERE id=?', (encoded, next_revision, character_id))
            con.execute('INSERT INTO character_versions VALUES(?,?,?,?)', (character_id, next_revision, store.now(), encoded))
            for row in con.execute('SELECT * FROM clips WHERE batch_id=?', (project_id,)).fetchall():
                clip = json.loads(row['data'])
                changed = False
                for shot in clip.get('shots', []):
                    for character in shot.get('cast_setup', {}).get('characters', []):
                        if character.get('project_character_id') == character_id:
                            character.update(data, project_character_revision=next_revision)
                            changed = True
                if changed:
                    # Only automatically managed prompts follow the updated character.
                    segments.refresh_auto_prompts(clip)
                    clip['result'] = None
                    con.execute('UPDATE clips SET data=?,revision=revision+1 WHERE id=?', (json.dumps(clip), row['id']))
                    changed_clips.append(row['id'])
    for clip_id in changed_clips:
        store.persist_manifest(store.clip_get(clip_id))
    return next(c for c in listing(project_id) if c['id'] == character_id)


def shot_record(character, local_id=None):
    return {k:character[k] for k in FIELDS} | {'id':local_id or character['id'],
        'project_character_id':character['id'], 'project_character_revision':character['revision']}


def migrate_store():
    """Promote exact existing references, once, without rewriting shot identities."""
    changed = []
    with store.LOCK, store.db() as con:
        con.execute('BEGIN IMMEDIATE')
        marker = con.execute("SELECT value FROM settings WHERE key='project_character_schema'").fetchone()
        if marker and json.loads(marker[0]) >= 1:
            return
        pending = {r[0] for r in con.execute("SELECT clip_id FROM jobs WHERE state IN ('queued','waiting','running','cancelling')")}
        for batch in con.execute('SELECT id FROM batches').fetchall():
            project_id = batch[0]
            matches = {}
            for row in con.execute('SELECT * FROM project_characters WHERE batch_id=?', (project_id,)):
                character = record(row)
                matches[tuple(character[k] for k in FIELDS)] = character
            for row in con.execute('SELECT * FROM clips WHERE batch_id=?', (project_id,)).fetchall():
                clip = json.loads(row['data']); dirty = False
                for shots in [clip.get('shots', []), *[v['shots'] for v in clip.get('shot_layout_history', [])]]:
                    for shot in shots:
                        for character in shot.get('cast_setup', {}).get('characters', []):
                            if character.get('project_character_id'):
                                continue
                            if row['id'] in pending:
                                raise store.Conflict('Finish active clip jobs before migrating project characters')
                            key = tuple(character[k] for k in FIELDS)
                            # Empty placeholders are not evidence of a shared identity.
                            asset = matches.get(key) if character['reference'] or character['description'] else None
                            if asset is None:
                                asset = insert(con, project_id, {k:copy.deepcopy(character[k]) for k in FIELDS})
                                matches[key] = asset
                            character.update(project_character_id=asset['id'], project_character_revision=asset['revision'])
                            dirty = True
                if dirty:
                    con.execute('UPDATE clips SET data=?,revision=revision+1 WHERE id=?', (json.dumps(clip), row['id']))
                    changed.append(row['id'])
        con.execute("INSERT OR REPLACE INTO settings VALUES('project_character_schema','1')")
    for clip_id in changed:
        store.persist_manifest(store.clip_get(clip_id))
