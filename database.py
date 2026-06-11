import os
import json
import sqlite3
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse


def _sqlite_path_from_env():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        parsed = urlparse(database_url)
        if parsed.scheme == "sqlite":
            if parsed.netloc:
                return unquote(f"/{parsed.netloc}{parsed.path}")
            return unquote(parsed.path)
        raise RuntimeError(
            "Only sqlite DATABASE_URL values are supported by this local build. "
            "Use sqlite:////absolute/path.db or leave DATABASE_URL unset."
        )
    return os.environ.get("DB_PATH") or os.path.join(os.path.dirname(__file__), "analytics.db")


DB_PATH = _sqlite_path_from_env()


def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            url TEXT,
            has_login INTEGER DEFAULT 0,
            session_cookie TEXT,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            captured_at TEXT NOT NULL,
            followers INTEGER,
            views INTEGER,
            likes INTEGER,
            posts INTEGER,
            posts_json TEXT,
            error TEXT,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT,
            message TEXT,
            error TEXT,
            queued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

        CREATE TABLE IF NOT EXISTS media_transcripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            video_url TEXT,
            source_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            transcript TEXT,
            source_label TEXT NOT NULL,
            duration_seconds REAL,
            status TEXT NOT NULL,
            error TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );
    """)

    conn.commit()
    conn.close()


def get_config(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_config(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_clients():
    conn = get_db()
    rows = conn.execute("SELECT * FROM clients ORDER BY sort_order, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_accounts(client_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM accounts WHERE client_id=? ORDER BY platform", (client_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_snapshot(account_id):
    """Returns the most recent snapshot (including errors)."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM snapshots WHERE account_id=? ORDER BY captured_at DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_latest_good_snapshot(account_id):
    """Returns the most recent snapshot without an error."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM snapshots WHERE account_id=? AND error IS NULL ORDER BY captured_at DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_best_posts_json(account_id):
    """Return known posts while preserving the highest observed cumulative view count."""
    conn = get_db()
    rows = conn.execute(
        """SELECT posts_json FROM snapshots
           WHERE account_id=? AND error IS NULL AND posts_json IS NOT NULL
           ORDER BY captured_at ASC, id ASC""",
        (account_id,),
    ).fetchall()
    conn.close()
    posts_by_key = {}
    for row in rows:
        try:
            posts = json.loads(row["posts_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for post in posts:
            key = post.get("url") or post.get("shortcode")
            if not key:
                continue
            existing = posts_by_key.get(key)
            best_views = max(
                [view for view in (post.get("views"), existing.get("views") if existing else None)
                 if view is not None],
                default=None,
            )
            likes = post.get("likes")
            if likes is None and existing:
                likes = existing.get("likes")
            posts_by_key[key] = {
                **(existing or {}),
                **post,
                "views": best_views,
                "likes": likes,
            }
    return json.dumps(list(posts_by_key.values())) if posts_by_key else None


def get_snapshot_days_ago(account_id, days):
    conn = get_db()
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
    row = conn.execute(
        """SELECT * FROM snapshots
           WHERE account_id=? AND error IS NULL AND captured_at <= ?
           ORDER BY captured_at DESC LIMIT 1""",
        (account_id, cutoff),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_snapshot(account_id, data):
    conn = get_db()
    conn.execute(
        """INSERT INTO snapshots (account_id, captured_at, followers, views, likes, posts, posts_json, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            account_id,
            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            data.get("followers"),
            data.get("views"),
            data.get("likes"),
            data.get("posts"),
            data.get("posts_json"),
            data.get("error"),
        ),
    )
    conn.commit()
    conn.close()


def save_media_transcript(
    source_path,
    file_name,
    source_label,
    status,
    transcript=None,
    account_id=None,
    video_url=None,
    duration_seconds=None,
    error=None,
):
    conn = get_db()
    updated_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute(
        """INSERT INTO media_transcripts
           (account_id, video_url, source_path, file_name, transcript, source_label,
            duration_seconds, status, error, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_path) DO UPDATE SET
             account_id=excluded.account_id,
             video_url=excluded.video_url,
             file_name=excluded.file_name,
             transcript=excluded.transcript,
             source_label=excluded.source_label,
             duration_seconds=excluded.duration_seconds,
             status=excluded.status,
             error=excluded.error,
             updated_at=excluded.updated_at""",
        (
            account_id,
            video_url,
            source_path,
            file_name,
            transcript,
            source_label,
            duration_seconds,
            status,
            error,
            updated_at,
        ),
    )
    conn.commit()
    conn.close()


def get_media_transcripts(account_id=None):
    conn = get_db()
    if account_id is None:
        rows = conn.execute(
            "SELECT * FROM media_transcripts ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM media_transcripts
               WHERE account_id=? ORDER BY updated_at DESC, id DESC""",
            (account_id,),
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_transcripts_by_url(account_id):
    return {
        row["video_url"]: row
        for row in get_media_transcripts(account_id)
        if row.get("video_url") and row.get("status") == "complete"
    }


def upsert_client(client_id, name):
    conn = get_db()
    if client_id:
        conn.execute("UPDATE clients SET name=? WHERE id=?", (name, client_id))
    else:
        conn.execute("INSERT INTO clients (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()


def delete_client(client_id):
    conn = get_db()
    account_ids = [
        r["id"]
        for r in conn.execute("SELECT id FROM accounts WHERE client_id=?", (client_id,)).fetchall()
    ]
    for aid in account_ids:
        conn.execute("DELETE FROM snapshots WHERE account_id=?", (aid,))
    conn.execute("DELETE FROM accounts WHERE client_id=?", (client_id,))
    conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.commit()
    conn.close()


def upsert_account(account_id, client_id, platform, handle, url, has_login, session_cookie=None):
    conn = get_db()
    if account_id:
        conn.execute(
            """UPDATE accounts SET platform=?, handle=?, url=?, has_login=?, session_cookie=?
               WHERE id=?""",
            (platform, handle, url, has_login, session_cookie, account_id),
        )
    else:
        conn.execute(
            """INSERT INTO accounts (client_id, platform, handle, url, has_login, session_cookie)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (client_id, platform, handle, url, has_login, session_cookie),
        )
    conn.commit()
    conn.close()


def delete_account(account_id):
    conn = get_db()
    conn.execute("DELETE FROM snapshots WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    conn.commit()
    conn.close()


def create_job(kind, payload=None):
    conn = get_db()
    queued_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    cur = conn.execute(
        "INSERT INTO jobs (kind, status, payload, queued_at) VALUES (?, 'queued', ?, ?)",
        (kind, json.dumps(payload) if payload is not None else None, queued_at),
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    return job_id


def update_job(job_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [job_id]
    conn = get_db()
    conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", values)
    conn.commit()
    conn.close()


def get_job(job_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_active_jobs():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_finished_job():
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('succeeded','failed') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def reset_stuck_jobs():
    """Mark jobs from a previous process as failed (their worker thread is gone)."""
    conn = get_db()
    finished_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute(
        """UPDATE jobs SET status='failed', error='proces je bil ponovno zagnan',
                            finished_at=?
           WHERE status IN ('queued','running')""",
        (finished_at,),
    )
    conn.commit()
    conn.close()


def prune_old_jobs(keep_last=50):
    conn = get_db()
    conn.execute(
        """DELETE FROM jobs WHERE id NOT IN (
               SELECT id FROM jobs ORDER BY id DESC LIMIT ?
           )""",
        (keep_last,),
    )
    conn.commit()
    conn.close()


def get_account_history(account_id, limit=30):
    """Return last `limit` good snapshots, oldest-first, for sparklines."""
    conn = get_db()
    rows = conn.execute(
        """SELECT captured_at, followers, views, likes, posts
           FROM snapshots
           WHERE account_id=? AND error IS NULL
           ORDER BY captured_at DESC LIMIT ?""",
        (account_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]
