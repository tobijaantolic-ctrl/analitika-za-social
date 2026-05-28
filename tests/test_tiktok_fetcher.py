import os
import sys
import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from fetchers import tiktok


class TikTokDiscoveryTestCase(unittest.TestCase):
    def test_paginated_public_list_is_preferred_and_deduplicated(self):
        output = "\n".join([
            "https://www.tiktok.com/@kdo.kakor.on/video/1",
            "https://www.tiktok.com/@kdo.kakor.on/video/2",
            "https://www.tiktok.com/@kdo.kakor.on/video/1",
            "https://www.tiktok.com/@someone.else/video/3",
        ])
        with patch.object(
            tiktok.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=output, stderr=""),
        ), patch.object(tiktok, "_discover_video_urls_openclaw") as browser_fallback:
            urls = tiktok._discover_video_urls("https://www.tiktok.com/@kdo.kakor.on")

        self.assertEqual(urls, [
            "https://www.tiktok.com/@kdo.kakor.on/video/1",
            "https://www.tiktok.com/@kdo.kakor.on/video/2",
        ])
        browser_fallback.assert_not_called()

    def test_browser_discovery_is_used_when_public_list_cannot_run(self):
        with patch.object(
            tiktok.subprocess,
            "run",
            side_effect=OSError("yt-dlp ni nameščen"),
        ), patch.object(
            tiktok,
            "_discover_video_urls_openclaw",
            return_value=["https://www.tiktok.com/@fallback/video/10"],
        ) as browser_fallback:
            urls = tiktok._discover_video_urls("https://www.tiktok.com/@fallback")

        self.assertEqual(urls, ["https://www.tiktok.com/@fallback/video/10"])
        browser_fallback.assert_called_once()

    def test_image_post_is_not_reported_as_transcribable_video(self):
        payload = {
            "__DEFAULT_SCOPE__": {
                "webapp.video-detail": {
                    "itemInfo": {
                        "itemStruct": {
                            "author": {"uniqueId": "kdo.kakor.on"},
                            "stats": {"playCount": 9, "diggCount": 2},
                            "createTime": 1770000000,
                            "desc": "Foto objava",
                            "video": {},
                            "imagePost": {"images": [{}, {}]},
                        }
                    }
                }
            }
        }
        html = (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">'
            + json.dumps(payload)
            + "</script>"
        )
        with patch.object(
            tiktok.requests,
            "get",
            return_value=SimpleNamespace(text=html),
        ):
            post = tiktok._fetch_video_stats(
                "https://www.tiktok.com/@kdo.kakor.on/video/1"
            )

        self.assertFalse(post["is_video"])
        self.assertIsNone(post["transcript"])


if __name__ == "__main__":
    unittest.main()
