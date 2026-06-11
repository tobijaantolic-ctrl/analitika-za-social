from flask import Flask, Response, render_template, request, redirect, url_for, jsonify, session
import csv
import database as db
import importlib
import io
import json as _json
import os
import re
import secrets
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from werkzeug.security import check_password_hash

import config
import worker

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024

PROJECT_DIR = config.PROJECT_DIR
TIKTOK_UPLOAD_DIR = config.TIKTOK_UPLOAD_DIR
TRANSCRIPTION_PYTHON = config.TRANSCRIPTION_PYTHON
TIKTOK_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".m4a"}

from urllib.parse import quote as _urlquote, urlparse as _urlparse
app.jinja_env.filters["urlencode"] = lambda s: _urlquote(str(s), safe="")

PLATFORMS = ["youtube", "instagram", "facebook", "tiktok"]
PLATFORM_LABELS = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
}
PLATFORM_ICONS = {
    "youtube": "▶",
    "instagram": "📷",
    "facebook": "f",
    "tiktok": "♪",
}


def _auth_enabled():
    return config.AUTH_REQUIRED


def _check_admin_password(password):
    if config.ADMIN_PASSWORD_HASH:
        return check_password_hash(config.ADMIN_PASSWORD_HASH, password or "")
    if config.ADMIN_PASSWORD:
        return secrets.compare_digest(config.ADMIN_PASSWORD, password or "")
    return False


def _unauthorized():
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Analytics Dashboard"'},
    )


def _csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = _csrf_token


@app.before_request
def require_auth_and_csrf():
    if request.endpoint in {"health", "static"}:
        return None
    if _auth_enabled():
        auth = request.authorization
        if (
            not auth
            or auth.username != config.ADMIN_USERNAME
            or not _check_admin_password(auth.password)
        ):
            return _unauthorized()
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            sent_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
            if not sent_token or not secrets.compare_digest(sent_token, session.get("_csrf_token", "")):
                return Response("Invalid CSRF token.", 403)
    return None


def _fmt_number(n):
    if n is None:
        return None
    return f"{int(n):,}".replace(",", ".")


def _delta(current, old):
    if current is None or old is None:
        return None
    return current - old


def _pct_change(current, old):
    if current is None or old is None or old == 0:
        return None
    return round((current - old) / abs(old) * 100, 1)


def _display_handle(handle, platform):
    text = (handle or "").strip()
    if not text:
        return ""
    if platform == "facebook":
        m = re.search(r"profile\.php\?id=(\d+)", text)
        if m:
            return f"FB ID {m.group(1)}"
        if "facebook.com/" in text:
            slug = text.split("facebook.com/", 1)[1].rstrip("/").split("?")[0]
            return slug or text
    if text.startswith("http"):
        cleaned = text.rstrip("/").split("/")[-1].split("?")[0]
        return cleaned or text
    return text.lstrip("@")


def _time_ago(dt_str):
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        if secs < 120:
            return "pravkar"
        if secs < 3600:
            return f"{secs // 60}min nazaj"
        if secs < 86400:
            h = secs // 3600
            return f"{h}h nazaj"
        d = secs // 86400
        return f"{d}d nazaj"
    except Exception:
        return None


def _is_stale(dt_str, hours=12):
    if not dt_str:
        return True
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() > hours * 3600
    except Exception:
        return True


def _sparkline_svg(values, width=80, height=28):
    nums = [v for v in values if v is not None]
    if len(nums) < 2:
        return None
    mn, mx = min(nums), max(nums)
    pad = 2
    rh = height - pad * 2
    if mx == mn:
        pts = [(i / (len(nums) - 1) * width, height / 2) for i in range(len(nums))]
    else:
        pts = [
            (i / (len(nums) - 1) * width, pad + rh - (v - mn) / (mx - mn) * rh)
            for i, v in enumerate(nums)
        ]
    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    color = "#3ecf8e" if nums[-1] >= nums[0] else "#f76f6f"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'fill="none" xmlns="http://www.w3.org/2000/svg">'
        f'<path d="{path_d}" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def get_dashboard_data(days=30, content_scope="video"):
    clients = db.get_clients()
    result = []
    cutoff_ts = (
        int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        if days
        else None
    )

    for client in clients:
        accounts = db.get_accounts(client["id"])
        acc_data = []

        for acc in accounts:
            latest = db.get_latest_snapshot(acc["id"])
            good = db.get_latest_good_snapshot(acc["id"])
            snap_7d = db.get_snapshot_days_ago(acc["id"], 7)
            snap_30d = db.get_snapshot_days_ago(acc["id"], 30)
            history = db.get_account_history(acc["id"], limit=30)
            followers_hist = [h.get("followers") for h in history]
            mapped_transcripts = db.get_transcripts_by_url(acc["id"])

            def build_metric(key, history_vals=None):
                cur = good[key] if good else None
                d7 = _delta(cur, snap_7d[key] if snap_7d else None)
                d30 = _delta(cur, snap_30d[key] if snap_30d else None)
                p7 = _pct_change(cur, snap_7d[key] if snap_7d else None)
                p30 = _pct_change(cur, snap_30d[key] if snap_30d else None)
                sparkline = _sparkline_svg(history_vals) if history_vals is not None else None
                return {
                    "value": cur,
                    "fmt": _fmt_number(cur),
                    "delta_7d": d7,
                    "delta_7d_fmt": ("+" if d7 and d7 > 0 else "") + _fmt_number(d7) if d7 is not None else None,
                    "delta_30d": d30,
                    "delta_30d_fmt": ("+" if d30 and d30 > 0 else "") + _fmt_number(d30) if d30 is not None else None,
                    "pct_7d": p7,
                    "pct_7d_fmt": (f"+{p7}" if p7 is not None and p7 > 0 else str(p7)) + "%" if p7 is not None else None,
                    "pct_30d": p30,
                    "pct_30d_fmt": (f"+{p30}" if p30 is not None and p30 > 0 else str(p30)) + "%" if p30 is not None else None,
                    "sparkline": sparkline,
                }

            # Parse per-post data
            posts_list = []
            if good and good.get("posts_json"):
                try:
                    raw = _json.loads(good["posts_json"])
                    platform = acc["platform"]

                    if platform == "instagram":
                        for p in raw:
                            ts = p.get("timestamp")
                            date_str = (
                                datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m.%Y")
                                if ts else ""
                            )
                            posts_list.append({
                                "shortcode": p.get("shortcode", ""),
                                "is_video": p.get("is_video", False),
                                "typename": p.get("typename", ""),
                                "timestamp": ts,
                                "date": date_str,
                                "views": p.get("views"),
                                "views_fmt": _fmt_number(p.get("views")),
                                "likes": p.get("likes"),
                                "likes_fmt": _fmt_number(p.get("likes")),
                                "thumbnail": p.get("thumbnail", ""),
                                "caption": p.get("caption", ""),
                                "url": (
                                    f"https://www.instagram.com/reel/{p.get('shortcode', '')}/"
                                    if p.get("is_video")
                                    else f"https://www.instagram.com/p/{p.get('shortcode', '')}/"
                                ),
                            })

                    elif platform == "tiktok":
                        for p in raw:
                            linked = mapped_transcripts.get(p.get("url", ""), {})
                            posts_list.append({
                                "url": p.get("url", ""),
                                "title": p.get("title", ""),
                                "is_video": p.get("is_video", True),
                                "timestamp": p.get("timestamp"),
                                "date": p.get("date", ""),
                                "views": p.get("views"),
                                "views_fmt": _fmt_number(p.get("views")),
                                "likes": p.get("likes"),
                                "likes_fmt": _fmt_number(p.get("likes")),
                                "thumbnail": p.get("thumbnail", ""),
                                "transcript": p.get("transcript") or linked.get("transcript"),
                                "transcript_source": (
                                    p.get("transcript_source") if p.get("transcript")
                                    else linked.get("source_label")
                                ),
                            })

                    elif platform == "facebook":
                        for p in raw:
                            posts_list.append({
                                "url": p.get("url", ""),
                                "title": p.get("title", ""),
                                "timestamp": p.get("timestamp"),
                                "date": p.get("date", ""),
                                "type": p.get("type", "post"),
                                "is_video": p.get("type") in ("reel", "video"),
                                "views": p.get("views"),
                                "views_fmt": _fmt_number(p.get("views")),
                                "likes": p.get("likes"),
                                "likes_fmt": _fmt_number(p.get("likes")),
                            })

                    elif platform == "youtube":
                        for p in raw:
                            posts_list.append({
                                "url": p.get("url", ""),
                                "title": p.get("title", ""),
                                "is_video": p.get("is_video", True),
                                "is_short": p.get("is_short", False),
                                "timestamp": p.get("timestamp"),
                                "date": p.get("date", ""),
                                "views": p.get("views"),
                                "views_fmt": _fmt_number(p.get("views")),
                                "likes": p.get("likes"),
                                "likes_fmt": _fmt_number(p.get("likes")),
                                "thumbnail": p.get("thumbnail", ""),
                            })
                except Exception:
                    pass

            all_posts_list = posts_list
            posts_list = [
                post for post in all_posts_list
                if cutoff_ts is None
                or (post.get("timestamp") is not None and post["timestamp"] >= cutoff_ts)
            ]
            if content_scope == "video":
                posts_list = [post for post in posts_list if post.get("is_video")]

            # Annotate each post with engagement_rate for color coding + max for relative bar
            for post in posts_list:
                views = post.get("views")
                likes = post.get("likes")
                if views and views > 0 and likes is not None:
                    post["engagement_rate"] = round(likes / views * 100, 1)
                else:
                    post["engagement_rate"] = None
            max_views = max((p.get("views") or 0) for p in posts_list) if posts_list else 0
            for post in posts_list:
                v = post.get("views") or 0
                post["views_pct"] = round(v / max_views * 100) if max_views else 0
            period_views = [post["views"] for post in posts_list if post.get("views") is not None]
            period_likes = [post["likes"] for post in posts_list if post.get("likes") is not None]
            engagement_posts = [
                post for post in posts_list
                if post.get("views") not in (None, 0) and post.get("likes") is not None
            ]
            engagement_views = sum(post["views"] for post in engagement_posts)
            engagement_likes = sum(post["likes"] for post in engagement_posts)
            engagement_rate = (
                round(engagement_likes / engagement_views * 100, 1)
                if engagement_views else None
            )

            last_update_raw = (good or latest)["captured_at"] if (good or latest) else None
            acc_data.append({
                "id": acc["id"],
                "platform": acc["platform"],
                "platform_label": PLATFORM_LABELS.get(acc["platform"], acc["platform"]),
                "platform_icon": PLATFORM_ICONS.get(acc["platform"], ""),
                "handle": acc["handle"],
                "display_handle": _display_handle(acc["handle"], acc["platform"]),
                "has_login": acc["has_login"],
                "last_update": last_update_raw[:16].replace("T", " ") if last_update_raw else None,
                "time_ago": _time_ago(last_update_raw),
                "is_stale": _is_stale(last_update_raw),
                "error": latest["error"] if latest and latest.get("error") else None,
                "warning": None,
                "followers": build_metric("followers", followers_hist),
                "views": build_metric("views"),
                "likes": build_metric("likes"),
                "posts": build_metric("posts"),
                "posts_list": posts_list,
                "max_views": max_views,
                "known_posts_count": len(all_posts_list),
                "period": {
                    "count": len(posts_list),
                    "views_fmt": _fmt_number(sum(period_views)) if period_views else None,
                    "likes_fmt": _fmt_number(sum(period_likes)) if period_likes else None,
                    "views_coverage": len(period_views),
                    "likes_coverage": len(period_likes),
                    "engagement_fmt": (
                        str(engagement_rate).replace(".", ",") + " %"
                        if engagement_rate is not None else None
                    ),
                },
                "last_post_date": (posts_list[0]["date"] or None) if posts_list else None,
            })

        # Per-platform top reels for the boards (one section per platform).
        platform_buckets = {}
        for a in acc_data:
            for p in a["posts_list"]:
                if (p.get("views") or 0) <= 0:
                    continue
                bucket = platform_buckets.setdefault(a["platform"], {
                    "platform": a["platform"],
                    "platform_label": a["platform_label"],
                    "platform_icon": a["platform_icon"],
                    "handle": a.get("display_handle") or a["handle"],
                    "posts": [],
                })
                bucket["posts"].append({
                    "title": (p.get("title") or p.get("caption") or "(brez naslova)").strip(),
                    "date": p.get("date") or "",
                    "url": p.get("url"),
                    "thumbnail": p.get("thumbnail"),
                    "platform": a["platform"],
                    "views": p.get("views"),
                    "views_fmt": p.get("views_fmt"),
                    "likes": p.get("likes"),
                    "likes_fmt": p.get("likes_fmt"),
                    "engagement_rate": p.get("engagement_rate"),
                    "transcript": p.get("transcript"),
                    "is_short": p.get("is_short", False),
                    "is_video": p.get("is_video", False),
                })
        platform_boards = []
        for platform_key in ("tiktok", "instagram", "youtube", "facebook"):
            bucket = platform_buckets.get(platform_key)
            if not bucket or not bucket["posts"]:
                continue
            bucket["posts"] = sorted(bucket["posts"], key=lambda p: p["views"], reverse=True)[:12]
            platform_boards.append(bucket)

        total_followers = sum((a["followers"]["value"] or 0) for a in acc_data)
        total_period_views = sum(
            int(a["period"]["views_fmt"].replace(".", "")) if a["period"]["views_fmt"] else 0
            for a in acc_data
        )
        total_period_likes = sum(
            int(a["period"]["likes_fmt"].replace(".", "")) if a["period"]["likes_fmt"] else 0
            for a in acc_data
        )
        total_period_posts = sum(a["period"]["count"] for a in acc_data)
        avg_engagement = (
            round(total_period_likes / total_period_views * 100, 1)
            if total_period_views else None
        )

        result.append({
            "id": client["id"],
            "name": client["name"],
            "accounts": acc_data,
            "account_count": len(acc_data),
            "platform_boards": platform_boards,
            "summary": {
                "total_followers_fmt": _fmt_number(total_followers) if total_followers else None,
                "total_period_views_fmt": _fmt_number(total_period_views) if total_period_views else None,
                "total_period_likes_fmt": _fmt_number(total_period_likes) if total_period_likes else None,
                "total_period_posts": total_period_posts,
                "avg_engagement_fmt": (
                    str(avg_engagement).replace(".", ",") + " %"
                    if avg_engagement is not None else None
                ),
            },
        })

    return result


@app.route("/")
def dashboard():
    days, content_scope = _read_dashboard_filters()
    clients_data = get_dashboard_data(days=days, content_scope=content_scope)
    period_label = "Vsi zajeti" if days == 0 else f"Zadnjih {days} dni"
    content_label = "reeli / videi" if content_scope == "video" else "posamezne objave"
    return render_template(
        "dashboard.html",
        clients=clients_data,
        platform_labels=PLATFORM_LABELS,
        days=days,
        period_label=period_label,
        content_scope=content_scope,
        content_label=content_label,
    )


@app.route("/health")
def health():
    return jsonify({"ok": True, "app": "analytics-dashboard"})


def _read_dashboard_filters():
    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    if days not in (0, 7, 30):
        days = 30
    content_scope = request.args.get("content", "video")
    if content_scope not in ("all", "video"):
        content_scope = "video"
    return days, content_scope


def _csv_value(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


@app.route("/export.csv")
def export_csv():
    days, content_scope = _read_dashboard_filters()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Stranka", "Platforma", "Racun", "Datum", "Vrsta", "Opis", "Ogledi", "Vsecki/Reakcije", "Transkript", "Povezava"])
    for client in get_dashboard_data(days=days, content_scope=content_scope):
        for account in client["accounts"]:
            for post in account["posts_list"]:
                kind = "Video" if post.get("is_video") else "Objava"
                description = post.get("caption") or post.get("title") or ""
                writer.writerow([
                    _csv_value(client["name"]),
                    account["platform_label"],
                    _csv_value(account["handle"]),
                    post.get("date", ""),
                    kind,
                    _csv_value(description),
                    post.get("views", ""),
                    post.get("likes", ""),
                    _csv_value(post.get("transcript", "")),
                    post.get("url", ""),
                ])
    body = "\ufeff" + output.getvalue()
    return Response(
        body,
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="social-analytics.csv"'},
    )


def _transcript_clients_data():
    tiktok_rows = []
    tiktok_video_count = 0
    completed_count = 0
    tiktok_accounts = []
    transcript_clients = []

    for client in get_dashboard_data(days=0, content_scope="video"):
        client_rows = []
        client_video_count = 0
        client_completed_count = 0
        for account in client["accounts"]:
            if account["platform"] != "tiktok":
                continue
            account_video_count = len(account["posts_list"])
            account_completed_count = 0
            local_by_url = {
                row["video_url"]: row
                for row in db.get_media_transcripts(account["id"])
                if row.get("video_url")
            }
            for post in account["posts_list"]:
                tiktok_video_count += 1
                client_video_count += 1
                linked = local_by_url.get(post.get("url"), {})
                transcript = post.get("transcript")
                status = "complete" if transcript else linked.get("status", "missing")
                if status == "complete":
                    completed_count += 1
                    account_completed_count += 1
                    client_completed_count += 1
                views_n = post.get("views")
                likes_n = post.get("likes")
                engagement = (
                    round(likes_n / views_n * 100, 1)
                    if views_n and views_n > 0 and likes_n is not None else None
                )
                row = {
                    "account_id": account["id"],
                    "client_id": client["id"],
                    "client_name": client["name"],
                    "handle": account["handle"],
                    "title": post.get("title") or "TikTok video",
                    "date": post.get("date"),
                    "timestamp": post.get("timestamp"),
                    "views": views_n,
                    "views_fmt": post.get("views_fmt"),
                    "likes": likes_n,
                    "likes_fmt": post.get("likes_fmt"),
                    "engagement_rate": engagement,
                    "video_url": post.get("url"),
                    "transcript": transcript,
                    "source_label": post.get("transcript_source") or linked.get("source_label"),
                    "duration_seconds": linked.get("duration_seconds"),
                    "status": status,
                    "error": linked.get("error"),
                }
                tiktok_rows.append(row)
                client_rows.append(row)
            tiktok_accounts.append({
                "id": account["id"],
                "handle": account["handle"],
                "video_count": account_video_count,
                "completed_count": account_completed_count,
            })
        if client_rows:
            transcript_clients.append({
                "id": client["id"],
                "name": client["name"],
                "rows": client_rows,
                "video_count": client_video_count,
                "completed_count": client_completed_count,
            })

    return {
        "tiktok_rows": tiktok_rows,
        "tiktok_video_count": tiktok_video_count,
        "completed_count": completed_count,
        "tiktok_accounts": tiktok_accounts,
        "transcript_clients": transcript_clients,
    }


def _markdown_filename(client_name):
    slug = re.sub(r"[^a-z0-9]+", "-", client_name.lower()).strip("-")
    return f"tiktok-prepisi-{slug or 'stranka'}.md"


def _client_transcripts_markdown(client):
    lines = [
        f"# TikTok prepisi - {client['name']}",
        "",
        f"Izvozeno: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"Video prepisi: {client['completed_count']} / {client['video_count']}",
        "",
    ]
    current_handle = None
    for index, row in enumerate(client["rows"], start=1):
        if row["handle"] != current_handle:
            current_handle = row["handle"]
            lines.extend([f"## {current_handle}", ""])
        lines.extend([
            f"### {index}. {row['title']}",
            "",
            f"- Datum: {row['date'] or 'ni podatka'}",
            f"- Ogledi: {row['views_fmt'] or 'ni podatka'}",
            f"- Vsecki: {row['likes_fmt'] or 'ni podatka'}",
            f"- Povezava: {row['video_url']}",
            f"- Status: {'prepisano' if row['status'] == 'complete' else row['status']}",
            "",
            "**Prepis**",
            "",
            (row["transcript"] or row["error"] or "Prepis ni na voljo.").strip(),
            "",
            "---",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


@app.route("/transcripts")
def transcripts():
    data = _transcript_clients_data()

    return render_template(
        "transcripts.html",
        **data,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@app.route("/transcripts/export/<int:client_id>.md")
def export_client_transcripts(client_id):
    client = next(
        (item for item in _transcript_clients_data()["transcript_clients"] if item["id"] == client_id),
        None,
    )
    if not client:
        return Response("Za to stranko ni TikTok video prepisov.", status=404)
    return Response(
        _client_transcripts_markdown(client),
        content_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_markdown_filename(client["name"])}"'
        },
    )


@app.post("/transcripts/refresh")
def refresh_transcripts():
    if not config.TRANSCRIPTION_ENABLED:
        return jsonify({"ok": False, "error": "Transkripcija je v tem okolju izklopljena."}), 400
    data = request.get_json(silent=True) or {}
    account_ids = data.get("account_ids")
    if account_ids is not None and not isinstance(account_ids, list):
        return jsonify({"ok": False, "error": "account_ids mora biti list."}), 400
    if account_ids:
        try:
            account_ids = [int(x) for x in account_ids]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Neveljaven account_id."}), 400
    job_id = worker.enqueue_transcribe_tiktok(account_ids)
    return jsonify({"ok": True, "job_id": job_id})


@app.post("/transcripts/upload")
def upload_tiktok_transcript():
    if not config.TRANSCRIPTION_ENABLED:
        return redirect(url_for("transcripts", error="Prepisovanje videov je v tem okolju izklopljeno."))

    video_url = request.form.get("video_url", "").strip()
    try:
        account_id = int(request.form.get("account_id", ""))
    except ValueError:
        return redirect(url_for("transcripts", error="Neveljavna TikTok objava."))

    valid_post = False
    for client in get_dashboard_data(days=0, content_scope="video"):
        for account in client["accounts"]:
            if account["id"] == account_id and account["platform"] == "tiktok":
                valid_post = any(post.get("url") == video_url for post in account["posts_list"])
                break
    if not valid_post:
        return redirect(url_for("transcripts", error="TikTok objava ni najdena med zajetimi videi."))

    media = request.files.get("video_file")
    suffix = Path(media.filename or "").suffix.lower() if media else ""
    video_id_match = re.search(r"/video/(\d+)", video_url)
    if not media or suffix not in TIKTOK_UPLOAD_EXTENSIONS or not video_id_match:
        return redirect(url_for("transcripts", error="Nalozi shranjen video MP4, MOV, M4V ali WEBM."))

    TIKTOK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = TIKTOK_UPLOAD_DIR / f"{video_id_match.group(1)}{suffix}"
    media.save(destination)
    command = [
        str(TRANSCRIPTION_PYTHON),
        str(PROJECT_DIR / "scripts" / "transcribe_media.py"),
        "--file", str(destination),
        "--account-id", str(account_id),
        "--video-url", video_url,
        "--model", config.WHISPER_MODEL,
        "--language", config.WHISPER_LANGUAGE,
        "--force",
    ]
    try:
        result = subprocess.run(
            command, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=1800, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return redirect(url_for("transcripts", error="Prepisovanje videa ni uspelo."))
    if result.returncode != 0:
        return redirect(url_for("transcripts", error="Prepisovanje videa ni uspelo."))
    return redirect(url_for("transcripts", notice="TikTok video je nalozen in prepisan."))


def fetch_and_preserve_posts(account):
    previous = db.get_latest_good_snapshot(account["id"])
    best_posts_json = db.get_best_posts_json(account["id"])
    fetch_account = dict(account)
    fetch_account["_previous_posts_json"] = best_posts_json
    fetcher = importlib.import_module(f"fetchers.{account['platform']}")
    result = fetcher.fetch(fetch_account)
    if not result.get("error") and not result.get("posts_json") and best_posts_json:
        result["posts_json"] = best_posts_json
    elif not result.get("error") and result.get("posts_json") and best_posts_json:
        try:
            historical = {
                post.get("url") or post.get("shortcode"): post
                for post in _json.loads(best_posts_json)
            }
            current = _json.loads(result["posts_json"])
            current_keys = set()
            for post in current:
                key = post.get("url") or post.get("shortcode")
                current_keys.add(key)
                old = historical.get(key)
                if old and old.get("views") is not None:
                    if post.get("views") is None or post["views"] < old["views"]:
                        post["views"] = old["views"]
                if old and post.get("likes") is None and old.get("likes") is not None:
                    post["likes"] = old["likes"]
            current.extend(post for key, post in historical.items() if key not in current_keys)
            result["posts_json"] = _json.dumps(current)
        except (TypeError, ValueError):
            result["posts_json"] = best_posts_json
    return result


def refresh_accounts(account_ids, discover_new=False):
    def read_account(account_id):
        conn = db.get_db()
        account = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        if not account:
            return None, None
        account = dict(account)
        account["_discover_new"] = discover_new
        try:
            return account, fetch_and_preserve_posts(account)
        except Exception as exc:
            return account, {"error": str(exc)}

    errors = []
    with ThreadPoolExecutor(max_workers=min(3, len(account_ids) or 1)) as executor:
        futures = [executor.submit(read_account, account_id) for account_id in account_ids]
        for future in as_completed(futures):
            account, result = future.result()
            if not account:
                continue
            db.save_snapshot(account["id"], result)
            if result.get("error"):
                errors.append(f"{account['handle']}: {result['error']}")
    return errors


@app.route("/refresh", methods=["POST"])
def refresh():
    account_id = request.form.get("account_id")
    client_id = request.form.get("client_id")

    if account_id:
        account_ids = [int(account_id)]
    elif client_id:
        accounts = db.get_accounts(int(client_id))
        account_ids = [a["id"] for a in accounts]
    else:
        clients = db.get_clients()
        account_ids = []
        for c in clients:
            for a in db.get_accounts(c["id"]):
                account_ids.append(a["id"])

    refresh_accounts(account_ids)

    return redirect(url_for("dashboard"))


@app.route("/refresh_ajax", methods=["POST"])
def refresh_ajax():
    data_in = request.get_json(silent=True) or {}
    account_id = data_in.get("account_id")
    client_id = data_in.get("client_id")
    refresh_all = data_in.get("all", False)
    discover_new = data_in.get("discover_new", False)

    if refresh_all:
        clients = db.get_clients()
        account_ids = []
        for c in clients:
            for a in db.get_accounts(c["id"]):
                account_ids.append(a["id"])
    elif account_id:
        account_ids = [int(account_id)]
    elif client_id:
        accounts = db.get_accounts(int(client_id))
        account_ids = [a["id"] for a in accounts]
    else:
        return jsonify({"ok": False, "error": "Ni podanega ID-ja"})

    job_id = worker.enqueue_refresh(account_ids, discover_new=discover_new)
    return jsonify({"ok": True, "job_id": job_id, "account_count": len(account_ids)})


@app.route("/api/fb_cookies/status")
def fb_cookies_status():
    path = Path(config.FB_COOKIES_FILE)
    if not path.exists():
        return jsonify({
            "exists": False,
            "warning_days": config.FB_COOKIES_EXPIRY_WARNING_DAYS,
        })
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    age_days = (datetime.now(timezone.utc) - modified).days
    # Count Facebook cookie entries (rough — just non-comment, non-empty lines)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        cookie_count = sum(
            1 for line in lines
            if line.strip() and not line.startswith("#") and "facebook.com" in line
        )
    except Exception:
        cookie_count = 0
    return jsonify({
        "exists": True,
        "modified": modified.replace(tzinfo=None).isoformat(),
        "age_days": age_days,
        "cookie_count": cookie_count,
        "size_bytes": stat.st_size,
        "warning_days": config.FB_COOKIES_EXPIRY_WARNING_DAYS,
        "near_expiry": age_days >= config.FB_COOKIES_EXPIRY_WARNING_DAYS,
    })


@app.post("/setup/facebook_cookies")
def upload_facebook_cookies():
    file = request.files.get("cookies_file")
    if not file:
        return redirect(url_for("setup", error="Manjka datoteka."))
    content = file.read()
    if not content or b"facebook.com" not in content:
        return redirect(url_for("setup", error="Datoteka ne vsebuje Facebook cookies."))
    if len(content) > 256 * 1024:
        return redirect(url_for("setup", error="Datoteka je prevelika (max 256 KB)."))
    path = Path(config.FB_COOKIES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return redirect(url_for("setup", notice="Facebook cookies shranjeni. Zaženi refresh."))


@app.post("/setup/facebook_cookies/delete")
def delete_facebook_cookies():
    path = Path(config.FB_COOKIES_FILE)
    if path.exists():
        path.unlink()
    return redirect(url_for("setup", notice="Facebook cookies odstranjeni."))


@app.route("/api/jobs/active")
def active_jobs():
    active = db.get_active_jobs()
    latest = db.get_latest_finished_job()
    return jsonify({
        "active": active,
        "latest_finished": latest,
        "now": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    })


@app.route("/setup")
def setup():
    clients = db.get_clients()
    yt_key = config.YOUTUBE_API_KEY or db.get_config("youtube_api_key", "")
    accounts_by_client = {c["id"]: db.get_accounts(c["id"]) for c in clients}

    fb_cookies_path = Path(config.FB_COOKIES_FILE)
    if fb_cookies_path.exists():
        stat = fb_cookies_path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        age_days = (datetime.now(timezone.utc) - modified).days
        try:
            lines = fb_cookies_path.read_text(encoding="utf-8").splitlines()
            cookie_count = sum(1 for line in lines if line.strip() and not line.startswith("#") and "facebook.com" in line)
        except Exception:
            cookie_count = 0
        fb_cookies_status = {
            "exists": True,
            "modified": modified.strftime("%d.%m.%Y %H:%M"),
            "age_days": age_days,
            "cookie_count": cookie_count,
            "near_expiry": age_days >= config.FB_COOKIES_EXPIRY_WARNING_DAYS,
            "warning_days": config.FB_COOKIES_EXPIRY_WARNING_DAYS,
        }
    else:
        fb_cookies_status = {"exists": False, "warning_days": config.FB_COOKIES_EXPIRY_WARNING_DAYS}

    return render_template(
        "setup.html",
        clients=clients,
        accounts_by_client=accounts_by_client,
        yt_key=yt_key,
        platforms=PLATFORMS,
        platform_labels=PLATFORM_LABELS,
        fb_cookies=fb_cookies_status,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@app.route("/setup/config", methods=["POST"])
def save_config():
    yt_key = request.form.get("youtube_api_key", "").strip()
    if yt_key and not config.YOUTUBE_API_KEY:
        db.set_config("youtube_api_key", yt_key)
    return redirect(url_for("setup"))


@app.route("/setup/client", methods=["POST"])
def save_client():
    client_id = request.form.get("client_id") or None
    name = request.form.get("name", "").strip()
    if name:
        db.upsert_client(client_id, name)
    return redirect(url_for("setup"))


@app.route("/setup/client/delete", methods=["POST"])
def delete_client():
    client_id = request.form.get("client_id")
    if client_id:
        db.delete_client(int(client_id))
    return redirect(url_for("setup"))


@app.route("/setup/account", methods=["POST"])
def save_account():
    account_id = request.form.get("account_id") or None
    client_id = request.form.get("client_id")
    platform = request.form.get("platform")
    handle = request.form.get("handle", "").strip()
    url = request.form.get("url", "").strip()

    if handle and platform and client_id:
        db.upsert_account(account_id, client_id, platform, handle, url, 0, None)
    return redirect(url_for("setup"))


@app.route("/setup/account/delete", methods=["POST"])
def delete_account():
    account_id = request.form.get("account_id")
    if account_id:
        db.delete_account(int(account_id))
    return redirect(url_for("setup"))


_ALLOWED_IMG_DOMAINS = frozenset([
    "instagram.com", "cdninstagram.com", "fbcdn.net",
    "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "tiktokv.com", "musical.ly",
])


@app.route("/img_proxy")
def img_proxy():
    import requests as _req
    from flask import Response
    url = request.args.get("url", "")
    if not url or not url.startswith("https://"):
        return "", 400
    try:
        host = _urlparse(url).hostname or ""
    except Exception:
        return "", 400
    if not any(host == d or host.endswith("." + d) for d in _ALLOWED_IMG_DOMAINS):
        return "", 403
    try:
        r = _req.get(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            "Referer": "https://www.instagram.com/",
        }, timeout=8, stream=True, allow_redirects=False)
        if r.status_code >= 300:
            return "", 502
        content_type = r.headers.get("Content-Type", "")
        if not content_type.lower().startswith("image/"):
            return "", 502
        return Response(r.content, content_type=content_type,
                        headers={"Cache-Control": "public, max-age=3600"})
    except Exception:
        return "", 502

db.init_db()
db.reset_stuck_jobs()


_scheduler = None


def _start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    interval_hours = int(db.get_config("auto_refresh_hours", "6") or "6")
    if interval_hours <= 0:
        return None

    from apscheduler.schedulers.background import BackgroundScheduler

    sched = BackgroundScheduler(timezone="UTC", daemon=True)
    sched.add_job(
        worker.enqueue_refresh_all,
        trigger="interval",
        hours=interval_hours,
        id="auto_refresh_all",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    sched.start()
    _scheduler = sched
    return sched


@app.before_request
def _ensure_scheduler_started():
    if _scheduler is None:
        try:
            _start_scheduler()
        except Exception as exc:  # pragma: no cover — defensive
            import sys
            print(f"[scheduler] failed to start: {exc}", file=sys.stderr)


if __name__ == "__main__":
    db.init_db()
    app.run(debug=not config.IS_PRODUCTION, host=config.HOST, port=config.PORT)
