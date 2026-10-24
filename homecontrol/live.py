"""Live 'dad on the TV': webcam + microphone -> MPEG-TS over HTTP -> Samsung TV via DLNA.

One ffmpeg process captures and encodes; every HTTP client (the TV probes more than once)
gets the same stream from the point it joins."""
import queue
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sounddevice as sd

from .reminder import FONT

PATH = "/live.ts"
RATE, CHANNELS = 48000, 2


def wasapi_input(name):
    """Index of the WASAPI input device whose name contains `name`. The Intel mic array
    hangs when opened through DirectShow, so audio is captured here instead of in ffmpeg."""
    wasapi = next(i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"])
    for i, d in enumerate(sd.query_devices()):
        if d["hostapi"] == wasapi and d["max_input_channels"] > 0 and name in d["name"]:
            return i
    raise SystemExit(f"找不到麦克风: {name}")


class Broadcaster:
    def __init__(self, camera, microphone, label):
        self.clients = set()
        self.lock = threading.Lock()
        vf = ("scale=1920:1080:force_original_aspect_ratio=decrease,"
              "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x05070f,"
              "drawbox=x=0:y=0:w=iw:h=ih:color=0x00e5ff@0.9:t=8,"
              f"drawtext=fontfile='{FONT}':text='{label}':fontsize=56:fontcolor=0x00e5ff:"
              "x=60:y=50:borderw=2:bordercolor=0x05070f")
        # -nostdin: stdin carries the audio, ffmpeg must not read it as keyboard commands
        cmd = [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "dshow", "-rtbufsize", "200M", "-video_size", "1280x720", "-framerate", "30",
               "-i", f"video={camera}",
               "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS), "-use_wallclock_as_timestamps", "1",
               "-i", "pipe:0",
               "-map", "0:v", "-map", "1:a", "-vf", vf, "-af", "aresample=async=1000",
               "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
               "-pix_fmt", "yuv420p", "-g", "30", "-b:v", "4M",
               "-c:a", "aac", "-ar", "48000", "-b:a", "128k",
               "-f", "mpegts", "-"]
        self.mic = wasapi_input(microphone)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        threading.Thread(target=self._record, daemon=True).start()
        threading.Thread(target=self._pump, daemon=True).start()

    def _record(self):
        # ffmpeg waits for audio before it emits anything, so keep the mic flowing:
        # if Windows resets the device, reopen it
        while self.alive():
            try:
                with sd.RawInputStream(device=self.mic, samplerate=RATE, channels=CHANNELS,
                                       dtype="int16", blocksize=960) as stream:
                    while self.alive():
                        data, _ = stream.read(960)
                        self.proc.stdin.write(bytes(data))
            except sd.PortAudioError:
                time.sleep(0.5)
            except OSError:  # ffmpeg exited
                return

    def _pump(self):
        while True:
            chunk = self.proc.stdout.read(188 * 64)
            if not chunk:
                break
            with self.lock:
                for q in list(self.clients):
                    try:
                        q.put_nowait(chunk)
                    except queue.Full:  # slow client: drop it rather than stall everyone
                        self.clients.discard(q)
        with self.lock:
            for q in self.clients:
                q.put_nowait(None)

    def subscribe(self):
        q = queue.Queue(maxsize=2000)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        self.proc.terminate()


def start_live_server(broadcaster, port):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # stream until we close; no Content-Length

        def log_message(self, *args):
            pass

        def _headers(self):
            if self.path != PATH:
                self.send_error(404)
                return False
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("transferMode.dlna.org", "Streaming")
            self.send_header("contentFeatures.dlna.org", "DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000")
            self.end_headers()
            return True

        def do_HEAD(self):
            self._headers()

        def do_GET(self):
            if not self._headers():
                return
            q = broadcaster.subscribe()
            try:
                while (chunk := q.get()) is not None:
                    self.wfile.write(chunk)
            except OSError:
                pass  # TV disconnected
            finally:
                broadcaster.unsubscribe(q)

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
