import re
import json
import sys
import time
import shutil
import subprocess
import requests
from html import unescape
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)
UA_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36"
)


def _username(handle_or_url):
    text = handle_or_url.strip().rstrip("/")
    if "tiktok.com/@" in text:
        return text.split("tiktok.com/@")[-1].split("?")[0]
    return text.lstrip("@")


def _caption_text(video, video_url):
    """Return only original-language Slovenian public captions.

    TikTok may publish machine translations or captions whose detected
    language does not match the spoken video. Presenting those as transcripts
    is misleading, so retain only non-translated Slovenian caption tracks.
    """
    subtitles = [
        subtitle for subtitle in (video.get("subtitleInfos") or [])
        if str(subtitle.get("LanguageCodeName", "")).lower().startswith(("sl-", "sl_", "slv"))
        and subtitle.get("Source") != "MT"
    ]
    for subtitle in subtitles:
        url = subtitle.get("Url") or subtitle.get("url")
        if not url:
            continue
        try:
            response = requests.get(
                url,
                headers={"User-Agent": UA_DESKTOP, "Referer": video_url},
                timeout=10,
            )
            if response.status_code != 200:
                continue
            text = response.text
            lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip()
                and not line.startswith("WEBVTT")
                and "-->" not in line
                and not line.isdigit()
            ]
            caption = " ".join(lines).strip()
            if caption:
                return unescape(caption)
        except Exception:
            continue
    return None


def _fetch_video_stats(video_url):
    """Fetch stats for a single public TikTok video page (no login needed)."""
    try:
        r = requests.get(video_url, headers={"User-Agent": UA_DESKTOP}, timeout=15)
        m = re.search(
            r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            r.text,
        )
        if not m:
            return None
        payload = json.loads(m.group(1))
        item = payload["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
        stats = item["stats"]
        ts = int(item.get("createTime", 0))
        video = item.get("video", {})
        is_video = not bool(item.get("imagePost"))
        return {
            "url": video_url,
            "author": item.get("author", {}).get("uniqueId", ""),
            "title": (item.get("desc") or "").strip()[:120],
            "is_video": is_video,
            "timestamp": ts,
            "date": datetime.fromtimestamp(ts).strftime("%d.%m.%Y") if ts else "",
            "views": int(stats.get("playCount", 0)),
            "likes": int(stats.get("diggCount", 0)),
            "thumbnail": video.get("cover", ""),
            "transcript": _caption_text(video, video_url) if is_video else None,
            "transcript_source": "TikTok slovenski podnapisi",
        }
    except Exception:
        return None


def _run_openclaw(*args):
    openclaw = shutil.which("openclaw")
    if not openclaw:
        raise ValueError("OpenClaw ni najden.")
    cmd = [openclaw, "browser", "--browser-profile", "openclaw", "--timeout", "30000", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("OpenClaw se ni pravočasno odzval.")
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode:
        raise ValueError(f"OpenClaw napaka: {output.strip()[:180]}")
    return output


def _parse_openclaw_urls(output):
    start = output.find('"[')
    end = output.rfind(']"')
    if start < 0 or end < start:
        return []
    try:
        return json.loads(json.loads(output[start: end + 2]))
    except Exception:
        return []


def _discover_video_urls_ytdlp(profile_url):
    """Collect all publicly listed profile videos through TikTok's public list."""
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--ignore-errors",
        "--no-warnings",
        "--print",
        "%(webpage_url)s",
        profile_url,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    username = _username(profile_url).lower()
    prefix = f"https://www.tiktok.com/@{username}/video/"
    return list(dict.fromkeys(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().lower().startswith(prefix)
    ))


def _discover_video_urls_openclaw(profile_url, expected_count=None):
    """Use a real browser as a fallback when the public paginated list is unavailable."""
    username = _username(profile_url)
    profile_prefix = f"/@{username.lower()}/video/"
    _run_openclaw("start")
    opened = _run_openclaw("open", profile_url)
    tab_match = re.search(r"tab:\s+(\S+)", opened)
    tab_id = tab_match.group(1) if tab_match else None

    try:
        urls = []
        stable_rounds = 0
        for i in range(45):
            time.sleep(2 if i == 0 else 1)
            output = _run_openclaw(
                "evaluate",
                "--fn",
                (
                    '() => JSON.stringify(Array.from(new Set(Array.from('
                    'document.querySelectorAll("a[href*=\'/video/\']"))'
                    '.map(a => a.href.split("?")[0])'
                    f'.filter(u => new URL(u).pathname.toLowerCase().startsWith("{profile_prefix}")))))'
                ),
            )
            before_count = len(urls)
            for url in _parse_openclaw_urls(output):
                if url not in urls:
                    urls.append(url)
            if expected_count and len(urls) >= expected_count:
                break
            stable_rounds = stable_rounds + 1 if len(urls) == before_count else 0
            if stable_rounds >= 6 and len(urls) > 0:
                break
            _run_openclaw(
                "evaluate",
                "--fn",
                "() => { window.scrollTo(0, document.body.scrollHeight); return true; }",
            )

        return urls[:expected_count] if expected_count else urls
    finally:
        if tab_id:
            _run_openclaw("close", tab_id)


def _discover_video_urls(profile_url, expected_count=None):
    """Return public profile video URLs, preferring the complete paginated list."""
    urls = _discover_video_urls_ytdlp(profile_url)
    if urls:
        return urls[:expected_count] if expected_count else urls
    return _discover_video_urls_openclaw(profile_url, expected_count)


def fetch(account):
    username = _username(account["handle"])

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        # Aggregate stats via Playwright (headless — no video grid needed)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA_MOBILE, locale="en-US")
            page = ctx.new_page()
            try:
                page.goto(
                    f"https://www.tiktok.com/@{username}",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                page.wait_for_timeout(3000)
            except PWTimeout:
                browser.close()
                return {"error": "TikTok se ni naložil v času. Poskusi znova."}
            html = page.content()
            browser.close()

        followers = likes = posts = None

        m = re.search(r'"followerCount":(\d+)', html)
        if m:
            followers = int(m.group(1))
        m = re.search(r'"heartCount":(\d+)', html)
        if m:
            likes = int(m.group(1))
        m = re.search(r'"videoCount":(\d+)', html)
        if m:
            posts = int(m.group(1))
        if followers is None:
            m = re.search(r'"fans":(\d+)', html)
            if m:
                followers = int(m.group(1))
        if likes is None:
            m = re.search(r'"diggCount":(\d+)', html)
            if m:
                likes = int(m.group(1))

        if followers is None:
            return {
                "followers": None, "views": None, "likes": None, "posts": None,
                "_warning": "TikTok ni vrnil podatkov. Profil je morda zaseben ali username napačen.",
            }

        previous_posts = []
        try:
            previous_posts = json.loads(account.get("_previous_posts_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            pass
        previous_by_url = {p.get("url"): p for p in previous_posts if p.get("url")}
        known_urls = list(previous_by_url)

        # Known videos refresh directly; profile browsing is only needed for discovery.
        video_posts = []
        try:
            discovered_urls = []
            if not known_urls or account.get("_discover_new"):
                profile_url = f"https://www.tiktok.com/@{username}"
                discovered_urls = _discover_video_urls(profile_url, expected_count=posts)
            video_urls = list(dict.fromkeys(discovered_urls + known_urls))
            with ThreadPoolExecutor(max_workers=min(10, len(video_urls) or 1)) as executor:
                futures = [executor.submit(_fetch_video_stats, url) for url in video_urls]
                for future in as_completed(futures):
                    stats = future.result()
                    if stats and stats.get("author", "").lower() == username.lower():
                        video_posts.append(stats)
            fetched_urls = {post["url"] for post in video_posts}
            video_posts.extend(
                post for url, post in previous_by_url.items() if url not in fetched_urls
            )
            video_posts.sort(key=lambda p: p.get("timestamp", 0), reverse=True)
        except Exception:
            video_posts = previous_posts

        return {
            "followers": followers,
            "views": None,
            "likes": likes,
            "posts": posts,
            "posts_json": json.dumps(video_posts) if video_posts else None,
        }

    except ImportError:
        return {"error": "Playwright ni nameščen. Zaženi: playwright install chromium"}
    except Exception as e:
        return {"error": str(e)}
