import re
import requests
import json
import shutil
import subprocess
import time
from datetime import datetime

import config

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "X-IG-App-ID": "936619743392459",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

UA_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36"
)

# ─── OpenClaw helpers ────────────────────────────────────────────────────────

_OC_URL = config.OPENCLAW_URL
_OC_TOKEN = config.OPENCLAW_TOKEN


def _oc(*args, timeout=60):
    if not config.ENABLE_OPENCLAW:
        raise RuntimeError("openclaw disabled")
    if not _OC_TOKEN:
        raise RuntimeError("OPENCLAW_TOKEN is not configured")
    openclaw = shutil.which("openclaw")
    if not openclaw:
        raise RuntimeError("openclaw not found")
    cmd = [
        openclaw, "browser",
        "--url", _OC_URL,
        "--token", _OC_TOKEN,
        "--timeout", "35000",
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"openclaw error: {out.strip()[:200]}")
    return out


def _oc_is_logged_in_ig():
    """Return True if OpenClaw browser has an active Instagram session."""
    try:
        cookies_out = _oc("cookies")
        cookies = json.loads(cookies_out)
        names = {c["name"] for c in cookies if "instagram.com" in c.get("domain", "")}
        return "sessionid" in names
    except Exception:
        return False


def _oc_fetch_ig_profile(username):
    """Scrape Instagram profile + reels using the logged-in OpenClaw browser."""
    tab_id = None
    try:
        opened = _oc("open", f"https://www.instagram.com/{username}/")
        m = re.search(r"tab:\s+(\S+)", opened)
        tab_id = m.group(1) if m else None
        time.sleep(5)

        # Extract profile stats
        JS_PROFILE = (
            "() => {"
            "  const getText = el => (el ? el.textContent || '' : '').trim();"
            "  // meta description contains 'X Followers, Y Following, Z Posts'"
            "  const meta = document.querySelector('meta[name=\"description\"]');"
            "  const desc = meta ? meta.content : '';"
            "  let followers = null, posts_count = null;"
            "  const fm = desc.match(/([\\.\\d,]+[KkMm]?)\\s*Followers/i);"
            "  if (fm) followers = fm[1];"
            "  const pm = desc.match(/([\\.\\d,]+[KkMm]?)\\s*Posts/i);"
            "  if (pm) posts_count = pm[1];"
            "  // Reels: collect shortcodes"
            "  const reelLinks = [];"
            "  const seen = new Set();"
            "  document.querySelectorAll('a[href*=\"/reel/\"]').forEach(a => {"
            "    const m = (a.href || '').match(/\\/reel\\/([^/?]+)/);"
            "    if (!m || seen.has(m[1])) return;"
            "    seen.add(m[1]);"
            "    const spans = Array.from(a.querySelectorAll('span')).map(s => s.textContent.trim()).filter(Boolean);"
            "    const views = spans.length ? spans[spans.length - 1] : null;"
            "    reelLinks.push({ shortcode: m[1], url: a.href.split('?')[0], views });"
            "  });"
            "  return JSON.stringify({ followers, posts_count, reels: reelLinks.slice(0, 30) });"
            "}"
        )
        raw = _oc("evaluate", "--fn", JS_PROFILE)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        profile = json.loads(raw[start:end])

        # Also navigate to the reels tab to capture more
        _oc("navigate", f"https://www.instagram.com/{username}/reels/")
        time.sleep(4)
        for _ in range(3):
            _oc("evaluate", "--fn", "() => { window.scrollTo(0, document.body.scrollHeight); return true; }")
            time.sleep(2)

        JS_REELS = (
            "() => {"
            "  const out = {};"
            "  document.querySelectorAll('a[href*=\"/reel/\"]').forEach(a => {"
            "    const m = (a.href || '').match(/\\/reel\\/([^/?]+)/);"
            "    if (!m) return;"
            "    const sc = m[1];"
            "    if (out[sc]) return;"
            "    const spans = Array.from(a.querySelectorAll('span')).map(s => s.textContent.trim()).filter(Boolean);"
            "    if (spans.length) out[sc] = spans[spans.length - 1];"
            "  });"
            "  return JSON.stringify(out);"
            "}"
        )
        raw2 = _oc("evaluate", "--fn", JS_REELS)
        s2 = raw2.find("{")
        e2 = raw2.rfind("}") + 1
        reels_views = {}
        if s2 >= 0 and e2 > s2:
            reels_views = json.loads(raw2[s2:e2])

        return {"profile": profile, "reels_views": reels_views}

    except Exception:
        return None
    finally:
        if tab_id:
            try:
                _oc("close", tab_id)
            except Exception:
                pass


# ─── Count parser ─────────────────────────────────────────────────────────────

def _username(handle_or_url):
    text = handle_or_url.strip().rstrip("/")
    if "instagram.com/" in text:
        return text.split("instagram.com/")[-1].split("?")[0].lstrip("@")
    return text.lstrip("@")


def _parse_ig_count(text):
    """Convert '88.7K', '5,369', '1.2M' to int."""
    if text is None:
        return None
    text = str(text).strip().replace(",", "")
    if text.upper().endswith("K"):
        try:
            return int(float(text[:-1]) * 1_000)
        except ValueError:
            return None
    if text.upper().endswith("M"):
        try:
            return int(float(text[:-1]) * 1_000_000)
        except ValueError:
            return None
    try:
        return int(float(text))
    except ValueError:
        return None


# ─── Playwright fallback ──────────────────────────────────────────────────────

def _fetch_reels_plays(username):
    """Load Reels tab with Playwright, extract {shortcode: play_count} from DOM."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA_DESKTOP, locale="en-US")
            page = ctx.new_page()
            try:
                page.goto(
                    f"https://www.instagram.com/{username}/reels/",
                    wait_until="networkidle",
                    timeout=25000,
                )
                page.wait_for_timeout(3000)
            except PWTimeout:
                browser.close()
                return {}

            result = page.evaluate("""() => {
                const out = {};
                document.querySelectorAll('a[href*="/reel/"]').forEach(a => {
                    const m = a.href.match(/[/]reel[/]([^/?]+)/);
                    if (!m) return;
                    const shortcode = m[1];
                    if (out[shortcode]) return;
                    const spans = Array.from(a.querySelectorAll('span')).map(s => s.textContent.trim()).filter(Boolean);
                    if (spans.length > 0) {
                        out[shortcode] = spans[spans.length - 1];
                    }
                });
                return out;
            }""")
            browser.close()
            return {sc: _parse_ig_count(v) for sc, v in (result or {}).items()}
    except Exception:
        return {}


# ─── Main fetch ───────────────────────────────────────────────────────────────

def fetch(account):
    username = _username(account["handle"])

    # ── Try OpenClaw logged-in session first ─────────────────────────────────
    if shutil.which("openclaw") and _oc_is_logged_in_ig():
        try:
            oc_data = _oc_fetch_ig_profile(username)
            if oc_data:
                profile = oc_data.get("profile") or {}
                reels_views = oc_data.get("reels_views") or {}

                followers = _parse_ig_count(profile.get("followers"))
                posts_count = _parse_ig_count(profile.get("posts_count"))

                posts = []
                for reel in (profile.get("reels") or []):
                    sc = reel.get("shortcode")
                    views_raw = reels_views.get(sc) or reel.get("views")
                    posts.append({
                        "shortcode": sc,
                        "url": reel.get("url") or f"https://www.instagram.com/reel/{sc}/",
                        "is_video": True,
                        "typename": "GraphVideo",
                        "views": _parse_ig_count(views_raw),
                        "likes": None,
                        "thumbnail": None,
                        "caption": "",
                    })

                if followers is not None:
                    return {
                        "followers": followers,
                        "views": None,
                        "likes": None,
                        "posts": posts_count,
                        "posts_json": json.dumps(posts) if posts else None,
                        "_source": "openclaw_logged_in",
                    }
        except Exception:
            pass  # Fall through

    # ── Public API fallback ───────────────────────────────────────────────────
    try:
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()

        data = r.json()
        user = data["data"]["user"]

        followers = user.get("edge_followed_by", {}).get("count")
        posts_count = user.get("edge_owner_to_timeline_media", {}).get("count", 0)
        edges = user.get("edge_owner_to_timeline_media", {}).get("edges", [])

        total_likes = 0
        posts = []

        for edge in edges:
            node = edge.get("node", {})
            likes = (
                node.get("edge_liked_by", {}).get("count")
                or node.get("edge_media_preview_like", {}).get("count")
                or 0
            )
            total_likes += likes

            caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
            caption = ""
            if caption_edges:
                caption = caption_edges[0].get("node", {}).get("text", "")[:120]

            posts.append({
                "shortcode": node.get("shortcode"),
                "is_video": node.get("is_video", False),
                "typename": node.get("__typename", ""),
                "timestamp": node.get("taken_at_timestamp"),
                "views": None,
                "likes": likes,
                "thumbnail": node.get("thumbnail_src") or node.get("display_url"),
                "caption": caption,
            })

        reels_plays = _fetch_reels_plays(username)
        if reels_plays:
            for p in posts:
                sc = p.get("shortcode")
                if sc and sc in reels_plays and reels_plays[sc] is not None:
                    p["views"] = reels_plays[sc]

        return {
            "followers": followers,
            "views": None,
            "likes": total_likes if total_likes > 0 else None,
            "posts": posts_count,
            "posts_json": json.dumps(posts),
        }

    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        if code == 404:
            return {"error": f"Instagram profil '@{username}' ni najden"}
        if code in (401, 403):
            return {"error": "Instagram blokira dostop. Poskusi čez nekaj minut."}
        return {"error": f"HTTP {code}"}
    except Exception as e:
        return {"error": str(e)}
