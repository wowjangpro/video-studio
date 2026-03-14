#!/usr/bin/env python3
"""YouTube 영상 다운로드 스크립트 (yt-dlp)"""

import sys
import json
import yt_dlp


def progress_hook(d):
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes", 0)
        if total > 0:
            percent = round(downloaded / total * 100, 1)
        else:
            percent = -1
        print(
            json.dumps({"status": "downloading", "percent": percent}, ensure_ascii=False),
            flush=True,
        )
    elif d["status"] == "finished":
        print(
            json.dumps({"status": "processing"}, ensure_ascii=False),
            flush=True,
        )


def main():
    if len(sys.argv) < 2:
        print(
            json.dumps({"status": "error", "message": "Usage: download.py <URL> [output_path] [--info-only]"}),
            flush=True,
        )
        sys.exit(1)

    url = sys.argv[1]
    info_only = "--info-only" in sys.argv
    output_path = None if info_only else (sys.argv[2] if len(sys.argv) >= 3 else None)

    if not info_only and not output_path:
        print(
            json.dumps({"status": "error", "message": "output_path is required for download"}),
            flush=True,
        )
        sys.exit(1)

    # 영상 정보 가져오기
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "socket_timeout": 30}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get("title", "Unknown")
            duration = info.get("duration", 0)
            description = info.get("description", "")
            print(
                json.dumps(
                    {"status": "info", "title": title, "duration": duration, "description": description},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except Exception as e:
        print(
            json.dumps({"status": "error", "message": f"영상 정보를 가져올 수 없습니다: {str(e)}"}),
            flush=True,
        )
        sys.exit(1)

    if info_only:
        sys.exit(0)

    # 다운로드
    ydl_opts = {
        "format": "bestvideo[height<=480][ext=mp4]+bestaudio/best[height<=480][ext=mp4]/best",
        "outtmpl": output_path,
        "merge_output_format": "mp4",
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        print(
            json.dumps({"status": "done", "path": output_path}, ensure_ascii=False),
            flush=True,
        )
    except Exception as e:
        print(
            json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False),
            flush=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
