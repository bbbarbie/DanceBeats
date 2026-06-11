#!/usr/bin/env python3
"""
BeatMatch local server
- Serves index.html at http://localhost:8765
- GET /download?url=<youtube-or-video-url>  →  streams mp4 via yt-dlp
- Requires: pip install yt-dlp
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
            # Write to a predictable temp file so we can stream it back
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                tmp_path = f.name

            print(f"[yt-dlp] downloading: {url}")
            result = subprocess.run(
                [
                    "yt-dlp",
                    "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "--merge-output-format", "mp4",
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
                self._json_error(500, result.stderr.strip() or "yt-dlp failed")
                return

            size = os.path.getsize(tmp_path)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", 'inline; filename="video.mp4"')
            self._cors()
            self.end_headers()

            with open(tmp_path, "rb") as f:
                while chunk := f.read(65536):
                    self.wfile.write(chunk)

        except subprocess.TimeoutExpired:
            self._json_error(504, "Download timed out (>3 min). Try a shorter video.")
        except BrokenPipeError:
            pass  # client closed connection
        except Exception as e:
            self._json_error(500, str(e))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

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
        print("yt-dlp not found. Install it first:")
        print("  pip install yt-dlp")
        raise SystemExit(1)

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), BeatMatchHandler) as httpd:
        print(f"BeatMatch server running → http://localhost:{PORT}")
        print("Press Ctrl-C to stop.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
