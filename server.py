#!/usr/bin/env python3
"""
BeatMatch local server
- Serves index.html at http://localhost:8765
- GET /download?url=<youtube-or-video-url>  →  downloads via yt-dlp, streams with range support
- Requires: pip install yt-dlp
- Optional: install ffmpeg for best quality (brew install ffmpeg)
"""

import http.server
import socketserver
import subprocess
import tempfile
import os
import json
import urllib.parse
from pathlib import Path

PORT = 8765
DIR  = Path(__file__).parent


def has_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


class BeatMatchHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIR), **kwargs)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/download":
            self._handle_download(parsed)
        else:
            super().do_GET()

    def _handle_download(self, parsed):
        params = urllib.parse.parse_qs(parsed.query)
        url = params.get("url", [None])[0]
        if not url:
            self._json_error(400, "Missing ?url= parameter")
            return

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                tmp_path = f.name

            print(f"[yt-dlp] downloading: {url}")

            # Prefer a single-file h264+aac mp4 so Chrome can always play it.
            # Falls back progressively to whatever is available.
            if has_ffmpeg():
                fmt = (
                    "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                    "/best[ext=mp4]/best"
                )
                extra = ["--merge-output-format", "mp4", "--recode-video", "mp4"]
            else:
                # No ffmpeg — pick a pre-muxed mp4 so no merging is needed
                fmt = (
                    "best[ext=mp4][vcodec^=avc1]"
                    "/best[ext=mp4]"
                    "/best"
                )
                extra = []

            result = subprocess.run(
                [
                    "yt-dlp",
                    "-f", fmt,
                    *extra,
                    "-o", tmp_path,
                    "--no-playlist",
                    "--no-warnings",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )

            if result.returncode != 0:
                msg = result.stderr.strip() or result.stdout.strip() or "yt-dlp failed"
                print(f"[yt-dlp] error: {msg}")
                self._json_error(500, msg)
                return

            # yt-dlp may rename the file (e.g. when recoding); find the actual output
            actual_path = tmp_path
            if not os.path.exists(actual_path) or os.path.getsize(actual_path) == 0:
                self._json_error(500, "Downloaded file is empty or missing.")
                return

            self._stream_file(actual_path, "video/mp4")

        except subprocess.TimeoutExpired:
            self._json_error(504, "Download timed out (>3 min). Try a shorter clip.")
        except BrokenPipeError:
            pass
        except Exception as e:
            self._json_error(500, str(e))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _stream_file(self, path, content_type):
        """Send a file with HTTP range-request support so Chrome can seek."""
        size = os.path.getsize(path)
        range_header = self.headers.get("Range", "")

        start, end = 0, size - 1
        status = 200

        if range_header.startswith("bytes="):
            try:
                parts = range_header[6:].split("-")
                start = int(parts[0]) if parts[0] else 0
                end   = int(parts[1]) if parts[1] else size - 1
                end   = min(end, size - 1)
                status = 206
            except (ValueError, IndexError):
                pass

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", 'inline; filename="video.mp4"')
        self._cors()
        self.end_headers()

        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _json_error(self, code, message):
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


def check_ytdlp():
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


if __name__ == "__main__":
    if not check_ytdlp():
        print("yt-dlp not found.  Install it:  pip3 install yt-dlp")
        raise SystemExit(1)

    ffmpeg = has_ffmpeg()
    print(f"ffmpeg: {'found ✓' if ffmpeg else 'not found — install for best quality: brew install ffmpeg'}")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), BeatMatchHandler) as httpd:
        print(f"BeatMatch server running → http://localhost:{PORT}")
        print("Press Ctrl-C to stop.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
