#!/usr/bin/env python3
"""Transcribe linked TikTok media files and store the results for the dashboard."""

import argparse
import csv
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import database as db

MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mp3", ".wav", ".m4a"}


def duration_and_audio(path):
    duration_command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    audio_command = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    duration_result = subprocess.run(duration_command, capture_output=True, text=True, check=False)
    audio_result = subprocess.run(audio_command, capture_output=True, text=True, check=False)
    duration = duration_result.stdout.strip()
    return (float(duration) if duration else None, bool(audio_result.stdout.strip()))


def manifest_links(path):
    if not path:
        return {}
    links = {}
    with open(path, newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            source_path = row.get("source_path") or row.get("file_path") or row.get("file_name")
            if source_path and row.get("video_url"):
                links[source_path] = row["video_url"]
                links[Path(source_path).name] = row["video_url"]
    return links


def media_paths(folder, single_file):
    if single_file:
        return [Path(single_file).expanduser().resolve()]
    root = Path(folder).expanduser().resolve()
    return sorted(path for path in root.rglob("*") if path.suffix.lower() in MEDIA_EXTENSIONS)


def main():
    parser = argparse.ArgumentParser(description="Lokalna transkripcija povezanih TikTok videov.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--folder", help="Mapa z odobrenimi video datotekami.")
    source.add_argument("--file", help="Ena video datoteka za preizkus.")
    parser.add_argument("--account-id", type=int, help="TikTok account id za povezovanje z objavami.")
    parser.add_argument("--video-url", help="TikTok URL za eno nalozeno video datoteko.")
    parser.add_argument("--manifest", help="CSV s stolpcema source_path/file_name in video_url.")
    parser.add_argument("--model", default="turbo", help="Whisper model, privzeto turbo.")
    parser.add_argument("--language", default="sl", help="Jezik govora, privzeto sl.")
    parser.add_argument("--limit", type=int, help="Največ datotek v tej izvedbi.")
    parser.add_argument("--force", action="store_true", help="Ponovno obdelaj že zaključene prepise.")
    args = parser.parse_args()
    if args.video_url and not args.file:
        parser.error("--video-url je dovoljen samo skupaj z --file.")

    try:
        import whisper
    except ImportError:
        raise SystemExit(
            "Python okolje nima Whisperja. Zaženi skript z okoljem, kjer je nameščen whisper."
        )

    db.init_db()
    completed_paths = {
        row["source_path"] for row in db.get_media_transcripts()
        if row["status"] == "complete"
    }
    links = manifest_links(args.manifest)
    paths = media_paths(args.folder, args.file)
    if args.limit:
        paths = paths[: args.limit]

    pending = [path for path in paths if args.force or str(path) not in completed_paths]
    if not pending:
        print("Ni novih datotek za obdelavo.")
        return

    print(f"Nalaganje Whisper modela {args.model} ...", flush=True)
    model = whisper.load_model(args.model)
    complete_count = 0
    for index, path in enumerate(pending, start=1):
        duration, has_audio = duration_and_audio(path)
        video_url = args.video_url or links.get(str(path)) or links.get(path.name)
        source_label = (
            f"TikTok video - lokalni Whisper ({args.model})"
            if video_url else f"Lokalni Whisper ({args.model})"
        )
        if not has_audio:
            db.save_media_transcript(
                source_path=str(path),
                file_name=path.name,
                source_label="TikTok video brez zvoka" if video_url else "Lokalna datoteka",
                status="no_audio",
                account_id=args.account_id,
                video_url=video_url,
                duration_seconds=duration,
                error="Datoteka nima zvocne sledi.",
            )
            print(f"[{index}/{len(pending)}] Brez zvoka: {path.name}", flush=True)
            continue
        print(f"[{index}/{len(pending)}] Transkribiram: {path.name}", flush=True)
        try:
            result = model.transcribe(str(path), language=args.language, task="transcribe", fp16=False)
            transcript = (result.get("text") or "").strip()
            db.save_media_transcript(
                source_path=str(path),
                file_name=path.name,
                source_label=source_label,
                status="complete",
                transcript=transcript,
                account_id=args.account_id,
                video_url=video_url,
                duration_seconds=duration,
            )
            complete_count += 1
        except Exception as exc:
            db.save_media_transcript(
                source_path=str(path),
                file_name=path.name,
                source_label=source_label,
                status="error",
                account_id=args.account_id,
                video_url=video_url,
                duration_seconds=duration,
                error=str(exc)[:300],
            )
            print(f"[{index}/{len(pending)}] Napaka: {path.name}: {exc}", flush=True)
    print(f"Zakljuceno: {complete_count} novih transkriptov.", flush=True)


if __name__ == "__main__":
    main()
