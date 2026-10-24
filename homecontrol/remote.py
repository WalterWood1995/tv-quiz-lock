"""Samsung Tizen TV remote control over the TV's websocket API (port 8002).

First connection shows an 'Allow' prompt on the TV; the token it returns is saved so
later connections are silent. Written directly on websocket-client because this TV
(QN85B, 2022) sends ms.remote.touchEnable before ms.channel.connect, which
samsungtvws treats as a failed connection."""
import base64
import json
import ssl
import urllib.request
from pathlib import Path

import websocket

TOKEN_FILE = Path(__file__).resolve().parent.parent / "data" / "tv_token.txt"

# 2020 年以后的机型（含这台 QN85B）浏览器是 3202010022079；更早的机型是 org.tizen.browser
BROWSER_IDS = ("3202010022079", "org.tizen.browser")

# Friendly names -> Samsung key codes
KEYS = {
    "power": "KEY_POWER", "home": "KEY_HOME", "back": "KEY_RETURN", "ok": "KEY_ENTER",
    "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
    "vol+": "KEY_VOLUP", "vol-": "KEY_VOLDOWN", "mute": "KEY_MUTE",
    "ch+": "KEY_CHUP", "ch-": "KEY_CHDOWN", "source": "KEY_SOURCE", "hdmi": "KEY_HDMI",
    "play": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP", "menu": "KEY_MENU",
    "exit": "KEY_EXIT", "info": "KEY_INFO", "guide": "KEY_GUIDE",
}


class PairingError(RuntimeError):
    pass


class TVRemote:
    def __init__(self, ip, name="HomeControl", timeout=45):
        self.ip, self.timeout = ip, timeout
        self.name = base64.b64encode(name.encode()).decode()
        self.ws = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self):
        token = TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else ""
        url = f"wss://{self.ip}:8002/api/v2/channels/samsung.remote.control?name={self.name}"
        if token:
            url += f"&token={token}"
        self.ws = websocket.create_connection(url, sslopt={"cert_reqs": ssl.CERT_NONE},
                                              timeout=self.timeout, skip_utf8_validation=True)
        while True:
            msg = self._recv()
            event = msg.get("event")
            if event == "ms.channel.connect":
                new = msg.get("data", {}).get("token")
                if new and new != token:
                    TOKEN_FILE.parent.mkdir(exist_ok=True)
                    TOKEN_FILE.write_text(new)
                return
            if event in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                self.close()
                raise PairingError("电视拒绝了连接，或没有在电视上点「允许」")
            # ms.remote.touchEnable / touchDisable etc. arrive first on this model: skip

    def _recv(self):
        return json.loads(self.ws.recv())

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def _emit(self, payload):
        self.ws.send(json.dumps(payload))

    def key(self, key):
        code = KEYS.get(key.lower(), key.upper())
        self._emit({"method": "ms.remote.control", "params": {
            "Cmd": "Click", "DataOfCmd": code, "Option": "false", "TypeOfRemote": "SendRemoteKey"}})

    def apps(self):
        self._emit({"method": "ms.channel.emit", "params": {"event": "ed.installedApp.get", "to": "host"}})
        for _ in range(10):
            msg = self._recv()
            if msg.get("event") == "ed.installedApp.get":
                return [(a["appId"], a["name"]) for a in msg["data"]["data"]]
        return []

    def launch(self, app_id, meta=""):
        """Start an installed app; for the browser, meta is the URL to open."""
        self._emit({"method": "ms.channel.emit", "params": {
            "event": "ed.apps.launch", "to": "host",
            "data": {"appId": app_id, "action_type": "NATIVE_LAUNCH" if not meta else "DEEP_LINK",
                     "metaTag": meta}}})

    def browser_id(self):
        """The built-in browser's app id differs by model year; ask the TV which one it has."""
        for app_id in BROWSER_IDS:
            try:
                with urllib.request.urlopen(f"http://{self.ip}:8001/api/v2/applications/{app_id}", timeout=4):
                    return app_id
            except OSError:  # 404 for an id this model does not have
                continue
        return BROWSER_IDS[-1]

    def open_browser(self, url):
        """Older models open `url`; the QN85B (2022) ignores websocket launches of its browser
        entirely — use SamsungTV.launch_app plus a browser home page instead."""
        self.launch(self.browser_id(), url)
