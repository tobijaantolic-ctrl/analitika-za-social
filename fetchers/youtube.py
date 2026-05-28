import requests
import re

import config

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"


def _resolve_channel_id(handle_or_url, api_key):
    """Resolve @handle, channel URL, or raw channel ID to a channel ID."""
    text = handle_or_url.strip()

    # Extract from URL
    if "youtube.com" in text:
        m = re.search(r"youtube\.com/@([^/?&]+)", text)
        if m:
            text = "@" + m.group(1)
        m2 = re.search(r"youtube\.com/channel/([^/?&]+)", text)
        if m2:
            return m2.group(1)

    # Already a channel ID
    if re.match(r"^UC[\w-]{22}$", text):
        return text

    # @handle lookup
    handle = text.lstrip("@")
    r = requests.get(
        f"{YOUTUBE_API}/channels",
        params={"part": "id", "forHandle": handle, "key": api_key},
        timeout=10,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if items:
        return items[0]["id"]

    return None


def fetch(account):
    from database import get_config

    api_key = config.YOUTUBE_API_KEY or get_config("youtube_api_key")
    if not api_key:
        return {"error": "YouTube API ključ ni nastavljen. Pojdi na Setup."}

    try:
        channel_id = _resolve_channel_id(account["handle"], api_key)
        if not channel_id:
            return {"error": f"Kanal ni najden: {account['handle']}"}

        r = requests.get(
            f"{YOUTUBE_API}/channels",
            params={
                "part": "statistics",
                "id": channel_id,
                "key": api_key,
            },
            timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return {"error": "Kanal ni najden"}

        stats = items[0]["statistics"]
        return {
            "followers": int(stats.get("subscriberCount", 0)),
            "views": int(stats.get("viewCount", 0)),
            "likes": None,  # YouTube ne izpostavi skupnih všečkov kanala
            "posts": int(stats.get("videoCount", 0)),
        }
    except Exception as e:
        return {"error": str(e)}
