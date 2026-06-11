import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import yt_dlp


class _SilentLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


MAX_VIDEOS = 50
MAX_SHORTS = 50
PER_VIDEO_WORKERS = 2


def _normalize_channel_url(handle_or_url):
    text = (handle_or_url or "").strip().rstrip("/")
    if not text:
        return None
    if text.startswith("http"):
        return text
    if text.startswith("@"):
        return f"https://www.youtube.com/{text}"
    if re.match(r"^UC[\w-]{22}$", text):
        return f"https://www.youtube.com/channel/{text}"
    return f"https://www.youtube.com/@{text.lstrip('@')}"


def _tab_url(channel_url, tab):
    base = channel_url.rstrip("/")
    for suffix in ("/videos", "/shorts", "/streams"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return f"{base}/{tab}"


def _flat_listing(url, limit):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "skip_download": True,
        "logger": _SilentLogger(),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def _video_detail(video_url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "logger": _SilentLogger(),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(video_url, download=False)
    except Exception:
        return None


def _is_short(info):
    if info.get("webpage_url", "").find("/shorts/") >= 0:
        return True
    if info.get("url", "").find("/shorts/") >= 0:
        return True
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration and duration <= 60:
        return True
    return False


def _format_post(info, is_short=False):
    upload_date = info.get("upload_date")
    timestamp = None
    date_str = ""
    if upload_date:
        try:
            dt = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            timestamp = int(dt.timestamp())
            date_str = dt.strftime("%d.%m.%Y")
        except ValueError:
            pass
    if timestamp is None and info.get("timestamp"):
        try:
            timestamp = int(info["timestamp"])
            date_str = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            pass

    return {
        "url": info.get("webpage_url") or info.get("original_url") or info.get("url"),
        "title": (info.get("title") or "").strip()[:160],
        "is_video": True,
        "is_short": is_short or _is_short(info),
        "timestamp": timestamp,
        "date": date_str,
        "views": info.get("view_count"),
        "likes": info.get("like_count"),
        "thumbnail": info.get("thumbnail"),
    }


def fetch(account):
    channel_url = _normalize_channel_url(account.get("handle"))
    if not channel_url:
        return {"error": "Manjka YouTube handle ali URL."}

    followers = None
    url_short_flag = {}
    total_entries = 0

    for tab, limit in (("videos", MAX_VIDEOS), ("shorts", MAX_SHORTS)):
        try:
            listing = _flat_listing(_tab_url(channel_url, tab), limit)
        except yt_dlp.utils.DownloadError:
            continue
        except Exception:
            continue
        if followers is None:
            followers = listing.get("channel_follower_count")
        entries = listing.get("entries") or []
        total_entries += len(entries)
        for entry in entries:
            if not entry:
                continue
            url = entry.get("url") or entry.get("webpage_url")
            if not url:
                continue
            if url.startswith("/"):
                url = "https://www.youtube.com" + url
            if not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            url_short_flag.setdefault(url, tab == "shorts")

    if followers is None:
        return {"error": "YouTube kanal ni dostopen."}

    if not url_short_flag:
        return {
            "followers": followers,
            "views": None,
            "likes": None,
            "posts": 0,
            "_warning": "YouTube kanal nima vidnih videov.",
        }

    posts = []
    items = list(url_short_flag.items())
    with ThreadPoolExecutor(max_workers=min(PER_VIDEO_WORKERS, len(items))) as executor:
        future_to_short = {
            executor.submit(_video_detail, url): is_short for url, is_short in items
        }
        for future in as_completed(future_to_short):
            info = future.result()
            if info:
                posts.append(_format_post(info, is_short=future_to_short[future]))

    posts.sort(key=lambda p: p.get("timestamp") or 0, reverse=True)

    # If per-video fetch was largely blocked (bot check), leave aggregates None so
    # the merge logic in app.fetch_and_preserve_posts keeps prior values.
    success_ratio = len(posts) / len(items) if items else 0
    if success_ratio < 0.5:
        return {
            "followers": followers,
            "views": None,
            "likes": None,
            "posts": total_entries,
            "posts_json": json.dumps(posts) if posts else None,
            "_warning": "YouTube je delno blokiral per-video poizvedbe.",
        }

    total_views = sum((p.get("views") or 0) for p in posts) or None
    total_likes = sum((p.get("likes") or 0) for p in posts) or None

    return {
        "followers": followers,
        "views": total_views,
        "likes": total_likes,
        "posts": total_entries or len(posts),
        "posts_json": json.dumps(posts) if posts else None,
    }
