import re
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import config

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ─── OpenClaw helpers ────────────────────────────────────────────────────────

_OC_URL = config.OPENCLAW_URL
_OC_TOKEN = config.OPENCLAW_TOKEN


def _oc(*args, timeout=45):
    """Run an openclaw browser command and return stdout."""
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
        "--timeout", "30000",
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"openclaw error: {out.strip()[:200]}")
    return out


def _oc_eval(js):
    """Evaluate JS in the current OpenClaw tab; return the raw output string."""
    return _oc("evaluate", "--fn", js)


def _oc_is_logged_in_fb():
    """Return True if the current OpenClaw session has a Facebook login."""
    try:
        cookies_out = _oc("cookies")
        cookies = json.loads(cookies_out)
        names = {c["name"] for c in cookies if "facebook.com" in c.get("domain", "")}
        return "c_user" in names and "xs" in names
    except Exception:
        return False


def _oc_get_fb_page_data(slug):
    """Use OpenClaw (logged-in) to scrape Facebook page followers, likes and posts."""
    tab_id = None
    try:
        opened = _oc("open", f"https://www.facebook.com/{slug}")
        m = re.search(r"tab:\s+(\S+)", opened)
        tab_id = m.group(1) if m else None
        time.sleep(4)

        # Scroll to load more posts
        for _ in range(3):
            _oc_eval("() => { window.scrollTo(0, document.body.scrollHeight); return true; }")
            time.sleep(2)

        # Extract page stats and posts in one JS call
        JS_EXTRACT = (
            "() => {"
            "  const getText = el => (el && el.textContent || '').trim();"
            "  // Followers"
            "  let followers = null;"
            "  document.querySelectorAll('a[href*=\"followers\"]').forEach(a => {"
            "    const t = getText(a);"
            "    const m = t.match(/^([\\d,.]+[KkMm]?)\\s*(followers?|sledilci)/i);"
            "    if (m && !followers) followers = m[1];"
            "  });"
            "  // Likes (people who like this)"
            "  let pagelikes = null;"
            "  document.querySelectorAll('a[href*=\"likes\"]').forEach(a => {"
            "    const t = getText(a);"
            "    const m = t.match(/^([\\d,.]+[KkMm]?)\\s*(likes?|všečki)/i);"
            "    if (m && !pagelike) pagelike = m[1];"
            "  });"
            "  // Posts"
            "  const postLinks = [];"
            "  const seen = new Set();"
            "  document.querySelectorAll('a[href*=\"/posts/\"],a[href*=\"/videos/\"],a[href*=\"/reels/\"]').forEach(a => {"
            "    const href = (a.href || '').split('?')[0];"
            "    if (!href.includes('facebook.com') || seen.has(href)) return;"
            "    if (!href.includes('/' + arguments[0] + '/') && !href.toLowerCase().includes('facebook.com/' + arguments[0].toLowerCase() + '/')) return;"
            "    seen.add(href);"
            "    let el = a.parentElement, dateText = '', reactions = null, views = null;"
            "    for (let i = 0; i < 20 && el; i++) {"
            "      const t = getText(el);"
            "      const dm = t.match(/\\b(\\d+[hdwm]|on (Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*|about a week ago|\\d+ weeks? ago|Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\\b/i);"
            "      if (dm && !dateText) dateText = dm[0];"
            "      const rm = t.match(/([\\d,.]+[KkMm]?)\\s*(reactions?|likes?|všečk)/i);"
            "      if (rm && reactions === null) reactions = rm[1];"
            "      const vm = t.match(/([\\d,.]+[KkMm]?)\\s*(views?|ogledi)/i);"
            "      if (vm && views === null) views = vm[1];"
            "      el = el.parentElement;"
            "    }"
            "    postLinks.push({ href, dateText, reactions, views });"
            "  });"
            "  return JSON.stringify({ followers, pagelike: pagelike, posts: postLinks.slice(0, 30) });"
            "}"
        )
        # Note: the JS above has a bug (pagelike reference) - simplified version below
        JS = (
            "() => {"
            "  const getText = el => (el ? el.textContent || '' : '').trim();"
            "  let followers = null, pagelike = null;"
            "  document.querySelectorAll('a').forEach(a => {"
            "    const t = getText(a);"
            "    if (!followers) { const m = t.match(/^([\\d,.]+[KkMm]?)\\s*(followers?)/i); if (m) followers = m[1]; }"
            "    if (!pagelike) { const m = t.match(/^([\\d,.]+[KkMm]?)\\s*(likes?)/i); if (m) pagelike = m[1]; }"
            "  });"
            "  const postLinks = [];"
            "  const seen = new Set();"
            "  document.querySelectorAll('a[href]').forEach(a => {"
            "    const href = (a.href || '').split('?')[0];"
            "    if (!/\\/posts\\/|\\/videos\\/|\\/reels\\//.test(href)) return;"
            "    if (!href.includes('facebook.com/')) return;"
            "    if (seen.has(href)) return; seen.add(href);"
            "    let el = a.parentElement, dateText = '', reactions = null, views = null;"
            "    for (let i = 0; i < 20 && el; i++) {"
            "      const t = getText(el);"
            "      if (!dateText) { const m = t.match(/\\b(\\d+[hdwm]|on (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|about a week ago|\\d+ weeks? ago|(January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d+)\\b/i); if (m) dateText = m[0]; }"
            "      if (reactions===null) { const m = t.match(/([\\.\\d,]+[KkMm]?)\\s*(reactions?|likes?)/i); if (m) reactions = m[1]; }"
            "      if (views===null) { const m = t.match(/([\\.\\d,]+[KkMm]?)\\s*(views?)/i); if (m) views = m[1]; }"
            "      el = el.parentElement;"
            "    }"
            "    postLinks.push({ href, dateText, reactions, views });"
            "  });"
            "  return JSON.stringify({ followers, pagelike, posts: postLinks.slice(0, 30) });"
            "}"
        )

        raw = _oc_eval(JS)
        # extract JSON from output
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        return json.loads(raw[start:end])
    except Exception as e:
        return None
    finally:
        if tab_id:
            try:
                _oc("close", tab_id)
            except Exception:
                pass


# ─── Parsing helpers ──────────────────────────────────────────────────────────

def _parse_count(text):
    if text is None:
        return None
    text = str(text).strip().replace(",", "").replace(".", "").replace("\xa0", "")
    if text.upper().endswith("K"):
        try:
            return int(float(text[:-1]) * 1000)
        except ValueError:
            return None
    if text.upper().endswith("M"):
        try:
            return int(float(text[:-1]) * 1_000_000)
        except ValueError:
            return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_count_str(text):
    """Parse '1.2K', '5,369', '1.2M' → int (handles decimal separators)."""
    if text is None:
        return None
    text = str(text).strip()
    # Remove thousand separators that are commas
    text = text.replace(",", "")
    up = text.upper()
    if up.endswith("K"):
        try:
            return int(float(text[:-1]) * 1_000)
        except ValueError:
            return None
    if up.endswith("M"):
        try:
            return int(float(text[:-1]) * 1_000_000)
        except ValueError:
            return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _page_slug(handle_or_url):
    text = handle_or_url.strip().rstrip("/")
    if "facebook.com/" in text:
        return text.split("facebook.com/")[-1].split("?")[0]
    return text


def _parse_fb_date(text):
    """Convert Facebook relative/day-name date to (dd.mm.yyyy, timestamp)."""
    if not text:
        return "", 0
    text = text.strip()
    now = datetime.now()

    m = re.match(r"^on (\w+)$", text, re.I)
    if m:
        day_name = m.group(1).capitalize()
        if day_name in DAYS:
            target_wd = DAYS.index(day_name)
            current_wd = now.weekday()
            days_back = (current_wd - target_wd) % 7
            if days_back == 0:
                days_back = 7
            dt = now - timedelta(days=days_back)
            return dt.strftime("%d.%m.%Y"), int(dt.timestamp())

    if re.match(r"about a week ago", text, re.I):
        dt = now - timedelta(days=7)
        return dt.strftime("%d.%m.%Y"), int(dt.timestamp())

    m = re.match(r"(\d+)\s+weeks?\s+ago", text, re.I)
    if m:
        dt = now - timedelta(weeks=int(m.group(1)))
        return dt.strftime("%d.%m.%Y"), int(dt.timestamp())

    m = re.match(r"^(\d+)d$", text, re.I)
    if m:
        dt = now - timedelta(days=int(m.group(1)))
        return dt.strftime("%d.%m.%Y"), int(dt.timestamp())

    m = re.match(r"^(\d+)h$", text, re.I)
    if m:
        dt = now - timedelta(hours=int(m.group(1)))
        return dt.strftime("%d.%m.%Y"), int(dt.timestamp())

    m = re.match(r"^(\d+)w$", text, re.I)
    if m:
        dt = now - timedelta(weeks=int(m.group(1)))
        return dt.strftime("%d.%m.%Y"), int(dt.timestamp())

    m = re.match(r"^(\d+)m$", text, re.I)
    if m:
        dt = now - timedelta(minutes=int(m.group(1)))
        return dt.strftime("%d.%m.%Y"), int(dt.timestamp())

    for fmt in ["%B %d", "%b %d"]:
        try:
            dt = datetime.strptime(f"{text} {now.year}", f"{fmt} %Y")
            if dt > now:
                dt = dt.replace(year=now.year - 1)
            return dt.strftime("%d.%m.%Y"), int(dt.timestamp())
        except ValueError:
            pass

    return text, 0


# ─── Playwright fallback ──────────────────────────────────────────────────────

def _extract_posts_plugin(slug):
    """Use Facebook Page Plugin (no login required) to get posts from last 7 days."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        plugin_url = (
            "https://www.facebook.com/plugins/page.php"
            f"?href={quote('https://www.facebook.com/' + slug, safe='')}"
            "&tabs=timeline&width=800&height=6000"
            "&small_header=false&adapt_container_width=true"
            "&hide_cover=false&show_facepile=false"
        )

        EXTRACT_JS = (
            '() => { const res = []; const seen = new Set(); '
            'document.querySelectorAll("a").forEach(a => { '
            '  if (!a.href || !a.href.includes("/posts/")) return; '
            '  const href = a.href.split("?")[0]; '
            '  if (!href.includes("facebook.com/")) return; '
            '  if (seen.has(href)) return; seen.add(href); '
            '  let el = a.parentElement, txt = ""; '
            '  for (var i = 0; i < 15; i++) { '
            '    if (!el) break; '
            '    const t = (el.innerText || "").trim(); '
            '    if (t.length > 10 && t.length < 500) { txt = t; break; } '
            '    el = el.parentElement; '
            '  } '
            '  res.push({ href, txt }); '
            '}); '
            'return res; }'
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, locale="en-US",
                                      viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            try:
                page.goto(plugin_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(5000)
            except PWTimeout:
                browser.close()
                return []

            seen_urls = set()
            all_items = []
            for _ in range(4):
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2500)
                items = page.evaluate(EXTRACT_JS) or []
                for item in items:
                    href = item.get("href", "")
                    if href and href not in seen_urls:
                        seen_urls.add(href)
                        all_items.append(item)

            browser.close()

        cutoff = datetime.now() - timedelta(days=7)
        cutoff_ts = int(cutoff.timestamp())

        posts = []
        for item in all_items:
            href = item.get("href", "")
            txt = item.get("txt", "")

            if "/" + slug.lower() + "/posts/" not in href.lower():
                continue

            date_text = ""
            m = re.search(r"\bon (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", txt, re.I)
            if m:
                date_text = "on " + m.group(1)
            if not date_text:
                m = re.search(r"\babout a week ago\b", txt, re.I)
                if m:
                    date_text = "about a week ago"
            if not date_text:
                m = re.search(r"\b(\d+)\s+weeks? ago\b", txt, re.I)
                if m:
                    date_text = m.group(0)
            if not date_text:
                m = re.search(r"\b(\d+[hdwm])\b", txt)
                if m:
                    date_text = m.group(1)
            if not date_text:
                m = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+\b", txt, re.I)
                if m:
                    date_text = m.group(0)

            date_str, ts = _parse_fb_date(date_text)

            if ts and ts < cutoff_ts:
                continue

            post_type = "reel" if "/reels/" in href else ("video" if "/videos/" in href else "post")
            posts.append({
                "url": href,
                "date": date_str,
                "timestamp": ts,
                "type": post_type,
                "likes": None,
            })

        posts.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return posts

    except Exception:
        return []


# ─── Main fetch ───────────────────────────────────────────────────────────────

def fetch(account):
    slug = _page_slug(account["handle"])

    # ── Try OpenClaw first (uses logged-in session if available) ──────────────
    if shutil.which("openclaw") and _oc_is_logged_in_fb():
        try:
            data = _oc_get_fb_page_data(slug)
            if data:
                followers = _parse_count_str(data.get("followers"))
                likes = _parse_count_str(data.get("pagelike"))

                posts = []
                for item in (data.get("posts") or []):
                    href = item.get("href", "")
                    if not href:
                        continue
                    date_str, ts = _parse_fb_date(item.get("dateText", ""))
                    post_type = "reel" if "/reels/" in href else ("video" if "/videos/" in href else "post")
                    posts.append({
                        "url": href,
                        "date": date_str,
                        "timestamp": ts,
                        "type": post_type,
                        "likes": _parse_count_str(item.get("reactions")),
                        "views": _parse_count_str(item.get("views")),
                    })
                posts.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

                if followers is not None or likes is not None:
                    return {
                        "followers": followers,
                        "views": None,
                        "likes": likes,
                        "posts": None,
                        "posts_json": json.dumps(posts) if posts else None,
                        "_source": "openclaw_logged_in",
                    }
        except Exception:
            pass  # Fall through to headless

    # ── Headless Playwright fallback (public, no login) ───────────────────────
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, locale="en-US")
            page = ctx.new_page()

            try:
                page.goto(
                    f"https://www.facebook.com/{slug}",
                    wait_until="networkidle",
                    timeout=30000,
                )
                page.wait_for_timeout(2000)
            except PWTimeout:
                browser.close()
                return {"error": "Facebook se ni naložil v času."}

            actual_slug = page.url.split("facebook.com/")[-1].rstrip("/").split("?")[0]
            if actual_slug and actual_slug not in ("", "login"):
                slug = actual_slug

            html = page.content()
            browser.close()

        likes = None
        followers = None

        m = re.search(r'og:description[^>]+content="[^"]*?([\d,]+)\s+likes', html, re.IGNORECASE)
        if m:
            likes = _parse_count(m.group(1))

        m = re.search(r'"text":"([\d,\.]+[KkMm]?)\s+followers?"', html, re.IGNORECASE)
        if m:
            followers = _parse_count(m.group(1))

        if followers is None:
            m = re.search(r"([\d,\.]+[KkMm]?)\s*[Ff]ollowers?", html)
            if m:
                followers = _parse_count(m.group(1))

        if likes is None:
            m = re.search(r"([\d,\.]+[KkMm]?)\s*people\s+like\s+this", html, re.IGNORECASE)
            if m:
                likes = _parse_count(m.group(1))

        if followers is None and likes is None:
            return {
                "followers": None, "views": None, "likes": None, "posts": None,
                "_warning": "Facebook ni vrnil podatkov. Stran je morda zasebna.",
            }

        posts = _extract_posts_plugin(slug)

        return {
            "followers": followers,
            "views": None,
            "likes": likes,
            "posts": None,
            "posts_json": json.dumps(posts) if posts else None,
        }

    except ImportError:
        return {"error": "Playwright ni nameščen."}
    except Exception as e:
        return {"error": str(e)}
