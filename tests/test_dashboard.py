import json
import base64
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

import app
import database as db


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", os.path.join(self.temp_dir.name, "analytics.db"))
        self.db_patch.start()
        db.init_db()
        conn = db.get_db()
        conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Martin Golob')")
        conn.execute(
            """INSERT INTO accounts (id, client_id, platform, handle, url)
               VALUES (1, 1, 'instagram', 'golobmartin', 'https://www.instagram.com/golobmartin/')"""
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_numbers_are_exact_and_instagram_videos_link_to_reels(self):
        db.save_snapshot(
            1,
            {
                "followers": 73033,
                "likes": 14747,
                "posts": 597,
                "posts_json": json.dumps(
                    [
                        {
                            "shortcode": "abc123",
                            "is_video": True,
                            "timestamp": 1779297432,
                            "views": 21800,
                            "likes": 328,
                            "caption": "Prazniki",
                        }
                    ]
                ),
            },
        )

        account = app.get_dashboard_data()[0]["accounts"][0]
        self.assertEqual(account["followers"]["fmt"], "73.033")
        self.assertEqual(account["posts_list"][0]["views_fmt"], "21.800")
        self.assertEqual(account["posts_list"][0]["likes_fmt"], "328")
        self.assertEqual(account["posts_list"][0]["url"], "https://www.instagram.com/reel/abc123/")
        self.assertIn("youtube", app.PLATFORMS)

    def test_refresh_keeps_existing_post_numbers_if_fetcher_has_no_list(self):
        original_posts = json.dumps([{"shortcode": "existing", "views": 5000, "likes": 42}])
        db.save_snapshot(1, {"followers": 73000, "posts_json": original_posts})

        empty_list_result = {"followers": 73010, "likes": 1, "posts": 598, "posts_json": None}
        fetcher = SimpleNamespace(fetch=lambda account: empty_list_result.copy())
        with patch.object(app.importlib, "import_module", return_value=fetcher):
            app.refresh_accounts([1])

        self.assertEqual(db.get_latest_good_snapshot(1)["posts_json"], original_posts)

    def test_refresh_does_not_replace_cumulative_views_with_lower_value(self):
        original_posts = json.dumps([{"shortcode": "reel1", "views": 88700, "likes": 970}])
        db.save_snapshot(1, {"followers": 73000, "posts_json": original_posts})
        reduced = {
            "followers": 73010,
            "posts_json": json.dumps([{"shortcode": "reel1", "views": 27810, "likes": 972}]),
        }
        fetcher = SimpleNamespace(fetch=lambda account: reduced.copy())
        with patch.object(app.importlib, "import_module", return_value=fetcher):
            app.refresh_accounts([1])

        posts = json.loads(db.get_latest_good_snapshot(1)["posts_json"])
        self.assertEqual(posts[0]["views"], 88700)
        self.assertEqual(posts[0]["likes"], 972)

    def test_refresh_keeps_known_post_like_when_public_page_omits_it(self):
        old = json.dumps([{"url": "https://facebook.example/post/1", "views": None, "likes": 197}])
        db.save_snapshot(1, {"followers": 118000, "posts_json": old})
        missing_reaction = {
            "followers": 118000,
            "posts_json": json.dumps(
                [{"url": "https://facebook.example/post/1", "views": None, "likes": None}]
            ),
        }
        fetcher = SimpleNamespace(fetch=lambda account: missing_reaction.copy())
        with patch.object(app.importlib, "import_module", return_value=fetcher):
            app.refresh_accounts([1])

        post = json.loads(db.get_latest_good_snapshot(1)["posts_json"])[0]
        self.assertEqual(post["likes"], 197)

    def test_period_view_only_shows_posts_in_selected_window(self):
        now = datetime.now(timezone.utc)
        db.save_snapshot(
            1,
            {
                "followers": 73033,
                "posts_json": json.dumps(
                    [
                        {
                            "shortcode": "recent",
                            "is_video": True,
                            "timestamp": int((now - timedelta(days=2)).timestamp()),
                            "views": 2500,
                            "likes": 81,
                        },
                        {
                            "shortcode": "older",
                            "is_video": True,
                            "timestamp": int((now - timedelta(days=14)).timestamp()),
                            "views": 9000,
                            "likes": 300,
                        },
                    ]
                ),
            },
        )

        account = app.get_dashboard_data(days=7)[0]["accounts"][0]
        self.assertEqual([post["shortcode"] for post in account["posts_list"]], ["recent"])
        self.assertEqual(account["known_posts_count"], 2)
        self.assertEqual(account["period"]["views_fmt"], "2.500")
        self.assertEqual(account["period"]["likes_fmt"], "81")
        self.assertEqual(account["period"]["views_coverage"], 1)
        self.assertEqual(account["period"]["engagement_fmt"], "3,2 %")

        response = self.client.get("/?days=7")
        self.assertIn(b"Zadnjih 7 dni", response.data)
        self.assertIn(b"I\xc5\xa1\xc4\x8di objavo", response.data)

    def test_video_filter_and_csv_export_follow_visible_scope(self):
        now = int(datetime.now(timezone.utc).timestamp())
        db.save_snapshot(
            1,
            {
                "followers": 73033,
                "posts_json": json.dumps(
                    [
                        {
                            "shortcode": "video",
                            "is_video": True,
                            "timestamp": now,
                            "views": 2500,
                            "likes": 81,
                            "caption": "Reel za porocilo",
                        },
                        {
                            "shortcode": "photo",
                            "is_video": False,
                            "timestamp": now,
                            "views": None,
                            "likes": 10,
                            "caption": "=NEVARNA_FORMULA",
                        },
                    ]
                ),
            },
        )

        filtered = app.get_dashboard_data(days=30, content_scope="video")[0]["accounts"][0]
        self.assertEqual([post["shortcode"] for post in filtered["posts_list"]], ["video"])

        response = self.client.get("/export.csv?days=30&content=video")
        export_text = response.data.decode("utf-8-sig")
        self.assertIn("Reel za porocilo", export_text)
        self.assertNotIn("NEVARNA_FORMULA", export_text)
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="social-analytics.csv"')

        full_export = self.client.get("/export.csv?days=30&content=all").data.decode("utf-8-sig")
        self.assertIn("'=NEVARNA_FORMULA", full_export)

        page = self.client.get("/?days=30&content=video").data
        self.assertIn(b"Reeli / videi", page)
        self.assertIn(b"rezultati so prikazani lo\xc4\x8deno", page)
        self.assertNotIn(b"sledilcev skupaj", page)
        self.assertIn(b"class=\"active\">Reeli / videi", self.client.get("/").data)

    def test_image_proxy_rejects_non_platform_domains(self):
        response = self.client.get("/img_proxy?url=https://example.com/private.png")
        self.assertEqual(response.status_code, 403)

    def test_refresh_ajax_handles_invalid_json_without_server_error(self):
        response = self.client.post(
            "/refresh_ajax",
            data="{bad json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["ok"])

    def test_auth_and_csrf_can_protect_mutating_routes(self):
        credentials = base64.b64encode(b"admin:secret").decode("ascii")
        auth_header = {"Authorization": f"Basic {credentials}"}
        fetcher = SimpleNamespace(fetch=lambda account: {"followers": 1})

        with patch.object(app.config, "AUTH_REQUIRED", True), patch.object(
            app.config, "ADMIN_PASSWORD", "secret"
        ), patch.object(app.config, "ADMIN_PASSWORD_HASH", None), patch.object(
            app.importlib, "import_module", return_value=fetcher
        ):
            self.assertEqual(self.client.get("/").status_code, 401)
            page = self.client.get("/", headers=auth_header)
            self.assertEqual(page.status_code, 200)
            with self.client.session_transaction() as sess:
                csrf_token = sess["_csrf_token"]
            missing_csrf = self.client.post("/refresh_ajax", json={"account_id": 1}, headers=auth_header)
            self.assertEqual(missing_csrf.status_code, 403)
            ok = self.client.post(
                "/refresh_ajax",
                json={"account_id": 1},
                headers={**auth_header, "X-CSRF-Token": csrf_token},
            )
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.get_json()["ok"])

    def test_transcript_library_and_linked_tiktok_export(self):
        conn = db.get_db()
        conn.execute(
            """INSERT INTO accounts (id, client_id, platform, handle, url)
               VALUES (2, 1, 'tiktok', '@zupnikm', 'https://www.tiktok.com/@zupnikm')"""
        )
        conn.commit()
        conn.close()
        video_url = "https://www.tiktok.com/@zupnikm/video/123"
        db.save_snapshot(
            2,
            {
                "posts_json": json.dumps(
                    [
                        {
                            "url": video_url,
                            "timestamp": int(datetime.now(timezone.utc).timestamp()),
                            "date": "27.05.2026",
                            "views": 500,
                            "likes": 25,
                        },
                        {
                            "url": "https://www.tiktok.com/@zupnikm/video/public",
                            "timestamp": int(datetime.now(timezone.utc).timestamp()),
                            "date": "27.05.2026",
                            "views": 725,
                            "likes": 40,
                            "transcript": "TikTok javni podnapisi.",
                            "transcript_source": "TikTok slovenski podnapisi",
                        },
                        {
                            "url": "https://www.tiktok.com/@zupnikm/video/photo",
                            "title": "Foto objava ni video prepis.",
                            "is_video": False,
                            "timestamp": int(datetime.now(timezone.utc).timestamp()),
                            "views": 900,
                            "likes": 90,
                        }
                    ]
                )
            },
        )
        db.save_media_transcript(
            source_path="/approved/video123.mp4",
            file_name="video123.mp4",
            source_label="Lokalni Whisper (turbo)",
            status="complete",
            transcript="To je celoten prepis videa.",
            account_id=2,
            video_url=video_url,
            duration_seconds=12.5,
        )
        db.save_media_transcript(
            source_path="/unlinked/edit-source.mp4",
            file_name="edit-source.mp4",
            source_label="Lokalni Whisper (turbo)",
            status="complete",
            transcript="Ta nepovezani montazni prepis ne sodi med TikToke.",
            account_id=2,
        )

        account = app.get_dashboard_data(days=30, content_scope="video")[0]["accounts"][1]
        self.assertEqual(account["posts_list"][0]["transcript"], "To je celoten prepis videa.")
        transcripts_page = self.client.get("/transcripts").data
        self.assertIn(b"@zupnikm", transcripts_page)
        self.assertIn(b"2 / 2 prepisov", transcripts_page)
        self.assertIn(b"Kopiraj vse prepise", transcripts_page)
        self.assertIn(b"Shrani .md", transcripts_page)
        self.assertIn(b"Prepisi pripravljeni za kopiranje", transcripts_page)
        self.assertIn(b"/transcripts/export/1.md", transcripts_page)
        self.assertIn(b"To je celoten prepis videa.", transcripts_page)
        self.assertIn(b"TikTok javni podnapisi.", transcripts_page)
        self.assertIn(b"725", transcripts_page)
        self.assertNotIn(b"nepovezani montazni prepis", transcripts_page)
        self.assertNotIn(b"Foto objava ni video prepis.", transcripts_page)
        export = self.client.get("/export.csv?days=30&content=video").data.decode("utf-8-sig")
        self.assertIn("To je celoten prepis videa.", export)
        self.assertIn("TikTok javni podnapisi.", export)
        self.assertNotIn("Foto objava ni video prepis.", export)
        markdown_response = self.client.get("/transcripts/export/1.md")
        markdown = markdown_response.data.decode("utf-8")
        self.assertEqual(markdown_response.status_code, 200)
        self.assertIn('filename="tiktok-prepisi-martin-golob.md"', markdown_response.headers["Content-Disposition"])
        self.assertIn("# TikTok prepisi - Martin Golob", markdown)
        self.assertIn("## @zupnikm", markdown)
        self.assertIn("- Ogledi: 500", markdown)
        self.assertIn("To je celoten prepis videa.", markdown)
        self.assertNotIn("Foto objava ni video prepis.", markdown)

    def test_tiktok_upload_runs_transcription_for_selected_video_only(self):
        conn = db.get_db()
        conn.execute(
            """INSERT INTO accounts (id, client_id, platform, handle, url)
               VALUES (2, 1, 'tiktok', '@zupnikm', 'https://www.tiktok.com/@zupnikm')"""
        )
        conn.commit()
        conn.close()
        video_url = "https://www.tiktok.com/@zupnikm/video/123456"
        db.save_snapshot(
            2,
            {"posts_json": json.dumps([{
                "url": video_url,
                "timestamp": int(datetime.now(timezone.utc).timestamp()),
                "date": "27.05.2026",
                "views": 10,
                "likes": 2,
            }])},
        )
        uploads_dir = Path(self.temp_dir.name) / "uploads"
        with patch.object(app, "TIKTOK_UPLOAD_DIR", uploads_dir), patch.object(
            app.subprocess, "run", return_value=SimpleNamespace(returncode=0)
        ) as transcribe:
            response = self.client.post(
                "/transcripts/upload",
                data={
                    "account_id": "2",
                    "video_url": video_url,
                    "video_file": (io.BytesIO(b"video"), "real-tiktok.mp4"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue((uploads_dir / "123456.mp4").exists())
        command = transcribe.call_args.args[0]
        self.assertIn("--video-url", command)
        self.assertIn(video_url, command)


if __name__ == "__main__":
    unittest.main()
