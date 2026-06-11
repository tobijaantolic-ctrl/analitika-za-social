import re
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import config


# ─── Cookie helpers ──────────────────────────────────────────────────────────

def _load_cookies_netscape(path):
    """Parse Netscape cookies.txt and return list of Playwright cookie dicts."""
    path = Path(path)
    if not path.exists():
        return []
    cookies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, cpath, secure, expires, name, value = parts[:7]
        # Only keep Facebook cookies; skip others to limit blast radius
        if "facebook.com" not in domain:
            continue
        try:
            expires_int = int(expires)
        except ValueError:
            expires_int = 0
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": cpath,
            "expires": expires_int if expires_int > 0 else -1,
            "httpOnly": False,
            "secure": secure.upper() == "TRUE",
            "sameSite": "Lax",
        })
    return cookies


def _have_valid_cookies():
    path = Path(config.FB_COOKIES_FILE)
    if not path.exists():
        return False
    return len(_load_cookies_netscape(path)) > 0


def _looks_like_login_wall(url, html):
    url_low = (url or "").lower()
    if "/login" in url_low or "checkpoint" in url_low or "two_step" in url_low:
        return True
    head = (html or "")[:3000].lower()
    return (
        "you must log in" in head
        or "log in to facebook" in head
        or "log in or sign up" in head
    )

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
        text = text.split("facebook.com/", 1)[1]
    m = re.match(r"^profile\.php\?id=(\d+)", text)
    if m:
        return f"profile.php?id={m.group(1)}"
    return text.split("?")[0]


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


_VIDEO_URL_RE = re.compile(r"facebook\.com/(\d+)/videos/(\d+)")
_REEL_URL_RE = re.compile(r"facebook\.com/reel/(\d+)")


def _parse_sl_count(text):
    """Parse counts in Slovenian: 3.472, 7,6 tis., 1,2 mio."""
    if not text:
        return None
    text = text.strip().lower()
    # Strip thousand-separator dots when no decimal mark follows
    if re.match(r"^\d{1,3}(\.\d{3})+$", text):
        return int(text.replace(".", ""))
    m = re.match(r"^([\d,]+)\s*(tis|k|mio|m)\.?", text)
    if m:
        n = float(m.group(1).replace(",", "."))
        unit = m.group(2)
        if unit in ("tis", "k"):
            n *= 1000
        elif unit in ("mio", "m"):
            n *= 1_000_000
        return int(n)
    try:
        return int(re.sub(r"[\s\.]", "", text))
    except ValueError:
        return None


def _fetch_logged_in_followers_and_posts(page_path, cookies, is_profile_id=False):
    """Visit profile/page + /videos endpoint with cookies to extract follower count and reels.

    `page_path` is either:
      - a numeric profile ID (when ``is_profile_id=True``) — visited via profile.php
      - a vanity slug (e.g. "Zupnikm") — visited directly

    Returns (followers, posts_list). Used when we have a logged-in FB session.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None, []

    if is_profile_id:
        profile_url = f"https://www.facebook.com/profile.php?id={page_path}"
        videos_url = f"https://www.facebook.com/{page_path}/videos"
    else:
        profile_url = f"https://www.facebook.com/{page_path}"
        videos_url = f"https://www.facebook.com/{page_path}/videos"

    followers = None
    posts = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, locale="sl-SI",
                                      viewport={"width": 1440, "height": 900})
            try:
                ctx.add_cookies(cookies)
            except Exception:
                pass
            page = ctx.new_page()

            # ── 1. Base profile URL → follower count
            try:
                page.goto(profile_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2500)
            except PWTimeout:
                pass

            if _looks_like_login_wall(page.url, page.content()):
                browser.close()
                return None, []

            follow_text = page.evaluate(
                """() => {
                    const lines = document.body.innerText.split('\\n');
                    for (const line of lines) {
                        // Slovenian: "sledilci: 7,6 tis." or "Sledilcev 7,6 tis."
                        let m = line.match(/sledilc\\w*\\s*:?\\s*([\\d,\\.]+\\s*(?:tis|k|mio|m)?\\.?)/i);
                        if (m) return m[1].trim();
                        // English: "7.6K followers"
                        m = line.match(/([\\d,\\.]+\\s*(?:k|m)?\\.?)\\s+followers?/i);
                        if (m) return m[1].trim();
                    }
                    return null;
                }"""
            )
            if follow_text:
                followers = _parse_sl_count(follow_text)

            # ── 2. /videos endpoint → reels list with views
            try:
                page.goto(videos_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)
            except PWTimeout:
                browser.close()
                return followers, []

            # Scroll to load more reels
            for _ in range(6):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)

            raw = page.evaluate(
                """() => {
                    const out = [];
                    const seen = new Set();
                    document.querySelectorAll('a[href*="/reel/"], a[href*="/videos/"]').forEach(a => {
                        const url = (a.href || '').split('?')[0];
                        if (!url || seen.has(url)) return;
                        if (!/\\/(reel|videos)\\/\\d/.test(url)) return;
                        seen.add(url);
                        // Find the parent container whose innerText contains both
                        // a duration (M:SS) and a view count — that's the reel card.
                        let bestText = '';
                        let el = a;
                        for (let i = 0; i < 8 && el; i++) {
                            const t = (el.innerText || '').trim();
                            if (t.length > 20 && t.length < 800
                                && /\\d:\\d{2}/.test(t)
                                && /ogled|view|prikaz/i.test(t)) {
                                bestText = t;
                                break;
                            }
                            el = el.parentElement;
                        }
                        out.push({ url, text: bestText });
                    });
                    return out;
                }"""
            )
            browser.close()
    except Exception:
        return followers, posts

    for item in raw:
        url = item["url"]
        m_v = _VIDEO_URL_RE.search(url)
        m_r = _REEL_URL_RE.search(url)
        is_reel = bool(m_r)
        if not (m_v or m_r):
            continue
        parsed = _parse_reel_card_text(item.get("text", ""))
        posts.append({
            "url": url,
            "title": parsed["title"],
            "date": parsed["date"],
            "timestamp": parsed["timestamp"],
            "type": "reel" if is_reel else "video",
            "views": parsed["views"],
            "likes": parsed["likes"],
        })

    # Dedup by URL (keep best data) since same reel may appear in multiple sections
    by_url = {}
    for p in posts:
        existing = by_url.get(p["url"])
        if not existing or (p.get("views") or 0) > (existing.get("views") or 0):
            by_url[p["url"]] = p
    posts = list(by_url.values())
    # Sort by views (desc); fallback to most recent
    posts.sort(
        key=lambda p: (p.get("views") or 0, p.get("timestamp") or 0),
        reverse=True,
    )
    return followers, posts


def _parse_reel_card_text(text):
    """Pull title, date, views, likes out of a reel card's innerText blob."""
    out = {"title": "", "date": "", "timestamp": 0, "views": None, "likes": None}
    if not text:
        return out
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # Title — first non-duration, non-status line
    for line in lines:
        if re.match(r"^\d{1,2}:\d{2}$", line):
            continue
        if re.match(r"^(0|pred|·|\xa0)", line, re.IGNORECASE):
            continue
        if "ogled" in line.lower() or "view" in line.lower():
            continue
        if "oseb" in line.lower() and "drug" in line.lower():
            continue
        if "Najbolj priljubljeni" in line or "Žal je prišlo" in line:
            continue
        out["title"] = line[:140]
        break

    # Date — Slovenian: "pred N (urami|dnevi|tedni|meseci)"
    date_str, ts = _parse_sl_relative_date(text)
    out["date"] = date_str
    out["timestamp"] = ts

    # Views — "14,2 tis. ogleda" / "350 ogleda"
    m = re.search(r"([\d,\.\s]+(?:tis|K|M|mio)?\.?)\s+ogled", text, re.IGNORECASE)
    if m:
        out["views"] = _parse_sl_count(m.group(1).strip())

    # Likes — "Tobija Antolic in 205 drugih oseb" => 206 reactions
    m = re.search(r"in\s+([\d,\.\s]+(?:tis|K|M|mio)?\.?)\s+drugih\s+oseb", text, re.IGNORECASE)
    if m:
        others = _parse_sl_count(m.group(1).strip()) or 0
        out["likes"] = others + 1

    return out


def _parse_sl_relative_date(text):
    """Slovenian relative dates: 'pred 2 dnevi', '5 h', '3 min'."""
    if not text:
        return "", 0
    text_low = text.lower()
    units = {
        "min": timedelta(minutes=1),
        "ur": timedelta(hours=1),
        "h": timedelta(hours=1),
        "dn": timedelta(days=1),
        "ted": timedelta(weeks=1),
        "mes": timedelta(days=30),
        "let": timedelta(days=365),
    }
    # "pred N <unit>" or "N <unit>"
    patterns = [
        r"pred\s+(\d{1,3})\s*(min|urami?|ur|h|dnevi|dni|tedni|tednom|meseci|mesecem|letom|leti)",
        r"(\d{1,3})\s*(min|h|d)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text_low)
        if m:
            n = int(m.group(1))
            unit = m.group(2)[:3]
            if unit == "ura":
                unit = "ur"
            delta = units.get(unit) or units.get(unit[:2])
            if not delta:
                continue
            dt = datetime.now() - n * delta
            return dt.strftime("%d.%m.%Y"), int(dt.timestamp())
    # Absolute "5. junij 2024"
    m = re.search(r"(\d{1,2})\.\s*([a-zšč]+)\s*(\d{4})", text_low)
    if m:
        months_sl = {
            "januar": 1, "februar": 2, "marec": 3, "april": 4, "maj": 5, "junij": 6,
            "julij": 7, "avgust": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "avg": 8,
            "sep": 9, "okt": 10, "nov": 11, "dec": 12,
        }
        mon = months_sl.get(m.group(2))
        if mon:
            try:
                dt = datetime(int(m.group(3)), mon, int(m.group(1)))
                return dt.strftime("%d.%m.%Y"), int(dt.timestamp())
            except ValueError:
                pass
    return "", 0


def _parse_fb_date_loose(text):
    """Look for any date-shaped substring in a blob and return (dd.mm.yyyy, ts)."""
    if not text:
        return "", 0
    # "May 7" / "May 7, 2025"
    m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{1,2})(?:,\s*(\d{4}))?", text)
    if m:
        mon_name, day, year = m.group(1), int(m.group(2)), int(m.group(3) or datetime.now().year)
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        try:
            dt = datetime(year, months.index(mon_name) + 1, day)
            return dt.strftime("%d.%m.%Y"), int(dt.timestamp())
        except ValueError:
            pass
    # "2h" / "3d" / "1w"
    m = re.search(r"\b(\d{1,2})\s*([hdwm])\b", text.lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n), "m": timedelta(days=30*n)}[unit]
        dt = datetime.now() - delta
        return dt.strftime("%d.%m.%Y"), int(dt.timestamp())
    return "", 0


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

    # ── Logged-in fast path — works for both profile.php and vanity URLs ─────
    # When cookies are present the /videos endpoint gives the full reels list
    # with view counts + reaction counts. For profile.php this is the ONLY way
    # to get per-post data. For vanity URLs it returns far more posts than the
    # 5-10 from the Page Plugin.
    cookies = _load_cookies_netscape(config.FB_COOKIES_FILE)
    if cookies:
        profile_id_match = re.match(r"^profile\.php\?id=(\d+)$", slug)
        if profile_id_match:
            followers, posts = _fetch_logged_in_followers_and_posts(
                profile_id_match.group(1), cookies, is_profile_id=True
            )
        else:
            # Vanity slug (e.g. "Zupnikm"). Skip if it looks like a URL fragment
            # we shouldn't visit (login, etc.).
            if slug and not slug.lower().startswith(("login", "plugins", "checkpoint")):
                followers, posts = _fetch_logged_in_followers_and_posts(
                    slug, cookies, is_profile_id=False
                )
            else:
                followers, posts = None, []
        if followers is not None or posts:
            return {
                "followers": followers,
                "views": None,
                "likes": None,
                "posts": None,
                "posts_json": json.dumps(posts) if posts else None,
            }

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
    # NOTE: for vanity-URL Pages we deliberately do NOT use cookies — being
    # signed in makes FB serve the admin/management view, which breaks the
    # og:description / "followers" regex below. The Page Plugin works fine
    # anonymously for these.
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        cookies = []
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

            raw_path = page.url.split("facebook.com/")[-1].rstrip("/")
            m = re.match(r"^profile\.php\?id=(\d+)", raw_path)
            if m:
                actual_slug = f"profile.php?id={m.group(1)}"
            else:
                actual_slug = raw_path.split("?")[0]
            if actual_slug and actual_slug not in ("", "login"):
                slug = actual_slug

            html = page.content()
            final_url = page.url
            browser.close()

        if _looks_like_login_wall(final_url, html):
            if cookies:
                return {"error": "FB seja je potekla. Naloži nove cookies v Setup."}
            return {"error": "FB zahteva prijavo. Naloži cookies v Setup."}

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
        # Page Plugin doesn't work for profile.php URLs — fall back to direct
        # logged-in scrape when we have cookies. Harmless for vanity pages too,
        # but we skip it to avoid the extra browser session if posts were found.
        if not posts and cookies:
            posts = _extract_posts_from_profile_logged_in(slug, cookies)

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
