#!/usr/bin/env python3
"""Download publicly playable TikTok media for linked dashboard posts.

This retrieves the same MP4 stream that an anonymous TikTok web page exposes
for playback. It validates the public post owner before storing a file and
writes a manifest that can be consumed by ``transcribe_media.py``.
"""

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import database as db

UA_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36"
)
PAGE_DATA_RE = re.compile(
    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>'
)
VIDEO_ID_RE = re.compile(r"/video/(\d+)")


def public_posts(account_id):
    snapshot = db.get_latest_good_snapshot(account_id)
    if not snapshot or not snapshot.get("posts_json"):
        raise SystemExit("Za TikTok racun ni zajetih javnih objav.")
    return json.loads(snapshot["posts_json"])


def account_handle(account_id):
    conn = db.get_db()
    row = conn.execute("SELECT handle, platform FROM accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    if not row or row["platform"] != "tiktok":
        raise SystemExit("Podani racun ni TikTok racun.")
    return row["handle"].lstrip("@").lower()


def media_urls(video):
    urls = []
    # Prefer broadly decodable H.264, then fall back to the public HEVC
    # representation when TikTok no longer serves an older H.264 rendition.
    for info in video.get("bitrateInfo") or []:
        if info.get("CodecType") != "h264":
            continue
        urls.extend((info.get("PlayAddr") or {}).get("UrlList") or [])
    for info in video.get("bitrateInfo") or []:
        if info.get("CodecType") == "h264":
            continue
        urls.extend((info.get("PlayAddr") or {}).get("UrlList") or [])
    if video.get("playAddr"):
        urls.append(video["playAddr"])
    return list(dict.fromkeys(urls))


def is_mp4(path):
    try:
        with path.open("rb") as source:
            return b"ftyp" in source.read(32)
    except OSError:
        return False


def fetch_one(post, expected_handle, output_dir, force=False):
    video_url = post.get("url") or ""
    match = VIDEO_ID_RE.search(video_url)
    if not match:
        return {"video_url": video_url, "status": "error", "error": "Manjka TikTok video ID."}
    video_id = match.group(1)
    destination = output_dir / f"{video_id}.mp4"
    if destination.exists() and destination.stat().st_size > 1024 and is_mp4(destination) and not force:
        return {
            "video_id": video_id,
            "video_url": video_url,
            "source_path": str(destination),
            "status": "existing",
            "bytes": destination.stat().st_size,
        }

    session = requests.Session()
    headers = {"User-Agent": UA_DESKTOP}
    try:
        page = session.get(video_url, headers=headers, timeout=25)
        page.raise_for_status()
        match_data = PAGE_DATA_RE.search(page.text)
        if not match_data:
            raise ValueError("TikTok stran nima javnih podatkov za video.")
        payload = json.loads(match_data.group(1))
        item = payload["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
        if str(item.get("id")) != video_id:
            raise ValueError("ID javne objave se ne ujema.")
        if str(item.get("author", {}).get("uniqueId", "")).lower() != expected_handle:
            raise ValueError("Objava ni od zahtevanega profila.")
        stream_urls = media_urls(item.get("video", {}))
        if not stream_urls:
            raise ValueError("Javni predvajalni MP4 ni na voljo.")

        partial = destination.with_suffix(".mp4.part")
        last_stream_error = None
        for stream_url in stream_urls:
            partial.unlink(missing_ok=True)
            try:
                with session.get(
                    stream_url,
                    headers={**headers, "Referer": video_url, "Accept": "*/*"},
                    timeout=90,
                    stream=True,
                ) as response:
                    response.raise_for_status()
                    if "video/mp4" not in response.headers.get("Content-Type", "").lower():
                        raise ValueError("Javni predvajalni odziv ni MP4.")
                    with partial.open("wb") as target:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                target.write(chunk)
                if is_mp4(partial):
                    break
                raise ValueError("Prenesena datoteka ni veljaven MP4.")
            except Exception as exc:
                last_stream_error = exc
        else:
            raise ValueError(f"Noben predvajalni MP4 ni uspel: {last_stream_error}")
        partial.replace(destination)
        return {
            "video_id": video_id,
            "video_url": video_url,
            "source_path": str(destination),
            "status": "downloaded",
            "bytes": destination.stat().st_size,
        }
    except Exception as exc:
        destination.with_suffix(".mp4.part").unlink(missing_ok=True)
        return {
            "video_id": video_id,
            "video_url": video_url,
            "status": "error",
            "error": str(exc)[:240],
        }


def write_manifest(path, posts, results):
    by_url = {row["video_url"]: row for row in results if row.get("source_path")}
    with path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=["source_path", "file_name", "video_url", "title", "views", "likes", "status"],
        )
        writer.writeheader()
        for post in posts:
            row = by_url.get(post.get("url"))
            if not row:
                continue
            writer.writerow({
                "source_path": row["source_path"],
                "file_name": Path(row["source_path"]).name,
                "video_url": post.get("url", ""),
                "title": post.get("title", ""),
                "views": post.get("views", ""),
                "likes": post.get("likes", ""),
                "status": row["status"],
            })


def main():
    parser = argparse.ArgumentParser(description="Prenesi javno predvajane TikTok videe za prepis.")
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--output-dir", default=str(PROJECT_DIR / "uploads" / "tiktok"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    db.init_db()
    handle = account_handle(args.account_id)
    posts = public_posts(args.account_id)
    photo_count = sum(post.get("is_video") is False for post in posts)
    posts = [post for post in posts if post.get("is_video", True)]
    if args.limit:
        posts = posts[:args.limit]
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Prenos javnih MP4 za @{handle}: {len(posts)} objav ...", flush=True)
    if photo_count:
        print(f"Izpuscene foto objave brez video toka: {photo_count}.", flush=True)

    results = []
    workers = max(1, min(args.workers, 5))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_one, post, handle, output_dir, args.force): post
            for post in posts
        }
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            status = result["status"]
            detail = result.get("video_id", result.get("video_url", ""))
            if status == "error":
                print(f"[{index}/{len(posts)}] Napaka {detail}: {result['error']}", flush=True)
            else:
                print(f"[{index}/{len(posts)}] {status}: {detail}", flush=True)
            if status == "downloaded":
                time.sleep(0.05)

    manifest = output_dir / "manifest.csv"
    write_manifest(manifest, posts, results)
    ready = sum(1 for result in results if result["status"] in ("downloaded", "existing"))
    errors = sum(1 for result in results if result["status"] == "error")
    print(f"Pripravljenih video datotek: {ready}/{len(posts)}. Napake: {errors}.", flush=True)
    print(f"Manifest: {manifest}", flush=True)
    raise SystemExit(0 if errors == 0 else 1)


if __name__ == "__main__":
    main()
