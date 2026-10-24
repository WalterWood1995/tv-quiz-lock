"""Tiny HTTP server so the TV can fetch the reminder video. Supports Range requests,
which Samsung's media player needs for seeking/streaming MP4."""
import os
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial

from .reminder import ASSETS


class RangeHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return None
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        f = open(path, "rb")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else size - int(b)
            end = int(b) if a and b else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("transferMode.dlna.org", "Streaming")
        self.end_headers()
        f.seek(start)
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        while self._remaining > 0:
            chunk = source.read(min(65536, self._remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            self._remaining -= len(chunk)


def local_ip(towards):
    """The LAN address of this PC as seen from `towards` (the TV)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((towards, 9))
        return s.getsockname()[0]


def start_server(port):
    handler = partial(RangeHandler, directory=str(ASSETS))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
