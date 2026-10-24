"""电视答题页的 HTTP 服务：一个页面 + 几个 JSON 接口。判题和记账交给 host（Watcher）。

host 需要提供：quiz_lang / quiz_state() / quiz_question(difficulty) / quiz_answer(qid, choice) / heartbeat()
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .speech import speak

PAGE = Path(__file__).resolve().parent.parent / "assets" / "quiz.html"


class QuizHandler(BaseHTTPRequestHandler):
    host = None

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/quiz"):
            # 每次都现读文件：改了页面不用重启，电视下次打开答题页就是新的
            page = PAGE.read_bytes().replace(b"__LANG__", self.host.quiz_lang.encode())
            self._send(200, page, "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(self.host.quiz_state())
        elif path == "/api/tts":
            text = parse_qs(urlsplit(self.path).query).get("text", [""])[0]
            try:
                self._send(200, speak(text, self.host.quiz_lang), "audio/mpeg")
            except Exception as e:  # 没网或没装 edge-tts：页面不出声，照常答题
                self._json({"error": str(e)}, 503)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path, body = self.path.split("?")[0], self._body()
        try:
            if path == "/api/question":
                self._json(self.host.quiz_question(body.get("difficulty", "medium")))
            elif path == "/api/answer":
                self._json(self.host.quiz_answer(str(body.get("id", "")), int(body.get("choice", -1))))
            elif path == "/api/heartbeat":
                self.host.heartbeat()
                self._json(self.host.quiz_state())
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # 页面上能看到原因，服务不中断
            self._json({"error": str(e)}, 500)


def start_quiz_server(host, port):
    handler = type("BoundQuizHandler", (QuizHandler,), {"host": host})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
