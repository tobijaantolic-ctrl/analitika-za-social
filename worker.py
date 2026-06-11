"""Background job worker for refresh tasks.

Runs jobs sequentially in a single thread to avoid Playwright/yt-dlp contention.
Job lifecycle is tracked in the `jobs` table.
"""

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import config
import database as db


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refresh-worker")


def _now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _run_refresh_job(job_id, account_ids, discover_new):
    """Worker body — executed in the single background thread."""
    import app as _app  # imported lazily to avoid circular import at module load

    db.update_job(job_id, status="running", started_at=_now_iso(), message="Osveževanje računov...")
    try:
        errors = _app.refresh_accounts(account_ids, discover_new=discover_new)
        status = "failed" if errors else "succeeded"
        message = (
            f"Osveženo {len(account_ids)} računov."
            if not errors
            else f"Težave: {len(errors)}"
        )
        db.update_job(
            job_id,
            status=status,
            finished_at=_now_iso(),
            message=message,
            error=json.dumps(errors) if errors else None,
        )
    except Exception as exc:  # pragma: no cover — defensive
        db.update_job(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            error=str(exc),
            message="Napaka v workerju.",
        )
    finally:
        db.prune_old_jobs(keep_last=50)


def enqueue_refresh(account_ids, discover_new=False, kind="manual_refresh"):
    """Create a job row and submit to the executor. Returns job_id."""
    job_id = db.create_job(kind, payload={"account_ids": account_ids, "discover_new": discover_new})
    _executor.submit(_run_refresh_job, job_id, account_ids, discover_new)
    return job_id


def enqueue_refresh_all(kind="auto_refresh"):
    """Convenience wrapper that refreshes every configured account."""
    account_ids = []
    for client in db.get_clients():
        for account in db.get_accounts(client["id"]):
            account_ids.append(account["id"])
    if not account_ids:
        return None
    return enqueue_refresh(account_ids, discover_new=False, kind=kind)


# ─── TikTok transcription ────────────────────────────────────────────────────


def _tiktok_accounts():
    """Return list of (account_id, handle) for every TikTok account."""
    out = []
    for client in db.get_clients():
        for acc in db.get_accounts(client["id"]):
            if acc["platform"] == "tiktok":
                out.append((acc["id"], acc["handle"]))
    return out


def _run_transcribe_tiktok_job(job_id, account_ids):
    """Worker body: download + transcribe missing TikTok videos sequentially."""
    if not config.TRANSCRIPTION_ENABLED:
        db.update_job(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            error="Transkripcija je v tem okolju izklopljena.",
            message="Onemogočeno",
        )
        return

    db.update_job(job_id, status="running", started_at=_now_iso(),
                  message="Iščem TikTok račune...")

    project_dir = config.PROJECT_DIR
    py = str(config.TRANSCRIPTION_PYTHON)
    base_output = Path(config.TIKTOK_UPLOAD_DIR)

    accounts = _tiktok_accounts()
    if account_ids:
        wanted = set(account_ids)
        accounts = [a for a in accounts if a[0] in wanted]
    if not accounts:
        db.update_job(job_id, status="failed", finished_at=_now_iso(),
                      message="Ni TikTok računov.", error="Ni TikTok računov.")
        return

    base_output.mkdir(parents=True, exist_ok=True)

    summary = []
    errors = []
    for idx, (acc_id, handle) in enumerate(accounts, start=1):
        # Download + transcribe share the SAME flat directory. Existing files
        # downloaded by previous runs (or earlier CLI usage) are recognized so
        # only genuinely new videos get processed.
        db.update_job(job_id, message=f"[{idx}/{len(accounts)}] {handle}: prenos videov...")
        dl_cmd = [
            py, str(project_dir / "scripts" / "download_tiktok_media.py"),
            "--account-id", str(acc_id),
            "--output-dir", str(base_output),
            "--workers", "3",
        ]
        try:
            dl = subprocess.run(dl_cmd, cwd=project_dir, capture_output=True,
                                text=True, timeout=1800, check=False)
        except subprocess.TimeoutExpired:
            errors.append(f"{handle}: timeout pri prenosu")
            continue
        if dl.returncode != 0:
            errors.append(f"{handle}: download napaka ({dl.returncode})")
            # don't continue — transcription on existing files may still help
        manifest = base_output / "manifest.csv"

        # 2. Transcribe new files (already-transcribed source paths are skipped).
        # NOTE: we do NOT pass --account-id here because the folder may hold files
        # from multiple accounts. Manifest.csv produced by the latest download is
        # the source of truth that links file → account.
        db.update_job(job_id, message=f"[{idx}/{len(accounts)}] {handle}: Whisper transkripcija...")
        tr_cmd = [
            py, str(project_dir / "scripts" / "transcribe_media.py"),
            "--folder", str(base_output),
            "--account-id", str(acc_id),
            "--model", config.WHISPER_MODEL,
            "--language", config.WHISPER_LANGUAGE,
        ]
        if manifest.exists():
            tr_cmd.extend(["--manifest", str(manifest)])
        try:
            tr = subprocess.run(tr_cmd, cwd=project_dir, capture_output=True,
                                text=True, timeout=7200, check=False)
        except subprocess.TimeoutExpired:
            errors.append(f"{handle}: timeout pri transkripciji")
            continue
        if tr.returncode != 0:
            errors.append(f"{handle}: transkripcija napaka ({tr.returncode})")

        # Count new completed transcripts
        completed = sum(
            1 for row in db.get_media_transcripts(acc_id)
            if row.get("status") == "complete"
        )
        summary.append(f"{handle}: {completed} skupaj prepisanih")

    final_message = " · ".join(summary) if summary else "Brez sprememb."
    if errors:
        final_message = f"{final_message} · Težave: {len(errors)}"

    db.update_job(
        job_id,
        status="failed" if errors else "succeeded",
        finished_at=_now_iso(),
        message=final_message,
        error=json.dumps(errors) if errors else None,
    )


def enqueue_transcribe_tiktok(account_ids=None, kind="transcribe_tiktok"):
    """Create a transcription job. account_ids=None means all TikTok accounts."""
    job_id = db.create_job(kind, payload={"account_ids": account_ids})
    _executor.submit(_run_transcribe_tiktok_job, job_id, account_ids)
    return job_id
