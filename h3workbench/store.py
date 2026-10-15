import contextlib
import fcntl
import hashlib
import json
import mimetypes
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from .config import DATA, MEDIA, PILOT, PORTABLE

LOCK = threading.RLock()


class Conflict(ValueError):
    pass


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


def now():
    return time.time()


@contextlib.contextmanager
def db():
    con = sqlite3.connect(DATA / "workbench.sqlite", timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def init():
    with db() as c:
        c.executescript("""PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL);
        CREATE TABLE IF NOT EXISTS clips(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER, mime TEXT, name TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, clip_id TEXT, shot_id TEXT, state TEXT NOT NULL, created REAL, updated REAL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state,created);
        """)
        if PORTABLE:
            for key in ('shared_executor', 'unified_workers'):
                c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, 'true'))


def batch_create(name):
    id = uid("batch-")
    with db() as c:
        c.execute(
            "INSERT INTO batches VALUES(?,?,?)",
            (id, name.strip()[:160] or "Untitled batch", now()),
        )
    return id


def batch_rename(id, name, expected_name):
    name = name.strip()[:160]
    if not name:
        raise ValueError("Choose a project name")
    with db() as c:
        if not c.execute("SELECT 1 FROM batches WHERE id=?", (id,)).fetchone():
            raise KeyError("Project not found")
        changed = c.execute("UPDATE batches SET name=? WHERE id=? AND name=?", (name, id, expected_name)).rowcount
        if not changed:
            raise Conflict("The project name changed elsewhere; refresh before renaming")
    return {"id": id, "name": name}


def batches():
    with db() as c:
        return [
            dict(x)
            for x in c.execute(
                "SELECT b.*, count(c.id) AS clips FROM batches b LEFT JOIN clips c ON b.id=c.batch_id GROUP BY b.id ORDER BY b.created"
            )
        ]


def clip_get(id):
    with db() as c:
        row = c.execute("SELECT * FROM clips WHERE id=?", (id,)).fetchone()
    if not row:
        raise KeyError("Clip not found")
    return {
        **json.loads(row["data"]),
        "id": row["id"],
        "batch_id": row["batch_id"],
        "revision": row["revision"],
    }


def clips(batch_id=None):
    with db() as c:
        rows = c.execute(
            "SELECT * FROM clips"
            + (" WHERE batch_id=?" if batch_id else "")
            + " ORDER BY rowid",
            (batch_id,) if batch_id else (),
        ).fetchall()
    return [
        {
            **json.loads(x["data"]),
            "id": x["id"],
            "batch_id": x["batch_id"],
            "revision": x["revision"],
        }
        for x in rows
    ]


def clip_create(batch, data):
    id = data.get("id") or uid("clip-")
    with db() as c:
        if not c.execute("SELECT id FROM batches WHERE id=?", (batch,)).fetchone():
            raise KeyError("Batch not found")
        c.execute("INSERT INTO clips VALUES(?,?,?,?)", (id, batch, 1, json.dumps(data)))
    snapshot = clip_get(id)
    persist_manifest(snapshot)
    return snapshot


def persist_manifest(snapshot):
    target = MEDIA / "batches" / snapshot["batch_id"] / snapshot["id"]
    target.mkdir(parents=True, exist_ok=True)
    with (target / "manifest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        text = json.dumps(snapshot, indent=2)
        revision = target / f"revision-{snapshot['revision']:06}.json"
        if not revision.exists():
            temp = revision.with_suffix(".tmp")
            temp.write_text(text)
            temp.replace(revision)
        current = target / "manifest.json"
        if (
            not current.exists()
            or json.loads(current.read_text())["revision"] <= snapshot["revision"]
        ):
            temp = target / "manifest.json.tmp"
            temp.write_text(text)
            temp.replace(current)


def clip_mutate(id, fn, revision=None):
    with LOCK, db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM clips WHERE id=?", (id,)).fetchone()
        if not row:
            raise KeyError("Clip not found")
        if revision is not None and revision != row["revision"]:
            raise Conflict(
                "This clip changed in another session. Reload before saving."
            )
        data = json.loads(row["data"])
        fn(data)
        c.execute(
            "UPDATE clips SET data=?,revision=revision+1 WHERE id=?",
            (json.dumps(data), id),
        )
    snapshot = clip_get(id)
    persist_manifest(snapshot)
    return snapshot


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def asset_add(path, name=None):
    p = Path(path).resolve(strict=True)
    allowed = [MEDIA.resolve()] + ([] if PORTABLE else [PILOT.parent.resolve()])
    if not any(p.is_relative_to(root) for root in allowed):
        raise ValueError("Media must be within the configured project media drive")
    if not p.is_file():
        raise ValueError("Media is not a regular file")
    with db() as c:
        row = c.execute("SELECT * FROM assets WHERE path=?", (str(p),)).fetchone()
        if row:
            return row["id"]
        digest = sha(p)
        id = uid("asset-")
        c.execute(
            "INSERT OR IGNORE INTO assets VALUES(?,?,?,?,?,?)",
            (
                id,
                str(p),
                digest,
                p.stat().st_size,
                mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                name or p.name,
            ),
        )
        id = c.execute("SELECT id FROM assets WHERE path=?", (str(p),)).fetchone()[0]
    return id


def asset(id):
    with db() as c:
        row = c.execute("SELECT * FROM assets WHERE id=?", (id,)).fetchone()
    if not row:
        raise KeyError("Asset not found")
    return dict(row)


def path(id):
    return Path(asset(id)["path"])


def setting(key, default=None):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(key, value):
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value))
        )


def jobs():
    with db() as c:
        return [
            {**dict(x), "data": json.loads(x["data"])}
            for x in c.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 300")
        ]


def pending_jobs(states=("queued", "waiting"), full=False):
    """Dispatch/recovery cannot be bounded by browser history pagination."""
    placeholders = ",".join("?" for _ in states)
    fields = ("worker", "requested_worker", "execution_resource", "gpu_waiting", "retry_after",
              "phase", "runtime", "cancel_requested", "lora", "options", "execution_backend", "shared_stages")
    pairs = ",".join(f"'$.{k}',json_extract(data,'$.{k}')" for k in fields)
    columns = "*" if full else f"id,kind,clip_id,shot_id,state,created,updated,json_set('{{}}',{pairs}) AS data"
    with db() as c:
        return [{**dict(row), "data": json.loads(row["data"])} for row in c.execute(
            f"SELECT {columns} FROM jobs WHERE state IN ({placeholders}) ORDER BY created,id", states)]


def claim_job(id, worker=None, execution_resource=None):
    """Only one dispatcher can claim a pending row, including across processes."""
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone()
        if not row or row["state"] not in {"queued", "waiting"}:
            return False
        data = json.loads(row["data"])
        data["phase"] = "Starting"
        if worker:
            data["worker"] = worker
        if execution_resource:
            data["execution_resource"] = execution_resource
        c.execute("UPDATE jobs SET state='running',updated=?,data=? WHERE id=?", (now(), json.dumps(data), id))
        return True


def job_get(id):
    with db() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone()
    if not row:
        raise KeyError("Job not found")
    return {**dict(row), "data": json.loads(row["data"])}


PUBLIC_JOB_FIELDS = ("phase", "error", "worker", "requested_worker", "auto_placement", "execution_resource", "gpu_waiting", "options", "runtime_status",
                     "progress", "capacity_failure", "render_spec", "generation", "lora", "execution_backend", "shared_phase", "shared_job_id")
PUBLIC_FOREGROUND_FIELDS = ("mask_points", "face", "gate_px", "core_px", "mask_text",
                            "selection", "subject_index", "alpha_seed", "auto_face")


def public_job(job):
    data = job["data"]
    shot = data.get("shot") or {}
    result = data.get("result") or {}
    return {**{k: v for k, v in job.items() if k != "data"}, "data": {
        **{k: data[k] for k in PUBLIC_JOB_FIELDS if k in data},
        "shot": {**{k: shot[k] for k in ("seed", "prompt", "mask_points") if k in shot},
                 "refine": {side: {k: v for k, v in value.items() if k in PUBLIC_FOREGROUND_FIELDS and v is not None}
                            for side, value in (shot.get("refine") or {}).items()
                            if side in {"source", "character"} and isinstance(value, dict)}},
        "result": {k: result[k] for k in ("asset", "result", "attachment") if k in result},
    }}


def public_jobs():
    # Project in SQLite so history snapshots never need to cross the HTTP
    # boundary or be decoded into Python on every browser poll.
    fields = list(PUBLIC_JOB_FIELDS) + ["shot.seed", "shot.prompt", "shot.mask_points"]
    fields += [f"shot.refine.{side}.{key}" for side in ("source", "character")
               for key in PUBLIC_FOREGROUND_FIELDS]
    fields += ["result.asset", "result.result", "result.attachment"]
    pairs = ",".join(f"'$.{key}',json_extract(data,'$.{key}')" for key in fields)
    with db() as c:
        rows = c.execute(f"SELECT id,kind,clip_id,shot_id,state,created,updated,"
                         f"json_set('{{}}',{pairs}) AS data FROM jobs ORDER BY created DESC LIMIT 300").fetchall()
    return [public_job({**dict(row), "data": json.loads(row["data"])}) for row in rows]


def job_create(kind, clip_id=None, shot_id=None, data=None):
    with LOCK, db() as c:
        rows = c.execute(
            "SELECT id,data FROM jobs WHERE kind=? AND clip_id IS ? AND shot_id IS ? AND state IN ('queued','running','waiting','cancelling')",
            (kind, clip_id, shot_id),
        ).fetchall()
        for row in rows:
            previous = json.loads(row["data"])
            # Source and character extraction are independent jobs. Identical
            # clicks remain idempotent while their stage is active.
            frame_edit = (
                kind in {"refine_mask", "mask_edit"}
                and (data or {}).get("options", {}).get("mode") == "frame"
            )
            if frame_edit:
                if previous.get("options") == data.get("options") and previous.get(
                    "frame_request"
                ) == data.get("frame_request"):
                    return row["id"]
            elif kind not in {
                "refine_extract",
                "refine_mask",
                "refine_face",
            } or previous.get("options") == (data or {}).get("options"):
                return row["id"]
        id = uid("job-")
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
            (
                id,
                kind,
                clip_id,
                shot_id,
                "queued",
                now(),
                now(),
                json.dumps(data or {}),
            ),
        )
    return id


def job_update(id, state=None, **values):
    with LOCK, db() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone()
        data = {**json.loads(row["data"]), **values}
        c.execute(
            "UPDATE jobs SET state=?,updated=?,data=? WHERE id=?",
            (state or row["state"], now(), json.dumps(data), id),
        )
